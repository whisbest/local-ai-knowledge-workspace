from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import threading
from sqlalchemy import (create_engine, String, Integer, BigInteger, Boolean, DateTime,
                        ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, text)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, REAL
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from alembic.config import Config as AlembicConfig
from alembic import command


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(10), default='user')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change: Mapped[bool] = mapped_column(Boolean, default=True)
    quota: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_claim: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("role IN ('admin','user')"),)


class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Model(Base):
    __tablename__ = 'model_configs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(10))
    purpose: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(10), default='api')
    endpoint: Mapped[str] = mapped_column(String(2048))
    model: Mapped[str] = mapped_column(String(180))
    revision: Mapped[str] = mapped_column(String(32), default=uid)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default='untested')
    dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    secret: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint("(scope='platform' AND owner_id IS NULL) OR (scope='personal' AND owner_id IS NOT NULL)"),
                      CheckConstraint("purpose IN ('chat','embedding')"),
                      CheckConstraint("mode IN ('api','local')"))


class Preferences(Base):
    __tablename__ = 'user_preferences'
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=lambda: {'chunk_length': 800, 'chunk_overlap': 120,
            'answer_length': 'normal', 'source_panel': True, 'default_chat': None,
            'default_embedding': None, 'default_kb': None})


class Record(Base):
    __tablename__ = 'records'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    model_id: Mapped[str | None] = mapped_column(ForeignKey('model_configs.id'), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default='active', index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('owner_id', 'id'),
                      ForeignKeyConstraint(['owner_id', 'parent_id'], ['records.owner_id', 'records.id']),)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    target: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    stage: Mapped[str] = mapped_column(String(24), default='queued')
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    external_started: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('owner_id', 'id'),
                     ForeignKeyConstraint(['owner_id', 'target'], ['records.owner_id', 'records.id']),)


class Vector(Base):
    __tablename__ = 'vectors'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    document_id: Mapped[str] = mapped_column(String(32))
    version_id: Mapped[str] = mapped_column(String(32))
    index_id: Mapped[str] = mapped_column(String(32), index=True)
    chunk_id: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[int] = mapped_column(Integer)
    values: Mapped[list] = mapped_column(ARRAY(REAL))
    __table_args__ = tuple(ForeignKeyConstraint(['owner_id', key], ['records.owner_id', 'records.id'])
                          for key in ('document_id', 'version_id', 'index_id', 'chunk_id')) + (
                          UniqueConstraint('owner_id', 'index_id', 'chunk_id'),
                          CheckConstraint('dimension > 0 AND cardinality(values) = dimension'),)


class Event(Base):
    __tablename__ = 'stream_events'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    generation_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (ForeignKeyConstraint(['owner_id', 'generation_id'], ['records.owner_id', 'records.id']),
                     UniqueConstraint('owner_id', 'generation_id', 'seq'))


class Idempotency(Base):
    __tablename__ = 'idempotency'
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    operation: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict | list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Reservation(Base):
    __tablename__ = 'quota_reservations'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    resource: Mapped[str] = mapped_column(String(30))
    request_id: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16), default='reserved')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('owner_id', 'resource', 'request_id'),
                     CheckConstraint("state IN ('reserved','committed','released','unknown') AND amount >= 0"))


class Audit(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    event: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginLimit(Base):
    __tablename__ = 'login_limits'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailChallenge(Base):
    __tablename__ = 'email_challenges'
    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), default=uid)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)


class State(Base):
    __tablename__ = 'platform_state'
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class Store:
    def __init__(self, config):
        self.config = config
        self.engine = create_engine(config.database_url, pool_pre_ping=True, hide_parameters=True)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.worker_context=threading.local()

    def migrate(self):
        cfg = AlembicConfig()
        cfg.set_main_option('script_location', str(Path(__file__).resolve().parents[2] / 'migrations_multiuser'))
        cfg.attributes['engine'] = self.engine
        command.upgrade(cfg, 'head')

    @contextmanager
    def session(self):
        with self.Session() as session:
            with session.begin():
                # Shared transaction lock coordinates every API/worker write with maintenance CLI.
                session.execute(text('SELECT pg_advisory_xact_lock_shared(76241011)'))
                yield session


def record_data(record):
    return {'id': record.id, 'kind': record.kind, 'state': record.state,
            'created_at': record.created_at.isoformat(), 'updated_at': record.updated_at.isoformat(),
            **record.payload}


def job_data(job):
    return {key: (value.isoformat() if isinstance(value, datetime) else value)
            for key in ('id', 'kind', 'target', 'state', 'stage', 'done', 'total', 'attempt', 'error', 'created_at', 'updated_at')
            for value in [getattr(job, key)]}
