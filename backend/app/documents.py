from __future__ import annotations
import hashlib
import os
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import FileResponse
from .core import Record,Job,Vector,AppError,uid,dump,load,record_data,job_data,now
from .parsers import parse_file,chunk_spans

FORMATS={'pdf','docx','md','txt'}
MAX_UPLOAD=64*1024*1024


def require(s,id,kind):
    r=s.get(Record,id)
    if not r or r.kind!=kind or r.state=='deleted':raise AppError('NOT_FOUND','资料不存在或已删除。',404)
    return r


def new_job(s,doc,version,kind='process'):
    for old in s.query(Job).filter(Job.target==doc.id,Job.state.in_(['queued','running'])):old.state='cancelled';old.stage='cancelled'
    j=Job(kind=kind,target=doc.id,payload=dump({'version_id':version.id}));s.add(j)
    doc.state='queued';doc.updated_at=now();s.flush();return j


def invalidate(s,doc):
    s.query(Vector).filter_by(document_id=doc.id).delete()
    for r in s.query(Record).filter_by(kind='index',parent_id=doc.id):r.state='stale'


def delete_document(store,s,doc):
    invalidate(s,doc)
    doc.state='deleted';doc.updated_at=now()
    for job in s.query(Job).filter(Job.target==doc.id,Job.state.in_(['queued','running'])):job.state='cancelled';job.stage='cancelled'
    # Historical citation snapshots are deliberately independent of these files/chunks.
    versions=s.query(Record).filter_by(kind='version',parent_id=doc.id).all()
    for version in versions:
        for chunk in s.query(Record).filter_by(kind='chunk',parent_id=version.id):s.delete(chunk)
        version.state='deleted'
    return versions


def cleanup_files(store,versions):
    for v in versions:
        values=load(v.payload)
        for key in ('source_path','parsed_path'):
            relative=values.get(key)
            if relative:
                target=(store.current/relative).resolve()
                if target.is_relative_to(store.current):
                    try:target.unlink(missing_ok=True)
                    except OSError:pass # Tombstone blocks retrieval; startup garbage collection retries.


def execute_process(store,id):
    with store.session() as s:
        job=s.get(Job,id);v=require(s,load(job.payload)['version_id'],'version');doc=require(s,job.target,'document')
        version=record_data(v);settings=load(s.get(Record,'preferences').payload)
        job.stage='parsing';doc.state='parsing'
    result=parse_file(store.current/version['source_path'],version['format'])
    chunks=chunk_spans(result['spans'],settings['chunk_length'],settings['chunk_overlap'])
    parsed_path='parsed/'+version['id']+'.json'
    with store.session() as s:
        job=s.get(Job,id);v=s.get(Record,version['id']);doc=s.get(Record,job.target)
        if job.state!='running' or doc.state=='deleted' or load(doc.payload)['current_version_id']!=v.id:return
        job.stage='chunking';job.total=len(chunks)
        for r in s.query(Record).filter_by(kind='chunk',parent_id=v.id):s.delete(r)
        for chunk in chunks:s.add(Record(kind='chunk',parent_id=v.id,payload=dump({**chunk,'profile':{'length':settings['chunk_length'],'overlap':settings['chunk_overlap'],'unit':'characters'}})))
        # Immutable version file replacement only occurs for explicit reprocessing at a safe transaction boundary.
        tmp=store.current/(parsed_path+'.tmp');tmp.write_text(dump(result),encoding='utf8');os.replace(tmp,store.current/parsed_path)
        v.payload=dump({**load(v.payload),'parsed_path':parsed_path,'warnings':result['warnings'],'chunk_profile':{'length':settings['chunk_length'],'overlap':settings['chunk_overlap']}});v.state='parsed'
        doc.state='parsed';job.state='completed';job.stage='parsed';job.done=job.total;job.updated_at=now()
        if settings.get('default_embedding'):
            # The same persistent job continues into indexing after the parsing transaction commits.
            job.state='running'
    if settings.get('default_embedding'):
        from .indexes import execute_index
        execute_index(store,id)


def mount_documents(app,store):
    router=APIRouter(prefix='/api')
    @router.get('/documents')
    def documents(kbId:str='',status:str='',format:str='',q:str='',page:int=1,page_size:int=20):
        if page<1 or not 1<=page_size<=100:raise AppError('PAGE','分页参数不合法。')
        with store.session() as s:
            rows=[record_data(r) for r in s.query(Record).filter(Record.kind=='document',Record.state!='deleted').order_by(Record.created_at.desc())]
            rows=[r for r in rows if (not kbId or r['kb_id']==kbId) and (not status or r['state']==status) and (not format or r['format']==format) and q.casefold() in r['name'].casefold()]
            return {'items':rows[(page-1)*page_size:page*page_size],'total':len(rows),'page':page,'page_size':page_size}

    @router.post('/documents/upload')
    async def upload(kb_id:str=Form(...),decision:str=Form('ask'),request_key:str=Form(...),files:list[UploadFile]=File(...)):
        from .core import Idempotency
        if decision not in ('ask','skip','replace') or not 1<=len(files)<=20 or len(request_key)>150:raise AppError('UPLOAD_INPUT','每批最多 20 个文件，请检查上传参数。')
        with store.session() as s:
            require(s,kb_id,'kb')
            previous=s.get(Idempotency,'upload:'+request_key)
            if previous:return load(previous.result)
        results=[]
        for file in files:
            staging=store.root/'state/staging'/('upload-'+uid())
            try:
                name=(file.filename or '').replace('\\','/').rsplit('/',1)[-1]
                format=Path(name).suffix.lower().lstrip('.')
                if format not in FORMATS:raise AppError('FORMAT','支持 PDF、DOCX、Markdown 和 TXT。')
                digest=hashlib.sha256();size=0
                with staging.open('wb') as out:
                    while data:=await file.read(1024*1024):
                        size+=len(data)
                        if size>MAX_UPLOAD:raise AppError('UPLOAD_LIMIT','单个文件不能超过 64 MB。')
                        digest.update(data);out.write(data)
                if not size:raise AppError('EMPTY_FILE','文件为空。')
                checksum=digest.hexdigest()
                with store.session() as s:
                    require(s,kb_id,'kb')
                    candidates=[r for r in s.query(Record).filter(Record.kind=='document',Record.state!='deleted') if load(r.payload)['kb_id']==kb_id]
                    duplicate=next((r for r in candidates if load(r.payload)['checksum']==checksum),None)
                    same_name=next((r for r in candidates if load(r.payload)['name']==name),None)
                    existing=duplicate or same_name
                    if existing and decision=='ask':
                        results.append({'name':name,'state':'duplicate','document_id':existing.id,'reason':'内容重复' if duplicate else '同名资料已存在'});continue
                    if existing and decision=='skip':results.append({'name':name,'state':'skipped','document_id':existing.id});continue
                    doc=existing or Record(id=uid(),kind='document',parent_id=kb_id)
                    if not existing:s.add(doc)
                    else:invalidate(s,doc)
                    number=s.query(Record).filter_by(kind='version',parent_id=doc.id).count()+1
                    version_id=uid();relative='documents/'+version_id+'.'+format
                    os.replace(staging,store.current/relative)
                    version=Record(id=version_id,kind='version',parent_id=doc.id,state='queued',payload=dump({'version':number,'source_path':relative,'checksum':checksum,'format':format,'size':size,'name':name}));s.add(version)
                    doc.payload=dump({'kb_id':kb_id,'name':name,'format':format,'size':size,'checksum':checksum,'current_version_id':version.id,'version':number});job=new_job(s,doc,version)
                    results.append({'name':name,'state':'queued','document_id':doc.id,'job_id':job.id})
            except AppError as e:results.append({'name':file.filename,'state':'failed','code':e.code,'error':e.message})
            finally:staging.unlink(missing_ok=True);await file.close()
        with store.session() as s:s.add(Idempotency(key='upload:'+request_key,result=dump(results)))
        return results

    @router.get('/documents/{id}')
    def detail(id:str):
        with store.session() as s:
            doc=require(s,id,'document');v=require(s,load(doc.payload)['current_version_id'],'version')
            return {**record_data(doc),'current_version':record_data(v),'versions':[record_data(r) for r in s.query(Record).filter_by(kind='version',parent_id=id).order_by(Record.created_at)],'jobs':[job_data(j) for j in s.query(Job).filter_by(target=id).order_by(Job.created_at.desc())]}

    @router.get('/documents/{id}/file')
    def file(id:str):
        with store.session() as s:
            doc=require(s,id,'document');v=load(require(s,load(doc.payload)['current_version_id'],'version').payload)
        return FileResponse(store.current/v['source_path'],filename=v['name'],content_disposition_type='inline',media_type='application/pdf' if v['format']=='pdf' else None)

    @router.get('/documents/{id}/parsed')
    def parsed(id:str):
        with store.session() as s:
            d=require(s,id,'document');v=load(require(s,load(d.payload)['current_version_id'],'version').payload)
        if not v.get('parsed_path'):raise AppError('NOT_PARSED','尚未完成解析，请查看处理任务。',409)
        return load((store.current/v['parsed_path']).read_text(encoding='utf8'))

    @router.get('/documents/{id}/chunks')
    def chunks(id:str,page:int=1,page_size:int=20):
        if page<1 or not 1<=page_size<=100:raise AppError('PAGE','分页参数不合法。')
        with store.session() as s:
            d=require(s,id,'document');v=load(d.payload)['current_version_id'];rows=sorted([record_data(r) for r in s.query(Record).filter_by(kind='chunk',parent_id=v)],key=lambda r:r['ordinal'])
            return {'items':rows[(page-1)*page_size:page*page_size],'total':len(rows),'page':page}

    @router.post('/documents/{id}/reprocess')
    def reprocess(id:str):
        with store.session() as s:
            d=require(s,id,'document');v=require(s,load(d.payload)['current_version_id'],'version');invalidate(s,d)
            return job_data(new_job(s,d,v))

    @router.delete('/documents/{id}')
    def delete(id:str):
        with store.session() as s:versions=delete_document(store,s,require(s,id,'document'))
        cleanup_files(store,versions);return {'deleted':True}

    @router.get('/knowledge-bases/{id}/impact')
    def impact(id:str):
        with store.session() as s:
            require(s,id,'kb');docs=[record_data(r) for r in s.query(Record).filter(Record.kind=='document',Record.parent_id==id,Record.state!='deleted')]
            token=hashlib.sha256(dump([(d['id'],d['updated_at']) for d in docs]).encode()).hexdigest()
            return {'documents':len(docs),'token':token,'message':'删除知识库及其资料和索引，历史对话引用快照保留。'}

    @router.delete('/knowledge-bases/{id}')
    def delete_kb(id:str,token:str):
        if token!=impact(id)['token']:raise AppError('IMPACT_CHANGED','资料已变化，请重新查看影响范围。',409)
        versions=[]
        with store.session() as s:
            kb=require(s,id,'kb');kb.state='deleted'
            for doc in s.query(Record).filter(Record.kind=='document',Record.parent_id==id,Record.state!='deleted'):versions.extend(delete_document(store,s,doc))
        cleanup_files(store,versions);return {'deleted':True}

    @router.post('/jobs/{id}/retry')
    def retry(id:str):
        with store.session() as s:
            old=s.get(Job,id)
            if not old or old.state not in ('failed','interrupted','cancelled'):raise AppError('JOB_STATE','该任务无需重试。',409)
            if old.kind=='pull':
                j=Job(kind=old.kind,target=old.target,payload=old.payload,attempt=old.attempt+1);s.add(j);s.flush()
            else:
                d=require(s,old.target,'document');v=require(s,load(d.payload)['current_version_id'],'version');j=new_job(s,d,v,old.kind);j.attempt=old.attempt+1
            return job_data(j)

    @router.post('/jobs/{id}/cancel')
    def cancel(id:str):
        with store.session() as s:
            j=s.get(Job,id)
            if not j:raise AppError('NOT_FOUND','任务不存在。',404)
            if j.state in ('queued','running'):
                j.state='cancelled';j.stage='cancelled'
                d=s.get(Record,j.target)
                if d and d.kind=='document' and d.state!='deleted':d.state='interrupted'
            return job_data(j)
    app.include_router(router)
