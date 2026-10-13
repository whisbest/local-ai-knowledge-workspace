"""Persistent queue with fenced leases and cross-process scheduling."""
import os
import threading
import time
import signal
from datetime import timedelta
from sqlalchemy import select, update, exists, text
from ..core import AppError
from .db import Job, User, Record, Reservation, now, uid


def valid_lease(job,token):
    return bool(job and job.state=='running' and job.lease_token==token and job.lease_until and job.lease_until>now())


def recover(store):
    with store.session() as s:
        expired=list(s.scalars(select(Job).where(Job.state=='running',Job.lease_until<now()).with_for_update(skip_locked=True)))
        for job in expired:
            # Never auto-repeat externally uncertain generations (or embeddings).
            retryable=job.kind=='process' and not job.external_started and job.attempt<3
            job.state='queued' if retryable else 'interrupted';job.stage=job.state
            job.error='执行器租约已过期，任务等待恢复。' if retryable else '执行器中断，外部调用结果可能未知，请检查后主动重试。'
            job.lease_token=None;job.lease_until=None
            target=s.get(Record,job.target)
            if target and target.state!='deleted':target.state=job.state
            if target and target.kind=='generation':
                assistant=s.get(Record,target.payload['assistant_message_id'])
                if assistant:assistant.state='interrupted'


def claim(store):
    recover(store)
    with store.session() as s:
        # A brief scheduler transaction lock makes fairness deterministic across workers.
        if not s.scalar(text('SELECT pg_try_advisory_xact_lock(76241020)')):return None
        active=select(Job.owner_id).where(Job.state=='running')
        eligible=select(User).where(User.enabled==True,~User.id.in_(active),
                  exists(select(Job.id).where(Job.owner_id==User.id,Job.state=='queued'))).order_by(User.last_claim,User.id)
        user=s.scalar(eligible.with_for_update(skip_locked=True).limit(1))
        if not user:return None
        job=s.scalar(select(Job).where(Job.owner_id==user.id,Job.state=='queued').order_by(Job.created_at,Job.id)
                     .with_for_update(skip_locked=True).limit(1))
        if not job:return None
        job.state='running';job.stage='running';job.lease_token=uid();job.lease_until=now()+timedelta(seconds=store.config.lease_seconds)
        job.heartbeat_at=now();job.updated_at=now();job.attempt+=1;user.last_claim=now()
        return job.id,job.lease_token


def heartbeat(store,id,token):
    with store.session() as s:
        result=s.execute(update(Job).where(Job.id==id,Job.state=='running',Job.lease_token==token,Job.lease_until>now())
            .values(lease_until=now()+timedelta(seconds=store.config.lease_seconds),heartbeat_at=now()))
        return result.rowcount==1


def fenced_update(store,id,token,state):
    with store.session() as s:
        result=s.execute(update(Job).where(Job.id==id,Job.state=='running',Job.lease_token==token,Job.lease_until>now())
                .values(state=state,stage=state,lease_token=None,lease_until=None,updated_at=now()))
        return result.rowcount==1


def mark_external(store,id,token):
    with store.session() as s:
        job=s.scalar(select(Job).where(Job.id==id).with_for_update())
        if not valid_lease(job,token):raise AppError('LEASE','任务租约失效，已阻止外部调用。',409)
        if not s.get(User,job.owner_id).enabled:raise AppError('CANCELLED','账号已停用。',409)
        job.external_started=True


class Worker:
    def __init__(self,store):self.store=store;self.stop_event=threading.Event()

    def execute(self,id,token):
        heartbeat_stop=threading.Event()
        def keepalive():
            while not heartbeat_stop.wait(max(.1,self.store.config.lease_seconds/3)):
                if not heartbeat(self.store,id,token):return
        thread=threading.Thread(target=keepalive,daemon=True);thread.start()
        def cancelled():
            with self.store.session() as s:return not valid_lease(s.get(Job,id),token)
        self.store.worker_context.value=(id,token,cancelled)
        try:
            with self.store.session() as s:kind=s.get(Job,id).kind
            if kind=='process':
                from .documents import process_document
                process_document(self.store,id,token)
            elif kind=='generation':
                from .chat import execute_generation
                execute_generation(self.store,id,token)
            else:raise AppError('JOB_KIND','不支持的任务类型。',409)
        except Exception as exc:
            with self.store.session() as s:
                job=s.scalar(select(Job).where(Job.id==id).with_for_update())
                if valid_lease(job,token):
                    job.state='failed';job.stage='failed';job.error=exc.message if isinstance(exc,AppError) else '任务处理失败，请检查服务后重试。'
                    target=s.get(Record,job.target)
                    if target and target.state!='deleted':target.state='failed'
        finally:
            self.store.worker_context.value=None;heartbeat_stop.set();thread.join(timeout=2)
            with self.store.session() as s:
                job=s.get(Job,id)
                if job and job.lease_token==token and job.state!='running':job.lease_token=None;job.lease_until=None

    def run_once(self):
        pair=claim(self.store)
        if pair:self.execute(*pair)
        return pair

    def run(self):
        while not self.stop_event.is_set():
            if not self.run_once():self.stop_event.wait(.2)
