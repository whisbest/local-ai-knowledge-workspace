import hashlib
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app as old_app
from app.multiuser.migration import migrate,rollback
from app.multiuser.db import Record
from app.core import AppError
from sqlalchemy import select
import pytest


def test_legacy_readonly_dryrun_owner_duplicate_and_rollback(store,clients,accounts,tmp_path):
    source=tmp_path/'v1'
    with TestClient(old_app(source,worker=False)) as old:
        kb=old.post('/api/knowledge-bases',json={'name':'Legacy private'}).json()['id']
        doc=old.post('/api/documents/upload',data={'kb_id':kb,'request_key':'legacy'},files={'files':('legacy.txt',b'LEGACY-ONLY-MARKER')}).json()[0]['document_id']
    database=source/'state/current/app.sqlite';before=hashlib.sha256(database.read_bytes()).hexdigest()
    plan=migrate(store,source,accounts['alice'],[doc]);assert plan['dry_run'] and plan['counts']['document']==1
    assert clients['alice'].get('/api/documents').json()['total']==0
    result=migrate(store,source,accounts['alice'],[doc],dry_run=False)
    assert clients['alice'].get('/api/documents').json()['total']==1
    assert clients['bob'].get('/api/documents').json()['total']==0
    new=clients['alice'].get('/api/documents').json()['items'][0]
    assert clients['alice'].get('/api/documents/'+new['id']+'/file').content==b'LEGACY-ONLY-MARKER'
    assert new['state']=='needs_rebuild'
    assert migrate(store,source,accounts['alice'],[doc],dry_run=False)['already_imported']
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    rollback(store,accounts['alice'],result['batch'])
    assert clients['alice'].get('/api/documents').json()['total']==0
    assert not list((store.config.data_root/'users'/accounts['alice']).glob('*.txt'))
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    with pytest.raises(AppError):migrate(store,source,accounts['alice'],[])
