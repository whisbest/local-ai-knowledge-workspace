from __future__ import annotations
import json
import os
import threading
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Text, Float, ForeignKey, UniqueConstraint


def uid(): return uuid4().hex
def now(): return datetime.now(timezone.utc).isoformat()
def dump(value): return json.dumps(value, ensure_ascii=False)
def load(value, default=None): return json.loads(value) if value else default


class AppError(Exception):
    def __init__(self, code, message, status=400, retryable=False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


class Base(DeclarativeBase): pass


class Record(Base):
    """Typed record envelope; module payloads are versioned JSON with explicit validation."""
    __tablename__ = 'records'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String, index=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    state: Mapped[str] = mapped_column(String, default='active', index=True)
    payload: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String, index=True)
    state: Mapped[str] = mapped_column(String, default='queued', index=True)
    stage: Mapped[str] = mapped_column(String, default='queued')
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class Idempotency(Base):
    __tablename__ = 'idempotency'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    result: Mapped[str] = mapped_column(Text)


class Vector(Base):
    __tablename__ = 'vectors'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(String, index=True)
    version_id: Mapped[str] = mapped_column(String, index=True)
    generation_id: Mapped[str] = mapped_column(String, index=True)
    fingerprint: Mapped[str] = mapped_column(String, index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(String, unique=True)
    values: Mapped[str] = mapped_column(Text)


class StreamEvent(Base):
    __tablename__ = 'stream_events'
    __table_args__ = (UniqueConstraint('generation_id', 'seq'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_id: Mapped[str] = mapped_column(String, index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[str] = mapped_column(Text)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.current = self.root / 'state/current'
        self.locked = False
        self.model_lock = threading.RLock()
        self.gate = threading.Lock()
        self.request_count = 0
        self.inflight_workers = 0
        self.recover_files()
        self.initialize()

    def recover_files(self):
        journal=self.root/'state/recovery.json'
        if not journal.exists():return
        data=load(journal.read_text(encoding='utf8'));old=self.root/'state/rollback'/data['id']
        if data['phase']!='committed' and old.exists():
            if self.current.exists():
                failed=self.root/'state/staging'/('failed-'+uid());self.current.rename(failed)
            old.rename(self.current)
        elif not self.current.exists():raise RuntimeError('恢复日志存在但当前数据不存在，请保留数据并检查恢复前备份。')
        journal.unlink()

    def initialize(self):
        for relative in ['state/current/documents', 'state/current/parsed', 'state/staging', 'state/rollback', 'secrets', 'backups', 'logs']:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self.engine = create_engine('sqlite:///' + str(self.current / 'app.sqlite'), connect_args={'check_same_thread': False, 'timeout': 15})
        @event.listens_for(self.engine, 'connect')
        def pragmas(conn, _):
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=15000')
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        from alembic.config import Config
        from alembic import command
        migration_root = Path(__file__).resolve().parents[1] / 'migrations'
        cfg = Config()
        cfg.set_main_option('script_location', str(migration_root))
        cfg.set_main_option('sqlalchemy.url', str(self.engine.url).replace('%','%%'))
        cfg.attributes['engine'] = self.engine
        command.upgrade(cfg, 'head')
        with self.session() as s:
            if not s.get(Record, 'preferences'):
                s.add(Record(id='preferences', kind='settings', payload=dump({'chunk_length':800, 'chunk_overlap':120, 'answer_length':'normal', 'source_panel':True, 'default_chat':None, 'default_embedding':None, 'default_kb':None})))

    @contextmanager
    def session(self):
        with self.Session() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    def recover(self):
        with self.session() as s:
            for job in s.query(Job).filter(Job.state.in_(['running'])):
                job.state, job.stage, job.error, job.lease = 'interrupted', 'interrupted', '服务停止导致任务中断，请重试。', None
                d=s.get(Record,job.target)
                if d and d.kind=='document' and d.state!='deleted':d.state='interrupted'
            for record in s.query(Record).filter(Record.kind=='generation', Record.state.in_(['queued','retrieving','generating'])):
                record.state = 'interrupted'
                payload=load(record.payload)
                message=s.get(Record,payload.get('assistant_message_id'))
                if message:message.state='interrupted'
            for index in s.query(Record).filter_by(kind='index',state='building'):
                index.state='failed';s.query(Vector).filter_by(generation_id=index.id).delete()
        from .documents import cleanup_files
        with self.session() as s:
            cleanup_files(self,[r for r in s.query(Record).filter_by(kind='version',state='deleted')])

    def claim(self, kind=None):
        with self.gate:
            if self.locked:return None
            return self._claim(kind)

    def _claim(self, kind=None):
        with self.engine.connect() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            row = conn.execute(text('SELECT id FROM jobs WHERE state=:state' + (' AND kind=:kind' if kind else '') + " ORDER BY CASE kind WHEN 'generation' THEN 0 ELSE 1 END, created_at LIMIT 1"), {'state':'queued', 'kind':kind}).first()
            if row:
                conn.execute(text("UPDATE jobs SET state='running',lease=:lease,updated_at=:updated WHERE id=:id AND state='queued'"), {'id':row[0], 'lease':uid(), 'updated':now()})
            conn.commit()
            return row[0] if row else None


def record_data(record):
    return {'id':record.id, 'kind':record.kind, 'state':record.state, 'created_at':record.created_at, 'updated_at':record.updated_at, **load(record.payload,{})}


def job_data(job):
    return {key:getattr(job,key) for key in ['id','kind','target','state','stage','done','total','attempt','error','created_at','updated_at']}
