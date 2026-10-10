import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.providers import Provider,fingerprint
from app.core import AppError,Record,load

def mock(request):
    body=json.loads(request.content) if request.content else {}
    if request.url.path.endswith('embeddings'):return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.,2.,3.]} for i in range(len(body['input']))]})
    if request.url.path.endswith('chat/completions'):return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'测试成功'}}]})+'\n\ndata: [DONE]\n\n')
    return httpx.Response(401,json={'error':'secret-test-key'})

def test_independent_api_capabilities_and_secrets(tmp_path):
    app=create_app(tmp_path,worker=False);app.state.store.provider_transport=httpx.MockTransport(mock)
    with TestClient(app) as c:
        configs=[]
        for purpose in ('chat','embedding'):
            r=c.post('/api/model-configs',json={'purpose':purpose,'mode':'api','endpoint':'https://example.test/v1/','model':'test','key':'secret-test-key'})
            assert r.status_code==200;assert 'secret-test-key' not in r.text
            id=r.json()['id'];configs.append(id)
            r=c.post('/api/model-configs/'+id+'/test');assert r.status_code==200,r.text
            if purpose=='embedding':assert r.json()['dimension']==3
        assert c.put('/api/settings',json={'default_chat':configs[0],'default_embedding':configs[1]}).status_code==200
        assert 'secret-test-key' not in c.get('/api/model-configs').text
        with app.state.store.session() as s:assert 'secret-test-key' not in ''.join(r.payload for r in s.query(Record))
        assert c.delete('/api/model-configs/'+configs[0]+'/key').status_code==200
        assert c.post('/api/model-configs/'+configs[0]+'/test').json()['code']=='MISSING_KEY'
    with TestClient(create_app(tmp_path,worker=False)) as c:assert c.get('/api/settings').json()['default_embedding']==configs[1]

def test_provider_errors_dimensions_and_no_fallback():
    cfg={'purpose':'embedding','mode':'api','protocol':'openai-compatible','endpoint':'https://example.test/v1','model':'x'}
    for response,code in [(httpx.Response(401,json={'error':'private-key'}),'MODEL_SERVICE'),(httpx.Response(200,json={'data':[{'index':0,'embedding':[0,0]}]}),'EMBEDDING_RESPONSE')]:
        calls=[]
        def handle(req):calls.append(req);return response
        p=Provider(cfg,'private-key',httpx.MockTransport(handle))
        with pytest.raises(AppError) as e:p.embed(['one'])
        assert e.value.code==code;assert 'private-key' not in e.value.message;assert len(calls)==1;p.close()
    assert fingerprint(cfg,3)==fingerprint({**cfg,'key':'changed','revision':'2'},3)
    assert fingerprint(cfg,3)!=fingerprint({**cfg,'model':'new'},3)

def test_local_endpoint_and_download_confirmation(tmp_path):
    with TestClient(create_app(tmp_path,worker=False)) as c:
        assert c.post('/api/model-configs',json={'purpose':'chat','mode':'local','endpoint':'https://remote.test','model':'x'}).status_code==400
        assert c.post('/api/model-configs',json={'purpose':'chat','mode':'api','endpoint':'https://secret@example.test','model':'x'}).status_code==400
        assert c.post('/api/local-models/pull',json={'model':'x'}).status_code==400
        assert c.post('/api/local-models/pull',json={'model':'x','confirm_network':True}).status_code==200

def test_real_local_capabilities_separately(tmp_path):
    with TestClient(create_app(tmp_path,worker=False)) as c:
        for purpose,model in [('chat','qwen2.5:3b'),('embedding','qwen3-embedding:0.6b')]:
            r=c.post('/api/model-configs',json={'purpose':purpose,'mode':'local','endpoint':'http://127.0.0.1:11434','model':model});assert r.status_code==200
            result=c.post('/api/model-configs/'+r.json()['id']+'/test');assert result.status_code==200,result.text
            if purpose=='embedding':assert result.json()['dimension']==1024
