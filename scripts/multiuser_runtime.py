"""Shared Windows/Linux preparation/start/stop implementation."""
import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]


def docker_path():
    found=shutil.which('docker')
    if found:return found
    candidate=Path(os.environ.get('LOCALAPPDATA',''))/'Programs/DockerDesktop/resources/bin/docker.exe'
    if not candidate.is_file():raise RuntimeError('Docker is unavailable; install/start Docker first')
    return str(candidate)


def run(command,**kwargs):
    result=subprocess.run(command,cwd=ROOT,**kwargs)
    if result.returncode:raise RuntimeError('Project command failed; check Docker/project health')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','start','stop','admin'])
    parser.add_argument('--config',default=str(ROOT/'.runtime/multiuser.env'))
    parser.add_argument('--project',default='lkq-multiuser');parser.add_argument('--port',type=int,default=18770)
    parser.add_argument('--production',action='store_true');args=parser.parse_args()
    path=Path(args.config).resolve()
    if args.action=='prepare':
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            text='\n'.join(['MULTIUSER_DB_PASSWORD='+secrets.token_urlsafe(32),
                'MULTIUSER_MASTER_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                'MULTIUSER_PORT='+str(args.port),'MULTIUSER_HOSTS=localhost,127.0.0.1',
                f'MULTIUSER_ORIGINS=http://localhost:{args.port},http://127.0.0.1:{args.port}',
                'MULTIUSER_SECURE_COOKIE=false','MULTIUSER_PLATFORM_HOSTS=host.docker.internal,ollama',
                'MULTIUSER_REGISTRATION_ENABLED=true','MULTIUSER_MAIL_BACKEND=local'])+'\n'
            path.write_text(text,encoding='utf8');path.chmod(0o600)
        print('Private configuration prepared; existing secrets were preserved.')
    if not path.exists():raise RuntimeError('Private configuration missing; run prepare-multiuser first')
    values=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))
    if args.production and args.action in ('prepare','start'):
        domain=values.get('DOMAIN','')
        if not domain or values.get('MULTIUSER_SECURE_COOKIE','').lower()!='true' or domain not in values.get('MULTIUSER_HOSTS','').split(',') or values.get('MULTIUSER_ORIGINS')!='https://'+domain:
            raise RuntimeError('Production requires DOMAIN, matching HOSTS/https ORIGIN and SECURE_COOKIE=true; update private configuration first')
        if values.get('MULTIUSER_REGISTRATION_ENABLED', '').lower() == 'true' and (
                values.get('MULTIUSER_MAIL_BACKEND') != 'smtp' or any(not values.get(key) for key in
                ('MULTIUSER_SMTP_HOST', 'MULTIUSER_SMTP_USERNAME', 'MULTIUSER_SMTP_PASSWORD', 'MULTIUSER_MAIL_FROM'))):
            raise RuntimeError('Public email registration requires configured SMTP; local mail preview cannot run in production')
    command=[docker_path(),'compose','--env-file',str(path),'-p',args.project,'-f',str(ROOT/'compose.multiuser.yaml')]
    if args.production:command+=['-f',str(ROOT/'compose.prod.yaml')]
    if args.action=='prepare':
        run(command+['build'])
        run([docker_path(),'image','inspect','postgres:17-alpine'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        print('Images ready. Run start-multiuser, then initialize administrator.')
    elif args.action=='start':
        for image in ('postgres:17-alpine','local-knowledge-qa-api:v2','local-knowledge-qa-web:v2'):
            run([docker_path(),'image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if args.production:run([docker_path(),'image','inspect','caddy:2.10.2-alpine'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        run(command+['up','-d','--no-build','--pull','never','--wait','--wait-timeout','120'])
        if not args.production:
            url='http://127.0.0.1:'+values.get('MULTIUSER_PORT','18770')
            with httpx.Client(trust_env=False,timeout=15) as client:
                if client.get(url+'/api/health').status_code!=200 or client.get(url).status_code!=200:
                    raise RuntimeError('Services started but page/health unavailable')
            print('Workspace ready: '+url)
        else:print('Production containers started. Verify domain HTTPS and sign-in separately.')
    elif args.action=='stop':
        run(command+['stop','-t','240']);print('Only this project stopped. All persistent volumes retained.')
    else:
        # Interactive password prompt inside the container; no password in shell args/history.
        username=input('Initial administrator username: ').strip()
        code="from app.multiuser.config import Config; from app.multiuser.db import Store,User,Audit; from app.multiuser.auth import create_user; from sqlalchemy import select,text; import getpass; s=Store(Config.from_env()); p=getpass.getpass('Initial password: '); assert p==getpass.getpass('Confirm: ');\nwith s.session() as db:\n db.execute(text('SELECT pg_advisory_xact_lock(76241012)')); assert not db.scalar(select(User).where(User.role=='admin')), 'Administrator already exists'; create_user(db,"+repr(username)+",p,'admin');\nprint('Administrator created; change password on first login.')"
        run(command+['exec','api','python','-c',code])


if __name__=='__main__':main()
