"""30-minute mixed operations with three users, private PG and multiple workers."""
import argparse
import base64
import hashlib
import json
import secrets
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx
import psutil
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text,select,func
from sqlalchemy.orm import sessionmaker
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from app.multiuser.config import Config
from app.multiuser.db import Store,Model,Job,Reservation,uid
from app.multiuser.main import create_app
from app.multiuser.auth import create_user
from app.multiuser.secrets import Vault
from app.multiuser.worker import Worker


def write_report(path, report):
    # PostgreSQL SUM(bigint) is numeric/Decimal, even for SQLAlchemy BigInteger.
    # Normalize only integral count/size fields; never hide arbitrary JSON errors.
    report = dict(report)
    for field in ('stuck_tasks', 'max_attempt', 'model_calls', 'committed_calls',
                  'peak_original_file_bytes', 'database_bytes', 'worker_restarts'):
        value = report[field]
        if value != int(value):
            raise ValueError('Non-integral stability statistic: ' + field)
        report[field] = int(value)
    encoded = json.dumps(report, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding='utf8')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--duration-seconds',type=int,default=1800);args=parser.parse_args()
    if args.duration_seconds<1800:raise SystemExit('Acceptance stability duration must be at least 1800 seconds')
    values=json.loads((ROOT/'.runtime/multiuser-test.json').read_text(encoding='utf8'));url=values['database_url'];schema='stability_'+uid()
    admin=create_engine(url,hide_parameters=True)
    with admin.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
    temp=tempfile.TemporaryDirectory(prefix='lkq-stability-');store=Store(Config(url,Path(temp.name),master_key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),platform_calls=100000))
    store.engine.dispose();store.engine=create_engine(url,hide_parameters=True,connect_args={'options':'-csearch_path='+schema})
    store.Session=sessionmaker(store.engine,expire_on_commit=False);store.migrate();owners={};model_ids={}
    with store.session() as s:
        for name in ('alice','bob','carol'):owners[name]=create_user(s,name,'Stability-'+name+'-2026!',must_change=False).id
        for purpose in ('chat','embedding'):
            model=Model(scope='platform',purpose=purpose,name=purpose,mode='api',endpoint='https://api.example.com/v1',model='fixture',status='available',
                        dimension=8 if purpose=='embedding' else None,fingerprint='a'*64 if purpose=='embedding' else None)
            s.add(model);s.flush();model.secret=Vault(store.config.master_key).encrypt('stability-only',model.id);model_ids[purpose]=model.id
    call_count=0;call_lock=threading.Lock();errors=[];latencies=[];queue_wait=[];first_output=[];markers={name:'PRIVATE-STABILITY-'+name for name in owners}
    store.resolver=lambda host,port,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',port))]
    def transport(request):
        nonlocal call_count
        with call_lock:call_count+=1
        body=json.loads(request.content)
        if request.url.path.endswith('embeddings'):
            return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.]+[x/255 for x in hashlib.sha256(t.encode()).digest()[:7]]} for i,t in enumerate(body['input'])]})
        content=body['messages'][0]['content'];present=[name for name,marker in markers.items() if marker in content]
        assert len(present)==1,'Cross-user content in model context'
        return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':markers[present[0]]+' [1]'}}]})+'\n\ndata: [DONE]\n\n')
    store.model_transport=httpx.MockTransport(transport);app=create_app(store=store)
    workers=[Worker(store),Worker(store)];threads=[threading.Thread(target=w.run,daemon=True) for w in workers]
    for thread in threads:thread.start()
    clients={};kbs={};docs={};convs={};iterations={name:0 for name in owners};peak_rss=0;peak_disk=0;restarts=0;started=time.monotonic();deadline=started+args.duration_seconds
    def wait(c,target):
        limit=time.monotonic()+30
        while time.monotonic()<limit:
            jobs=c.get('/api/jobs').json();job=next((j for j in jobs if j['target']==target),None)
            if job and job['state'] in ('completed','failed','interrupted','cancelled'):
                assert job['state']=='completed',job
                return
            time.sleep(.03)
        raise AssertionError('Task exceeded 30 second terminal deadline')
    try:
        for name in owners:
            c=TestClient(app);r=c.post('/api/auth/login',json={'username':name,'password':'Stability-'+name+'-2026!'})
            assert r.status_code==200;c.headers['x-csrf-token']=r.json()['csrf'];clients[name]=c
            kbs[name]=c.post('/api/knowledge-bases',json={'name':name,'embedding_id':model_ids['embedding']}).json()['id']
            r=c.post('/api/documents/upload',data={'kb_id':kbs[name],'request_key':'initial'},files={'files':('same.txt',markers[name].encode())})
            assert r.status_code==200,r.text;docs[name]=r.json()[0]['document_id'];wait(c,docs[name])
            convs[name]=c.post('/api/conversations',json={}).json()['id']
        def cycle(name):
            c=clients[name];i=iterations[name];iterations[name]+=1;start=time.perf_counter()
            if i%10==0:
                r=c.post('/api/documents/upload',data={'kb_id':kbs[name],'request_key':f'replace-{i}','decision':'replace'},files={'files':('same.txt',(markers[name]+str(i)).encode())})
                assert r.status_code==200,r.text;wait(c,docs[name])
            for other in owners:
                if other!=name:assert c.get('/api/documents/'+docs[other]+'/file').status_code==404
            question={'question':'private','model_id':model_ids['chat'],'request_key':f'question-{i}','scope':{'type':'documents','ids':[docs[name]]}}
            r=c.post('/api/conversations/'+convs[name]+'/generations',json=question);assert r.status_code==200,r.text
            assert c.post('/api/conversations/'+convs[name]+'/generations',json=question).json()==r.json()
            g=r.json()['id']
            if i%7==0:
                assert c.post('/api/generations/'+g+'/stop').status_code==200
            else:
                wait(c,g);sources=c.get('/api/generations/'+g+'/sources').json();assert markers[name] in str(sources)
                assert all(markers[other] not in str(sources) for other in owners if other!=name)
                events=c.get('/api/generations/'+g+'/events?stream=false').text
                assert 'event: done' in events and all(markers[other] not in events for other in owners if other!=name)
                with store.session() as s:
                    job=s.scalar(select(Job).where(Job.target==g))
                    queue_wait.append((job.heartbeat_at-job.created_at).total_seconds())
                first_output.append(time.perf_counter()-start)
            dashboard=c.get('/api/dashboard').json();assert dashboard['documents']==1
            latencies.append(time.perf_counter()-start)
        with ThreadPoolExecutor(max_workers=3) as pool:
            last_progress=started
            while time.monotonic()<deadline:
                futures=[pool.submit(cycle,name) for name in owners]
                for future in futures:future.result(timeout=40)
                assert all(thread.is_alive() for thread in threads)
                peak_rss=max(peak_rss,psutil.Process().memory_info().rss)
                peak_disk=max(peak_disk,sum(p.stat().st_size for p in Path(temp.name).rglob('*') if p.is_file()))
                if not restarts and time.monotonic()-started>=args.duration_seconds/2:
                    # Graceful restart preserves pending work and original files in the same DB.
                    for worker in workers:worker.stop_event.set()
                    for thread in threads:thread.join(timeout=35);assert not thread.is_alive()
                    workers=[Worker(store),Worker(store)];threads=[threading.Thread(target=w.run,daemon=True) for w in workers]
                    for thread in threads:thread.start()
                    restarts+=1
                if time.monotonic()-last_progress>=30:
                    print(json.dumps({'elapsed_seconds':round(time.monotonic()-started),'iterations':iterations,'errors':len(errors)}),flush=True);last_progress=time.monotonic()
                time.sleep(1)
        with store.session() as s:
            stuck=s.scalar(select(func.count()).select_from(Job).where(Job.state.in_(['running','queued'])))
            attempts=max((job.attempt for job in s.scalars(select(Job))),default=0)
            committed=s.scalar(select(func.coalesce(func.sum(Reservation.amount),0)).where(Reservation.resource=='platform_calls',Reservation.state=='committed'))
            db_bytes=s.scalar(text('SELECT COALESCE(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()'))
        assert stuck==0 and attempts<=1 and committed==call_count and restarts==1,(stuck,attempts,committed,call_count,restarts)
        report={'duration_seconds':time.monotonic()-started,'users':3,'iterations':iterations,'errors':errors,'stuck_tasks':stuck,
                'max_attempt':attempts,'model_calls':call_count,'committed_calls':committed,'peak_process_rss_mb':peak_rss/1024**2,
                'max_cycle_seconds':max(latencies),'max_queue_wait_seconds':max(queue_wait),'max_answer_available_seconds':max(first_output),
                'peak_original_file_bytes':peak_disk,'database_bytes':int(db_bytes),'worker_restarts':restarts,
                'passed':True,'model':'deterministic protocol; real Ollama independently tested'}
        path=ROOT/'.reports/multiuser/P11/stability.json';report=write_report(path,report);print(json.dumps(report),flush=True)
    except Exception as exc:
        path=ROOT/'.reports/multiuser/P11'/('stability-failure-'+uid()+'.json');path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'passed':False,'elapsed_seconds':time.monotonic()-started,'iterations':iterations,'error_type':type(exc).__name__}),encoding='utf8')
        raise
    finally:
        for worker in workers:worker.stop_event.set()
        for thread in threads:thread.join(timeout=10)
        for client in clients.values():client.close()
        store.engine.dispose()
        with admin.begin() as c:c.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose();temp.cleanup()


if __name__=='__main__':main()
