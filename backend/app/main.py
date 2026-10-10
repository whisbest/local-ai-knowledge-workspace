from __future__ import annotations
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from .core import Store, Record, Job, AppError, dump, load, record_data, job_data, now


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    @field_validator('name')
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError('名称不能为空。')
        return value.strip()


def create_app(root=None, worker=True):
    store = Store(root or os.environ.get('DATA_ROOT','data'))
    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        from .worker import Worker
        runner=Worker(store)
        store.worker_runner=runner
        if worker: runner.start()
        yield
        if worker: runner.stop()
        store.engine.dispose()
    app = FastAPI(title='个人知识库', lifespan=lifespan)
    app.state.store, app.state.worker_enabled = store, worker

    @app.middleware('http')
    async def local_requests(request: Request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if request.headers.get('sec-fetch-site')=='cross-site':return JSONResponse({'code':'ORIGIN','message':'拒绝跨站修改请求。'},status_code=403)
            if request.headers.get('host','').split(':')[0] not in ('localhost','127.0.0.1','testserver','api'):
                return JSONResponse({'code':'HOST','message':'只能从本机访问。'},status_code=403)
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'code':'ORIGIN','message':'请求来源不匹配。'},status_code=403)
        with store.gate:
            if store.locked and request.url.path!='/api/health':return JSONResponse({'code':'MAINTENANCE','message':'数据维护中，请稍后再试。'},status_code=423)
            store.request_count+=1
        try:return await call_next(request)
        finally:
            with store.gate:store.request_count-=1

    @app.exception_handler(AppError)
    async def error_handler(request, e):
        return JSONResponse({'code':e.code,'message':e.message,'retryable':e.retryable},status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def input_error(request,e):
        # Pydantic errors normally echo submitted values, which can include a supplied key.
        return JSONResponse({'code':'INPUT','message':'输入格式不合法，请检查必填项、名称和长度。','retryable':False},status_code=422)

    @app.get('/api/health')
    def health(): return {'status':'ok','maintenance':store.locked}

    @app.get('/api/settings')
    def settings():
        with store.session() as s: return load(s.get(Record,'preferences').payload)

    @app.put('/api/settings')
    def save_settings(body:dict):
        with store.session() as s:
            r=s.get(Record,'preferences'); previous=load(r.payload);values={**previous,**body}
            if set(body)-set(previous):raise AppError('SETTINGS','包含不支持的设置。')
            if not isinstance(values['chunk_length'],int) or not isinstance(values['chunk_overlap'],int):raise AppError('CHUNK_SETTINGS','切片参数必须为整数。')
            if values['answer_length'] not in ('short','normal','long') or not isinstance(values['source_panel'],bool):raise AppError('SETTINGS','回答长度或来源面板设置不合法。')
            if not (1 <= values['chunk_length'] <= 12000 and 0 <= values['chunk_overlap'] < values['chunk_length']): raise AppError('CHUNK_SETTINGS','切片长度必须为正，重叠必须小于切片长度。')
            from .indexes import changed_indexes
            from .models_service import config_for
            for purpose in ('chat','embedding'):
                if values.get('default_'+purpose):config_for(store,values['default_'+purpose],purpose)
            if values.get('default_kb'):
                from .documents import require
                require(s,values['default_kb'],'kb')
            changed_indexes(s,values,previous)
            r.payload=dump(values); r.updated_at=now()
            return values

    @app.get('/api/knowledge-bases')
    def knowledge_bases():
        with store.session() as s: return [record_data(r) for r in s.query(Record).filter_by(kind='kb',state='active').order_by(Record.created_at)]

    @app.post('/api/knowledge-bases')
    def create_kb(body:NameBody):
        with store.session() as s:
            r=Record(kind='kb',payload=dump({'name':body.name.strip()})); s.add(r); s.flush(); return record_data(r)

    @app.patch('/api/knowledge-bases/{id}')
    def rename_kb(id:str,body:NameBody):
        with store.session() as s:
            r=s.get(Record,id)
            if not r or r.kind!='kb' or r.state!='active': raise AppError('NOT_FOUND','知识库不存在。',404)
            r.payload=dump({'name':body.name.strip()}); r.updated_at=now(); return record_data(r)

    @app.get('/api/jobs')
    def jobs(state:str='', documentId:str=''):
        with store.session() as s:
            q=s.query(Job)
            if state: q=q.filter(Job.state.in_(state.split(',')))
            if documentId: q=q.filter_by(target=documentId)
            return [job_data(r) for r in q.order_by(Job.created_at.desc())]

    @app.get('/api/dashboard')
    def dashboard():
        with store.session() as s:
            docs=s.query(Record).filter(Record.kind=='document',Record.state!='deleted').all()
            conversations=s.query(Record).filter_by(kind='conversation',state='active').order_by(Record.updated_at.desc()).limit(5).all()
            return {'knowledge_bases':s.query(Record).filter_by(kind='kb',state='active').count(),'documents':len(docs),'ready':sum(r.state=='ready' for r in docs),'processing':sum(r.state in ['queued','parsing','chunking','embedding'] for r in docs),'failed':sum(r.state in ['failed','interrupted'] for r in docs),'recent_documents':[record_data(r) for r in sorted(docs,key=lambda r:r.created_at,reverse=True)[:5]],'recent_conversations':[record_data(r) for r in conversations]}
    from .models_service import mount_models
    mount_models(app,store)
    from .documents import mount_documents
    mount_documents(app,store)
    from .indexes import mount_indexes
    mount_indexes(app,store)
    from .chat import mount_chat
    mount_chat(app,store)
    from .data_service import mount_data
    mount_data(app,store)
    from .backups import mount_backups
    mount_backups(app,store)
    return app

def application():
    """Uvicorn factory avoids touching production data when tests import create_app."""
    return create_app()
