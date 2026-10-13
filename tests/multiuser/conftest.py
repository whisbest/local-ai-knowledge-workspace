"""Dedicated V2 fixtures. No production paths or credentials are used."""
import sys
import json
import hashlib
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
import uuid
import base64
import secrets
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.multiuser.config import Config
from app.multiuser.db import Store


@pytest.fixture
def store(tmp_path):
    root = Path(__file__).resolve().parents[2]
    settings_file = root / '.runtime/multiuser-test.json'
    if not settings_file.exists():
        pytest.fail('Run scripts/prepare-multiuser-test.py; tests require real PostgreSQL')
    url = json.loads(settings_file.read_text(encoding='utf8'))['database_url']
    schema = 'test_' + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    config = Config(url, tmp_path / 'private-workspace', master_key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    instance = Store(config)
    instance.engine.dispose()
    instance.engine = create_engine(url, hide_parameters=True, connect_args={'options': f'-csearch_path={schema}'})
    from sqlalchemy.orm import sessionmaker
    instance.Session = sessionmaker(instance.engine, expire_on_commit=False)
    instance.migrate()
    yield instance
    instance.engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    admin.dispose()


@pytest.fixture
def identities():
    return {name: {'username': name, 'role': 'admin' if name == 'admin' else 'user',
                   'marker': f'PRIVATE-{name}-unique-knowledge',
                   'password': f'Test-{name}-only-2026!'} for name in ('alice', 'bob', 'carol', 'admin')}


@pytest.fixture
def protocol_server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, body))
            if self.path.endswith('/embeddings'):
                values = body['input'] if isinstance(body['input'], list) else [body['input']]
                data = {'data': [{'index': i, 'embedding': [1.0] +
                        [x / 255 for x in hashlib.sha256(value.encode()).digest()[:7]]}
                                for i, value in enumerate(values)]}
                raw = json.dumps(data).encode()
                content_type = 'application/json'
            elif self.path.endswith('/chat/completions'):
                context = body['messages'][-1]['content']
                raw = ('data: ' + json.dumps({'choices': [{'delta': {'content': context[:100] + ' [1]'}}]})
                       + '\n\ndata: [DONE]\n\n').encode()
                content_type = 'text/event-stream'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1', requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.fixture
def accounts(store, identities):
    from app.multiuser.auth import create_user
    with store.session() as s:
        return {name: create_user(s, value['username'], value['password'], value['role'], must_change=False).id
                for name, value in identities.items()}


@pytest.fixture
def clients(store, accounts, identities):
    from fastapi.testclient import TestClient
    from app.multiuser.main import create_app
    app = create_app(store=store)
    result = {}
    for name, value in identities.items():
        client = TestClient(app)
        response = client.post('/api/auth/login', json={'username': name, 'password': value['password']})
        assert response.status_code == 200, response.text
        client.headers['x-csrf-token'] = response.json()['csrf']
        result[name] = client
    yield result
    for client in result.values(): client.close()


@pytest.fixture
def embedding_fixture(store, protocol_server):
    from app.multiuser.db import Model
    from app.providers import Provider
    endpoint, requests = protocol_server
    with store.session() as s:
        model = Model(scope='platform', purpose='embedding', name='Fixture vectors',mode='api',
                      endpoint=endpoint,model='fixture',status='available',dimension=8,fingerprint='a'*64)
        s.add(model);s.flush();id=model.id
    store.provider_factory=lambda owner, model_id, purpose: Provider(
        {'endpoint':endpoint,'purpose':purpose,'mode':'api','model':'fixture'},'fixture-only')
    return id,requests
