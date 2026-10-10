import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
from app.main import create_app
from test_stage5 import prepare
from test_stage3 import drain

def test_statistics_preferences_and_confirmed_index_clear(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        assert c.get('/api/dashboard').json()['recent_conversations']==[]
        conv,doc,calls=prepare(c,app);g=c.post('/api/conversations/'+conv+'/generations',json={'question':'退款期限？','request_key':'home'}).json();drain(app.state.store)
        dash=c.get('/api/dashboard').json();assert dash['ready']==1;assert dash['documents']==c.get('/api/documents').json()['total'];assert dash['recent_conversations'][0]['id']==conv
        p=c.post('/api/data/clear/preview',json={'category':'indexes'}).json();assert p['counts']['vectors']>0
        assert c.post('/api/data/clear',json={'category':'indexes','token':'wrong'}).status_code==409
        assert c.post('/api/data/clear',json={'category':'indexes','token':p['token']}).status_code==200
        assert c.get('/api/documents/'+doc+'/file').status_code==200;assert c.get('/api/conversations/'+conv+'/messages').json()['messages'];assert c.get('/api/dashboard').json()['ready']==0
        assert c.put('/api/settings',json={'answer_length':'short','source_panel':False}).status_code==200
        assert c.put('/api/settings',json={'answer_length':'invalid'}).status_code==400
        assert c.put('/api/settings',json={'chunk_length':'10'}).status_code==400
        assert c.get('/api/data/usage').json()['host_path']
    with TestClient(create_app(tmp_path,worker=False)) as c:assert c.get('/api/settings').json()['source_panel'] is False
