import re
import hashlib
from urllib.parse import urlsplit
from pydantic import BaseModel, Field
from fastapi import Request
from sqlalchemy import select, or_
from ..core import AppError
from ..providers import Provider
from .db import Model, Record, Preferences, Audit, uid
from .auth import identity, administrator
from .secrets import Vault
from .outbound import Policy,SafeTransport,normalize
from .tenancy import require_model
from .documents import lock_owner, name
from .db import Reservation, now
from .quotas import MeteredProvider
from datetime import timedelta
from sqlalchemy import func


class ModelBody(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    purpose:str
    endpoint:str=Field(min_length=1,max_length=2048)
    model:str=Field(min_length=1,max_length=180)
    mode:str='api'
    key:str|None=Field(default=None,max_length=2048)


def public(model,admin=False):
    result={key:getattr(model,key) for key in ('id','name','purpose','scope','mode','model','revision','enabled','status','dimension')}
    result.update(test_status=model.status,key_present=bool(model.secret),
        data_destination='平台服务器模型' if model.mode=='local' else ('平台配置的外部 API' if model.scope=='platform' else '你配置的 API 服务商'))
    if model.scope=='personal' or admin:result['endpoint']=model.endpoint
    if admin:result['fingerprint']=model.fingerprint
    return result


def policy(store,model):
    return Policy(model.endpoint,platform=model.scope=='platform',allowed_hosts=store.config.platform_hosts,
                  resolver=getattr(store,'resolver',None))


def provider_for(store,owner,id,purpose):
    with store.session() as s:
        model=require_model(s,owner,id,purpose)
        secret=Vault(store.config.master_key).decrypt(model.secret,model.id,model.key_version) if model.secret else None
        config={key:getattr(model,key) for key in ('endpoint','purpose','mode','model')}
        transport=SafeTransport(policy(store,model))
        # Tests inject protocol transport explicitly. Production never reads this from config.
        if getattr(store,'model_transport',None):transport=store.model_transport
    return Provider(config,secret,transport)


def save(store,body,owner,scope):
    if body.purpose not in ('chat','embedding') or body.mode not in ('api','local') or (scope=='personal' and body.mode!='api'):
        raise AppError('MODEL','请选择支持的回答/向量能力和接入方式。',422)
    if not re.fullmatch(r'[\w./:@+\-]+',body.model):raise AppError('MODEL','模型名称格式不合法。',422)
    endpoint=normalize(body.endpoint)
    candidate=Model(id=uid(),owner_id=owner,scope=scope,purpose=body.purpose,name=name(body.name),
                    mode=body.mode,endpoint=endpoint,model=body.model)
    policy(store,candidate).validate()
    if body.mode=='local' and urlsplit(endpoint).hostname not in store.config.platform_hosts:
        raise AppError('MODEL','Ollama 地址需要明确列入平台内网许可。',403)
    candidate.secret=Vault(store.config.master_key).encrypt(body.key,candidate.id) if body.key else None
    with store.session() as s:s.add(candidate);s.flush();return public(candidate,scope=='platform')


def editable(s,owner,id,platform=False):
    model=s.scalar(select(Model).where(Model.id==id,Model.scope=='platform' if platform else Model.owner_id==owner).with_for_update())
    if not model:raise AppError('NOT_FOUND','模型配置不存在。',404)
    return model


def test_model(store,owner,id):
    with store.session() as s:
        lock_owner(s,owner)
        model=require_model(s,owner,id,available=False)
        if not model.enabled:raise AppError('MODEL_DISABLED','此模型已停用。',409)
        recent=s.scalar(select(func.count()).select_from(Reservation).where(
            Reservation.owner_id==owner,Reservation.resource=='model_tests',
            Reservation.created_at>now()-timedelta(seconds=60)))
        if recent>=10:raise AppError('MODEL_TEST_LIMIT','模型测试过于频繁，请一分钟后重试。',429)
        s.add(Reservation(owner_id=owner,resource='model_tests',request_id=uid(),amount=1,state='committed'))
        values={key:getattr(model,key) for key in ('id','purpose','mode','endpoint','model','scope')}
        secret=Vault(store.config.master_key).decrypt(model.secret,model.id,model.key_version) if model.secret else None
        transport=getattr(store,'model_transport',None) or SafeTransport(policy(store,model))
    inner=Provider(values,secret,transport)
    provider=MeteredProvider(store,owner,id,inner,allow_untested=True)
    try:
        if values['purpose']=='embedding':
            dimension=len(provider.embed(['模型能力测试'])[0]);model_digest=''
            if values['mode']=='local':
                inventory=inner.models()
                match=next((entry for entry in inventory if entry['name'] in (values['model'],values['model']+':latest')),None)
                if not match:raise AppError('MODEL_MISSING','Ollama 中未安装指定模型。',409)
                model_digest=match.get('digest','')
            fingerprint=hashlib.sha256((values['mode']+'|'+values['endpoint']+'|'+values['model']+'|'+str(dimension)+'|'+model_digest).encode()).hexdigest()
        else:
            text=''.join(provider.chat([{'role':'user','content':'请回答测试成功。'}],length='short'))
            if not text.strip():raise AppError('MODEL_EMPTY','模型未返回回答内容。',502)
            dimension=None;fingerprint=None
        with store.session() as s:
            model=s.get(Model,id)
            if model.fingerprint and model.fingerprint!=fingerprint:
                for index in s.scalars(select(Record).where(Record.kind=='index',Record.model_id==id,Record.state=='active')):
                    index.state='stale';doc=s.get(Record,index.parent_id)
                    if doc and doc.state=='ready':doc.state='needs_rebuild'
                for kb in s.scalars(select(Record).where(Record.kind=='kb',Record.model_id==id)):kb.state='active';kb.payload={**kb.payload,'rebuild_required':True}
            model.status='available';model.dimension=dimension;model.fingerprint=fingerprint
        return {'status':'available','dimension':dimension}
    except Exception:
        with store.session() as s:s.get(Model,id).status='unavailable'
        raise
    finally:provider.close()


def mount(app,store):
    @app.get('/api/model-configs')
    def list_models(request:Request):
        owner=identity(request).id
        with store.session() as s:return [public(m) for m in s.scalars(select(Model).where(or_(Model.scope=='platform',Model.owner_id==owner)).order_by(Model.name))]

    @app.post('/api/model-configs')
    def create(body:ModelBody,request:Request):return save(store,body,identity(request).id,'personal')

    @app.post('/api/model-configs/{id}/test')
    def test(id:str,request:Request):
        owner=identity(request).id
        with store.session() as s:editable(s,owner,id)
        return test_model(store,owner,id)

    @app.patch('/api/model-configs/{id}')
    def edit(id:str,body:dict,request:Request):
        owner=identity(request).id
        if set(body)-{'name','key'}:raise AppError('MODEL_VERSION','修改地址/模型/能力请新增配置，再确认重建。',409)
        with store.session() as s:
            model=editable(s,owner,id)
            if 'name' in body:model.name=name(body['name'])
            if 'key' in body:
                key=body['key']
                if not isinstance(key,str) or not 1<=len(key)<=2048:raise AppError('KEY','密钥格式不合法。',422)
                model.secret=Vault(store.config.master_key).encrypt(key,model.id,model.key_version)
                model.status='untested'
            return public(model)

    @app.delete('/api/model-configs/{id}')
    def remove(id:str,request:Request):
        owner=identity(request).id
        with store.session() as s:
            lock_owner(s,owner);model=editable(s,owner,id)
            prefs=s.get(Preferences,owner).payload
            if id in (prefs['default_chat'],prefs['default_embedding']) or s.scalar(select(Record.id).where(Record.owner_id==owner,Record.model_id==id).limit(1)):
                raise AppError('MODEL_IN_USE','配置正在被默认模型、知识库或历史任务引用，请先切换；历史绑定配置可停用保留。',409)
            s.delete(model)
        return {'deleted':True}

    @app.get('/api/admin/models')
    def platform_models(request:Request):
        administrator(request)
        with store.session() as s:return [public(m,True) for m in s.scalars(select(Model).where(Model.scope=='platform').order_by(Model.name))]

    @app.post('/api/admin/models')
    def platform_create(body:ModelBody,request:Request):
        actor=administrator(request);result=save(store,body,None,'platform')
        with store.session() as s:s.add(Audit(actor_id=actor.id,event='platform_model_created',object_id=result['id']))
        return result

    @app.post('/api/admin/models/{id}/test')
    def platform_test(id:str,request:Request):
        actor=administrator(request)
        with store.session() as s:editable(s,actor.id,id,True)
        return test_model(store,actor.id,id)

    @app.get('/api/admin/models/{id}/impact')
    def platform_impact(id:str,request:Request):
        actor=administrator(request)
        with store.session() as s:
            editable(s,actor.id,id,True)
            return {'knowledge_bases':len(list(s.scalars(select(Record.id).where(Record.model_id==id,Record.kind=='kb',Record.state!='deleted')))),
                    'defaults':sum(id in (p.payload['default_chat'],p.payload['default_embedding']) for p in s.scalars(select(Preferences)))}

    @app.patch('/api/admin/models/{id}')
    def platform_edit(id:str,body:dict,request:Request):
        actor=administrator(request)
        if set(body)-{'enabled','name','key'}:raise AppError('MODEL_VERSION','地址/模型变更需要新增不可变配置版本。',409)
        with store.session() as s:
            model=editable(s,actor.id,id,True)
            if 'enabled' in body:
                if type(body['enabled'])!=bool:raise AppError('INPUT','启停状态格式错误。',422)
                model.enabled=body['enabled']
            if 'name' in body:model.name=name(body['name'])
            if 'key' in body:
                if not isinstance(body['key'],str) or not 1<=len(body['key'])<=2048:raise AppError('KEY','密钥格式不合法。',422)
                model.secret=Vault(store.config.master_key).encrypt(body['key'],id,model.key_version);model.status='untested'
            s.add(Audit(actor_id=actor.id,event='platform_model_updated',object_id=id))
            return public(model,True)
