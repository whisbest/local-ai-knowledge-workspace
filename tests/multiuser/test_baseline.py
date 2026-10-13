import hashlib
import importlib.util
import sqlite3
import httpx
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_snapshot_is_readonly_consistent_and_excludes_keys(tmp_path):
    spec = importlib.util.spec_from_file_location('snapshot', ROOT / 'scripts/backup-single-user.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'original'
    database = source / 'state/current/app.sqlite'
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as conn:
        conn.execute('pragma journal_mode=WAL')
        conn.execute('create table proof(value text)')
        conn.execute('insert into proof values (?)', ('preserve-me',))
        conn.commit()
        (source / 'secrets').mkdir()
        (source / 'secrets/private').write_text('never-export-this')
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        manifest = module.snapshot(source, tmp_path / 'snapshot')
        assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with sqlite3.connect(tmp_path / 'snapshot/state/current/app.sqlite') as conn:
        assert conn.execute('select value from proof').fetchone()[0] == 'preserve-me'
    assert not (tmp_path / 'snapshot/secrets').exists()
    assert 'state/current/app.sqlite' in manifest


def test_protocol_fixture_and_distinct_identities(protocol_server, identities, tmp_path):
    endpoint, requests = protocol_server
    assert len({value['marker'] for value in identities.values()}) == 4
    assert not (tmp_path / 'data').exists()
    with httpx.Client(trust_env=False) as client:
        response = client.post(endpoint + '/embeddings', json={'input': ['test'], 'model': 'fixture'})
        assert response.status_code == 200
        assert len(response.json()['data'][0]['embedding']) == 8
        response = client.post(endpoint + '/chat/completions', json={'messages': [{'content': 'fixture'}]})
        assert '[DONE]' in response.text
    assert len(requests) == 2
