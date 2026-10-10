import sys,json,sqlite3,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx,pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Record,Vector,Job,Store,load,dump,uid
from test_stage5 import prepare
from test_stage3 import drain,upload

def test_model_connection_failure_keeps_question_without_cloud_fallback(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app);transport=app.state.store.provider_transport;requests=[]
        def offline(req):
            if req.url.path.endswith('chat/completions'):requests.append(str(req.url));raise httpx.ConnectError('private-secret',request=req)
            return transport.handle_request(req)
        app.state.store.provider_transport=httpx.MockTransport(offline)
        g=c.post('/api/conversations/'+conv+'/generations',json={'question':'故障问题','request_key':'failure'}).json()['id'];drain(app.state.store)
        assert c.get('/api/generations/'+g).json()['state']=='failed';assert requests==['https://mock.test/v1/chat/completions']
        text=c.get('/api/conversations/'+conv+'/messages').text;assert '故障问题' in text;assert 'private-secret' not in text
        c.get('/api/generations/'+g+'/events?stream=false');assert len(requests)==1

def test_answer_switch_preserves_index_and_snapshot(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app)
        with app.state.store.session() as s:before=[v.id for v in s.query(Vector)]
        cfg=c.post('/api/model-configs',json={'purpose':'chat','mode':'api','endpoint':'https://mock.test/v1','model':'new-answer','key':'fixture'}).json()['id'];assert c.put('/api/settings',json={'default_chat':cfg}).status_code==200
        assert c.get('/api/documents/'+doc).json()['state']=='ready'
        g=c.post('/api/conversations/'+conv+'/generations',json={'question':'退款','request_key':'model-switch'}).json()['id'];drain(app.state.store)
        assert c.get('/api/generations/'+g).json()['provider_snapshot']['model']=='new-answer'
        with app.state.store.session() as s:assert before==[v.id for v in s.query(Vector)]

def test_database_type_relationship_and_cross_site_defense(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        assert c.post('/api/knowledge-bases',json={'name':' '}).status_code==422
        assert c.post('/api/knowledge-bases',json={'name':'cross'},headers={'sec-fetch-site':'cross-site'}).status_code==403
        assert c.post('/api/knowledge-bases',json={'name':'dns'},headers={'host':'evil.test'}).status_code==403
        with pytest.raises(Exception):
            with app.state.store.session() as s:s.add(Record(kind='document',parent_id='no-parent',payload='{}'))

def test_delete_during_query_embedding_filters_stale_result(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,_=prepare(c,app);original=app.state.store.provider_transport
        def mutate(req):
            assert c.delete('/api/documents/'+doc).status_code==200
            return original.handle_request(req)
        app.state.store.provider_transport=httpx.MockTransport(mutate)
        r=c.post('/api/retrieval',json={'question':'退款'});assert r.status_code==200,r.text;assert r.json()['items']==[]

def test_no_out_of_scope_assistant_history_is_sent(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app)
        g=c.post('/api/conversations/'+conv+'/generations',json={'question':'退款','request_key':'context1','scope':{'type':'documents','ids':[doc]}}).json()['id'];drain(app.state.store)
        kb=c.post('/api/knowledge-bases',json={'name':'另一个范围'}).json()['id'];other=upload(c,kb,['policy-b.md'],key='scope-other').json()[0]['document_id'];drain(app.state.store)
        c.post('/api/conversations/'+conv+'/generations',json={'question':'期限','request_key':'context2','scope':{'type':'documents','ids':[other]}});drain(app.state.store)
        transmitted=json.dumps(calls[-1]['messages'],ensure_ascii=False)
        assert '七天' not in transmitted;assert '三十天' in transmitted

def test_clear_conversations_respects_foreign_key_order(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,_=prepare(c,app);c.post('/api/conversations/'+conv+'/generations',json={'question':'退款','request_key':'delete-all-chat'});drain(app.state.store)
        p=c.post('/api/data/clear/preview',json={'category':'conversations'}).json();r=c.post('/api/data/clear',json={'category':'conversations','token':p['token']});assert r.status_code==200,r.text
        assert c.get('/api/conversations').json()==[];assert c.get('/api/documents/'+doc+'/file').status_code==200

def test_scale_retrieval_is_bounded(tmp_path):
    import psutil,numpy as np
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,_=prepare(c,app);bench=[]
        for count in (100,1000,5000):
            with app.state.store.session() as s:
                original=s.query(Vector).first();base=load(s.get(Record,original.chunk_id).payload);version=original.version_id;generation=original.generation_id;fp=original.fingerprint
                existing=s.query(Vector).count()
                for n in range(existing,count):
                    chunk=Record(id=uid(),kind='chunk',parent_id=version,payload=dump({**base,'ordinal':n+1}));s.add(chunk);s.flush();s.add(Vector(document_id=doc,version_id=version,generation_id=generation,fingerprint=fp,dimension=3,chunk_id=chunk.id,values=dump([.9,.4,.1])))
            before=psutil.Process().memory_info().rss;start=time.perf_counter();r=c.post('/api/retrieval',json={'question':'期限'});assert r.status_code==200,r.text;assert len(r.json()['items'])==5
            bench.append({'chunks':count,'seconds':time.perf_counter()-start,'rss_mb':psutil.Process().memory_info().rss/1024**2,'rss_delta_mb':(psutil.Process().memory_info().rss-before)/1024**2})
        Path('.reports/retrieval-scale.json').write_text(json.dumps(bench,indent=2),encoding='utf8')

def test_question_runs_at_embedding_batch_boundary(tmp_path):
    from app.worker import Worker
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        conv,doc,calls=prepare(c,app);c.put('/api/settings',json={'chunk_length':8,'chunk_overlap':2})
        # Restore the original document's index first, then index another while a question arrives.
        c.post('/api/documents/'+doc+'/reprocess');drain(app.state.store)
        kb=c.get('/api/documents/'+doc).json()['kb_id'];upload(c,kb,['policy-b.md'],key='boundary-doc')
        worker=Worker(app.state.store);app.state.store.worker_runner=worker;original=app.state.store.provider_transport;sequence=[];submitted=[]
        def queued(req):
            if req.url.path.endswith('embeddings'):
                sequence.append('embedding')
                if not submitted:
                    submitted.append(c.post('/api/conversations/'+conv+'/generations',json={'question':'退款','scope':{'type':'documents','ids':[doc]},'request_key':'boundary-question'}).json()['id'])
            else:sequence.append('chat')
            return original.handle_request(req)
        app.state.store.provider_transport=httpx.MockTransport(queued)
        drain(app.state.store)
        assert 'chat' in sequence;assert sequence.index('chat')<len(sequence)-1
        assert c.get('/api/generations/'+submitted[0]).json()['state']=='completed'

def test_backup_blocks_stopped_worker_until_safe_boundary(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        app.state.store.inflight_workers=1
        assert c.post('/api/backups').status_code==409
        app.state.store.inflight_workers=0
        assert c.post('/api/backups').status_code==200

def test_importing_app_module_never_creates_default_data(tmp_path):
    import subprocess,os
    root=Path(__file__).resolve().parents[1];env=dict(os.environ,PYTHONPATH=str(root/'backend'),DATA_ROOT=str(tmp_path/'must-not-exist'))
    r=subprocess.run([sys.executable,'-c','import app.main'],cwd=tmp_path,env=env,capture_output=True,text=True)
    assert r.returncode==0,r.stderr;assert not (tmp_path/'must-not-exist').exists()
