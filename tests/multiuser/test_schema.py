import importlib
import hashlib
import sqlite3
import os
from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.multiuser.db import User, Record, Vector, Idempotency, Job, Event, uid
from app.multiuser.config import Config
from app.multiuser.storage import Storage
from app.core import AppError


def owners(store):
    with store.session() as s:
        a = User(username='a', password_hash='test'); b = User(username='b', password_hash='test')
        s.add_all([a, b]); s.flush()
        return a.id, b.id


def test_real_postgres_migrations_constraints_and_rollback(store):
    store.migrate()
    with store.session() as s:
        assert s.execute(text('SELECT version()')).scalar().startswith('PostgreSQL 17')
        assert s.execute(text('SELECT version_num FROM alembic_version')).scalar() == 'mu0003'
    a, b = owners(store)
    with store.session() as s:
        parent = Record(owner_id=a, kind='kb'); s.add(parent); s.flush(); parent_id = parent.id
    with pytest.raises(IntegrityError):
        with store.session() as s:
            s.add(Record(owner_id=b, kind='document', parent_id=parent_id))
    with store.session() as s:
        assert s.query(Record).count() == 1
        for user in (a, b):
            s.add(Idempotency(owner_id=user, operation='upload', key='same', digest='a'*64, result={}))
    with pytest.raises(IntegrityError):
        with store.session() as s:
            s.add(Idempotency(owner_id=a, operation='upload', key='same', digest='a'*64, result={}))
    for child in (Job(owner_id=b, target=parent_id, kind='process'),
                  Event(owner_id=b, generation_id=parent_id, seq=1, kind='delta'),
                  Vector(owner_id=b, document_id=parent_id, version_id=parent_id, index_id=parent_id,
                         chunk_id=parent_id, fingerprint='x'*64, dimension=2, values=[1., 2.])):
        with pytest.raises(IntegrityError):
            with store.session() as s:
                s.add(child)
    store.engine.dispose()
    with store.session() as s:
        assert s.get(Record, parent_id).owner_id == a


def test_storage_limits_traversal_checksum_and_compensation(store):
    storage = Storage(store.config.data_root)
    a, b = owners(store); key = uid() + '.txt'
    meta = storage.write(a, key, [b'first', b'second'], 20)
    assert storage.verify(a, key, meta['sha256'])
    assert not storage.path(b, key).exists()
    for owner, badkey in [(a, '../secret'), ('../../a', key), (a, '/etc/passwd')]:
        with pytest.raises(AppError): storage.path(owner, badkey)
    oversized = uid()+'.txt'
    with pytest.raises(AppError): storage.write(a, oversized, [b'x'*30], 20)
    assert not storage.path(a, oversized).exists()
    compensated = uid()+'.txt'
    with pytest.raises(IntegrityError):
        with store.session() as s:
            storage.write(a, compensated, [b'rollback'], 20)
            storage.compensate(s, a, [compensated])
            s.add(Record(owner_id='f'*32, kind='kb')); s.flush()
    assert not storage.path(a, compensated).exists()
    assert not list(storage.path(a, key).parent.glob('.pending-*'))


def test_import_and_config_do_not_touch_original_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import app.multiuser.db as module
    importlib.import_module(module.__name__)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError): Config('sqlite:///forbidden', tmp_path)
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError): Config('postgresql+psycopg://example', root/'data')


def test_storage_rejects_linked_file_and_legacy_reader_preserves_source(tmp_path):
    from app.multiuser.legacy import read_records
    source = tmp_path / 'legacy'; database = source / 'state/current/app.sqlite'
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE records(id TEXT, kind TEXT, payload TEXT, created_at TEXT)')
        c.execute("INSERT INTO records VALUES('original','kb','{}','2026')")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    assert read_records(source)[0]['id'] == 'original'
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    storage = Storage(tmp_path/'storage'); owner = uid(); key = uid()+'.txt'
    target = storage.path(owner,key); target.parent.mkdir(parents=True)
    other = tmp_path/'private.txt'; other.write_text('outside')
    # NTFS hard links are available without enabling Developer Mode; detect aliases too.
    os.link(other,target)
    with pytest.raises(AppError): storage.path(owner,key)
