import hashlib
import io
import json
import zipfile
from pathlib import Path
from sqlalchemy import select
import pytest
from app.multiuser.db import Record
from app.multiuser.exports import validate_archive
from app.multiuser.backups import backup,restore,restore_preflight
from app.core import AppError
from test_documents_isolation import upload


def test_personal_export_authorized_checksum_secret_exclusion_and_restore_not_exposed(store,clients,identities,tmp_path):
    for name in ('alice','bob'):
        c=clients[name];kb=c.post('/api/knowledge-bases',json={'name':name}).json()['id'];upload(c,kb,identities[name]['marker'].encode())
    a=clients['alice'];response=a.post('/api/exports');assert response.status_code==200,response.text
    id=response.json()['id'];content=a.get('/api/exports/'+id+'/file').content
    assert clients['bob'].get('/api/exports/'+id+'/file').status_code==404
    path=tmp_path/'personal.zip';path.write_bytes(content);validate_archive(path,'lkq-personal')
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        texts=''.join(z.read(name).decode(errors='ignore') for name in z.namelist())
        assert identities['alice']['marker'] in texts and identities['bob']['marker'] not in texts
        for value in ('password_hash','workspace_session','master_key','file_key',str(store.config.data_root)):assert value not in texts
    assert a.post('/api/restore/preview').status_code==404
    assert a.post('/api/backups/restore').status_code==404


def test_archive_traversal_corruption_and_unknown_format(tmp_path):
    for name,manifest in [('../evil',{'format':'lkq-personal','version':2,'files':{}}),('safe',{'format':'unknown','version':2,'files':{}}),
                          ('safe',{'format':'lkq-personal','version':2,'files':{'safe':{'size':4,'sha256':'bad'}}})]:
        path=tmp_path/'bad.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr(name,b'data');z.writestr('manifest.json',json.dumps(manifest))
        with pytest.raises(AppError):validate_archive(path,'lkq-personal')


def test_platform_backup_restore_failure_rolls_back_database_and_files(store,clients,tmp_path):
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'keep'}).json()['id'];doc=upload(a,kb,b'original').json()[0]['document_id']
    archive=tmp_path/'platform.zip';backup(store,archive);manifest,rows=restore_preflight(store,archive)
    assert manifest['format']=='lkq-platform'
    assert a.patch('/api/knowledge-bases/'+kb,json={'name':'later'}).status_code==200
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    def failure():raise RuntimeError('injected failure')
    with pytest.raises(RuntimeError):restore(store,archive,sha,failpoint=failure)
    assert a.get('/api/knowledge-bases').json()[0]['name']=='later'
    assert a.get('/api/documents/'+doc+'/file').content==b'original'
    restore(store,archive,sha)
    assert a.get('/api/knowledge-bases').json()[0]['name']=='keep'
    assert a.get('/api/documents/'+doc+'/file').content==b'original'
    assert list((store.config.data_root/'platform-backups').glob('before-restore-*.zip'))


def test_restore_journal_recovers_interrupted_file_swap_and_running_tasks_block_backup(store,clients,tmp_path):
    from app.multiuser.backups import recover_restore
    from app.multiuser.db import Job, now
    from datetime import timedelta
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'journal'}).json()['id'];doc=upload(a,kb,b'restore-me').json()[0]['document_id']
    with store.session() as s:
        job=s.scalar(select(Job));job.state='running';job.lease_token='a'*32;job.lease_until=now()+timedelta(seconds=30)
    with pytest.raises(AppError):backup(store,tmp_path/'busy.zip')
    with store.session() as s:s.scalar(select(Job)).state='completed'
    root=store.config.data_root;current=root/'users';rollback=root/'restore-rollback-test';current.rename(rollback);current.mkdir()
    journal=root/'restore-journal.json';journal.write_text(json.dumps({'id':'interrupted','current':str(current),'rollback':str(rollback),'stage':str(root/'restore-stage-test')}))
    recover_restore(store)
    assert a.get('/api/documents/'+doc+'/file').content==b'restore-me'
    assert not journal.exists()
