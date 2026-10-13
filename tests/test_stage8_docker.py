import os,sys,json,time,subprocess
import shutil
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
DOCKER=Path(shutil.which('docker') or str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs/DockerDesktop/resources/bin/docker.exe'))

def test_scripts_container_persistence_real_offline_import_and_qa(tmp_path):
    env=dict(os.environ,DATA_PATH=str(tmp_path).replace('\\','/'),APP_PORT='18769');env['PATH']=str(DOCKER.parent)+os.pathsep+env['PATH']
    def command(args,timeout=240):
        r=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=timeout);assert r.returncode==0,r.stdout+r.stderr;return r.stdout
    def start():command(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'start.ps1'),'-NoBrowser','-ComposeProject','lkq-final-test'])
    def stop():command(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'stop.ps1'),'-ComposeProject','lkq-final-test'])
    def wait(c,id):
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            g=c.get('/api/generations/'+id).json()
            if g['state'] in ('completed','failed','stopped','interrupted'):return g
            time.sleep(.2)
        raise AssertionError('Generation timed out')
    try:
        start()
        # Enforce egress denial inside the api container while keeping loopback and host Ollama.
        container=command([str(DOCKER),'compose','-p','lkq-final-test','ps','-q','api']).strip()
        host_ip=command([str(DOCKER),'compose','-p','lkq-final-test','exec','-T','api','python','-c',"import socket;print(socket.gethostbyname('host.docker.internal'))"]).strip()
        command([str(DOCKER),'run','--rm','--pull','never','--network','container:'+container,'--cap-add','NET_ADMIN','--entrypoint','sh','lkq-offline-guard:v1','-c',f'iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT && iptables -A OUTPUT -d 127.0.0.0/8 -j ACCEPT && iptables -A OUTPUT -d {host_ip} -p tcp --dport 11434 -j ACCEPT && iptables -A OUTPUT -p udp --dport 53 -j ACCEPT && iptables -A OUTPUT -j REJECT'])
        # The helper above enforces the actual running process. This extra client guard verifies startup.
        script="""import socket
from app.main import create_app
from fastapi.testclient import TestClient
original=socket.socket.connect
calls=[]
def guarded(self,address):
    host=address[0]
    if not(host.startswith(('127.','172.','192.168.')) or host=='::1'): raise AssertionError('External network attempted: '+host)
    calls.append(host);return original(self,address)
socket.socket.connect=guarded
with TestClient(create_app('/data')) as c:
    assert c.get('/api/health').status_code==200
print('offline guard ready')
"""
        command([str(DOCKER),'compose','-p','lkq-final-test','exec','-T','api','python','-c',script])
        denied=command([str(DOCKER),'compose','-p','lkq-final-test','exec','-T','api','python','-c',"import socket\ns=socket.socket();s.settimeout(2)\ntry:s.connect(('1.1.1.1',443));raise AssertionError('External egress allowed')\nexcept OSError:print('external egress denied')"])
        assert 'denied' in denied
        with httpx.Client(base_url='http://127.0.0.1:18769',trust_env=False,timeout=180) as c:
            for purpose,model in [('embedding','qwen3-embedding:0.6b'),('chat','qwen2.5:3b')]:
                cfg=c.post('/api/model-configs',json={'purpose':purpose,'mode':'local','endpoint':'http://host.docker.internal:11434','model':model}).json()['id'];r=c.post('/api/model-configs/'+cfg+'/test');assert r.status_code==200,r.text;assert c.put('/api/settings',json={'default_'+purpose:cfg}).status_code==200
            kb=c.post('/api/knowledge-bases',json={'name':'容器真实离线'}).json()['id'];start_import=time.perf_counter();uploaded=c.post('/api/documents/upload',data={'kb_id':kb,'request_key':'docker-real'},files=[('files',(name,(ROOT/'tests/fixtures'/name).read_bytes())) for name in ['policy-a.txt','two-pages.pdf','notes.docx']]).json();doc=uploaded[0]['document_id']
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                if c.get('/api/dashboard').json()['ready']==3:break
                time.sleep(.2)
            assert c.get('/api/dashboard').json()['ready']==3,c.get('/api/jobs').text
            conv=c.post('/api/conversations',json={}).json()['id'];start_qa=time.perf_counter();g=c.post('/api/conversations/'+conv+'/generations',json={'question':'青竹项目的退款期限是多少天？','scope':{'type':'documents','ids':[doc]},'request_key':'docker-answer'}).json()['id'];first_delta=None;generation=None
            peak_available=__import__('psutil').virtual_memory().available
            deadline=time.monotonic()+150
            while time.monotonic()<deadline:
                history=c.get('/api/conversations/'+conv+'/messages').json();generation=history['generations'][-1]
                if history['messages'][-1]['content'] and first_delta is None:first_delta=time.perf_counter()-start_qa
                peak_available=min(peak_available,__import__('psutil').virtual_memory().available)
                if generation['state'] in ('completed','failed','stopped','interrupted'):break
                time.sleep(.1)
            assert generation['state']=='completed',generation
            answer=c.get('/api/conversations/'+conv+'/messages').json()['messages'][-1]['content'];assert ('七' in answer or '7' in answer) and '[1]' in answer
            generation=c.get('/api/generations/'+g).json();source=c.get('/api/generations/'+g+'/sources').json();prefs=c.get('/api/settings').json();vectors=c.get('/api/indexes/impact').json()['vectors']
            assert c.post('/api/backups').status_code==200
            report={'import_seconds':start_qa-start_import,'first_output_seconds':first_delta,'qa_seconds':time.perf_counter()-start_qa,'minimum_available_ram_mb':peak_available/1024**2,'answer':answer,'offline':'api container OUTPUT external connections rejected; local Ollama + DNS allowed'}
        stop();start()
        with httpx.Client(base_url='http://127.0.0.1:18769',trust_env=False,timeout=20) as c:
            assert c.get('/api/documents/'+doc+'/file').content==(ROOT/'tests/fixtures/policy-a.txt').read_bytes();assert c.get('/api/indexes/impact').json()['vectors']==vectors
            assert c.get('/api/generations/'+g).json()==generation;assert c.get('/api/generations/'+g+'/sources').json()==source;assert c.get('/api/settings').json()==prefs
        report['docker_stats']=command([str(DOCKER),'stats','--no-stream','--format','{{.Name}} {{.MemUsage}}'])
        (ROOT/'.reports/stage8-docker-real.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    finally:stop()
