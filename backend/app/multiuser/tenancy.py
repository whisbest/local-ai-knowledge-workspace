from sqlalchemy import select
from ..core import AppError
from .db import Record, Job, Model


def require_owned(s, owner, id, kind=None, lock=False):
    statement = select(Record).where(Record.id == id, Record.owner_id == owner, Record.state != 'deleted')
    if kind: statement = statement.where(Record.kind == kind)
    if lock: statement = statement.with_for_update()
    value = s.scalar(statement)
    if not value: raise AppError('NOT_FOUND', '资料不存在或已删除。', 404)
    return value


def require_job(s, owner, id):
    job = s.scalar(select(Job).where(Job.owner_id == owner, Job.id == id).with_for_update())
    if not job: raise AppError('NOT_FOUND', '任务不存在。', 404)
    return job


def require_model(s, owner, id, purpose=None, available=True):
    value = s.scalar(select(Model).where(Model.id == id,
                         ((Model.scope == 'platform') | (Model.owner_id == owner))))
    if not value: raise AppError('NOT_FOUND', '模型配置不存在。', 404)
    if purpose and value.purpose != purpose:
        raise AppError('MODEL_CAPABILITY', '模型能力与用途不匹配。', 409)
    if available and (not value.enabled or value.status != 'available'):
        raise AppError('MODEL_UNAVAILABLE', '所选模型不可用，请测试配置或主动选择其他模型。', 409)
    return value


def validate_scope(s, owner, scope):
    scope = scope or {'type': 'all', 'ids': []}
    if not isinstance(scope, dict) or scope.get('type', 'all') not in ('all', 'kb', 'documents'):
        raise AppError('SCOPE', '资料范围格式不合法。', 422)
    kind = scope.get('type', 'all'); ids = scope.get('ids', [])
    if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(id,str) for id in ids):
        raise AppError('SCOPE', '请选择有效的资料范围。', 422)
    if kind != 'all':
        if not ids: raise AppError('SCOPE', '请至少选择一个知识库或文档。', 422)
        for id in ids: require_owned(s, owner, id, 'kb' if kind == 'kb' else 'document')
    return {'type': kind, 'ids': list(dict.fromkeys(ids))}
