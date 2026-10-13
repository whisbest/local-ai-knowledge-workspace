"""Explicit, read-only V1 import. Personal secrets and live model addresses are excluded."""
import hashlib
import json
from pathlib import Path
from datetime import datetime
from sqlalchemy import select,delete
from ..core import AppError
from .legacy import read_records
from .db import Record,Job,Preferences,User,uid
from .documents import lock_owner
from .storage import Storage
from .exports import strip_private


def plan(source,document_ids,include_conversations=False):
    source=Path(source).resolve();rows=read_records(source);by_id={r['id']:r for r in rows}
    if not document_ids:raise AppError('MIGRATION_SELECTION','必须显式选择待迁移文档；不会默认上传全部资料。',422)
    selected=set(document_ids)
    for id in selected:
        if id not in by_id or by_id[id]['kind']!='document' or by_id[id]['state']=='deleted':raise AppError('MIGRATION_SELECTION','选择包含不存在或已删除的文档。',422)
    for id in list(selected):
        parent=by_id[id]['payload'].get('kb_id') or by_id[id]['parent_id']
        if not parent or parent not in by_id or by_id[parent]['kind']!='kb':raise AppError('MIGRATION_RELATION','文档知识库关联缺失。',422)
        selected.add(parent)
    changed=True
    while changed:
        changed=False
        for row in rows:
            if row['parent_id'] in selected and row['kind'] in ('version','chunk') and row['state']!='deleted' and row['id'] not in selected:
                selected.add(row['id']);changed=True
    if include_conversations:
        selected.update(row['id'] for row in rows if row['kind'] in ('conversation','message','generation','source') and row['state']!='deleted')
    chosen=[r for r in rows if r['id'] in selected]
    for row in chosen:
        if row['parent_id'] and row['parent_id'] not in selected:raise AppError('MIGRATION_RELATION','迁移记录存在断开的父关系。',422)
    files={}
    for row in chosen:
        if row['kind']=='version':
            relative=row['payload'].get('source_path');path=(source/'state/current'/str(relative)).resolve()
            if not relative or not path.is_relative_to(source/'state/current/documents') or not path.is_file():raise AppError('MIGRATION_FILE','原文档路径缺失或越界。',422)
            files[row['id']]={'path':path,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            original_checksum=row['payload'].get('sha256') or row['payload'].get('checksum')
            if original_checksum and original_checksum!=files[row['id']]['sha256']:raise AppError('MIGRATION_CHECKSUM','旧文件校验不一致。',422)
    signature=hashlib.sha256(json.dumps({'ids':sorted(selected),'files':{key:value['sha256'] for key,value in files.items()},'rows':chosen},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return chosen,files,signature


def migrate(store,source,owner,document_ids,include_conversations=False,dry_run=True):
    chosen,files,signature=plan(source,document_ids,include_conversations)
    counts={kind:sum(r['kind']==kind for r in chosen) for kind in {r['kind'] for r in chosen}}
    with store.session() as s:
        if not s.get(User,owner):raise AppError('MIGRATION_OWNER','指定归属账号不存在。',404)
        previous=s.scalar(select(Record).where(Record.owner_id==owner,Record.kind=='migration',Record.payload['signature'].astext==signature,Record.state=='completed'))
        if previous:return {'already_imported':True,'batch':previous.id,'counts':counts,'dry_run':dry_run}
    if dry_run:return {'dry_run':True,'counts':counts,'files':len(files),'signature':signature,'secrets':'excluded','indexes':'rebuild_required'}
    storage=Storage(store.config.data_root);keys=[];mapping={r['id']:uid() for r in chosen};batch_id=uid()
    try:
        with store.session() as s:
            lock_owner(s,owner);prefs=s.get(Preferences,owner);prior_preferences=dict(prefs.payload)
            legacy_preferences=next((r['payload'] for r in read_records(source) if r['kind']=='settings'),{})
            new_preferences={**prior_preferences,**{k:legacy_preferences[k] for k in ('chunk_length','chunk_overlap','answer_length','source_panel') if k in legacy_preferences}}
            new_preferences['default_chat']=None;new_preferences['default_embedding']=None
            new_preferences['default_kb']=mapping.get(legacy_preferences.get('default_kb'))
            prefs.payload=new_preferences
            for row in chosen:
                if row['kind']=='version':
                    file=files[row['id']];key=mapping[row['id']]+'.'+row['payload']['format']
                    storage.write(owner,key,[file['path'].read_bytes()],store.config.upload_bytes);keys.append(key)
            remaining={r['id']:r for r in chosen};inserted=set();new_records=[]
            while remaining:
                ready=[r for r in remaining.values() if not r['parent_id'] or r['parent_id'] in inserted]
                if not ready:raise AppError('MIGRATION_RELATION','旧记录包含循环关系。',422)
                for row in ready:
                    payload=strip_private(row['payload']);kind=row['kind'];parent=mapping.get(row['parent_id'])
                    if payload.get('checksum'):payload['sha256']=payload.pop('checksum')
                    for key in ('kb_id','current_version_id','document_id','version_id','assistant_message_id','user_message_id','regeneration_of'):
                        if payload.get(key) in mapping:payload[key]=mapping[payload[key]]
                    payload.pop('model_id',None);payload.pop('provider_snapshot',None);payload.pop('model_config_id',None)
                    state=row['state']
                    if kind=='kb':
                        payload={**payload,'profile':{'length':new_preferences['chunk_length'],'overlap':new_preferences['chunk_overlap']},'embedding_id':None,'rebuild_required':True}
                    elif kind=='document':
                        parent=mapping[row['payload']['kb_id']];payload['kb_id']=parent;state='needs_rebuild'
                    elif kind=='version':
                        file=files[row['id']];payload['file_key']=mapping[row['id']]+'.'+payload['format'];payload['sha256']=file['sha256'];payload['size']=file['path'].stat().st_size
                        state='parsed' if row['payload'].get('parsed_path') else 'queued'
                        parsed_relative=row['payload'].get('parsed_path')
                        if parsed_relative:
                            parsed_path=(Path(source).resolve()/'state/current'/parsed_relative).resolve()
                            if not parsed_path.is_relative_to(Path(source).resolve()/'state/current/parsed'):raise AppError('MIGRATION_FILE','解析路径越界。',422)
                            if parsed_path.exists():payload['parsed']=json.loads(parsed_path.read_text(encoding='utf8'))
                    elif kind=='generation':
                        payload['provider_snapshot']={'model':'旧版模型（需重新选择）','mode':'unknown','scope':'legacy'}
                        if state not in ('completed','failed','stopped','interrupted'):state='interrupted'
                    record=Record(id=mapping[row['id']],owner_id=owner,kind=kind,parent_id=parent,state=state,payload=payload,
                                  created_at=datetime.fromisoformat(row['created_at']),updated_at=datetime.fromisoformat(row['updated_at']))
                    s.add(record);new_records.append(record);inserted.add(row['id']);remaining.pop(row['id'])
                s.flush()
            storage.compensate(s,owner,keys)
            from .quotas import reserve,settle
            user=s.get(User,owner)
            for record in new_records:
                if record.kind=='version':
                    reserve(s,owner,'storage_bytes',record.id,record.payload['size'],user.quota.get('storage_bytes',store.config.storage_bytes))
                    settle(s,owner,'storage_bytes',record.id,'committed')
            s.add(Record(id=batch_id,owner_id=owner,kind='migration',state='completed',payload={
                'signature':signature,'counts':counts,'record_ids':[r.id for r in new_records],'file_keys':keys,
                'prior_preferences':prior_preferences,'index_policy':'rebuild_required','secrets':'excluded'}))
        return {'batch':batch_id,'counts':counts,'files':len(files),'dry_run':False,'indexes':'rebuild_required'}
    except Exception:
        for key in keys:storage.delete(owner,key)
        raise


def rollback(store,owner,batch_id):
    storage=Storage(store.config.data_root)
    with store.session() as s:
        lock_owner(s,owner)
        batch=s.scalar(select(Record).where(Record.owner_id==owner,Record.id==batch_id,Record.kind=='migration').with_for_update())
        if not batch or batch.state!='completed':raise AppError('MIGRATION_BATCH','迁移批次不存在或已经回滚。',404)
        ids=set(batch.payload['record_ids']);keys=batch.payload['file_keys']
        from .quotas import release_storage
        for id in ids:release_storage(s,owner,id)
        if s.scalar(select(Job.id).where(Job.owner_id==owner,Job.target.in_(ids)).limit(1)):
            raise AppError('MIGRATION_CHANGED','迁移后资料已经创建新任务，不能直接回滚，请使用恢复前备份。',409)
        # Children first; snapshots refer to docs only through IDs in JSON.
        remaining={r.id:r for r in s.scalars(select(Record).where(Record.owner_id==owner,Record.id.in_(ids)))}
        while remaining:
            parents={r.parent_id for r in remaining.values()}
            leaves=[r for r in remaining.values() if r.id not in parents]
            if not leaves:raise AppError('MIGRATION_RELATION','关联关系异常，不能回滚。',409)
            for row in leaves:s.delete(row);remaining.pop(row.id)
            s.flush()
        s.get(Preferences,owner).payload=batch.payload['prior_preferences'];batch.state='rolled_back'
    for key in keys:storage.delete(owner,key)
    return {'rolled_back':True}
