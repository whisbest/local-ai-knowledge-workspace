from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from dataclasses import replace
from sqlalchemy import select
import pytest
from app.multiuser.db import Job, Record, now
from app.multiuser.worker import claim,recover,heartbeat,fenced_update,Worker
from app.multiuser.parsing import bounded_parse
from app.core import AppError
from test_documents_isolation import upload
import subprocess
import json
import sys
from pathlib import Path
from sqlalchemy import text


def queue(store,accounts,owner,count=1):
    with store.session() as s:
        ids=[]
        for i in range(count):
            record=Record(owner_id=owner,kind='document');s.add(record);s.flush()
            job=Job(owner_id=owner,target=record.id,kind='process');s.add(job);s.flush();ids.append(job.id)
        return ids


def test_workers_compete_fairly_and_reject_stale_leases(store,accounts):
    alice=queue(store,accounts,accounts['alice'],4);bob=queue(store,accounts,accounts['bob']);carol=queue(store,accounts,accounts['carol'])
    with ThreadPoolExecutor(max_workers=2) as pool:pairs=list(pool.map(lambda _:claim(store),range(2)))
    pairs=[p for p in pairs if p]
    while len(pairs)<3:
        pair=claim(store)
        if pair:pairs.append(pair)
    assert len({id for id,token in pairs})==3
    with store.session() as s:assert len({s.get(Job,id).owner_id for id,token in pairs})==3
    id,token=pairs[0];assert heartbeat(store,id,token)
    assert not fenced_update(store,id,'stale','completed')
    with store.session() as s:s.get(Job,id).lease_until=now()-timedelta(seconds=1)
    recover(store);assert not fenced_update(store,id,token,'completed')
    with store.session() as s:
        assert s.get(Job,id).state=='queued'
        assert s.get(Job,pairs[1][0]).state=='running'


def test_single_task_claimed_once_external_crash_not_retried(store,accounts):
    ids=queue(store,accounts,accounts['alice'])
    with ThreadPoolExecutor(max_workers=2) as pool:pairs=list(pool.map(lambda _:claim(store),range(2)))
    assert sum(p is not None for p in pairs)==1
    id,token=next(p for p in pairs if p)
    with store.session() as s:
        job=s.get(Job,id);job.external_started=True;job.lease_until=now()-timedelta(seconds=1)
    recover(store)
    with store.session() as s:assert s.get(Job,id).state=='interrupted'
    assert claim(store) is None


def test_real_processes_compete_for_single_task_and_reservation(store,accounts,tmp_path):
    queue(store,accounts,accounts['alice'])
    with store.session() as s:schema=s.scalar(text('SELECT current_schema()'))
    settings=tmp_path/'probe-private.json'
    settings.write_text(json.dumps({'url':store.config.database_url,'schema':schema,'root':str(store.config.data_root),'owner':accounts['alice']}),encoding='utf8')
    probe=Path(__file__).with_name('process_probe.py')
    processes=[subprocess.Popen([sys.executable,str(probe),str(settings),'claim'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    results=[]
    for process in processes:
        output,error=process.communicate(timeout=30);assert process.returncode==0,error;results.append(json.loads(output))
    assert sum(r is not None for r in results)==1
    processes=[subprocess.Popen([sys.executable,str(probe),str(settings),'reserve',str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(2)]
    results=[]
    for process in processes:
        output,error=process.communicate(timeout=30);assert process.returncode==0,error;results.append(output.strip())
    assert sorted(results)==['limited','reserved']


def test_worker_upload_pipeline_and_disabled_account_tasks(store,clients,accounts,embedding_fixture):
    model,_=embedding_fixture;a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'worker','embedding_id':model}).json()['id']
    response=upload(a,kb,b'Private worker document');assert response.status_code==200,response.text
    pair=Worker(store).run_once();assert pair
    assert a.get('/api/dashboard').json()['ready']==1
    assert a.get('/api/jobs').json()[0]['state']=='completed'
    upload(a,kb,b'next',filename='next.txt',key='second')
    assert clients['admin'].patch('/api/admin/users/'+accounts['alice'],json={'enabled':False}).status_code==200
    assert claim(store) is None
    with store.session() as s:assert all(job.state!='queued' for job in s.scalars(select(Job).where(Job.owner_id==accounts['alice'])))


def test_parser_timeout_bad_archive_and_disk_failure(store,clients,tmp_path,monkeypatch):
    path=tmp_path/'text.txt';path.write_text('bounded parser')
    with pytest.raises(AppError) as exc:bounded_parse(path,'txt',timeout=.001)
    assert exc.value.code=='PARSE_TIMEOUT'
    docx=tmp_path/'bad.docx';docx.write_text('not a zip')
    with pytest.raises(AppError):bounded_parse(docx,'docx')
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'disk'}).json()['id']
    from app.multiuser.storage import Storage
    original=Storage.write
    def fail(*args,**kwargs):raise AppError('DISK_FULL','文件存储失败。',507)
    monkeypatch.setattr(Storage,'write',fail)
    assert upload(a,kb,b'content').status_code==507
    assert a.get('/api/documents').json()['total']==0


def test_actual_worker_process_restart_recovers_local_work_without_repeating_external_call(store,clients,accounts,tmp_path):
    import time
    store.config=replace(store.config,lease_seconds=1)
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'restart'}).json()['id']
    response=upload(a,kb,b'Restart retains original file');assert response.status_code==200
    doc=response.json()[0]['document_id'];job_id=a.get('/api/jobs').json()[0]['id']
    with store.session() as s:schema=s.scalar(text('SELECT current_schema()'))
    settings=tmp_path/'restart-private.json'
    settings.write_text(json.dumps({'url':store.config.database_url,'schema':schema,'root':str(store.config.data_root),'owner':accounts['alice']}),encoding='utf8')
    probe=Path(__file__).with_name('process_probe.py')
    def process(action):
        result=subprocess.run([sys.executable,str(probe),str(settings),action],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    lost=process('claim-and-exit');assert lost[0]==job_id
    # Probe inherits the default 30-second lease; force a one-second expiry after process exit.
    with store.session() as s:s.get(Job,job_id).lease_until=now()+timedelta(seconds=1)
    time.sleep(1.1)
    resumed=process('run-once');assert resumed[0]==job_id and resumed[1]!=lost[1]
    assert not fenced_update(store,job_id,lost[1],'completed')
    with store.session() as s:
        job=s.get(Job,job_id);assert job.state=='completed' and job.attempt==2
    assert a.get('/api/documents/'+doc+'/file').content==b'Restart retains original file'
    # Once a process has marked an external call, recovery must expose interruption instead of retrying.
    external=queue(store,accounts,accounts['alice'])[0]
    pair=process('claim-external-and-exit');assert pair[0]==external
    with store.session() as s:s.get(Job,external).lease_until=now()-timedelta(seconds=1)
    assert process('run-once') is None
    with store.session() as s:
        job=s.get(Job,external);assert job.state=='interrupted' and job.attempt==1
