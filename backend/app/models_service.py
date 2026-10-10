from __future__ import annotations
import re
import threading
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel, Field
from .core import Record, Job, AppError, uid, dump, load, record_data, now
from .providers import Provider, normalize_endpoint, fingerprint


class ModelBody(BaseModel):
    purpose: str
    mode: str
    endpoint: str
    model: str = Field(min_length=1,max_length=180)
    name: str = Field(default='',max_length=100)
    key: str | None = Field(default=None,max_length=2048)


def config_for(store, id, purpose=None):
    with store.session() as s:
        r=s.get(Record,id)
        if not r or r.kind!='model' or (purpose and load(r.payload)['purpose']!=purpose): raise AppError('MODEL_CONFIG','请先选择对应用途的模型配置。')
        return record_data(r)


def provider_for(store, id, purpose=None):
    c=config_for(store,id,purpose)
    path=store.root/'secrets'/c['id']
    key=path.read_text(encoding='utf8') if path.is_file() and not c.get('restored_without_key') else None
    return Provider(c,key,getattr(store,'provider_transport',None))


def public_config(store, r):
    c=record_data(r)
    c['key_present']=(store.root/'secrets'/r.id).is_file() and not c.get('restored_without_key')
    c.pop('secret_ref',None)
    return c


def mount_models(app,store):
    router=APIRouter(prefix='/api')
    @router.get('/model-configs')
    def configs():
        with store.session() as s: return [public_config(store,r) for r in s.query(Record).filter_by(kind='model').order_by(Record.created_at)]

    @router.post('/model-configs')
    def save(body:ModelBody):
        if body.purpose not in ('chat','embedding') or body.mode not in ('local','api'): raise AppError('MODEL_CONFIG','请选择回答/向量用途和本地/API 模式。')
        endpoint=normalize_endpoint(body.endpoint,body.mode)
        model=body.model.strip()
        if not model or not re.fullmatch(r'[\w./:@+\-]+',model): raise AppError('MODEL_NAME','模型名包含不支持的字符。')
        id=uid(); keypath=store.root/'secrets'/id
        try:
            if body.key and body.mode=='api': keypath.write_text(body.key,encoding='utf8'); keypath.chmod(0o600)
            with store.session() as s:
                r=Record(id=id,kind='model',payload=dump({'purpose':body.purpose,'mode':body.mode,'endpoint':endpoint,'model':model,'name':body.name or model,'protocol':'ollama' if body.mode=='local' else 'openai-compatible','revision':id,'test_status':'untested'}))
                s.add(r);s.flush();return public_config(store,r)
        except Exception:
            keypath.unlink(missing_ok=True);raise

    @router.delete('/model-configs/{id}/key')
    def remove_key(id:str):
        config_for(store,id)
        (store.root/'secrets'/id).unlink(missing_ok=True)
        return {'removed':True}

    @router.post('/model-configs/{id}/test')
    def test(id:str):
        c=config_for(store,id);p=provider_for(store,id)
        try:
            result={}
            if c['mode']=='local':
                inventory=p.models()
                found=next((m for m in inventory if m['name']==c['model'] or m['name']==c['model']+':latest'),None)
                if not found: raise AppError('MODEL_MISSING','本地模型尚未下载，请在本地模型区域下载。')
                result['model_digest']=found.get('digest')
            if c['purpose']=='embedding':
                vectors=p.embed(['能力测试']); result['dimension']=len(vectors[0]);result['fingerprint']=fingerprint({**c,**result},result['dimension'])
            else:
                answer=''.join(p.chat([{'role':'user','content':'请只回答：测试成功'}],length='short'))
                if not answer: raise AppError('MODEL_RESPONSE','回答接口没有返回文字。',502)
            result['test_status']='available';result['tested_at']=now()
            with store.session() as s:
                r=s.get(Record,id);r.payload=dump({**load(r.payload),**result})
            return result
        except AppError as e:
            with store.session() as s:
                r=s.get(Record,id);r.payload=dump({**load(r.payload),'test_status':'failed','test_error':e.message})
            raise
        finally: p.close()

    @router.get('/local-models')
    def local_models(endpoint:str='http://host.docker.internal:11434'):
        p=Provider({'mode':'local','endpoint':normalize_endpoint(endpoint,'local')},transport=getattr(store,'provider_transport',None))
        try: return {'models':p.models(),'model_location':'由本机 Ollama 管理，Windows 默认 %USERPROFILE%/.ollama/models'}
        finally:p.close()

    @router.post('/local-models/pull')
    def pull(body:dict):
        if body.get('confirm_network') is not True: raise AppError('DOWNLOAD_CONFIRM','下载模型需要联网，请确认下载。')
        model=body.get('model','')
        if not re.fullmatch(r'[\w./:@+\-]{1,180}',model): raise AppError('MODEL_NAME','模型名不合法。')
        endpoint=normalize_endpoint(body.get('endpoint','http://host.docker.internal:11434'),'local')
        with store.session() as s:
            j=Job(kind='pull',target=model,payload=dump({'endpoint':endpoint}));s.add(j);s.flush();return {'id':j.id}
    app.include_router(router)


def execute_pull(store,id):
    with store.session() as s:
        j=s.get(Job,id);endpoint=load(j.payload)['endpoint'];model=j.target
    p=Provider({'mode':'local','endpoint':endpoint},transport=getattr(store,'provider_transport',None))
    def cancelled():
        with store.session() as s: return s.get(Job,id).state!='running'
    try:
        for item in p.pull(model,cancelled):
            with store.session() as s:
                j=s.get(Job,id)
                if j.state!='running':return
                j.stage=item.get('status','downloading')[:200];j.done=item.get('completed',0);j.total=item.get('total',0);j.updated_at=now()
        with store.session() as s:
            j=s.get(Job,id)
            if j.state=='running':j.state='completed';j.stage='completed'
    finally:p.close()
