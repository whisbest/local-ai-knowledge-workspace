from __future__ import annotations
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'code':'ORIGIN','message':'请求来源不匹配。'},status_code=403)
            if store.locked and not request.url.path.startswith('/api/restores/'):
                return JSONResponse({'code':'MAINTENANCE','message':'数据维护中，请稍后再试。'},status_code=423)
        return await call_next(request)

    @app.exception_handler(AppError)
    async def error_handler(request, e):
        return JSONResponse({'code':e.code,'message':e.message,'retryable':e.retryable},status_code=e.status)

    @app.get('/api/health')
    def health(): return {'status':'ok','maintenance':store.locked}

    @app.get('/api/settings')
    def settings():
        with store.session() as s: return load(s.get(Record,'preferences').payload)

    @app.put('/api/settings')
    def save_settings(body:dict):
        with store.session() as s:
            r=s.get(Record,'preferences'); values=load(r.payload); values.update(body)
            if not (1 <= values['chunk_length'] <= 12000 and 0 <= values['chunk_overlap'] < values['chunk_length']): raise AppError('CHUNK_SETTINGS','切片长度必须为正，重叠必须小于切片长度。')
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
            if state: q=q.filter_by(state=state)
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
    return app

app = create_app()
