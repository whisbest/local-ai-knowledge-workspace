import base64
import hashlib
import json
import os
import secrets
import shutil
import ssl
import subprocess
import time
from pathlib import Path
from datetime import datetime,timedelta,timezone
import httpx
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

ROOT=Path(__file__).resolve().parents[2]


def docker():
    return shutil.which('docker') or str(Path(os.environ['LOCALAPPDATA'])/'Programs/DockerDesktop/resources/bin/docker.exe')


def command(args,input=None,timeout=240):
    result=subprocess.run(args,cwd=ROOT,input=input,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=timeout)
    assert result.returncode==0,'Deployment command failed: '+result.stderr[-2500:]
    return result.stdout


def configure(path,port,secure=False):
    scheme='https' if secure else 'http'
    path.write_text('\n'.join(['MULTIUSER_DB_PASSWORD='+secrets.token_urlsafe(32),
        'MULTIUSER_MASTER_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        f'MULTIUSER_PORT={port}','MULTIUSER_HOSTS=localhost,127.0.0.1',
        f'MULTIUSER_ORIGINS={scheme}://localhost:{port},{scheme}://127.0.0.1:{port}',
        'MULTIUSER_SECURE_COOKIE='+str(secure).lower(),'MULTIUSER_PLATFORM_HOSTS=host.docker.internal'])+'\n',encoding='utf8')


def bootstrap(compose):
    code="""import json,sys
from app.multiuser.config import Config
from app.multiuser.db import Store,User
from app.multiuser.auth import create_user
from sqlalchemy import select
values=json.load(sys.stdin)
with Store(Config.from_env()).session() as s:
 for name in ('operator','alice','bob'):
  if not s.scalar(select(User).where(User.username==name)):
   create_user(s,name,values[name],role='admin' if name=='operator' else 'user',must_change=False)
print('Test accounts initialized')
"""
    passwords={name:secrets.token_urlsafe(24) for name in ('operator','alice','bob')}
    command(compose+['exec','-T','api','python','-c',code],input=json.dumps(passwords))
    return passwords


def login(client,name,password):
    r=client.post('/api/auth/login',json={'username':name,'password':password})
    assert r.status_code==200,r.text
    client.headers['x-csrf-token']=r.json()['csrf'];return r


def wait_job(client,id,timeout=180):
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        jobs=client.get('/api/jobs').json();job=next(j for j in jobs if j['id']==id)
        if job['state'] in ('completed','failed','interrupted','cancelled'):
            assert job['state']=='completed',job
            return job
        time.sleep(.2)
    raise AssertionError('Persistent job did not reach terminal state in time')


def test_docker_scripts_real_ollama_private_flow_and_container_recreation(tmp_path):
    env=tmp_path/'deployment.env';configure(env,18774)
    project='lkq-mu-deploy-'+secrets.token_hex(4)
    compose=[docker(),'compose','--env-file',str(env),'-p',project,'-f',str(ROOT/'compose.multiuser.yaml')]
    def script(name):
        command(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/name),
                 '-ConfigFile',str(env),'-ComposeProject',project])
    try:
        script('start-multiuser.ps1');passwords=bootstrap(compose)
        with httpx.Client(base_url='http://127.0.0.1:18774',trust_env=False,timeout=180) as admin,httpx.Client(base_url='http://127.0.0.1:18774',trust_env=False,timeout=180) as a,httpx.Client(base_url='http://127.0.0.1:18774',trust_env=False,timeout=30) as b:
            login(admin,'operator',passwords['operator']);login(a,'alice',passwords['alice']);login(b,'bob',passwords['bob'])
            assert '<script' in a.get('/').text
            ids={}
            for purpose,model in [('chat','qwen2.5:3b'),('embedding','qwen3-embedding:0.6b')]:
                response=admin.post('/api/admin/models',json={'name':purpose,'purpose':purpose,'mode':'local','endpoint':'http://host.docker.internal:11434','model':model})
                assert response.status_code==200,response.text;ids[purpose]=response.json()['id']
                assert admin.post('/api/admin/models/'+ids[purpose]+'/test').status_code==200
            kb=a.post('/api/knowledge-bases',json={'name':'Container private','embedding_id':ids['embedding']}).json()['id']
            fixture=ROOT/'tests/fixtures/two-pages.pdf'
            response=a.post('/api/documents/upload',data={'kb_id':kb,'request_key':'docker-upload'},files={'files':('two-pages.pdf',fixture.read_bytes())})
            assert response.status_code==200,response.text;doc=response.json()[0]['document_id']
            job=a.get('/api/jobs').json()[0]['id'];wait_job(a,job)
            assert b.get('/api/documents/'+doc+'/file').status_code==404
            assert a.get('/api/documents/'+doc+'/file').content==fixture.read_bytes()
            conv=a.post('/api/conversations',json={}).json()['id']
            r=a.post('/api/conversations/'+conv+'/generations',json={'question':'请总结第一份资料中的规定并引用来源。','model_id':ids['chat'],'request_key':'docker-answer','scope':{'type':'documents','ids':[doc]}})
            assert r.status_code==200,r.text;generation=r.json()['id']
            job=next(j['id'] for j in a.get('/api/jobs').json() if j['target']==generation);wait_job(a,job)
            assert a.get('/api/generations/'+generation+'/sources').json()
            assert 'event: done' in a.get('/api/generations/'+generation+'/events?stream=false').text
            assert b.get('/api/generations/'+generation+'/events?stream=false').status_code==404
            export=a.post('/api/exports').json()['id'];assert a.get('/api/exports/'+export+'/file').status_code==200
            before=a.get('/api/conversations/'+conv+'/messages').json()
        script('stop-multiuser.ps1')
        command(compose+['up','-d','--no-build','--pull','never','--wait','db'])
        # Run the operator commands inside the actual Linux image and persistent volumes.
        backup_path='/workspace-data/platform-backups/deployment.zip'
        command(compose+['run','--rm','--no-deps','api','python','-m','app.multiuser.operator_entry','backup','--destination',backup_path])
        preflight=command(compose+['run','--rm','--no-deps','api','python','-m','app.multiuser.operator_entry','restore','--archive',backup_path])
        checksum=preflight.strip().split('Confirm SHA256: ')[-1]
        assert len(checksum)==64
        command(compose+['run','--rm','--no-deps','api','python','-m','app.multiuser.operator_entry','restore','--archive',backup_path,'--confirm-sha256',checksum])
        command(compose+['up','-d','--no-build','--pull','never','--force-recreate','--wait','--wait-timeout','120'])
        with httpx.Client(base_url='http://127.0.0.1:18774',trust_env=False,timeout=30) as a:
            login(a,'alice',passwords['alice'])
            assert a.get('/api/documents/'+doc+'/file').content==fixture.read_bytes()
            assert a.get('/api/conversations/'+conv+'/messages').json()==before
            assert a.get('/api/exports/'+export+'/file').status_code==200
    finally:
        command(compose+['down'])  # no -v; the test's private persistent volumes are retained


def certificates(directory):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'LKQ integration test CA')])
    now=datetime.now(timezone.utc)
    ca=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(key,hashes.SHA256()))
    server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]))
        .issuer_name(subject).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(__import__('ipaddress').ip_address('127.0.0.1'))]),critical=False)
        .sign(key,hashes.SHA256()))
    (directory/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory/'server.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (directory/'server.key').write_bytes(server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))


def test_real_https_proxy_secure_cookie_host_origin_and_tls_verification(tmp_path):
    certificates(tmp_path);env=tmp_path/'https.env';configure(env,18776,True)
    env.write_text(env.read_text().replace('https://localhost:18776','https://localhost:18775').replace('https://127.0.0.1:18776','https://127.0.0.1:18775'))
    nginx=tmp_path/'tls.conf'
    nginx.write_text('''server {
listen 443 ssl; ssl_certificate /tls/server.pem; ssl_certificate_key /tls/server.key;
location / {proxy_pass http://web:80; proxy_set_header Host $http_host; proxy_buffering off; proxy_read_timeout 600s;}
}''')
    override=tmp_path/'tls-compose.json'
    override.write_text(json.dumps({'services':{'tls':{'image':'nginx:alpine','ports':['127.0.0.1:18775:443'],
        'volumes':[str(tmp_path).replace('\\','/')+':/tls:ro',str(nginx).replace('\\','/')+':/etc/nginx/conf.d/default.conf:ro'],'depends_on':['web']},
        'web':{'ports':['127.0.0.1:18776:80']}}}))
    project='lkq-mu-tls-'+secrets.token_hex(4)
    compose=[docker(),'compose','--env-file',str(env),'-p',project,'-f',str(ROOT/'compose.multiuser.yaml'),'-f',str(override)]
    try:
        command(compose+['up','-d','--no-build','--pull','never','--wait','--wait-timeout','120'])
        passwords=bootstrap(compose);context=ssl.create_default_context(cafile=str(tmp_path/'ca.pem'))
        with httpx.Client(base_url='https://localhost:18775',trust_env=False,verify=context,timeout=30) as client:
            assert client.get('/api/health').status_code==200
            r=login(client,'alice',passwords['alice']);assert 'Secure' in r.headers['set-cookie']
            assert client.get('/api/auth/me').status_code==200
            assert client.post('/api/knowledge-bases',json={'name':'TLS verified'},headers={'origin':'https://localhost:18775'}).status_code==200
            assert client.post('/api/knowledge-bases',json={'name':'blocked'},headers={'origin':'https://evil.example'}).status_code==403
            assert client.get('/api/auth/me',headers={'host':'evil.example'}).status_code==400
        with pytest.raises(httpx.ConnectError):
            httpx.get('https://localhost:18775/api/health',trust_env=False,timeout=5)
    finally:command(compose+['down'])


def test_linux_shell_start_stop_preserve_data_and_configuration(tmp_path):
    # Actual Bash/Python/Docker Compose on Linux, controlling only a random test project.
    image='lkq-linux-script-test:v2'
    command([docker(),'build','-f',str(ROOT/'tests/multiuser/Dockerfile.linux-scripts'),'-t',image,str(ROOT)])
    directory=ROOT/'.runtime'/('linux-script-'+secrets.token_hex(4));directory.mkdir()
    config=directory/'private.env';configure(config,18777)
    project='lkq-mu-linux-'+secrets.token_hex(4)
    compose=[docker(),'compose','--env-file',str(config),'-p',project,'-f',str(ROOT/'compose.multiuser.yaml')]
    original=hashlib.sha256(config.read_bytes()).hexdigest()
    payload='''set -euo pipefail
mkdir -p .venv/bin
ln -s /usr/local/bin/python .venv/bin/python
bash start-multiuser.sh --config private.env --project "$PROJECT"
python - <<'PY'
import httpx
with httpx.Client(trust_env=False) as c:
            assert c.get('http://127.0.0.1:18777/api/health').status_code==200
PY
bash stop-multiuser.sh --config private.env --project "$PROJECT"
bash start-multiuser.sh --config private.env --project "$PROJECT"
bash stop-multiuser.sh --config private.env --project "$PROJECT"
'''
    try:
        # Copy only public scripts/config to the temporary helper; no private user data is mounted.
        helper='lkq-linux-helper-'+secrets.token_hex(4)
        command([docker(),'create','--name',helper,'--network','host',
                 '-v','/var/run/docker.sock:/var/run/docker.sock','-e','PROJECT='+project,image,'bash','-c',payload])
        for file in ('start-multiuser.sh','stop-multiuser.sh','compose.multiuser.yaml'):
            normalized=directory/file;normalized.write_bytes((ROOT/file).read_bytes().replace(b'\r\n',b'\n'))
            command([docker(),'cp',str(normalized),helper+':/workspace/'+file])
        command([docker(),'cp',str(config),helper+':/workspace/private.env'])
        (directory/'scripts').mkdir();runtime=directory/'scripts/multiuser_runtime.py'
        runtime.write_bytes((ROOT/'scripts/multiuser_runtime.py').read_bytes())
        command([docker(),'cp',str(directory/'scripts'),helper+':/workspace/scripts'])
        command([docker(),'start','-a',helper],timeout=300)
        assert hashlib.sha256(config.read_bytes()).hexdigest()==original
        volumes=command(compose+['config','--volumes'])
        assert 'multiuser_pg' in volumes and 'multiuser_files' in volumes
        for name in ('multiuser_pg','multiuser_files'):
            command([docker(),'volume','inspect',project+'_'+name])
    finally:
        command(compose+['down'])
        if 'helper' in locals():command([docker(),'rm','-f',helper])
