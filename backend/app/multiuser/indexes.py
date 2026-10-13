import time
import numpy as np
from fastapi import Request
from sqlalchemy import select, delete
from ..core import AppError
from .auth import identity
from .db import Record, Vector, Job, uid, record_data, job_data
from .tenancy import require_owned, require_model, validate_scope
from .documents import documents, invalidate, enqueue, lock_owner


def provider(store, owner, model_id, purpose):
    from .quotas import MeteredProvider
    context=getattr(store.worker_context,'value',None)
    cancelled=context[2] if context else lambda:False
    if context:
        from .worker import mark_external
        mark_external(store,context[0],context[1])
    if getattr(store, 'provider_factory', None):
        inner=store.provider_factory(owner, model_id, purpose)
    else:
        from .models import provider_for
        inner=provider_for(store, owner, model_id, purpose)
    return MeteredProvider(store,owner,model_id,inner,cancelled)


def build_index(store, job_id, lease=None):
    with store.session() as s:
        job=s.get(Job,job_id);owner=job.owner_id
        from .worker import valid_lease
        if job.state not in ('queued','running') or (lease and not valid_lease(job,lease)):return
        doc=require_owned(s,owner,job.target,'document',lock=True)
        version=require_owned(s,owner,job.payload['version_id'],'version')
        if doc.payload['current_version_id']!=version.id:return
        model=require_model(s,owner,job.payload['model_id'],'embedding')
        if not model.dimension or not model.fingerprint:raise AppError('MODEL_UNTESTED','请先测试向量模型。',409)
        model_id=model.id;fingerprint=model.fingerprint;dimension=model.dimension
        chunks=list(s.scalars(select(Record).where(Record.owner_id==owner,Record.parent_id==version.id,Record.kind=='chunk',Record.state!='deleted').order_by(Record.created_at,Record.id)))
        chunk_values=[{'id':r.id,**r.payload} for r in chunks]
        if not chunks:raise AppError('NO_CHUNKS','没有可索引的片段。',409)
        invalidate(s,owner,doc)
        index=Record(id=uid(),owner_id=owner,kind='index',parent_id=doc.id,model_id=model_id,state='building',
                     payload={'version_id':version.id,'fingerprint':fingerprint,'dimension':dimension,'profile':job.payload['profile']})
        s.add(index);s.flush();index_id=index.id;doc_id=doc.id;version_id=version.id
        doc.state='embedding';job.stage='embedding';job.total=len(chunks);job.done=0
    p=provider(store,owner,model_id,'embedding')
    try:
        for offset in range(0,len(chunk_values),8):
            batch=chunk_values[offset:offset+8]
            with store.session() as s:
                job=s.get(Job,job_id)
                if job.state not in ('queued','running') or (lease and not valid_lease(job,lease)):return
                require_model(s,owner,model_id,'embedding')
            embeddings=p.embed([c['text'] for c in batch])
            if len(embeddings)!=len(batch) or any(len(v)!=dimension for v in embeddings):raise AppError('DIMENSION','向量维度已变化，请重新测试并重建。',409)
            with store.session() as s:
                job=s.get(Job,job_id);doc=require_owned(s,owner,doc_id,'document',lock=True)
                if job.state not in ('queued','running') or (lease and not valid_lease(job,lease)) or doc.payload['current_version_id']!=version_id:return
                for chunk,values in zip(batch,embeddings):
                    array=np.asarray(values,dtype=np.float32);norm=np.linalg.norm(array)
                    if not np.isfinite(array).all() or norm==0:raise AppError('VECTOR','向量数值不合法。',502)
                    s.add(Vector(owner_id=owner,document_id=doc_id,version_id=version_id,index_id=index_id,
                                 chunk_id=chunk['id'],fingerprint=fingerprint,dimension=dimension,values=(array/norm).tolist()))
                job.done=offset+len(batch)
        with store.session() as s:
            job=s.get(Job,job_id);doc=require_owned(s,owner,doc_id,'document',lock=True)
            kb=require_owned(s,owner,doc.parent_id,'kb');require_model(s,owner,model_id,'embedding')
            if job.state not in ('queued','running') or (lease and not valid_lease(job,lease)) or doc.payload['current_version_id']!=version_id or kb.model_id!=model_id or kb.payload['profile']!=job.payload['profile']:return
            s.get(Record,index_id).state='active';doc.state='ready';job.state='completed';job.stage='ready'
    finally:
        p.close()
        with store.session() as s:
            index=s.get(Record,index_id)
            if index.state!='active':
                index.state='failed';s.execute(delete(Vector).where(Vector.owner_id==owner,Vector.index_id==index_id))


def candidates(s,owner,scope):
    scope=validate_scope(s,owner,scope)
    statement=documents(s,owner)
    if scope['type']=='kb':statement=statement.where(Record.parent_id.in_(scope['ids']))
    if scope['type']=='documents':statement=statement.where(Record.id.in_(scope['ids']))
    docs=list(s.scalars(statement))
    warnings=[{'document_id':r.id,'name':r.payload['name'],'state':r.state} for r in docs if r.state!='ready']
    valid={r.id:r for r in docs if r.state=='ready'}
    indexes=[]
    if valid:
        for index in s.scalars(select(Record).where(Record.owner_id==owner,Record.kind=='index',Record.state=='active',Record.parent_id.in_(valid))):
            if index.payload['version_id']==valid[index.parent_id].payload['current_version_id']:
                indexes.append(index)
    fingerprints={r.payload['fingerprint'] for r in indexes}
    if len(fingerprints)>1:raise AppError('INCOMPATIBLE','所选资料使用不同向量空间，请缩小范围或统一模型后重建。',409)
    return valid,indexes,warnings


def search_vectors(s,owner,indexes,query,top_k):
    """Bounded batches; owner filtering happens before loading any vector."""
    if not indexes:return []
    query=np.asarray(query,dtype=np.float32);norm=np.linalg.norm(query)
    if not np.isfinite(query).all() or norm==0:raise AppError('VECTOR','查询向量不合法。',502)
    query=query/norm
    fingerprints={r.payload['fingerprint'] for r in indexes}
    if len(fingerprints)!=1 or any(r.payload['dimension']!=len(query) for r in indexes):raise AppError('DIMENSION','查询向量与资料索引不兼容。',409)
    statement=select(Vector).where(Vector.owner_id==owner,Vector.index_id.in_([r.id for r in indexes]),
            Vector.fingerprint==next(iter(fingerprints)),Vector.dimension==len(query)).order_by(Vector.id)
    best=[]
    # One server-side cursor avoids re-planning/scanning the same owner/index filters
    # for every page. Rows and ORM objects remain bounded to one batch plus top_k.
    result=s.scalars(statement.execution_options(yield_per=128,stream_results=True))
    for rows in result.partitions(128):
        matrix=np.asarray([r.values for r in rows],dtype=np.float32);scores=matrix@query
        best.extend((float(score),r) for r,score in zip(rows,scores));best.sort(key=lambda pair:pair[0],reverse=True);best=best[:top_k]
    items=[]
    for score,row in best:
        doc=require_owned(s,owner,row.document_id,'document');chunk=require_owned(s,owner,row.chunk_id,'chunk')
        index=require_owned(s,owner,row.index_id,'index')
        if doc.state!='ready' or doc.payload['current_version_id']!=row.version_id or index.state!='active':continue
        items.append({'chunk_id':row.chunk_id,'document_id':doc.id,'version_id':row.version_id,'score':score,
                      'file_name':doc.payload['name'],'format':doc.payload['format'],'text':chunk.payload['text'],'source_spans':chunk.payload['source_spans']})
    return items


def retrieve(store,owner,question,scope=None,top_k=5):
    if not isinstance(question,str) or not 1<=len(question.strip())<=4000 or type(top_k)!=int or not 1<=top_k<=10:
        raise AppError('QUESTION','问题需为 1–4000 字符，检索数量为 1–10。',422)
    started=time.perf_counter()
    with store.session() as s:
        docs,indexes,warnings=candidates(s,owner,scope)
        if not indexes:return {'items':[],'warnings':warnings,'elapsed_ms':(time.perf_counter()-started)*1000}
        model_id=indexes[0].model_id;require_model(s,owner,model_id,'embedding')
        fingerprint=indexes[0].payload['fingerprint']
    p=provider(store,owner,model_id,'embedding')
    try:query=p.embed([question])[0]
    finally:p.close()
    with store.session() as s:
        docs,indexes,warnings=candidates(s,owner,scope)
        if indexes and indexes[0].payload['fingerprint']!=fingerprint:raise AppError('INDEX_CHANGED','检索期间索引已变化，请重试。',409)
        require_model(s,owner,model_id,'embedding')
        items=search_vectors(s,owner,indexes,query,top_k)
    return {'items':items,'warnings':warnings,'elapsed_ms':(time.perf_counter()-started)*1000}


def mount(app,store):
    @app.post('/api/retrieval')
    def retrieval(body:dict,request:Request):
        return retrieve(store,identity(request).id,body.get('question'),body.get('scope'),body.get('top_k',5))

    @app.get('/api/indexes/impact')
    def impact(request:Request):
        from sqlalchemy import func
        with store.session() as s:
            owner=identity(request).id;rows=list(s.scalars(documents(s,owner)))
            return {'documents':[record_data(r) for r in rows],'total':len(rows),'vectors':s.scalar(select(func.count()).select_from(Vector).where(Vector.owner_id==owner))}

    @app.post('/api/indexes/rebuild')
    def rebuild(body:dict,request:Request):
        owner=identity(request).id
        with store.session() as s:
            lock_owner(s,owner);ids=body.get('document_ids')
            if ids is None:ids=[r.id for r in s.scalars(documents(s,owner))]
            if not isinstance(ids,list) or len(ids)>100:raise AppError('SCOPE','文档范围不合法。',422)
            docs=[require_owned(s,owner,id,'document') for id in ids]
            result=[]
            for doc in docs:
                kb=require_owned(s,owner,doc.parent_id,'kb');require_model(s,owner,kb.model_id,'embedding')
                invalidate(s,owner,doc);result.append(job_data(enqueue(s,store,owner,doc)))
            return result

    @app.post('/api/indexes/clear')
    def clear(body:dict,request:Request):
        if body.get('confirm') is not True:raise AppError('CONFIRM','请确认清理索引。',409)
        owner=identity(request).id
        with store.session() as s:
            lock_owner(s,owner);ids=body.get('document_ids')
            if ids is None:ids=[r.id for r in s.scalars(documents(s,owner))]
            docs=[require_owned(s,owner,id,'document') for id in ids]
            for doc in docs:
                invalidate(s,owner,doc);doc.state='needs_rebuild'
                for job in s.scalars(select(Job).where(Job.owner_id==owner,Job.target==doc.id,Job.state.in_(['queued','running']))):job.state='cancelled';job.lease_token=None
        return {'cleared':len(docs)}

    @app.get('/api/knowledge-bases/{id}/embedding-impact')
    def binding_impact(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;kb=require_owned(s,owner,id,'kb')
            return {'embedding_id':kb.model_id,'profile':kb.payload['profile'],
                    'documents':len(list(s.scalars(documents(s,owner).where(Record.parent_id==id))))}

    @app.put('/api/knowledge-bases/{id}/embedding')
    def binding(id:str,body:dict,request:Request):
        if body.get('confirm') is not True:raise AppError('CONFIRM','更改向量配置需要确认重建。',409)
        owner=identity(request).id
        with store.session() as s:
            lock_owner(s,owner);kb=require_owned(s,owner,id,'kb',lock=True)
            require_model(s,owner,body.get('embedding_id'),'embedding')
            profile=body.get('profile',kb.payload['profile']);length=profile.get('length');overlap=profile.get('overlap')
            if type(length)!=int or type(overlap)!=int or not 1<=length<=12000 or not 0<=overlap<length:raise AppError('CHUNK_SETTINGS','切片参数不合法。',422)
            kb.model_id=body['embedding_id'];kb.payload={**kb.payload,'embedding_id':kb.model_id,'profile':profile}
            jobs=[]
            for doc in s.scalars(documents(s,owner).where(Record.parent_id==id)):
                invalidate(s,owner,doc);jobs.append(job_data(enqueue(s,store,owner,doc)))
            return {'jobs':jobs,'rebuild_required':True}
