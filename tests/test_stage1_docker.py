import json
import os
import shutil
from pathlib import Path
import subprocess
import httpx

ROOT=Path(__file__).resolve().parents[1]
DOCKER=Path(shutil.which('docker') or str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs/DockerDesktop/resources/bin/docker.exe'))

def test_real_compose_data_survives_container_recreation(tmp_path):
    env=dict(os.environ,DATA_PATH=str(tmp_path).replace('\\','/'),APP_PORT='18766')
    env['PATH']=str(DOCKER.parent)+os.pathsep+env['PATH']
    def command(*args):
        result=subprocess.run([str(DOCKER),'compose','-p','lkq-stage1','-f',str(ROOT/'compose.yaml'),*args],env=env,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=240)
        assert result.returncode==0,result.stderr
    try:
        command('up','-d','--no-build','--wait','--wait-timeout','90')
        with httpx.Client(base_url='http://127.0.0.1:18766',trust_env=False,timeout=20) as c:
            assert c.get('/').status_code==200
            r=c.post('/api/knowledge-bases',json={'name':'容器重建保留'});assert r.status_code==200,r.text
            id=r.json()['id']
            assert c.put('/api/settings',json={'answer_length':'long'}).status_code==200
        command('down')
        command('up','-d','--no-build','--wait','--wait-timeout','90')
        with httpx.Client(base_url='http://127.0.0.1:18766',trust_env=False,timeout=20) as c:
            assert c.get('/api/knowledge-bases').json()[0]['id']==id
            assert c.get('/api/settings').json()['answer_length']=='long'
    finally:command('down')
