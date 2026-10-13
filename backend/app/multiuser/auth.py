"""Server-side authentication. All business routes inherit the middleware guard."""
import hashlib
import re
import secrets
from datetime import timedelta
from dataclasses import dataclass
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from sqlalchemy import select, update, text, or_
from fastapi import Request
from pydantic import BaseModel, Field
from ..core import AppError
from .db import User, Session, Preferences, LoginLimit, Audit, Job, Record, now

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY = HASHER.hash(secrets.token_urlsafe(32))
COOKIE = 'workspace_session'


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def check_password(value):
    if not 12 <= len(value) <= 128:
        raise AppError('PASSWORD', '密码需为 12–128 个字符。', 422)


def create_user(s, username, password, role='user', must_change=True):
    username = username.strip().casefold()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,79}', username) or role not in ('user', 'admin'):
        raise AppError('ACCOUNT', '账号需为 3–80 位字母、数字或 ._-。', 422)
    check_password(password)
    if s.scalar(select(User).where(User.username == username)):
        raise AppError('ACCOUNT_EXISTS', '账号已存在，不能覆盖。', 409)
    user = User(username=username, password_hash=HASHER.hash(password), role=role, must_change=must_change)
    s.add(user); s.flush()
    s.add(Preferences(owner_id=user.id))
    return user


def public_user(user):
    return {'id': user.id, 'username': user.email or user.username, 'email': user.email, 'role': user.role,
            'enabled': user.enabled, 'must_change': user.must_change}


@dataclass(frozen=True)
class Identity:
    id: str
    username: str
    role: str
    must_change: bool
    csrf: str
    session_hash: str


def authenticate(store, cookie):
    if not cookie or len(cookie) > 200:
        raise AppError('AUTH', '请先登录，或重新登录已过期的会话。', 401)
    with store.session() as s:
        session = s.get(Session, token_hash(cookie))
        user = s.get(User, session.owner_id) if session else None
        if not session or session.revoked or session.expires_at <= now() or not user or not user.enabled:
            raise AppError('AUTH', '登录已失效，请重新登录。', 401)
        return Identity(user.id, user.username, user.role, user.must_change, session.csrf, session.token_hash)


def identity(request: Request):
    return request.state.identity


def administrator(request: Request):
    user = identity(request)
    if user.role != 'admin':
        raise AppError('FORBIDDEN', '此操作需要管理员权限。', 403)
    return user


def revoke(s, user_id):
    s.execute(update(Session).where(Session.owner_id == user_id).values(revoked=True))


def disable_user(s, user):
    user.enabled = False
    revoke(s, user.id)
    jobs=list(s.scalars(select(Job).where(Job.owner_id==user.id,Job.state.in_(['queued','running'])).order_by(Job.id).with_for_update()))
    for job in jobs:
        job.state='cancelled';job.stage='cancelled';job.lease_token=None;job.lease_until=None
        job.error='账号已停用，任务已取消。'
        target=s.get(Record,job.target)
        if target and target.kind=='generation':
            from .chat import event
            target.state='stopped'
            assistant=s.get(Record,target.payload['assistant_message_id'])
            if assistant:assistant.state='stopped'
            event(s,target,'done',{'state':'stopped'})
        elif target and target.kind=='document' and target.state!='deleted':target.state='interrupted'


def limit_login(s, ip, username):
    keys = [token_hash('ip:' + ip), token_hash('account:' + username)]
    for key in sorted(keys):
        lock = int(key[:15], 16)
        s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
        row = s.get(LoginLimit, key)
        if not row:
            row = LoginLimit(key=key, failures=0, window_at=now()); s.add(row)
        if row.window_at < now() - timedelta(minutes=10):
            row.failures = 0; row.window_at = now()
        if row.failures >= (50 if key == keys[0] else 10):
            return False
        row.failures += 1
    return True


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class PasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


def mount(app, store):
    @app.post('/api/auth/login')
    def login(body: LoginBody, request: Request):
        username = body.username.strip().casefold()
        # Commit limit updates even when credentials fail; do not roll them back with errors.
        with store.session() as s:
            permitted = limit_login(s, request.client.host if request.client else 'unknown', username)
        if not permitted:
            raise AppError('LOGIN_LIMIT', '登录尝试过于频繁，请十分钟后重试。', 429)
        with store.session() as s:
            user = s.scalar(select(User).where(or_(User.username == username, User.email == username)))
            try:
                valid = HASHER.verify(user.password_hash if user else DUMMY, body.password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                valid = False
            if not valid or not user or not user.enabled:
                s.add(Audit(event='login_failed'))
                result = None
            else:
                token = secrets.token_urlsafe(48); csrf = secrets.token_urlsafe(32)
                s.add(Session(token_hash=token_hash(token), owner_id=user.id, csrf=csrf,
                              expires_at=now()+timedelta(seconds=store.config.session_seconds)))
                s.add(Audit(actor_id=user.id, event='login'))
                result = {**public_user(user), 'csrf': csrf}
        if result is None:
            raise AppError('LOGIN', '账号或密码不正确，或账号不可用。', 401)
        from fastapi.responses import JSONResponse
        response = JSONResponse(result)
        response.set_cookie(COOKIE, token, max_age=store.config.session_seconds, httponly=True,
                            secure=store.config.secure_cookie, samesite='strict', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/auth/me')
    def me(request: Request):
        current = identity(request)
        with store.session() as s:
            return {**public_user(s.get(User, current.id)), 'csrf': current.csrf}

    @app.post('/api/auth/logout')
    def logout(request: Request):
        current = identity(request)
        with store.session() as s:
            s.get(Session, current.session_hash).revoked = True
            s.add(Audit(actor_id=current.id, event='logout'))
        from fastapi.responses import JSONResponse
        response = JSONResponse({'logged_out': True}); response.delete_cookie(COOKIE, path='/')
        return response

    @app.post('/api/auth/password')
    def password(body: PasswordBody, request: Request):
        current = identity(request)
        check_password(body.new_password)
        with store.session() as s:
            user = s.scalar(select(User).where(User.id == current.id).with_for_update())
            try: HASHER.verify(user.password_hash, body.current_password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                raise AppError('PASSWORD', '当前密码不正确。', 403)
            if body.new_password == body.current_password:
                raise AppError('PASSWORD', '新密码不能与当前密码相同。', 422)
            user.password_hash = HASHER.hash(body.new_password); user.must_change = False
            revoke(s, current.id)
            s.add(Audit(actor_id=current.id, event='password_changed'))
        return {'changed': True, 'login_required': True}

    @app.get('/api/admin/users')
    def users(request: Request):
        administrator(request)
        with store.session() as s:
            return [public_user(user) for user in s.scalars(select(User).order_by(User.created_at))]

    @app.post('/api/admin/users')
    def new_user(body: LoginBody, request: Request):
        actor = administrator(request)
        with store.session() as s:
            user = create_user(s, body.username, body.password)
            s.add(Audit(actor_id=actor.id, event='user_created', object_id=user.id))
            return public_user(user)

    @app.patch('/api/admin/users/{id}')
    def change_user(id: str, body: dict, request: Request):
        actor = administrator(request)
        if set(body) - {'enabled', 'password', 'quota'}:
            raise AppError('INPUT', '不支持的账号字段。', 422)
        with store.session() as s:
            user = s.scalar(select(User).where(User.id == id).with_for_update())
            if not user: raise AppError('NOT_FOUND', '账号不存在。', 404)
            if 'enabled' in body:
                if not isinstance(body['enabled'], bool): raise AppError('INPUT', '启用状态格式错误。', 422)
                if id == actor.id and not body['enabled']: raise AppError('ACCOUNT', '不能禁用当前管理员。', 409)
                if body['enabled']: user.enabled = True
                else: disable_user(s, user)
            if 'password' in body:
                check_password(body['password']); user.password_hash = HASHER.hash(body['password'])
                user.must_change = True; revoke(s, id)
            if 'quota' in body:
                allowed = {'storage_bytes', 'documents', 'pending_jobs', 'platform_calls'}
                if not isinstance(body['quota'], dict) or set(body['quota']) - allowed or any(
                    not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in body['quota'].values()):
                    raise AppError('INPUT', '额度需为非负整数。', 422)
                user.quota = body['quota']
            s.add(Audit(actor_id=actor.id, event='user_updated', object_id=id))
            return public_user(user)
