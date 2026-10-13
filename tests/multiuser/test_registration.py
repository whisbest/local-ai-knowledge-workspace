from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import ssl
import zipfile
import hashlib

import pytest
from sqlalchemy import select, text

from app.multiuser.db import EmailChallenge, User, now
from app.multiuser.registration import send_mail
from app.multiuser.backups import backup, restore_preflight, restore


def enable(store):
    store.config = replace(store.config, registration_enabled=True, mail_backend='local')


def get_code(store, client, email):
    response = client.post('/api/auth/registration-code', json={'email': email})
    assert response.status_code == 200, response.text
    files = list((store.config.data_root / 'registration-mail').glob('*.json'))
    messages = [json.loads(path.read_text()) for path in files]
    message = next(item for item in messages if item['email'] == email.casefold().strip())
    assert message['code'] not in response.text
    return message['code']


def register(client, email, code, **extra):
    return client.post('/api/auth/register', json={
        'email': email, 'code': code, 'password': 'Personal-mail-password-2026!', **extra})


def test_verified_email_signup_login_private_space_and_existing_admin(store, clients, accounts):
    enable(store)
    client = clients['carol']
    code = get_code(store, client, '  New.Person@Example.com  ')
    assert not any('Personal-mail' in p.read_text() for p in (store.config.data_root/'registration-mail').glob('*.json'))
    assert register(client, 'NEW.PERSON@example.com', code).status_code == 200
    assert register(client, 'new.person@example.com', code).status_code == 400
    login = client.post('/api/auth/login', json={'username': 'New.Person@Example.com', 'password': 'Personal-mail-password-2026!'})
    assert login.status_code == 200, login.text
    assert login.json()['role'] == 'user' and login.json()['must_change'] is False
    assert login.json()['username'] == 'new.person@example.com'
    client.headers['x-csrf-token'] = login.json()['csrf']
    assert client.get('/api/dashboard').json()['documents'] == 0
    assert client.get('/api/admin/users').status_code == 403
    assert client.get('/api/admin/registration-mail').status_code == 403
    assert clients['admin'].get('/api/auth/me').json()['id'] == accounts['admin']
    with store.session() as s:
        user = s.scalar(select(User).where(User.email == 'new.person@example.com'))
        assert user.password_hash.startswith('$argon2id$')
        assert user.username.startswith('u_')
        assert not s.get(EmailChallenge, user.email)
    # Existing addresses get the same non-enumerating response and cannot be re-registered.
    response = client.post('/api/auth/registration-code', json={'email': 'new.person@example.com'})
    assert response.status_code == 200 and '如邮箱可用于注册' in response.json()['message']


def test_public_endpoints_cross_site_disabled_and_validation_do_not_leak(store, clients):
    c = clients['alice']
    assert c.post('/api/auth/registration-code', json={'email': 'x@example.com'}).status_code == 403
    enable(store)
    assert c.post('/api/auth/registration-code', json={'email': 'x@example.com'}, headers={'origin': 'https://evil.example'}).status_code == 403
    for email in ('not-an-email', 'a@localhost', 'a@example.com\r\nBcc: x@example.com', 'a..b@example.com'):
        assert c.post('/api/auth/registration-code', json={'email': email}).status_code == 422
    r = c.post('/api/auth/register', json={'email': 'x@example.com', 'code': '123456', 'password': 'DO-NOT-ECHO'})
    assert r.status_code == 422 and 'DO-NOT-ECHO' not in r.text
    assert register(c, 'x@example.com', '123456').status_code == 400
    store.config = replace(store.config, mail_backend='disabled')
    assert c.post('/api/auth/registration-code', json={'email': 'x@example.com'}).status_code == 503


def test_expiry_wrong_attempts_replay_and_resend_invalidates_old_code(store, clients):
    enable(store); c = clients['alice']; email = 'expiry@example.com'
    code = get_code(store, c, email)
    assert c.post('/api/auth/registration-code', json={'email': email}).status_code == 429
    with store.session() as s:
        row = s.get(EmailChallenge, email)
        assert row.code_hash != code and code not in row.code_hash
        row.expires_at = now()-timedelta(seconds=1); row.sent_at = now()-timedelta(seconds=61)
    assert register(c, email, code).status_code == 400
    newer = get_code(store, c, email)
    # Deterministic different code avoids a 1-in-a-million test collision.
    with store.session() as s:
        row = s.get(EmailChallenge, email)
        from app.multiuser.registration import code_digest
        newer = '111111' if code != '111111' else '222222'
        row.code_hash = code_digest(store, email, row.id, newer)
    assert register(c, email, code).status_code == 400
    wrong = '000000' if newer != '000000' else '999999'
    for _ in range(4): assert register(c, email, wrong).status_code == 400
    assert register(c, email, newer).status_code == 400
    with store.session() as s: assert s.get(EmailChallenge, email).attempts == 5


def test_concurrent_confirmation_creates_one_account_and_one_private_space(store, clients):
    enable(store); c = clients['alice']; email = 'race@example.com'; code = get_code(store, c, email)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: register(c, email, code), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 400]
    with store.session() as s: assert len(list(s.scalars(select(User).where(User.email == email)))) == 1


def test_send_failure_invalidates_challenge_and_error_is_redacted(store, clients):
    enable(store)
    def failing_sender(*args): raise RuntimeError('SMTP-AUTH-PRIVATE-SECRET')
    store.registration_sender = failing_sender
    response = clients['alice'].post('/api/auth/registration-code', json={'email': 'failure@example.com'})
    assert response.status_code == 503 and 'SMTP-AUTH-PRIVATE-SECRET' not in response.text
    with store.session() as s:
        row = s.get(EmailChallenge, 'failure@example.com')
        assert row.delivered is False and row.expires_at <= now()
    assert register(clients['alice'], 'failure@example.com', '123456').status_code == 400


def test_local_preview_only_admin_and_never_in_real_mail_mode(store, clients):
    enable(store)
    code = get_code(store, clients['alice'], 'preview@example.com')
    assert clients['alice'].get('/api/admin/registration-mail').status_code == 403
    response = clients['admin'].get('/api/admin/registration-mail')
    assert response.status_code == 200 and response.json()[0]['code'] == code
    assert register(clients['alice'], 'preview@example.com', code).status_code == 200
    assert clients['admin'].get('/api/admin/registration-mail').json() == []
    store.config = replace(store.config, mail_backend='smtp')
    assert clients['admin'].get('/api/admin/registration-mail').status_code == 404
    with pytest.raises(ValueError): replace(store.config, mail_backend='local', hosts=('public.example.com',))
    with pytest.raises(ValueError): replace(store.config, mail_backend='local', secure_cookie=True)
    with pytest.raises(ValueError): replace(store.config, smtp_security='none')


def test_send_throttle_is_persistent_and_does_not_create_unbounded_codes(store, clients):
    enable(store); c = clients['alice']; email = 'limits@example.com'
    for _ in range(6):
        assert c.post('/api/auth/registration-code', json={'email': email}).status_code == 200
        with store.session() as s: s.get(EmailChallenge, email).sent_at = now()-timedelta(seconds=61)
    assert c.post('/api/auth/registration-code', json={'email': email}).status_code == 429
    assert len(list((store.config.data_root/'registration-mail').glob('*.json'))) == 6


@pytest.mark.parametrize('security', ['ssl', 'starttls'])
def test_smtp_requires_verified_tls_before_auth_and_uses_expected_message(store, monkeypatch, security):
    calls = []
    class SMTP:
        def __init__(self, host, port, timeout, context=None):
            calls.append(('connect', host, port, context))
            if security == 'ssl': assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): calls.append(('ehlo',))
        def starttls(self, context):
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            calls.append(('tls',))
        def login(self, username, password): calls.append(('auth', username, password))
        def send_message(self, message):
            assert message['To'] == 'receiver@example.com' and message['From'] == 'sender@example.com'
            assert '123456' in message.get_content() and 'smtp-only-secret' not in message.as_string()
            calls.append(('sent',))
    monkeypatch.setattr('app.multiuser.registration.smtplib.SMTP', SMTP)
    monkeypatch.setattr('app.multiuser.registration.smtplib.SMTP_SSL', SMTP)
    store.config = replace(store.config, mail_backend='smtp', smtp_host='smtp.example.com',
                           smtp_security=security, smtp_username='sender@example.com',
                           smtp_password='smtp-only-secret', mail_from='sender@example.com')
    send_mail(store, 'receiver@example.com', '123456', 'a'*32)
    assert [r[0] for r in calls][-2:] == ['auth', 'sent']
    if security == 'starttls': assert next(i for i,r in enumerate(calls) if r[0]=='tls') < next(i for i,r in enumerate(calls) if r[0]=='auth')


def test_migration_preserves_legacy_account_and_old_backup_remains_restorable(store, clients, accounts, tmp_path):
    archive = tmp_path/'current.zip'; backup(store, archive)
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read('manifest.json')); rows = json.loads(z.read('database.json'))
    assert manifest['schema'] == 'mu0003'
    # Construct a genuine previous-schema archive with matching manifest checksum.
    rows.pop('email_challenges')
    for user in rows['users']: user.pop('email')
    raw = json.dumps(rows).encode(); manifest['schema'] = 'mu0002'
    manifest['files']['database.json'] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    previous = tmp_path/'previous.zip'
    with zipfile.ZipFile(previous, 'w') as z:
        z.writestr('database.json', raw); z.writestr('manifest.json', json.dumps(manifest))
    restore_preflight(store, previous)
    restore(store, previous, hashlib.sha256(previous.read_bytes()).hexdigest())
    assert clients['admin'].get('/api/auth/me').json()['id'] == accounts['admin']
    with store.session() as s: assert s.get(User, accounts['admin']).email is None
