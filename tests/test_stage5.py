import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx
from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Record,load
from test_stage3 import upload,drain
from test_stage4 import setup

def prepare(c,app,answer='退款期限是七天。[1]'):
    setup(c,app);kb=c.post('/api/knowledge-bases',json={'name':'资料'}).json()['id'];doc=upload(c,kb,['policy-a.txt']).json()[0]['document_id'];drain(app.state.store)
    original=app.state.store.provider_transport;calls=[]
    def handle(req):
        if req.url.path.endswith('chat/completions'):
            calls.append(json.loads(req.content));return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':answer}}]})+'\n\ndata: [DONE]\n\n')
        return original.handle_request(req)
    app.state.store.provider_transport=httpx.MockTransport(handle)
    cfg=c.post('/api/model-configs',json={'purpose':'chat','mode':'api','endpoint':'https://mock.test/v1','model':'answer','key':'fixture'}).json()['id'];c.put('/api/settings',json={'default_chat':cfg})
    conv=c.post('/api/conversations',json={}).json()['id'];return conv,doc,calls

def test_generation_idempotency_replay_sources_and_history(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app)
        body={'question':'退款期限？','request_key':'same','scope':{'type':'documents','ids':[doc]}}
        g=c.post('/api/conversations/'+conv+'/generations',json=body).json()['id'];assert c.post('/api/conversations/'+conv+'/generations',json=body).json()['id']==g;drain(app.state.store)
        assert c.get('/api/generations/'+g).json()['state']=='completed';assert len(calls)==1
        events=c.get('/api/generations/'+g+'/events?stream=false').text;assert 'event: delta' in events;assert 'event: done' in events
        assert c.get('/api/generations/'+g+'/events?stream=false&after=999').text=='';assert len(calls)==1
        sources=c.get('/api/generations/'+g+'/sources').json();assert sources[0]['citation_number']==1;assert sources[0]['document_id']==doc
        c.delete('/api/documents/'+doc)
        source=c.get('/api/generations/'+g+'/sources').json()[0];assert source['source_status']=='deleted';assert '七' in source['text']
        assert c.patch('/api/conversations/'+conv,json={'title':'退款记录'}).status_code==200;assert c.get('/api/conversations?q=退款').json()
        new=c.post('/api/generations/'+g+'/regenerate',json={'request_key':'new'}).json()['id'];assert new!=g;drain(app.state.store)
        assert c.get('/api/generations/'+new).json()['state']=='completed';assert len(calls)==1 # zero evidence -> deterministic insufficiency
    with TestClient(create_app(tmp_path,worker=False)) as c:
        assert c.get('/api/conversations/'+conv+'/messages').json()['messages'][1]['content']=='退款期限是七天。[1]'
        assert c.get('/api/generations/'+g+'/sources').json()[0]['text']==source['text']

def test_invalid_citation_fails_without_fake_links(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app,'虚构引用 [99]');g=c.post('/api/conversations/'+conv+'/generations',json={'question':'问题','request_key':'one'}).json()['id'];drain(app.state.store)
        assert c.get('/api/generations/'+g).json()['state']=='failed';assert len(c.get('/api/generations/'+g+'/sources').json())==1

def test_stop_queued_generation_and_restart_do_not_invoke_provider(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app);g=c.post('/api/conversations/'+conv+'/generations',json={'question':'退款？','request_key':'stop'}).json()['id']
        assert c.post('/api/generations/'+g+'/stop').json()['state']=='stopped';drain(app.state.store);assert calls==[]
        g2=c.post('/api/conversations/'+conv+'/generations',json={'question':'退款？','request_key':'interrupt'}).json()['id']
    with TestClient(create_app(tmp_path,worker=False)) as c:
        assert c.get('/api/generations/'+g2).json()['state']=='interrupted';assert c.get('/api/conversations/'+conv+'/messages').json()['messages'][-1]['state']=='interrupted'

def test_stop_after_delta_preserves_text_and_prevents_later_delta(tmp_path,monkeypatch):
    from app.providers import Provider
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app)
        g=c.post('/api/conversations/'+conv+'/generations',json={'question':'退款？','request_key':'partial'}).json()['id']
        def partial(self,messages,cancelled,length):
            yield '已保存的部分回答'
            assert c.post('/api/generations/'+g+'/stop').status_code==200
            yield '不应保存的后续输出'
        monkeypatch.setattr(Provider,'chat',partial)
        drain(app.state.store)
        assert c.get('/api/generations/'+g).json()['state']=='stopped'
        assert c.get('/api/conversations/'+conv+'/messages').json()['messages'][-1]['content']=='已保存的部分回答'
