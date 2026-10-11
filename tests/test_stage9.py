import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Job, Record, dump


def test_saved_model_configuration_can_be_deleted_with_usage_protection(tmp_path):
    app = create_app(tmp_path, worker=False)
    with TestClient(app) as client:
        config = client.post('/api/model-configs', json={
            'purpose': 'chat', 'mode': 'api', 'endpoint': 'https://example.test/v1',
            'model': 'fixture', 'key': 'private-key',
        }).json()
        config_id = config['id']
        assert client.delete('/api/model-configs/' + config_id).status_code == 200
        assert client.get('/api/model-configs').json() == []
        assert not (tmp_path / 'secrets' / config_id).exists()

        config = client.post('/api/model-configs', json={
            'purpose': 'chat', 'mode': 'api', 'endpoint': 'https://example.test/v1',
            'model': 'fixture', 'key': 'private-key',
        }).json()
        config_id = config['id']
        assert client.put('/api/settings', json={'default_chat': config_id}).status_code == 200
        response = client.delete('/api/model-configs/' + config_id)
        assert response.status_code == 409
        assert response.json()['code'] == 'MODEL_IN_USE'
        assert client.put('/api/settings', json={'default_chat': None}).status_code == 200
        assert client.delete('/api/model-configs/' + config_id).status_code == 200


def test_completed_failed_cancelled_processing_jobs_can_be_deleted_but_active_jobs_cannot(tmp_path):
    app = create_app(tmp_path, worker=False)
    with TestClient(app) as client:
        with app.state.store.session() as session:
            jobs = []
            for state in ('completed', 'failed', 'cancelled'):
                job = Job(kind='process', target='document-' + state, state=state, stage=state)
                session.add(job)
                session.flush()
                jobs.append(job.id)
            active = Job(kind='pull', target='model', state='running', stage='downloading')
            session.add(active)
            session.flush()
            active_id = active.id
        for job_id in jobs:
            response = client.delete('/api/jobs/' + job_id)
            assert response.status_code == 200, response.text
            assert response.json()['deleted'] is True
        response = client.delete('/api/jobs/' + active_id)
        assert response.status_code == 409
        assert response.json()['code'] == 'JOB_ACTIVE'
        with app.state.store.session() as session:
            assert session.get(Job, active_id) is not None


def test_model_configuration_in_use_by_active_index_cannot_be_deleted(tmp_path):
    app = create_app(tmp_path, worker=False)
    with TestClient(app) as client:
        config = client.post('/api/model-configs', json={
            'purpose': 'embedding', 'mode': 'api', 'endpoint': 'https://example.test/v1',
            'model': 'fixture', 'key': 'private-key',
        }).json()
        kb = client.post('/api/knowledge-bases', json={'name': '测试'}).json()
        with app.state.store.session() as session:
            document = Record(id='doc-for-index', kind='document', parent_id=kb['id'], state='ready', payload=dump({'name': '资料', 'current_version_id': 'version'}))
            index = Record(id='index-for-config', kind='index', parent_id=document.id, state='active', payload=dump({'model_config_id': config['id']}))
            session.add_all([document, index])
        response = client.delete('/api/model-configs/' + config['id'])
        assert response.status_code == 409
        assert response.json()['code'] == 'MODEL_IN_USE'
