from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.multiuser.main import create_app
from app.multiuser.db import User, Session, Audit, now
from app.multiuser.auth import COOKIE, token_hash, create_user


def test_login_credentials_cookie_csrf_and_logout(store, accounts, identities):
    app = create_app(store=store)
    with TestClient(app) as c:
        for path in ('/api/settings','/api/documents/private/file','/api/generations/private/events','/api/admin/users'):
            assert c.get(path).status_code == 401
        assert c.post('/api/auth/login', json={'username':'alice','password':'wrong'}).status_code == 401
        r = c.post('/api/auth/login', json={'username':'alice','password':identities['alice']['password']})
        assert r.status_code == 200
        assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
        cookie = c.cookies.get(COOKIE)
        assert c.get('/api/auth/me').json()['id'] == accounts['alice']
        assert c.post('/api/auth/logout').status_code == 403
        assert c.post('/api/auth/logout', headers={'x-csrf-token':'wrong'}).status_code == 403
        csrf = r.json()['csrf']
        assert c.post('/api/auth/logout', headers={'x-csrf-token':csrf}).status_code == 200
        c.cookies.set(COOKIE, cookie)
        assert c.get('/api/auth/me').status_code == 401
    with store.session() as s:
        user = s.get(User, accounts['alice'])
        assert user.password_hash.startswith('$argon2id$')
        assert identities['alice']['password'] not in user.password_hash
        session = s.get(Session, token_hash(cookie))
        assert session.revoked and cookie != session.token_hash


def test_roles_disable_reset_and_expired_sessions(store, accounts, clients, identities):
    alice, admin = clients['alice'], clients['admin']
    assert alice.get('/api/admin/users').status_code == 403
    assert alice.post('/api/admin/users', json={'username':'new','password':'sufficient-password'}).status_code == 403
    assert admin.patch('/api/admin/users/'+accounts['bob'], json={'enabled':False}).status_code == 200
    assert clients['bob'].get('/api/auth/me').status_code == 401
    assert admin.patch('/api/admin/users/'+accounts['carol'], json={'password':'Changed-password-2026'}).status_code == 200
    assert clients['carol'].get('/api/auth/me').status_code == 401
    with store.session() as s:
        session = s.scalar(select(Session).where(Session.owner_id == accounts['alice']))
        session.expires_at = now()-timedelta(seconds=1)
    assert alice.get('/api/auth/me').status_code == 401


def test_first_password_and_all_session_revocation(store):
    with store.session() as s: create_user(s, 'first', 'Temporary-pass-2026')
    with TestClient(create_app(store=store)) as c:
        r = c.post('/api/auth/login', json={'username':'first','password':'Temporary-pass-2026'})
        c.headers['x-csrf-token'] = r.json()['csrf']
        assert c.get('/api/auth/me').json()['must_change']
        assert c.get('/api/settings').json()['code'] == 'PASSWORD_CHANGE'
        assert c.post('/api/auth/password', json={'current_password':'wrong','new_password':'Changed-pass-2026'}).status_code == 403
        assert c.post('/api/auth/password', json={'current_password':'Temporary-pass-2026','new_password':'Changed-pass-2026'}).status_code == 200
        assert c.get('/api/auth/me').status_code == 401
        r = c.post('/api/auth/login', json={'username':'first','password':'Changed-pass-2026'})
        assert not r.json()['must_change']


def test_trusted_host_origin_proxy_and_login_throttling(store, accounts, identities):
    with TestClient(create_app(store=store)) as c:
        credentials = {'username':'alice','password':identities['alice']['password']}
        assert c.post('/api/auth/login',json=credentials, headers={'host':'evil.example'}).status_code == 400
        assert c.post('/api/auth/login',json=credentials, headers={'origin':'https://evil.example'}).status_code == 403
        assert c.post('/api/auth/login',json=credentials, headers={'x-forwarded-for':'127.0.0.1'}).status_code == 400
        assert c.post('/api/auth/login',json=credentials, headers={'sec-fetch-site':'cross-site'}).status_code == 403
        for _ in range(10):
            assert c.post('/api/auth/login',json={'username':'alice','password':'wrong'}).status_code == 401
        assert c.post('/api/auth/login',json=credentials).status_code == 429
        assert 'password' not in str(c.get('/api/health').json())


def test_bootstrap_refuses_overwrite_and_validation_redacts(store):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).resolve().parents[2]/'scripts/bootstrap-admin.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.bootstrap(store,'operator','Initial-secret-2026')
    with pytest.raises(ValueError): module.bootstrap(store,'operator','Replacement-secret')
    with TestClient(create_app(store=store)) as c:
        response = c.post('/api/auth/login',json={'username':'operator','password':'DO-NOT-ECHO'*30})
        assert response.status_code == 422 and 'DO-NOT-ECHO' not in response.text
