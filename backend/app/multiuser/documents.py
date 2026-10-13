import hashlib
import json
import re
from pathlib import Path
from fastapi import Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy import select, func, delete, text
from ..core import AppError
from ..parsers import parse_file, chunk_spans
from .auth import identity
from .db import Record, Job, Vector, Preferences, User, Idempotency, Reservation, uid, now, record_data, job_data
from .storage import Storage
from .tenancy import require_owned, require_job, require_model

FORMATS = {'pdf', 'docx', 'md', 'txt'}


def name(value):
    if not isinstance(value,str) or not 1 <= len(value.strip()) <= 100:
        raise AppError('NAME', '名称需为 1–100 个字符。', 422)
    return value.strip()


def lock_owner(s, owner):
    return s.scalar(select(User).where(User.id == owner).with_for_update())


def documents(s, owner):
    return select(Record).where(Record.owner_id == owner, Record.kind == 'document', Record.state != 'deleted')


def invalidate(s, owner, doc):
    s.execute(delete(Vector).where(Vector.owner_id == owner, Vector.document_id == doc.id))
    for index in s.scalars(select(Record).where(Record.owner_id == owner, Record.parent_id == doc.id, Record.kind == 'index')):
        index.state = 'stale'


def enqueue(s, store, owner, doc):
    s.execute(text('SELECT pg_advisory_xact_lock(76241021)'))
    user = lock_owner(s, owner)
    limit = user.quota.get('pending_jobs', store.config.pending_jobs)
    count = s.scalar(select(func.count()).select_from(Job).where(Job.owner_id == owner, Job.state.in_(['queued','running'])))
    existing = list(s.scalars(select(Job).where(Job.owner_id == owner, Job.target == doc.id, Job.state.in_(['queued','running']))))
    global_count=s.scalar(select(func.count()).select_from(Job).where(Job.state.in_(['queued','running'])))
    if global_count-len(existing)>=store.config.global_pending:raise AppError('QUEUE_LIMIT','平台任务队列已满，请稍后重试。',409)
    if count-len(existing) >= limit: raise AppError('QUEUE_LIMIT', '个人任务队列已满，请等待或取消任务。', 409)
    for job in existing: job.state = 'cancelled'; job.stage = 'cancelled'; job.lease_token = None
    kb = require_owned(s, owner, doc.parent_id, 'kb')
    job = Job(owner_id=owner, kind='process', target=doc.id,
              payload={'version_id':doc.payload['current_version_id'], 'model_id':kb.model_id,
                       'profile':kb.payload['profile']})
    s.add(job); doc.state='queued'; doc.updated_at=now(); s.flush()
    return job


def mark_deleted(s, owner, doc):
    invalidate(s,owner,doc); doc.state='deleted'; doc.updated_at=now()
    for job in s.scalars(select(Job).where(Job.owner_id==owner,Job.target==doc.id,Job.state.in_(['queued','running']))):
        job.state='cancelled'; job.stage='cancelled'; job.lease_token=None
    for version in s.scalars(select(Record).where(Record.owner_id==owner,Record.parent_id==doc.id,Record.kind=='version')):
        version.state='deleted'
        from .quotas import release_storage
        release_storage(s,owner,version.id)
        for chunk in s.scalars(select(Record).where(Record.owner_id==owner,Record.parent_id==version.id,Record.kind=='chunk')):
            chunk.state='deleted'


def cleanup_deleted(store, owner):
    storage=Storage(store.config.data_root)
    with store.session() as s:
        for version in s.scalars(select(Record).where(Record.owner_id==owner,Record.kind=='version',Record.state=='deleted')):
            storage.delete(owner,version.payload['file_key'])


def process_document(store, job_id, lease=None):
    """Reusable processing unit. A worker supplies a lease in P6; API never runs it."""
    with store.session() as s:
        job=s.get(Job,job_id)
        if not job or job.state not in ('running','queued'): return
        owner=job.owner_id; doc=require_owned(s,owner,job.target,'document')
        version=require_owned(s,owner,job.payload['version_id'],'version')
        payload=dict(version.payload); profile=job.payload['profile']; doc_id=doc.id; version_id=version.id
        if doc.payload['current_version_id'] != version.id: return
        if lease and job.lease_token != lease: raise AppError('LEASE','任务租约已失效。',409)
        job.stage='parsing'; doc.state='parsing'
    path=Storage(store.config.data_root).path(owner,payload['file_key'])
    from .parsing import bounded_parse
    def cancelled():
        with store.session() as s:
            current=s.get(Job,job_id)
            return not current or current.state not in ('queued','running') or (lease and (current.lease_token!=lease or current.lease_until<=now())) or not s.get(User,owner).enabled
    parsed=bounded_parse(path,payload['format'],store.config.parse_seconds,cancelled); chunks=chunk_spans(parsed['spans'],profile['length'],profile['overlap'])
    with store.session() as s:
        job=s.get(Job,job_id); doc=require_owned(s,owner,doc_id,'document',lock=True)
        if job.state not in ('running','queued') or (lease and (job.lease_token!=lease or job.lease_until<=now())) or doc.payload['current_version_id']!=version_id: return
        version=require_owned(s,owner,version_id,'version')
        for old in s.scalars(select(Record).where(Record.owner_id==owner,Record.parent_id==version_id,Record.kind=='chunk')):
            old.state='deleted'
        version.payload={**version.payload,'parsed':parsed,'warnings':parsed['warnings'],'chunk_profile':profile}
        for chunk in chunks:s.add(Record(owner_id=owner,kind='chunk',parent_id=version_id,payload=chunk))
        doc.state='parsed'; version.state='parsed'; job.stage='parsed';job.done=len(chunks);job.total=len(chunks)
        s.flush()
    if job.payload.get('model_id'):
        from .indexes import build_index
        build_index(store,job_id,lease)
    else:
        with store.session() as s:
            job=s.get(Job,job_id)
            if job.state in ('running','queued') and (not lease or job.lease_token==lease):job.state='completed'


def mount(app,store):
    storage=Storage(store.config.data_root)
    @app.get('/api/settings')
    def preferences(request:Request):
        with store.session() as s:return s.get(Preferences,identity(request).id).payload

    @app.put('/api/settings')
    def preferences_save(body:dict,request:Request):
        owner=identity(request).id
        with store.session() as s:
            lock_owner(s,owner); row=s.get(Preferences,owner); values={**row.payload,**body}
            if set(body)-set(row.payload): raise AppError('SETTINGS','包含不支持的设置。',422)
            length,overlap=values['chunk_length'],values['chunk_overlap']
            if type(length)!=int or type(overlap)!=int or not 1<=length<=12000 or not 0<=overlap<length:
                raise AppError('CHUNK_SETTINGS','切片长度与重叠设置不合法。',422)
            if values['answer_length'] not in ('short','normal','long') or type(values['source_panel'])!=bool:
                raise AppError('SETTINGS','回答偏好不合法。',422)
            for purpose in ('chat','embedding'):
                if values.get('default_'+purpose):require_model(s,owner,values['default_'+purpose],purpose)
            if values.get('default_kb'):require_owned(s,owner,values['default_kb'],'kb')
            row.payload=values;return values

    @app.get('/api/knowledge-bases')
    def kbs(request:Request):
        with store.session() as s:return [record_data(r) for r in s.scalars(select(Record).where(Record.owner_id==identity(request).id,Record.kind=='kb',Record.state=='active').order_by(Record.created_at))]

    @app.post('/api/knowledge-bases')
    def create_kb(body:dict,request:Request):
        owner=identity(request).id
        with store.session() as s:
            prefs=s.get(Preferences,owner).payload; model_id=body.get('embedding_id',prefs['default_embedding'])
            if model_id:require_model(s,owner,model_id,'embedding')
            kb=Record(owner_id=owner,kind='kb',model_id=model_id,payload={'name':name(body.get('name')),
                'profile':{'length':prefs['chunk_length'],'overlap':prefs['chunk_overlap']},'embedding_id':model_id})
            s.add(kb);s.flush();return record_data(kb)

    @app.patch('/api/knowledge-bases/{id}')
    def rename_kb(id:str,body:dict,request:Request):
        with store.session() as s:
            kb=require_owned(s,identity(request).id,id,'kb',lock=True)
            kb.payload={**kb.payload,'name':name(body.get('name'))};kb.updated_at=now();return record_data(kb)

    @app.get('/api/knowledge-bases/{id}/impact')
    def kb_impact(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;kb=require_owned(s,owner,id,'kb')
            count=s.scalar(select(func.count()).select_from(Record).where(Record.owner_id==owner,Record.parent_id==id,Record.kind=='document',Record.state!='deleted'))
            return {'documents':count,'token':hashlib.sha256((id+kb.updated_at.isoformat()+str(count)).encode()).hexdigest(),'message':'资料和索引将删除，历史引用快照保留。'}

    @app.delete('/api/knowledge-bases/{id}')
    def delete_kb(id:str,request:Request,token:str=''):
        owner=identity(request).id
        with store.session() as s:
            lock_owner(s,owner);kb=require_owned(s,owner,id,'kb',lock=True)
            docs=list(s.scalars(documents(s,owner).where(Record.parent_id==id)))
            expected=hashlib.sha256((id+kb.updated_at.isoformat()+str(len(docs))).encode()).hexdigest()
            if token!=expected:raise AppError('CONFIRM','请先确认删除影响。',409)
            for doc in docs:mark_deleted(s,owner,doc)
            kb.state='deleted'
        cleanup_deleted(store,owner)
        return {'deleted':True}

    @app.post('/api/documents/upload')
    def upload(request:Request,kb_id:str=Form(...),request_key:str=Form(...),decision:str=Form('ask'),files:list[UploadFile]=File(...)):
        owner=identity(request).id
        if not 1<=len(request_key)<=150 or decision not in ('ask','skip','replace'):
            raise AppError('UPLOAD','上传请求参数不合法。',422)
        if not 1<=len(files)<=store.config.batch_files:raise AppError('BATCH_LIMIT','上传数量超过每批限制。',413)
        saved=[];result=[]
        try:
            with store.session() as s:
                user=lock_owner(s,owner);kb=require_owned(s,owner,kb_id,'kb')
                prepared=[]
                for file in files:
                    filename=(file.filename or '').replace('\\','/').split('/')[-1]
                    format=Path(filename).suffix.lower().lstrip('.')
                    if format not in FORMATS or not filename or len(filename)>255:raise AppError('FORMAT','只支持 PDF、DOCX、MD、TXT。',422)
                    key=uid()+'.'+format
                    meta=storage.write(owner,key,iter(lambda:file.file.read(65536),b''),store.config.upload_bytes)
                    saved.append(key);prepared.append({'name':filename,'format':format,**meta})
                digest=hashlib.sha256(json.dumps({'kb':kb_id,'decision':decision,'files':[{k:m[k] for k in ('name','sha256')} for m in prepared]},sort_keys=True).encode()).hexdigest()
                old=s.get(Idempotency,(owner,'upload',request_key))
                if old:
                    if old.digest!=digest:raise AppError('IDEMPOTENCY','请求标识对应的内容不同。',409)
                    return old.result
                for meta in prepared:
                    current=list(s.scalars(documents(s,owner).where(Record.parent_id==kb.id)))
                    duplicate=next((r for r in current if r.payload['name']==meta['name'] or r.payload['sha256']==meta['sha256']),None)
                    if duplicate and decision!='replace':
                        result.append({'name':meta['name'],'state':'duplicate' if decision=='ask' else 'skipped','reason':'本人知识库内已存在同名或相同内容。','document_id':duplicate.id});continue
                    storage_limit=user.quota.get('storage_bytes',store.config.storage_bytes)
                    all_docs=list(s.scalars(documents(s,owner)))
                    used_bytes=sum(r.payload['size'] for r in s.scalars(select(Record).where(Record.owner_id==owner,Record.kind=='version',Record.state!='deleted')))
                    if used_bytes+meta['size']-(duplicate.payload['size'] if duplicate else 0)>storage_limit:
                        raise AppError('STORAGE_LIMIT','个人资料存储额度不足。',409)
                    if not duplicate and len(all_docs)>=user.quota.get('documents',store.config.documents):raise AppError('DOCUMENT_LIMIT','文档数量已达上限。',409)
                    doc=duplicate or Record(id=uid(),owner_id=owner,kind='document',parent_id=kb.id,payload={})
                    if not duplicate:s.add(doc)
                    else:
                        invalidate(s,owner,doc)
                        previous=require_owned(s,owner,doc.payload['current_version_id'],'version');previous.state='deleted'
                        from .quotas import release_storage
                        release_storage(s,owner,previous.id)
                        for chunk in s.scalars(select(Record).where(Record.owner_id==owner,Record.parent_id==previous.id,Record.kind=='chunk')):chunk.state='deleted'
                    number=doc.payload.get('version',0)+1
                    s.flush()
                    version=Record(id=uid(),owner_id=owner,kind='version',parent_id=doc.id,state='queued',payload={**meta,'file_key':meta['key'],'version':number})
                    s.add(version)
                    doc.payload={**meta,'kb_id':kb.id,'version':number,'current_version_id':version.id};doc.state='queued';doc.updated_at=now()
                    s.flush();enqueue(s,store,owner,doc)
                    from .quotas import reserve,settle
                    reservation=reserve(s,owner,'storage_bytes',version.id,meta['size'],storage_limit)
                    settle(s,owner,'storage_bytes',version.id,'committed')
                    result.append({'name':meta['name'],'state':'queued','document_id':doc.id})
                s.add(Idempotency(owner_id=owner,operation='upload',key=request_key,digest=digest,result=result))
                used={r.payload['file_key'] for r in s.scalars(select(Record).where(Record.owner_id==owner,Record.kind=='version'))}
                retained=[key for key in saved if key in used]
                storage.compensate(s,owner,retained)
            saved=[key for key in saved if key not in retained]
            cleanup_deleted(store,owner)
            return result
        finally:
            for key in saved:storage.delete(owner,key)

    @app.get('/api/documents')
    def list_documents(request:Request,kbId:str='',q:str='',status:str='',format:str='',page:int=1,page_size:int=20):
        owner=identity(request).id
        if not 1<=page<=100000 or not 1<=page_size<=100:raise AppError('PAGE','分页参数不合法。',422)
        with store.session() as s:
            statement=documents(s,owner)
            if kbId:require_owned(s,owner,kbId,'kb');statement=statement.where(Record.parent_id==kbId)
            if status:statement=statement.where(Record.state.in_(status.split(',')))
            if format:statement=statement.where(Record.payload['format'].astext==format)
            rows=[record_data(r) for r in s.scalars(statement.order_by(Record.created_at.desc())) if q.casefold() in r.payload['name'].casefold()]
            return {'items':rows[(page-1)*page_size:page*page_size],'total':len(rows)}

    @app.get('/api/documents/{id}')
    def detail(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;doc=require_owned(s,owner,id,'document')
            version=require_owned(s,owner,doc.payload['current_version_id'],'version')
            data=record_data(doc);data.pop('key',None)
            return {**data,'checksum':doc.payload['sha256'],
                    'versions':[{'id':v.id,'version':v.payload['version'],'state':v.state} for v in s.scalars(
                        select(Record).where(Record.owner_id==owner,Record.parent_id==id,Record.kind=='version').order_by(Record.created_at))],
                    'jobs':[job_data(j) for j in s.scalars(select(Job).where(Job.owner_id==owner,Job.target==id).order_by(Job.created_at.desc()))],
                    'current_version':{k:v for k,v in record_data(version).items() if k not in ('file_key','key','parsed')}}

    @app.get('/api/documents/{id}/file')
    def file(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;doc=require_owned(s,owner,id,'document');v=require_owned(s,owner,doc.payload['current_version_id'],'version')
            path=storage.path(owner,v.payload['file_key'])
            if not path.is_file():raise AppError('FILE_MISSING','原文件不可用。',404)
            return FileResponse(path,filename=doc.payload['name'],headers={'Cache-Control':'no-store'})

    @app.get('/api/documents/{id}/parsed')
    def parsed(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;doc=require_owned(s,owner,id,'document');v=require_owned(s,owner,doc.payload['current_version_id'],'version')
            if not v.payload.get('parsed'):raise AppError('NOT_PARSED','等待解析完成。',409)
            return v.payload['parsed']

    @app.get('/api/documents/{id}/chunks')
    def chunks(id:str,request:Request,page:int=1):
        if page<1:raise AppError('PAGE','分页参数不合法。',422)
        with store.session() as s:
            owner=identity(request).id;doc=require_owned(s,owner,id,'document')
            rows=[record_data(r) for r in s.scalars(select(Record).where(Record.owner_id==owner,Record.parent_id==doc.payload['current_version_id'],Record.kind=='chunk',Record.state!='deleted'))]
            rows.sort(key=lambda r:r['ordinal']);return {'items':rows[(page-1)*20:page*20],'total':len(rows)}

    @app.get('/api/documents/{id}/versions')
    def versions(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;require_owned(s,owner,id,'document')
            return [{'id':r.id,'version':r.payload['version'],'state':r.state,'sha256':r.payload['sha256']} for r in s.scalars(select(Record).where(Record.owner_id==owner,Record.parent_id==id,Record.kind=='version').order_by(Record.created_at))]

    @app.delete('/api/documents/{id}')
    def remove(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;lock_owner(s,owner);mark_deleted(s,owner,require_owned(s,owner,id,'document',lock=True))
        cleanup_deleted(store,owner)
        return {'deleted':True}

    @app.post('/api/documents/{id}/reprocess')
    def reprocess(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;doc=require_owned(s,owner,id,'document',lock=True);invalidate(s,owner,doc)
            return job_data(enqueue(s,store,owner,doc))

    @app.get('/api/jobs')
    def jobs(request:Request,state:str='',documentId:str=''):
        owner=identity(request).id
        with store.session() as s:
            statement=select(Job).where(Job.owner_id==owner)
            if documentId:require_owned(s,owner,documentId,'document');statement=statement.where(Job.target==documentId)
            if state:statement=statement.where(Job.state.in_(state.split(',')))
            return [job_data(j) for j in s.scalars(statement.order_by(Job.created_at.desc()))]

    @app.post('/api/jobs/{id}/cancel')
    def cancel(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;job=require_job(s,owner,id)
            if job.state in ('queued','running'):job.state='cancelled';job.stage='cancelled';job.lease_token=None
            doc=require_owned(s,owner,job.target)
            if doc.kind=='document':doc.state='interrupted'
            elif doc.kind=='generation' and doc.state not in ('completed','failed','stopped','interrupted'):
                from .chat import event
                doc.state='stopped'
                require_owned(s,owner,doc.payload['assistant_message_id'],'message').state='stopped'
                event(s,doc,'done',{'state':'stopped'})
            return job_data(job)

    @app.post('/api/jobs/{id}/retry')
    def retry(id:str,request:Request):
        with store.session() as s:
            owner=identity(request).id;old=require_job(s,owner,id)
            if old.kind!='process' or old.state not in ('failed','cancelled','interrupted'):raise AppError('RETRY','当前任务不可重试。',409)
            return job_data(enqueue(s,store,owner,require_owned(s,owner,old.target,'document')))

    @app.delete('/api/jobs/{id}')
    def remove_job(id:str,request:Request):
        with store.session() as s:
            job=require_job(s,identity(request).id,id)
            if job.state not in ('completed','failed','interrupted','cancelled'):
                raise AppError('JOB_ACTIVE','任务尚未结束，请先取消活动任务。',409)
            s.delete(job);return {'deleted':True}

    @app.get('/api/dashboard')
    def dashboard(request:Request):
        owner=identity(request).id
        with store.session() as s:
            rows=list(s.scalars(documents(s,owner).order_by(Record.created_at.desc())))
            conversations=list(s.scalars(select(Record).where(Record.owner_id==owner,Record.kind=='conversation',Record.state=='active').order_by(Record.updated_at.desc()).limit(5)))
            return {'knowledge_bases':s.scalar(select(func.count()).select_from(Record).where(Record.owner_id==owner,Record.kind=='kb',Record.state=='active')),
                    'documents':len(rows),'ready':sum(r.state=='ready' for r in rows),'processing':sum(r.state in ('queued','parsing','embedding') for r in rows),
                    'failed':sum(r.state in ('failed','interrupted') for r in rows),'recent_documents':[record_data(r) for r in rows[:5]],'recent_conversations':[record_data(r) for r in conversations]}

    @app.get('/api/data/overview')
    def overview(request:Request):
        owner=identity(request).id
        with store.session() as s:
            user=s.get(User,owner);rows=list(s.scalars(documents(s,owner)))
            call_states = dict(s.execute(select(Reservation.state, func.sum(Reservation.amount))
                               .where(Reservation.owner_id == owner, Reservation.resource == 'platform_calls')
                               .group_by(Reservation.state)).all())
            used_calls = sum(int(call_states.get(state, 0)) for state in ('reserved', 'committed', 'unknown'))
            call_limit = user.quota.get('platform_calls', store.config.platform_calls)
            return {'documents':len(rows),'storage_bytes':sum(r.payload['size'] for r in rows),
                    'storage_limit':user.quota.get('storage_bytes',store.config.storage_bytes),
                    'document_limit':user.quota.get('documents',store.config.documents),
                    'vectors':s.scalar(select(func.count()).select_from(Vector).where(Vector.owner_id==owner)),
                    'jobs':s.scalar(select(func.count()).select_from(Job).where(Job.owner_id==owner)),
                    'pending_jobs':s.scalar(select(func.count()).select_from(Job).where(Job.owner_id==owner, Job.state.in_(['queued', 'running']))),
                    'pending_job_limit':user.quota.get('pending_jobs', store.config.pending_jobs),
                    'platform_calls':used_calls, 'platform_call_limit':call_limit,
                    'platform_calls_remaining':max(0, call_limit-used_calls),
                    'platform_calls_reserved':int(call_states.get('reserved', 0)),
                    'platform_calls_committed':int(call_states.get('committed', 0)),
                    'platform_calls_unknown':int(call_states.get('unknown', 0))}

    @app.post('/api/data/clear')
    def clear_data(body:dict,request:Request):
        if body.get('confirm') is not True or body.get('scope') not in ('documents','conversations','all'):
            raise AppError('CONFIRM','请选择清理范围并确认。',409)
        owner=identity(request).id
        with store.session() as s:
            lock_owner(s,owner)
            # Same job-before-record order as worker live/recover transactions.
            if body['scope'] in ('conversations','all'):
                generation_jobs=list(s.scalars(select(Job).where(Job.owner_id==owner,Job.kind=='generation').order_by(Job.id).with_for_update()))
            if body['scope'] in ('documents','all'):
                for doc in s.scalars(documents(s,owner)):mark_deleted(s,owner,doc)
            if body['scope'] in ('conversations','all'):
                for row in s.scalars(select(Record).where(Record.owner_id==owner,Record.kind.in_(['conversation','generation','message']))):row.state='deleted'
                for job in generation_jobs:
                    if job.state in ('queued','running'):job.state='cancelled';job.lease_token=None
        cleanup_deleted(store,owner)
        return {'cleared':body['scope']}
