"""Email ownership verification before creating a private, ordinary account."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage

from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from ..core import AppError
from .auth import create_user, administrator
from .db import Audit, EmailChallenge, LoginLimit, User, now, uid


def normalize_email(value):
    value = value.strip().casefold()
    # Accept ordinary ASCII email addresses; reject display names, headers and controls.
    if len(value) > 254 or not re.fullmatch(
            r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
        raise AppError('EMAIL', '请输入有效邮箱地址，例如 name@qq.com。', 422)
    if len(value.split('@')[0]) > 64:
        raise AppError('EMAIL', '邮箱地址过长。', 422)
    return value


def mail_ready(config):
    return config.mail_backend == 'local' or (config.mail_backend == 'smtp' and
            bool(config.smtp_host and config.smtp_username and config.smtp_password and config.mail_from))


def require_registration(store):
    if not store.config.registration_enabled:
        raise AppError('REGISTRATION_CLOSED', '暂未开放自主注册，请联系管理员。', 403)
    if not mail_ready(store.config):
        raise AppError('MAIL_UNAVAILABLE', '注册邮件服务尚未配置，请联系管理员。', 503)


def code_digest(store, email, challenge_id, code):
    try:
        key = base64.urlsafe_b64decode(store.config.master_key.encode())
        if len(key) != 32:
            raise ValueError()
    except Exception:
        raise AppError('MAIL_UNAVAILABLE', '注册安全配置尚未就绪，请联系管理员。', 503) from None
    return hmac.new(key, f'registration:{email}:{challenge_id}:{code}'.encode(), hashlib.sha256).hexdigest()


def email_lock(s, email):
    value = int(hashlib.sha256(('registration-email:' + email).encode()).hexdigest()[:15], 16)
    s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': value})


def throttle(store, ip, email, action):
    # A separate committed transaction preserves counters on rejected requests.
    keys = [(hashlib.sha256(f'registration:{action}:ip:{ip}'.encode()).hexdigest(), 30),
            (hashlib.sha256(f'registration:{action}:email:{email}'.encode()).hexdigest(),
             6 if action == 'send' else 20)]
    permitted = True
    with store.session() as s:
        for key, limit in sorted(keys):
            s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(key[:15], 16)})
            row = s.get(LoginLimit, key)
            if not row:
                row = LoginLimit(key=key, failures=0, window_at=now()); s.add(row)
            if row.window_at < now() - timedelta(hours=1):
                row.failures = 0; row.window_at = now()
            if row.failures >= limit:
                permitted = False
            else:
                row.failures += 1
    if not permitted:
        raise AppError('REGISTRATION_LIMIT', '注册操作过于频繁，请稍后再试。', 429)


def send_mail(store, email, code, challenge_id):
    if getattr(store, 'registration_sender', None):
        store.registration_sender(email, code, challenge_id)
        return
    config = store.config
    if config.mail_backend == 'local':
        directory = config.data_root / 'registration-mail'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise AppError('MAIL_UNAVAILABLE', '本地验证邮件目录不可用。', 503)
        path = directory / (challenge_id + '.json')
        with path.open('x', encoding='utf8') as f:
            json.dump({'email': email, 'code': code, 'created_at': now().isoformat(),
                       'expires_at': (now()+timedelta(minutes=10)).isoformat()}, f)
        path.chmod(0o600)
        # This private development mailbox never enters personal exports or platform backup.
        for old in directory.glob('*.json'):
            if old.stat().st_mtime < now().timestamp()-600:
                old.unlink(missing_ok=True)
        return
    message = EmailMessage()
    message['Subject'] = 'Local AI Knowledge Workspace 注册验证码'
    message['From'] = normalize_email(config.mail_from)
    message['To'] = email
    message.set_content(f'你的注册验证码是：{code}\n\n10分钟内有效。若不是你本人操作，请忽略此邮件。')
    context = ssl.create_default_context()
    if config.smtp_security == 'ssl':
        connection = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=15, context=context)
    else:
        connection = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15)
    with connection as smtp:
        if config.smtp_security == 'starttls':
            smtp.ehlo(); smtp.starttls(context=context); smtp.ehlo()
        smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


class EmailBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class RegisterBody(EmailBody):
    code: str = Field(pattern=r'^\d{6}$')
    password: str = Field(min_length=12, max_length=128)


def mount(app, store):
    @app.get('/api/auth/options')
    def options():
        return {'registration_enabled': store.config.registration_enabled,
                'mail_ready': mail_ready(store.config),
                'local_mail_preview': store.config.registration_enabled and store.config.mail_backend == 'local'}

    @app.post('/api/auth/registration-code')
    def request_code(body: EmailBody, request: Request):
        require_registration(store)
        email = normalize_email(body.email)
        throttle(store, request.client.host if request.client else 'unknown', email, 'send')
        code = f'{secrets.randbelow(1000000):06d}'; challenge_id = uid()
        with store.session() as s:
            email_lock(s, email)
            existing = s.get(EmailChallenge, email)
            if existing and existing.sent_at > now()-timedelta(seconds=60):
                raise AppError('CODE_COOLDOWN', '请等待60秒后再发送验证码。', 429)
            # Same response for an existing account; do not expose account existence.
            if s.scalar(select(User.id).where(User.email == email)):
                return {'message': '如邮箱可用于注册，验证码将在稍后送达。', 'retry_after': 60}
            if existing:
                s.delete(existing); s.flush()
            s.add(EmailChallenge(email=email, id=challenge_id, code_hash=code_digest(store, email, challenge_id, code),
                                 expires_at=now()+timedelta(minutes=10)))
        try:
            send_mail(store, email, code, challenge_id)
        except Exception:
            with store.session() as s:
                email_lock(s, email)
                row = s.get(EmailChallenge, email)
                if row and row.id == challenge_id:
                    row.delivered = False; row.expires_at = now()
            raise AppError('MAIL_SEND', '验证码发送失败，请稍后重试或联系管理员。', 503) from None
        with store.session() as s:
            email_lock(s, email)
            row = s.get(EmailChallenge, email)
            if row and row.id == challenge_id:
                row.delivered = True
        return {'message': '如邮箱可用于注册，验证码将在稍后送达。', 'retry_after': 60}

    @app.post('/api/auth/register')
    def register(body: RegisterBody, request: Request):
        require_registration(store)
        email = normalize_email(body.email)
        throttle(store, request.client.host if request.client else 'unknown', email, 'verify')
        with store.session() as s:
            email_lock(s, email)
            row = s.get(EmailChallenge, email)
            valid = bool(row and row.delivered and row.expires_at > now() and row.attempts < 5)
            if valid:
                row.attempts += 1
                valid = hmac.compare_digest(row.code_hash, code_digest(store, email, row.id, body.code))
            if not valid or s.scalar(select(User.id).where(User.email == email)):
                result = None
            else:
                user = create_user(s, 'u_' + uid(), body.password, must_change=False)
                user.email = email
                s.delete(row)
                s.add(Audit(actor_id=user.id, event='email_registered', object_id=user.id))
                result = {'registered': True, 'email': email, 'login_required': True}
        if result is None:
            raise AppError('REGISTRATION_CODE', '验证码不正确、已过期或已使用，请检查后重新获取。', 400)
        return result

    @app.get('/api/admin/registration-mail')
    def local_mail(request: Request):
        administrator(request)
        if store.config.mail_backend != 'local':
            raise AppError('NOT_FOUND', '本地邮件预览未开放。', 404)
        directory = store.config.data_root / 'registration-mail'
        files = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        result = []
        with store.session() as s:
            for path in files:
                if path.is_symlink() or path.stat().st_mtime <= now().timestamp()-600:
                    continue
                message = json.loads(path.read_text(encoding='utf8'))
                challenge = s.get(EmailChallenge, message['email'])
                if (challenge and challenge.id == path.stem and challenge.delivered
                        and challenge.expires_at > now() and challenge.attempts < 5):
                    result.append(message)
        return result
