import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx,pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Record,Vector,load
from test_stage3 import upload,drain

def setup(c,app,dimension=3,model='vector'):
    def mock(req):
        b=json.loads(req.content)
        vectors=[]
        for text in b['input']:
            vectors.append(([1.,0.,.1] if '七' in text else [0.,1.,.1])[:dimension])
        return httpx.Response(200,json={'data':[{'index':i,'embedding':v} for i,v in enumerate(vectors)]})
    app.state.store.provider_transport=httpx.MockTransport(mock)
    cfg=c.post('/api/model-configs',json={'purpose':'embedding','mode':'api','endpoint':'https://mock.test/v1','model':model,'key':'fixture'}).json()['id']
    assert c.post('/api/model-configs/'+cfg+'/test').status_code==200
    assert c.put('/api/settings',json={'default_embedding':cfg}).status_code==200
    return cfg

def test_scope_version_delete_and_compatibility(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        setup(c,app)
        a=c.post('/api/knowledge-bases',json={'name':'A'}).json()['id'];b=c.post('/api/knowledge-bases',json={'name':'B'}).json()['id']
        da=upload(c,a,['policy-a.txt'],key='a').json()[0]['document_id'];db=upload(c,b,['policy-b.md'],key='b').json()[0]['document_id'];drain(app.state.store)
        result=c.post('/api/retrieval',json={'question':'退款七天？','scope':{'type':'kb','ids':[a]}}).json();assert result['items'];assert {x['document_id'] for x in result['items']}=={da}
        assert c.get('/api/dashboard').json()['ready']==2
        old_version=c.get('/api/documents/'+da).json()['current_version_id']
        assert upload(c,a,['policy-a.txt'],'replace','replace').status_code==200
        assert not c.post('/api/retrieval',json={'question':'七天','scope':{'type':'documents','ids':[da]}}).json()['items']
        drain(app.state.store)
        items=c.post('/api/retrieval',json={'question':'七天','scope':{'type':'documents','ids':[da]}}).json()['items'];assert all(i['version_id']!=old_version for i in items)
        assert c.delete('/api/documents/'+db).status_code==200
        assert all(i['document_id']!=db for i in c.post('/api/retrieval',json={'question':'三十天'}).json()['items'])
        setup(c,app,model='different')
        result=c.post('/api/retrieval',json={'question':'七天'}).json();assert result['items']==[];assert result['warnings'][0]['state']=='needs_rebuild'
        assert c.post('/api/indexes/rebuild',json={'document_ids':[da]}).status_code==200;drain(app.state.store)
        assert c.post('/api/retrieval',json={'question':'七天'}).json()['items']
        assert c.post('/api/indexes/clear',json={'confirm':True,'document_ids':[da]}).status_code==200
        assert c.get('/api/documents/'+da+'/file').status_code==200
        assert c.get('/api/documents/'+da).json()['state']=='needs_rebuild'
        assert c.post('/api/retrieval',json={'question':'七天'}).json()['items']==[]

def test_failed_dimension_build_never_activates(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        setup(c,app);kb=c.post('/api/knowledge-bases',json={'name':'失败'}).json()['id'];upload(c,kb,['policy-a.txt'])
        app.state.store.provider_transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'data':[{'index':0,'embedding':[1.,2.]}]}))
        drain(app.state.store)
        assert c.get('/api/documents').json()['items'][0]['state']=='failed'
        with app.state.store.session() as s:assert s.query(Vector).count()==0;assert s.query(Record).filter_by(kind='index',state='active').count()==0

def test_real_local_retrieval_and_memory(tmp_path):
    import psutil
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        cfg=c.post('/api/model-configs',json={'purpose':'embedding','mode':'local','endpoint':'http://127.0.0.1:11434','model':'qwen3-embedding:0.6b'}).json()['id']
        assert c.post('/api/model-configs/'+cfg+'/test').status_code==200;c.put('/api/settings',json={'default_embedding':cfg})
        kb=c.post('/api/knowledge-bases',json={'name':'实测'}).json()['id'];start=time.perf_counter();upload(c,kb,['policy-a.txt','policy-b.md','two-pages.pdf']);drain(app.state.store)
        r=c.post('/api/retrieval',json={'question':'退款期限是多少天？'});assert r.status_code==200,r.text;assert r.json()['items']
        report={'import_seconds':time.perf_counter()-start,'retrieval_ms':r.json()['elapsed_ms'],'python_rss_mb':psutil.Process().memory_info().rss/1024**2,'available_ram_mb':psutil.virtual_memory().available/1024**2}
        Path('.reports/stage4-resource.json').write_text(json.dumps(report),encoding='utf8')
