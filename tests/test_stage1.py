import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Store,Job

def test_settings_and_knowledge_survive_restart(tmp_path):
    with TestClient(create_app(tmp_path,worker=False)) as c:
        kb=c.post('/api/knowledge-bases',json={'name':'学习资料'}).json()
        assert c.put('/api/settings',json={'answer_length':'long'}).status_code==200
    with TestClient(create_app(tmp_path,worker=False)) as c:
        assert c.get('/api/knowledge-bases').json()[0]['id']==kb['id']
        assert c.get('/api/settings').json()['answer_length']=='long'
        assert c.get('/api/dashboard').json()['documents']==0

def test_only_one_worker_can_claim_and_recovery_marks_interrupted(tmp_path):
    a=Store(tmp_path); b=Store(tmp_path)
    with a.session() as s:s.add(Job(kind='process',target='document'))
    claimed=a.claim(); assert claimed
    assert b.claim() is None
    b.recover()
    with a.session() as s:assert s.get(Job,claimed).state=='interrupted'
    a.engine.dispose();b.engine.dispose()

def test_invalid_settings_and_cross_origin_are_rejected(tmp_path):
    with TestClient(create_app(tmp_path,worker=False)) as c:
        assert c.put('/api/settings',json={'chunk_length':100,'chunk_overlap':100}).status_code==400
        assert c.post('/api/knowledge-bases',json={'name':'evil'},headers={'origin':'https://example.com'}).status_code==403
        assert c.get('/api/knowledge-bases').json()==[]
