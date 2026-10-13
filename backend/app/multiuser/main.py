import secrets
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from ..core import AppError
from .config import Config
from .db import Store
from .auth import authenticate, COOKIE


def create_app(config=None, store=None):
    store = store or Store(config or Config.from_env())
    config = store.config
    from .backups import recover_restore
    recover_restore(store)
    app = FastAPI(title='Local AI Knowledge Workspace', docs_url=None, redoc_url=None)
    app.state.store = store

    @app.middleware('http')
    async def access(request: Request, call_next):
        try:
            host = urlsplit('http://' + request.headers.get('host', '')).hostname
            if host not in config.hosts:
                raise AppError('HOST', '访问域名不受信任。', 400)
            forwarded = any(h in request.headers for h in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto'))
            if forwarded and (not request.client or request.client.host not in config.trusted_proxies):
                raise AppError('PROXY', '代理来源不受信任。', 400)
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                origin = request.headers.get('origin')
                if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin not in config.origins):
                    raise AppError('ORIGIN', '拒绝跨站操作。', 403)
            if request.url.path not in ('/api/health', '/api/auth/login', '/api/auth/options',
                                        '/api/auth/registration-code', '/api/auth/register'):
                user = authenticate(store, request.cookies.get(COOKIE))
                request.state.identity = user
                if request.url.path.startswith('/api/admin/') and user.role!='admin':
                    raise AppError('FORBIDDEN','此操作需要管理员权限。',403)
                if user.must_change and request.url.path not in ('/api/auth/me','/api/auth/logout','/api/auth/password'):
                    raise AppError('PASSWORD_CHANGE', '请先修改临时密码。', 403)
                if request.method not in ('GET', 'HEAD', 'OPTIONS') and not secrets.compare_digest(
                    request.headers.get('x-csrf-token', ''), user.csrf):
                    raise AppError('CSRF', '安全校验已失效，请刷新页面。', 403)
            response = await call_next(request)
        except AppError as error:
            response = JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.exception_handler(AppError)
    async def error(request, exc):
        return JSONResponse({'code': exc.code, 'message': exc.message, 'retryable': exc.retryable}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'code': 'INPUT', 'message': '输入格式不合法，请检查字段和长度。'}, status_code=422)

    @app.get('/api/health')
    def health():
        with store.session() as s: s.execute(text('SELECT 1'))
        return {'status': 'ok', 'version': 2}

    from . import auth
    auth.mount(app, store)
    from . import registration
    registration.mount(app, store)
    from . import documents
    documents.mount(app, store)
    from . import indexes
    indexes.mount(app, store)
    from . import chat
    chat.mount(app, store)
    from . import models
    models.mount(app, store)
    from . import admin
    admin.mount(app, store)
    from . import exports
    exports.mount(app, store)
    return app


def application():
    return create_app()
