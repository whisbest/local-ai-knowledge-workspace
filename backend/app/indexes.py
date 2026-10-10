from __future__ import annotations
import time
import numpy as np
from fastapi import APIRouter
from .core import Record,Job,Vector,AppError,uid,dump,load,record_data,job_data,now
from .models_service import config_for,provider_for
from .providers import fingerprint
from .documents import require,invalidate,new_job


def embedding_config(store):
    with store.session() as s:id=load(s.get(Record,'preferences').payload)['default_embedding']
    if not id:raise AppError('EMBEDDING_CONFIG','请先配置并选择默认向量模型。')
    c=config_for(store,id,'embedding')
    if c.get('test_status')!='available' or not c.get('dimension'):raise AppError('EMBEDDING_UNTESTED','请先测试向量模型能力。')
    return c


def changed_indexes(s,values,previous):
    if all(values.get(k)==previous.get(k) for k in ('default_embedding','chunk_length','chunk_overlap')):return
    for d in s.query(Record).filter(Record.kind=='document',Record.state!='deleted'):
        if d.state in ('ready','parsed','needs_rebuild'):d.state='needs_rebuild'
    for r in s.query(Record).filter_by(kind='index',state='active'):r.state='stale'


def execute_index(store,id):
    config=embedding_config(store)
    with store.session() as s:
        j=s.get(Job,id);d=require(s,j.target,'document');v=require(s,load(j.payload)['version_id'],'version')
        version_id=v.id;doc_id=d.id
        chunks=sorted([record_data(r) for r in s.query(Record).filter_by(kind='chunk',parent_id=v.id)],key=lambda r:r['ordinal'])
        if not chunks:raise AppError('NO_CHUNKS','请先解析文档。')
        profile=load(v.payload)['chunk_profile'];prefs=load(s.get(Record,'preferences').payload)
        if profile!={'length':prefs['chunk_length'],'overlap':prefs['chunk_overlap']}:raise AppError('CHUNK_CHANGED','切片设置已变化，请重新处理文档。')
        invalidate(s,d)
        generation=Record(id=uid(),kind='index',parent_id=doc_id,state='building',payload=dump({'version_id':version_id,'fingerprint':config['fingerprint'],'dimension':config['dimension'],'profile':profile,'model_config_id':config['id']}));s.add(generation);generation_id=generation.id
        j.stage='embedding';j.total=len(chunks);j.done=0;d.state='embedding'
        # No old index is usable while rebuilding the same version.
    p=provider_for(store,config['id'],'embedding')
    try:
        with store.model_lock:
            for offset in range(0,len(chunks),2):
                with store.session() as s:
                    j=s.get(Job,id);d=s.get(Record,doc_id)
                    if j.state!='running' or d.state=='deleted' or load(d.payload)['current_version_id']!=version_id:return
                batch=chunks[offset:offset+2];vectors=p.embed([c['text'] for c in batch])
                if any(len(v)!=config['dimension'] for v in vectors):raise AppError('DIMENSION_CHANGED','模型返回维度已变化，请重新测试配置并重建。')
                with store.session() as s:
                    j=s.get(Job,id);d=s.get(Record,doc_id)
                    if j.state!='running' or d.state=='deleted' or load(d.payload)['current_version_id']!=version_id:return
                    for chunk,vector in zip(batch,vectors):
                        vector=np.asarray(vector,dtype=np.float32);vector/=np.linalg.norm(vector)
                        s.add(Vector(document_id=doc_id,version_id=version_id,generation_id=generation_id,fingerprint=config['fingerprint'],dimension=config['dimension'],chunk_id=chunk['id'],values=dump(vector.tolist())))
                    j.done=offset+len(batch);j.updated_at=now()
                # Small batches yield to queued questions without loading models concurrently.
                if getattr(store,'worker_runner',None):store.worker_runner.questions_at_boundary()
        with store.session() as s:
            j=s.get(Job,id);d=s.get(Record,doc_id);prefs=load(s.get(Record,'preferences').payload)
            if j.state!='running' or d.state=='deleted' or load(d.payload)['current_version_id']!=version_id:return
            if prefs['default_embedding']!=config['id'] or profile!={'length':prefs['chunk_length'],'overlap':prefs['chunk_overlap']}:raise AppError('CONFIG_CHANGED','处理期间配置已变化，请重新处理。')
            s.get(Record,generation_id).state='active';d.state='ready';j.state='completed';j.stage='ready';j.done=j.total;j.updated_at=now()
    except Exception:
        with store.session() as s:
            s.query(Vector).filter_by(generation_id=generation_id).delete()
            r=s.get(Record,generation_id)
            if r:r.state='failed'
        raise
    finally:
        p.close()
        with store.session() as s:
            idx=s.get(Record,generation_id)
            if idx and idx.state!='active':
                s.query(Vector).filter_by(generation_id=generation_id).delete();idx.state='failed'


def retrieve(store,question,scope=None,top_k=5):
    started=time.perf_counter();scope=scope or {};config=embedding_config(store)
    if not 1<=top_k<=10 or scope.get('type','all') not in ('all','kb','documents'):raise AppError('SCOPE','请选择全部资料、知识库或指定文档。')
    ids=scope.get('ids',[])
    if scope.get('type','all')!='all' and not ids:raise AppError('SCOPE','请选择至少一份资料或一个知识库。')
    with store.session() as s:
        docs=[r for r in s.query(Record).filter(Record.kind=='document',Record.state!='deleted')]
        docs=[r for r in docs if scope.get('type','all')=='all' or (scope['type']=='kb' and load(r.payload)['kb_id'] in ids) or (scope['type']=='documents' and r.id in ids)]
        warnings=[{'document_id':r.id,'name':load(r.payload)['name'],'state':r.state} for r in docs if r.state!='ready']
        active={r.parent_id:(r.id,load(r.payload)) for r in s.query(Record).filter_by(kind='index',state='active')}
        valid={r.id:record_data(r) for r in docs if r.state=='ready' and r.id in active and active[r.id][1]['fingerprint']==config['fingerprint'] and active[r.id][1]['version_id']==load(r.payload)['current_version_id']}
        valid_generations=[active[id][0] for id in valid]
    if not valid_generations:return {'items':[],'warnings':warnings,'elapsed_ms':(time.perf_counter()-started)*1000}
    p=provider_for(store,config['id'],'embedding')
    try:
        with store.model_lock:query=np.asarray(p.embed([question])[0],dtype=np.float32)
    finally:p.close()
    if len(query)!=config['dimension']:raise AppError('DIMENSION_CHANGED','查询向量维度不兼容，请重新测试并重建。')
    query/=np.linalg.norm(query);best=[]
    with store.session() as s:
        q=s.query(Vector).filter(Vector.generation_id.in_(valid_generations),Vector.fingerprint==config['fingerprint'],Vector.dimension==len(query))
        offset=0
        while True:
            rows=q.order_by(Vector.id).offset(offset).limit(128).all()
            if not rows:break
            matrix=np.asarray([load(r.values) for r in rows],dtype=np.float32);scores=matrix@query
            best.extend((float(score),r.chunk_id,r.document_id,r.version_id,r.generation_id) for r,score in zip(rows,scores));best=sorted(best,reverse=True)[:top_k];offset+=len(rows)
        items=[]
        for score,chunk_id,doc_id,version_id,generation_id in best:
            # Validate again after inference: delete/replace may have run while query embedding was in progress.
            s.expire_all();d=s.get(Record,doc_id);idx=s.get(Record,generation_id);chunk=s.get(Record,chunk_id)
            preferences=load(s.get(Record,'preferences').payload)
            if preferences['default_embedding']!=config['id']:raise AppError('CONFIG_CHANGED','检索期间向量配置已切换，请重新提问。',409)
            if not d or d.state!='ready' or load(d.payload)['current_version_id']!=version_id or not idx or idx.state!='active' or not chunk:continue
            payload=load(chunk.payload);items.append({'chunk_id':chunk_id,'document_id':doc_id,'version_id':version_id,'score':score,'file_name':load(d.payload)['name'],'format':load(d.payload)['format'],'text':payload['text'],'source_spans':payload['source_spans']})
    return {'items':items,'warnings':warnings,'elapsed_ms':(time.perf_counter()-started)*1000}


def mount_indexes(app,store):
    router=APIRouter(prefix='/api')
    @router.get('/indexes/impact')
    def impact():
        with store.session() as s:
            docs=[record_data(r) for r in s.query(Record).filter(Record.kind=='document',Record.state!='deleted')]
            return {'documents':docs,'total':len(docs),'vectors':s.query(Vector).count()}

    @router.post('/indexes/rebuild')
    def rebuild(body:dict):
        embedding_config(store)
        jobs=[]
        with store.session() as s:
            ids=body.get('document_ids') or [r.id for r in s.query(Record).filter(Record.kind=='document',Record.state!='deleted')]
            for id in ids:
                d=require(s,id,'document');v=require(s,load(d.payload)['current_version_id'],'version');invalidate(s,d);jobs.append(job_data(new_job(s,d,v,'process')))
        return jobs

    @router.post('/retrieval')
    def retrieval(body:dict):
        question=body.get('question','').strip()
        if not question or len(question)>4000:raise AppError('QUESTION','请输入 1–4000 字符的问题。')
        return retrieve(store,question,body.get('scope'),body.get('top_k',5))
    @router.post('/indexes/clear')
    def clear(body:dict):
        if body.get('confirm') is not True:raise AppError('CONFIRM','请确认只清理索引。')
        with store.session() as s:
            ids=body.get('document_ids') or [r.id for r in s.query(Record).filter(Record.kind=='document',Record.state!='deleted')]
            for id in ids:
                d=require(s,id,'document');invalidate(s,d);d.state='needs_rebuild'
                for j in s.query(Job).filter(Job.target==id,Job.state.in_(['queued','running'])):j.state='cancelled'
        return {'cleared':len(ids)}
    app.include_router(router)
