from pathlib import Path
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    database_url: str
    data_root: Path
    master_key: str = ''
    hosts: tuple[str, ...] = ('localhost', '127.0.0.1', 'testserver')
    origins: tuple[str, ...] = ('http://localhost:18770', 'http://127.0.0.1:18770', 'http://testserver')
    secure_cookie: bool = False
    session_seconds: int = 86400
    upload_bytes: int = 64 * 1024 * 1024
    batch_files: int = 20
    storage_bytes: int = 1024 * 1024 * 1024
    documents: int = 100
    pending_jobs: int = 20
    global_pending: int = 100
    api_concurrency: int = 2
    local_concurrency: int = 1
    lease_seconds: int = 30
    parse_seconds: int = 30
    platform_calls: int = 1000
    platform_hosts: tuple[str, ...] = ('host.docker.internal', '127.0.0.1', 'ollama')
    trusted_proxies: tuple[str, ...] = ()
    registration_enabled: bool = False
    mail_backend: str = 'disabled'
    smtp_host: str = ''
    smtp_port: int = 465
    smtp_security: str = 'ssl'
    smtp_username: str = ''
    smtp_password: str = ''
    mail_from: str = ''

    def __post_init__(self):
        if not self.database_url.startswith('postgresql+psycopg://'):
            raise ValueError('V2 requires PostgreSQL with psycopg')
        root = Path(self.data_root).resolve()
        original = Path(__file__).resolve().parents[3] / 'data'
        if root == original.resolve() or original.resolve() in root.parents:
            raise ValueError('V2 cannot use the original single-user data directory')
        object.__setattr__(self, 'data_root', root)
        if self.mail_backend not in ('disabled', 'local', 'smtp'):
            raise ValueError('Mail backend must be disabled, local or smtp')
        if self.mail_backend == 'local' and (self.secure_cookie or any(
                host not in ('localhost', '127.0.0.1', 'testserver') for host in self.hosts)):
            raise ValueError('Local mail preview is restricted to local development')
        if self.smtp_security not in ('ssl', 'starttls'):
            raise ValueError('SMTP must use SSL or STARTTLS')

    @classmethod
    def from_env(cls):
        if not os.getenv('MULTIUSER_DATABASE_URL') or not os.getenv('MULTIUSER_DATA_ROOT'):
            raise ValueError('Configure MULTIUSER_DATABASE_URL and MULTIUSER_DATA_ROOT first')
        kwargs = {'database_url': os.environ['MULTIUSER_DATABASE_URL'],
                  'data_root': Path(os.environ['MULTIUSER_DATA_ROOT']),
                  'master_key': os.getenv('MULTIUSER_MASTER_KEY', ''),
                  'secure_cookie': os.getenv('MULTIUSER_SECURE_COOKIE', 'false').lower() == 'true'}
        kwargs['registration_enabled'] = os.getenv('MULTIUSER_REGISTRATION_ENABLED', 'false').lower() == 'true'
        for key in ('mail_backend', 'smtp_host', 'smtp_security', 'smtp_username', 'smtp_password', 'mail_from'):
            if os.getenv('MULTIUSER_' + key.upper()):
                kwargs[key] = os.environ['MULTIUSER_' + key.upper()]
        if os.getenv('MULTIUSER_SMTP_PORT'):
            kwargs['smtp_port'] = int(os.environ['MULTIUSER_SMTP_PORT'])
            if not 1 <= kwargs['smtp_port'] <= 65535:
                raise ValueError('Invalid SMTP port')
        for key, env in [('hosts', 'MULTIUSER_HOSTS'), ('origins', 'MULTIUSER_ORIGINS'),
                         ('platform_hosts', 'MULTIUSER_PLATFORM_HOSTS')]:
            if os.getenv(env):
                kwargs[key] = tuple(v.strip() for v in os.environ[env].split(',') if v.strip())
        if os.getenv('MULTIUSER_TRUSTED_PROXIES'):
            kwargs['trusted_proxies'] = tuple(os.environ['MULTIUSER_TRUSTED_PROXIES'].split(','))
        for key in ('session_seconds', 'upload_bytes', 'batch_files', 'storage_bytes', 'documents',
                    'pending_jobs', 'global_pending', 'api_concurrency', 'local_concurrency',
                    'lease_seconds', 'parse_seconds', 'platform_calls'):
            if os.getenv('MULTIUSER_' + key.upper()):
                value = int(os.environ['MULTIUSER_' + key.upper()])
                if value <= 0:
                    raise ValueError('Resource limits must be positive')
                kwargs[key] = value
        return cls(**kwargs)
