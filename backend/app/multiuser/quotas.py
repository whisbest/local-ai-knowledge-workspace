from contextlib import contextmanager
import time
from sqlalchemy import select, func, text
from ..core import AppError
from .db import Reservation, User, Model, uid


def reserve(s, owner, resource, request_id, amount, limit):
    s.scalar(select(User).where(User.id==owner).with_for_update())
    existing=s.scalar(select(Reservation).where(Reservation.owner_id==owner,Reservation.resource==resource,Reservation.request_id==request_id))
    if existing:
        if existing.amount!=amount:raise AppError('QUOTA_CONFLICT','额度预留内容冲突。',409)
        return existing
    consumed=s.scalar(select(func.coalesce(func.sum(Reservation.amount),0)).where(Reservation.owner_id==owner,
                         Reservation.resource==resource,Reservation.state.in_(['reserved','committed','unknown'])))
    if consumed+amount>limit:raise AppError('QUOTA_LIMIT','可用额度不足，请联系管理员或主动选择其他服务。',409)
    row=Reservation(owner_id=owner,resource=resource,request_id=request_id,amount=amount,state='reserved')
    s.add(row);s.flush();return row


def settle(s,owner,resource,request_id,state):
    if state not in ('committed','released','unknown'):raise ValueError('Invalid settlement')
    row=s.scalar(select(Reservation).where(Reservation.owner_id==owner,Reservation.resource==resource,
                                          Reservation.request_id==request_id).with_for_update())
    if row and row.state=='reserved':row.state=state
    return row


def release_storage(s,owner,request_id):
    row=s.scalar(select(Reservation).where(Reservation.owner_id==owner,Reservation.resource=='storage_bytes',
                         Reservation.request_id==request_id).with_for_update())
    if row:row.state='released'


@contextmanager
def slot(store,mode,timeout=30,cancelled=lambda:False):
    limit=store.config.local_concurrency if mode=='local' else store.config.api_concurrency
    base=76242000 if mode=='local' else 76243000
    with store.engine.connect() as conn:
        started=time.monotonic();acquired=None
        while acquired is None:
            if cancelled():raise AppError('CANCELLED','任务已停止。',409)
            for i in range(limit):
                if conn.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':base+i}):acquired=base+i;break
            if acquired is not None:break
            conn.rollback()
            if time.monotonic()-started>=timeout:raise AppError('MODEL_BUSY','模型繁忙，等待超过上限，请稍后重试。',429)
            time.sleep(.05)
        try:yield
        finally:
            conn.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':acquired});conn.commit()


class MeteredProvider:
    def __init__(self,store,owner,model_id,inner,cancelled=lambda:False,allow_untested=False):
        self.store,self.owner,self.model_id,self.inner,self.cancelled=store,owner,model_id,inner,cancelled
        self.allow_untested=allow_untested

    def _reserve(self):
        with self.store.session() as s:
            user=s.scalar(select(User).where(User.id==self.owner).with_for_update());model=s.get(Model,self.model_id)
            if not user.enabled or self.cancelled():raise AppError('CANCELLED','账号或任务已停用。',409)
            if not model.enabled or (model.status!='available' and not self.allow_untested):raise AppError('MODEL_UNAVAILABLE','所选模型不可用。',409)
            request_id=uid()
            if model.scope=='platform':
                reserve(s,self.owner,'platform_calls',request_id,1,user.quota.get('platform_calls',self.store.config.platform_calls))
            return request_id,model.mode,model.scope=='platform'

    def _settle(self,request_id,state,metered):
        if metered:
            with self.store.session() as s:settle(s,self.owner,'platform_calls',request_id,state)

    def embed(self,texts):
        request_id,mode,metered=self._reserve();started=False
        try:
            with slot(self.store,mode,cancelled=self.cancelled):
                if self.cancelled():raise AppError('CANCELLED','任务已停止。',409)
                started=True;result=self.inner.embed(texts)
            self._settle(request_id,'committed',metered);return result
        except Exception:
            self._settle(request_id,'unknown' if started else 'released',metered);raise

    def chat(self,messages,cancelled=lambda:False,length='normal'):
        request_id,mode,metered=self._reserve();started=False
        try:
            with slot(self.store,mode,cancelled=lambda:self.cancelled() or cancelled()):
                if self.cancelled() or cancelled():raise AppError('CANCELLED','任务已停止。',409)
                started=True
                for delta in self.inner.chat(messages,lambda:self.cancelled() or cancelled(),length):yield delta
            self._settle(request_id,'committed',metered)
        except BaseException:
            self._settle(request_id,'unknown' if started else 'released',metered);raise

    def close(self):self.inner.close()
