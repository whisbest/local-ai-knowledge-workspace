import sys,io,json,zipfile,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core import Store,Record,Job,AppError,load,dump
from app.backups import commit_restore,journal_write
from test_stage5 import prepare
from test_stage3 import drain

def make(c,app):
    conv,doc,_=prepare(c,app);g=c.post('/api/conversations/'+conv+'/generations',json={'question':'期限','request_key':'backup'}).json()['id'];drain(app.state.store)
    backup=c.post('/api/backups');assert backup.status_code==200,backup.text
    payload=c.get('/api/backups/'+backup.json()['id']+'/download').content
    return payload,conv,doc,g

def validate(c,payload):return c.post('/api/restores/validate',files={'file':('backup.zip',payload)})

def test_complete_backup_restore_without_secrets(tmp_path):
    first=create_app(tmp_path/'source',worker=False)
    with TestClient(first) as c:payload,conv,doc,g=make(c,first)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        assert not any(p.startswith('secrets/') for p in z.namelist());assert b'fixture' not in z.read('app.sqlite') # model key does not appear in DB
    second=create_app(tmp_path/'destination',worker=False)
    with TestClient(second) as c:
        c.post('/api/knowledge-bases',json={'name':'恢复前资料'})
        p=validate(c,payload);assert p.status_code==200,p.text;p=p.json();assert p['counts']['documents']==1
        result=c.post('/api/restores/commit',json={'id':p['id'],'token':p['token']});assert result.status_code==200,result.text
        assert c.get('/api/documents/'+doc+'/file').status_code==200
        assert c.get('/api/conversations/'+conv+'/messages').json()['messages'][-1]['content']
        assert c.get('/api/generations/'+g+'/sources').json()[0]['text']
        assert c.get('/api/indexes/impact').json()['vectors']>0
        assert all(not m['key_present'] for m in c.get('/api/model-configs').json())
        assert c.get('/api/backups/'+result.json()['pre_restore_backup']+'/download').status_code==200

def test_invalid_archive_checksum_version_and_traversal_preserve_data(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        payload,conv,doc,g=make(c,app)
        original=c.get('/api/documents').json()
        for mutation in ('corrupt','version','path'):
            target=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(payload)) as source,zipfile.ZipFile(target,'w') as dest:
                for name in source.namelist():
                    content=source.read(name)
                    if mutation=='corrupt' and name=='app.sqlite':content=b'broken'
                    if mutation=='version' and name=='manifest.json':p=json.loads(content);p['format_version']=99;content=json.dumps(p).encode()
                    dest.writestr(name,content)
                if mutation=='path':dest.writestr('../outside','no')
            assert validate(c,target.getvalue()).status_code==400
            assert c.get('/api/documents').json()==original
        assert validate(c,b'not zip').status_code==400

@pytest.mark.parametrize('step',['old_moved','new_moved'])
def test_restore_exception_rolls_back_and_preserves_prebackup(tmp_path,step):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        payload,conv,doc,g=make(c,app);new=c.post('/api/knowledge-bases',json={'name':'不可丢失'}).json()['id'];p=validate(c,payload).json()
        def fault(phase):
            if phase==step:raise OSError('disk failure')
        with pytest.raises(AppError):commit_restore(app.state.store,p['id'],p['token'],fault)
        assert any(k['id']==new for k in c.get('/api/knowledge-bases').json());assert c.get('/api/documents/'+doc+'/file').status_code==200
        assert len(c.get('/api/backups').json())==2

def test_crash_journal_rolls_back_on_next_start(tmp_path):
    store=Store(tmp_path)
    with store.session() as s:s.add(Record(id='saved',kind='kb',payload=dump({'name':'保存'})))
    id='crashcase';store.engine.dispose();journal_write(store,id,'prepared');old=store.root/'state/rollback'/id;store.current.rename(old);journal_write(store,id,'old_moved')
    recovered=Store(tmp_path)
    with recovered.session() as s:assert load(s.get(Record,'saved').payload)['name']=='保存'
    recovered.engine.dispose()

def test_active_jobs_block_snapshot_and_disk_failure_keeps_source(tmp_path,monkeypatch):
    import app.backups as module
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        kb=c.post('/api/knowledge-bases',json={'name':'保留'}).json()['id']
        with app.state.store.session() as s:s.add(Job(kind='pull',target='model',state='running'))
        assert c.post('/api/backups').status_code==409
        with app.state.store.session() as s:s.query(Job).delete()
        def fail(*args,**kwargs):raise OSError('no space')
        monkeypatch.setattr(module.shutil,'copytree',fail)
        assert c.post('/api/backups').status_code==500
        assert c.get('/api/knowledge-bases').json()[0]['id']==kb
