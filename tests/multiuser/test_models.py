import base64
import secrets
import socket
import json
from dataclasses import replace
import pytest
import httpx
from sqlalchemy import select
from app.multiuser.db import Model
from sqlalchemy.exc import IntegrityError
from app.multiuser.secrets import Vault
from app.multiuser.documents import process_document
from app.multiuser.chat import execute_generation
from app.core import AppError
from test_documents_isolation import upload


def model_setup(store):
    requests=[]
    store.resolver=lambda host,port,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',port))]
    def handler(request):
        body=json.loads(request.content);requests.append(body)
        if request.url.path.endswith('embeddings'):
            return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.,2.,3.]} for i,_ in enumerate(body['input'])]})
        content='测试成功 [1]' if body['messages'][0]['role']=='system' else '测试成功'
        return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':content}}]})+'\n\ndata: [DONE]\n\n')
    store.model_transport=httpx.MockTransport(handler)
    return requests


@pytest.mark.parametrize('embedding_scope,chat_scope',[('platform','platform'),('personal','personal'),('platform','personal'),('personal','platform')])
def test_dual_source_and_mixed_pipeline_without_key_leaks(store,clients,embedding_scope,chat_scope):
    model_setup(store);a=clients['alice'];admin=clients['admin'];ids={}
    for purpose,scope in [('embedding',embedding_scope),('chat',chat_scope)]:
        prefix='/api/admin/models' if scope=='platform' else '/api/model-configs';client=admin if scope=='platform' else a
        secret=f'PRIVATE-{purpose}-API-KEY'
        response=client.post(prefix,json={'name':purpose,'purpose':purpose,'mode':'api','endpoint':'https://api.example.com/v1','model':purpose,'key':secret})
        assert response.status_code==200,response.text;assert secret not in response.text
        ids[purpose]=response.json()['id']
        assert client.post(prefix+'/'+ids[purpose]+'/test').status_code==200
        with store.session() as s:
            model=s.get(Model,ids[purpose]);assert secret not in model.secret
            assert Vault(store.config.master_key).decrypt(model.secret,model.id)==secret
    kb=a.post('/api/knowledge-bases',json={'name':'mixed','embedding_id':ids['embedding']}).json()['id']
    doc=upload(a,kb,b'Model source isolation').json()[0]['document_id'];process_document(store,a.get('/api/jobs').json()[0]['id'])
    assert a.get('/api/dashboard').json()['ready']==1
    conv=a.post('/api/conversations',json={}).json()['id']
    response=a.post('/api/conversations/'+conv+'/generations',json={'question':'test','request_key':'flow','model_id':ids['chat']})
    assert response.status_code==200,response.text
    generation=response.json()['id'];job=next(j for j in a.get('/api/jobs').json() if j['target']==generation)
    execute_generation(store,job['id']);assert a.get('/api/generations/'+generation).json()['state']=='completed'
    assert 'API-KEY' not in a.get('/api/model-configs').text
    if chat_scope=='platform':
        public=next(m for m in a.get('/api/model-configs').json() if m['id']==ids['chat']);assert 'endpoint' not in public
        assert clients['bob'].post('/api/model-configs/'+ids['chat']+'/test').status_code==404
    else:
        assert ids['chat'] not in clients['bob'].get('/api/model-configs').text
        assert clients['bob'].patch('/api/model-configs/'+ids['chat'],json={'key':'hacked'}).status_code==404


def test_keys_wrong_master_rotation_unavailable_no_fallback(store,clients):
    calls=model_setup(store);a=clients['alice'];admin=clients['admin']
    response=a.post('/api/model-configs',json={'name':'mine','purpose':'chat','endpoint':'https://api.example.com/v1','model':'fixture','key':'Never-echo-me'})
    id=response.json()['id'];assert a.post(f'/api/model-configs/{id}/test').status_code==200
    assert a.patch(f'/api/model-configs/{id}',json={'endpoint':'https://other.example.com'}).status_code==409
    assert a.patch(f'/api/model-configs/{id}',json={'key':'New-never-echo'}).json()['status']=='untested'
    assert a.post(f'/api/model-configs/{id}/test').status_code==200
    old_key=store.config.master_key;new_key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    store.config=replace(store.config,master_key=new_key)
    response=a.post(f'/api/model-configs/{id}/test');assert response.status_code==503 and 'New-never-echo' not in response.text
    with store.session() as s:
        model=s.get(Model,id);plain=Vault(old_key).decrypt(model.secret,id)
        model.key_version=2;model.secret=Vault(new_key).encrypt(plain,id,2)
    assert a.post(f'/api/model-configs/{id}/test').status_code==200
    assert a.delete(f'/api/model-configs/{id}').status_code==200
    assert a.post('/api/admin/models',json={}).status_code in (403,422)
    for endpoint in ('https://127.0.0.1','http://localhost:11434','https://[::1]'):
        store.resolver=lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        assert a.post('/api/model-configs',json={'name':'invalid','purpose':'chat','endpoint':endpoint,'model':'x','key':'do-not-echo'}).status_code in (403,422)
    assert 'Never-echo-me' not in str(calls)


def test_platform_disabled_does_not_disable_personal_or_fallback_and_private_binding_rejected(store,clients,accounts):
    requests=model_setup(store);admin=clients['admin'];a=clients['alice'];ids=[]
    for client,prefix in [(admin,'/api/admin/models'),(a,'/api/model-configs')]:
        r=client.post(prefix,json={'name':'choice','purpose':'embedding','endpoint':'https://api.example.com/v1','model':'fixture','key':'secret-fixture'})
        id=r.json()['id'];assert client.post(prefix+'/'+id+'/test').status_code==200;ids.append(id)
    platform,personal=ids
    kb=a.post('/api/knowledge-bases',json={'name':'binding','embedding_id':platform}).json()['id']
    doc=upload(a,kb,b'Content in old vector space').json()[0]['document_id'];process_document(store,a.get('/api/jobs').json()[0]['id'])
    with store.session() as s:
        from app.multiuser.db import Record
        with pytest.raises(IntegrityError):
            with s.begin_nested():s.add(Record(owner_id=accounts['bob'],kind='kb',model_id=personal));s.flush()
    assert admin.get('/api/admin/models/'+platform+'/impact').json()['knowledge_bases']==1
    assert admin.patch('/api/admin/models/'+platform,json={'enabled':False}).status_code==200
    previous=len(requests)
    r=a.post('/api/retrieval',json={'question':'source'});assert r.status_code==409 and r.json()['code']=='MODEL_UNAVAILABLE'
    assert len(requests)==previous
    assert a.post('/api/model-configs/'+personal+'/test').status_code==200
    assert a.put('/api/knowledge-bases/'+kb+'/embedding',json={'embedding_id':personal}).status_code==409
    response=a.put('/api/knowledge-bases/'+kb+'/embedding',json={'embedding_id':personal,'confirm':True})
    assert response.status_code==200,response.text
    process_document(store,response.json()['jobs'][0]['id'])
    assert a.post('/api/retrieval',json={'question':'source'}).json()['items']


def test_real_ollama_platform_embedding_and_chat(store,clients):
    admin=clients['admin'];ids={}
    for purpose,model in [('embedding','qwen3-embedding:0.6b'),('chat','qwen2.5:3b')]:
        r=admin.post('/api/admin/models',json={'name':'Real Ollama '+purpose,'purpose':purpose,'mode':'local',
                  'endpoint':'http://127.0.0.1:11434','model':model})
        assert r.status_code==200,r.text;ids[purpose]=r.json()['id']
        r=admin.post('/api/admin/models/'+ids[purpose]+'/test');assert r.status_code==200,r.text
        if purpose=='embedding':assert r.json()['dimension']>0
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'真实模型测试','embedding_id':ids['embedding']}).json()['id']
    upload(a,kb,'青竹项目退款期限为七天。'.encode());process_document(store,a.get('/api/jobs').json()[0]['id'])
    assert a.get('/api/dashboard').json()['ready']==1
    conv=a.post('/api/conversations',json={}).json()['id']
    g=a.post('/api/conversations/'+conv+'/generations',json={'question':'青竹项目退款期限是多少天？','request_key':'real','model_id':ids['chat']}).json()['id']
    execute_generation(store,next(j['id'] for j in a.get('/api/jobs').json() if j['target']==g))
    r=a.get('/api/generations/'+g);assert r.json()['state']=='completed',r.text
    answer=a.get('/api/conversations/'+conv+'/messages').json()['messages'][-1]['content']
    assert ('七' in answer or '7' in answer) and '[1]' in answer
