"""Stage 0 gates: real Docker, Ollama and persistent SQLite, no mock/skip."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import time
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKER = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/DockerDesktop/resources/bin/docker.exe'
if not DOCKER.exists():
    DOCKER = Path(shutil.which('docker') or 'docker')
IMAGE = 'python:3.11-slim'


def docker(*args, timeout=240):
    env = dict(os.environ)
    env['PATH'] = str(DOCKER.parent) + os.pathsep + env['PATH']
    result = subprocess.run([str(DOCKER), *args], capture_output=True, text=True, encoding='utf-8', timeout=timeout, env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def container_python(code, *options, timeout=240):
    return docker('run', '--rm', '--pull=never', '--memory=256m', *options, IMAGE, 'python', '-c', code, timeout=timeout)


def test_docker_server_and_model_inventory():
    info = json.loads(docker('version', '--format', '{{json .Server}}'))
    assert info['Os'] == 'linux'
    with httpx.Client(trust_env=False, timeout=10) as client:
        result = client.get('http://127.0.0.1:11434/api/tags')
        result.raise_for_status()
    names = {m['name'] for m in result.json()['models']}
    assert 'qwen2.5:3b' in names
    assert 'qwen3-embedding:0.6b' in names


def test_container_can_access_loopback_ollama_and_generate():
    code = """import json,time,urllib.request
base='http://host.docker.internal:11434'
tags=json.load(urllib.request.urlopen(base+'/api/tags',timeout=20))
assert tags['models']
start=time.monotonic()
body={'model':'qwen2.5:3b','prompt':'请只回答：测试成功','stream':False,'keep_alive':0,'options':{'num_ctx':1024,'num_predict':32,'num_gpu':0}}
req=urllib.request.Request(base+'/api/generate',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
result=json.load(urllib.request.urlopen(req,timeout=240))
assert result.get('done') and result.get('response','').strip(),result
print(json.dumps({'response':result['response'],'seconds':time.monotonic()-start,'total_duration':result.get('total_duration')},ensure_ascii=False))
"""
    output = container_python(code, timeout=300)
    (ROOT / '.reports/generation-environment.json').write_text(output, encoding='utf-8')


def test_container_embeddings():
    code = """import json,time,urllib.request
start=time.monotonic()
body={'model':'qwen3-embedding:0.6b','input':['个人知识库保存学习笔记。','文档在本机保存。'],'keep_alive':0}
req=urllib.request.Request('http://host.docker.internal:11434/api/embed',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
result=json.load(urllib.request.urlopen(req,timeout=240))
vec=result.get('embeddings',[])
assert len(vec)==2 and len(vec[0])>0 and len(vec[0])==len(vec[1]),result
assert any(x!=0 for x in vec[0])
print(json.dumps({'dimension':len(vec[0]),'seconds':time.monotonic()-start}))
"""
    output = container_python(code, timeout=300)
    (ROOT / '.reports/embedding-environment.json').write_text(output, encoding='utf-8')


def test_sqlite_wal_transaction_rollback_and_container_recreation(tmp_path):
    volume = f'{tmp_path.as_posix()}:/probe'
    write = """import sqlite3
c=sqlite3.connect('/probe/state.sqlite'); c.execute('pragma journal_mode=WAL'); c.execute('pragma foreign_keys=ON')
c.execute('create table records(id integer primary key,value text)'); c.execute('insert into records values(1,?)',('persistent',)); c.commit()
c.execute('insert into records values(2,?)',('rolled-back',)); c.rollback(); c.close()
"""
    container_python(write, '--network=none', '-v', volume)
    check = """import sqlite3
c=sqlite3.connect('/probe/state.sqlite'); assert c.execute('select * from records').fetchall()==[(1,'persistent')]
assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
c.execute('begin immediate'); c2=sqlite3.connect('/probe/state.sqlite',timeout=0.2)
try:
 c2.execute('insert into records values(2,?)',('blocked',)); raise AssertionError('concurrent writer was not locked')
except sqlite3.OperationalError as e: assert 'locked' in str(e)
finally: c.rollback(); c.close(); c2.close()
"""
    container_python(check, '--network=none', '-v', volume)


def test_local_models_without_external_network(monkeypatch):
    """Block all non-loopback connections for this model client; no global network changes."""
    import socket
    original = socket.socket.connect
    denied = []
    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
            denied.append(address)
            raise AssertionError(f'external network attempted: {address}')
        return original(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_only)
    with httpx.Client(trust_env=False, timeout=240) as client:
        generated = client.post('http://127.0.0.1:11434/api/generate', json={'model':'qwen2.5:3b','prompt':'回答：你好','stream':False,'keep_alive':0,'options':{'num_ctx':1024,'num_predict':16,'num_gpu':0}})
        generated.raise_for_status()
        assert generated.json()['response'].strip()
        embedded = client.post('http://127.0.0.1:11434/api/embed', json={'model':'qwen3-embedding:0.6b','input':'离线知识库','keep_alive':0})
        embedded.raise_for_status()
        assert embedded.json()['embeddings'][0]
    assert not denied
