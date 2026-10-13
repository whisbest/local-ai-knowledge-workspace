"""Prepare a dedicated local PostgreSQL test service; credentials stay under .runtime."""
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    path = ROOT / '.runtime/multiuser-test.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        settings = json.loads(path.read_text(encoding='utf8'))
    else:
        password = secrets.token_urlsafe(32)
        settings = {'password': password,
                    'database_url': f'postgresql+psycopg://workspace_test:{password}@127.0.0.1:55433/workspace_test'}
        path.write_text(json.dumps(settings), encoding='utf8')
        path.chmod(0o600)
    docker = shutil.which('docker') or str(Path(os.environ['LOCALAPPDATA']) / 'Programs/DockerDesktop/resources/bin/docker.exe')
    env = dict(os.environ, MULTIUSER_TEST_DB_PASSWORD=settings['password'])
    result = subprocess.run([docker, 'compose', '-f', str(ROOT / 'compose.test.yaml'), 'up', '-d', '--wait',
                             '--wait-timeout', '90'], cwd=ROOT, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Dedicated test database could not start; check Docker health and port 55433')
    print('Dedicated PostgreSQL test database ready on 127.0.0.1:55433; no production data mounted.')


if __name__ == '__main__':
    prepare()
