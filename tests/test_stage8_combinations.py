import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx,pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Record,Vector,load
from test_stage3 import upload,drain
from test_stage5 import prepare

@pytest.mark.parametrize('chat_mode,embedding_mode',[('local','api'),('api','local')])
def test_independent_mixed_modes_contract(tmp_path,chat_mode,embedding_mode):
    app=create_app(tmp_path,worker=False);used=[]
    def protocol(req):
        used.append(req.url.path)
        if req.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'chat:latest','digest':'one'},{'name':'embedding:latest','digest':'two'}]})
        b=json.loads(req.content)
        if req.url.path in ('/api/embed','/v1/embeddings'):
            v=[[1.,2.,3.] for _ in b['input']];return httpx.Response(200,json={'embeddings':v} if req.url.path=='/api/embed' else {'data':[{'index':i,'embedding':x} for i,x in enumerate(v)]})
        if req.url.path=='/api/chat':return httpx.Response(200,text=json.dumps({'message':{'content':'退款七天。[1]'},'done':False})+'\n'+json.dumps({'done':True})+'\n')
        return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'退款七天。[1]'}}]})+'\n\ndata: [DONE]\n\n')
    app.state.store.provider_transport=httpx.MockTransport(protocol)
    with TestClient(app) as c:
        for purpose,mode in [('chat',chat_mode),('embedding',embedding_mode)]:
            cfg=c.post('/api/model-configs',json={'purpose':purpose,'mode':mode,'endpoint':'http://127.0.0.1:11434' if mode=='local' else 'https://protocol.test/v1','model':purpose,'key':'contract-secret'}).json()['id'];assert c.post('/api/model-configs/'+cfg+'/test').status_code==200;c.put('/api/settings',json={'default_'+purpose:cfg})
        kb=c.post('/api/knowledge-bases',json={'name':'混合模式'}).json()['id'];upload(c,kb,['policy-a.txt']);drain(app.state.store)
        conv=c.post('/api/conversations',json={}).json()['id'];g=c.post('/api/conversations/'+conv+'/generations',json={'question':'退款期限','request_key':'mixed'}).json()['id'];drain(app.state.store)
        assert c.get('/api/generations/'+g).json()['state']=='completed'
        assert ('/api/chat' if chat_mode=='local' else '/v1/chat/completions') in used
        assert ('/api/embed' if embedding_mode=='local' else '/v1/embeddings') in used

def test_replacement_with_changed_content_never_uses_old_fact(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,_=prepare(c,app);g=c.post('/api/conversations/'+conv+'/generations',json={'question':'期限','request_key':'old-fact'}).json()['id'];drain(app.state.store)
        kb=c.get('/api/documents/'+doc).json()['kb_id']
        content='青竹项目的退款期限调整为三十天。'.encode('utf8')
        r=c.post('/api/documents/upload',data={'kb_id':kb,'decision':'replace','request_key':'changed-version'},files={'files':('policy-a.txt',content)});assert r.status_code==200,r.text;assert r.json()[0]['document_id']==doc
        assert c.post('/api/retrieval',json={'question':'退款期限'}).json()['items']==[];drain(app.state.store)
        result=c.post('/api/retrieval',json={'question':'退款期限'}).json();assert result['items'];assert all('七天' not in i['text'] for i in result['items']);assert '三十天' in result['items'][0]['text']
        source=c.get('/api/generations/'+g+'/sources').json()[0];assert source['source_status']=='replaced';assert '七天' in source['text']

def test_missing_local_model_and_stopped_service_are_diagnosed(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        cfg=c.post('/api/model-configs',json={'purpose':'chat','mode':'local','endpoint':'http://127.0.0.1:11434','model':'missing'}).json()['id']
        app.state.store.provider_transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'models':[]}))
        assert c.post('/api/model-configs/'+cfg+'/test').json()['code']=='MODEL_MISSING'
        def offline(r):raise httpx.ConnectError('offline',request=r)
        app.state.store.provider_transport=httpx.MockTransport(offline)
        assert c.post('/api/model-configs/'+cfg+'/test').json()['code']=='MODEL_SERVICE'

def test_request_validation_does_not_echo_supplied_key(tmp_path):
    with TestClient(create_app(tmp_path,worker=False)) as c:
        r=c.post('/api/model-configs',json={'key':'secret-marker','model':3});assert r.status_code==422;assert 'secret-marker' not in r.text
