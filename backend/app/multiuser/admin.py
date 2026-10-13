from fastapi import Request
from sqlalchemy import select,func,text
from ..core import AppError
from .auth import administrator
from .db import User,Job,Record,Model,Audit,Reservation,State


def mount(app,store):
    @app.get('/api/admin/status')
    def status(request:Request):
        administrator(request)
        with store.session() as s:
            s.execute(text('SELECT 1'))
            return {'database':'ok','users':s.scalar(select(func.count()).select_from(User)),
                    'queued':s.scalar(select(func.count()).select_from(Job).where(Job.state=='queued')),
                    'running':s.scalar(select(func.count()).select_from(Job).where(Job.state=='running')),
                    'failed':s.scalar(select(func.count()).select_from(Job).where(Job.state.in_(['failed','interrupted']))),
                    'documents':s.scalar(select(func.count()).select_from(Record).where(Record.kind=='document',Record.state!='deleted')),
                    'api_concurrency':store.config.api_concurrency,'local_concurrency':store.config.local_concurrency,
                    'global_pending':store.config.global_pending}

    @app.get('/api/admin/usage')
    def usage(request:Request):
        administrator(request)
        with store.session() as s:
            result=[]
            for user in s.scalars(select(User).order_by(User.username)):
                docs=list(s.scalars(select(Record).where(Record.owner_id==user.id,Record.kind=='document',Record.state!='deleted')))
                result.append({'id':user.id,'username':user.username,'quota':user.quota,
                       'storage_bytes':sum(r.payload.get('size',0) for r in docs),'documents':len(docs),
                       'platform_calls':s.scalar(select(func.coalesce(func.sum(Reservation.amount),0)).where(Reservation.owner_id==user.id,
                         Reservation.resource=='platform_calls',Reservation.state.in_(['committed','reserved','unknown'])))})
            return result

    @app.get('/api/admin/audit')
    def audit(request:Request):
        administrator(request)
        with store.session() as s:return [{'event':r.event,'object_id':r.object_id,'created_at':r.created_at.isoformat()}
                    for r in s.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(100))]
