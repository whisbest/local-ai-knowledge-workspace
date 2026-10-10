from __future__ import annotations
import hashlib
import shutil
import os
from fastapi import APIRouter
from .core import Record,Job,Vector,StreamEvent,Idempotency,AppError,dump,load,now
from .documents import delete_document,cleanup_files,invalidate


def clear_preview(store,category):
    if category not in ('indexes','documents','conversations','all'):raise AppError('CATEGORY','请选择索引、文档、对话或全部业务数据。')
    with store.session() as s:
        rows=s.query(Record).order_by(Record.id).all();vectors=s.query(Vector).count()
        counts={'documents':sum(r.kind=='document' and r.state!='deleted' for r in rows),'conversations':sum(r.kind=='conversation' and r.state!='deleted' for r in rows),'vectors':vectors}
        token=hashlib.sha256(dump({'category':category,'rows':[(r.id,r.state,r.updated_at,r.payload) for r in rows],'vectors':vectors}).encode()).hexdigest()
        return {'category':category,'counts':counts,'token':token,'message':{'indexes':'只清理索引，原文件、切片、对话与引用保留；文档需要重建。','documents':'删除原文档和索引，历史引用快照保留。','conversations':'清理对话、问题、回答和历史引用，原文档与索引保留。','all':'清理全部业务资料、对话和索引，保留模型配置与偏好。'}[category]}


def mount_data(app,store):
    router=APIRouter(prefix='/api/data')
    @router.get('/usage')
    def usage():
        sizes={name:sum(p.stat().st_size for p in (store.root/name).rglob('*') if p.is_file()) for name in ('state/current','backups','secrets','logs')}
        disk=shutil.disk_usage(store.root)
        with store.session() as s:counts={'documents':s.query(Record).filter(Record.kind=='document',Record.state!='deleted').count(),'conversations':s.query(Record).filter_by(kind='conversation',state='active').count(),'vectors':s.query(Vector).count()}
        return {'host_path':os.environ.get('HOST_DATA_PATH',str(store.root)),'container_path':str(store.root),'bytes':sizes,'free_bytes':disk.free,'counts':counts,'model_weights':'本机 Ollama 默认 %USERPROFILE%/.ollama/models（不包含在以上应用数据统计中）'}

    @router.post('/clear/preview')
    def preview(body:dict):return clear_preview(store,body.get('category'))

    @router.post('/clear')
    def clear(body:dict):
        category=body.get('category')
        if body.get('token')!=clear_preview(store,category)['token']:raise AppError('IMPACT_CHANGED','数据已变化，请重新预览后确认。',409)
        versions=[]
        with store.session() as s:
            if s.query(Job).filter_by(state='running').count():raise AppError('BUSY','有任务正在运行，请停止或等待完成后清理。',409)
            if category in ('indexes','all','documents'):
                for d in s.query(Record).filter(Record.kind=='document',Record.state!='deleted'):
                    if category=='indexes':
                        invalidate(s,d);d.state='needs_rebuild';d.updated_at=now()
                        for j in s.query(Job).filter(Job.target==d.id,Job.state=='queued'):j.state='cancelled'
                    else:versions.extend(delete_document(store,s,d))
            if category in ('conversations','all'):
                for r in s.query(Record).filter(Record.kind.in_(['conversation','message','generation','source'])):s.delete(r)
                s.query(StreamEvent).delete();s.query(Job).filter_by(kind='generation').delete()
                # Removed conversations/generations must not be returned by old request keys.
                s.query(Idempotency).filter(Idempotency.key.like('generation:%') | Idempotency.key.like('conversation:%')).delete()
            if category in ('documents','all'):s.query(Idempotency).filter(Idempotency.key.like('upload:%')).delete()
            if category=='all':
                for r in s.query(Record).filter_by(kind='kb'):r.state='deleted'
        cleanup_files(store,versions)
        return {'cleared':category}
    app.include_router(router)
