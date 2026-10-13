"""Operator-only logical backup/restore with a PostgreSQL maintenance barrier."""
import hashlib
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import text, select, delete, insert, DateTime
from sqlalchemy.orm import Session as DBSession
from ..core import AppError
from .db import Base,Job,uid
from .exports import validate_archive
from .storage import Storage


@contextmanager
def maintenance(store):
    with store.engine.connect() as conn:
        conn.execute(text("SET lock_timeout='30s'"))
        conn.execute(text('SELECT pg_advisory_lock(76241011)'))
        conn.commit()
        try:
            with conn.begin():
                if conn.scalar(select(Job.id).where(Job.state=='running').limit(1)):
                    raise AppError('BUSY','有活动 worker 任务，请停止 worker 并等待任务结束后维护。',409)
            yield conn
        finally:
            conn.execute(text('SELECT pg_advisory_unlock(76241011)'));conn.commit()


def database_rows(conn):
    return {table.name:[{key:(value.isoformat() if isinstance(value,datetime) else value) for key,value in dict(row).items()}
            for row in conn.execute(select(table)).mappings()] for table in Base.metadata.sorted_tables}


def write_snapshot(store,conn,destination):
    destination=Path(destination).resolve()
    if destination.exists():raise AppError('BACKUP_EXISTS','备份目标已存在，不能覆盖。',409)
    if (store.config.data_root/'users').resolve() in destination.parents:raise AppError('BACKUP_PATH','全站备份不能写入个人资料目录。',422)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.pending-'+uid());files={}
    try:
        with conn.begin():rows=database_rows(conn)
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            raw=json.dumps(rows,ensure_ascii=False).encode();archive.writestr('database.json',raw)
            files['database.json']={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            storage=Storage(store.config.data_root)
            for record in rows['records']:
                key=record['payload'].get('file_key') if record['kind'] in ('version','export') and record['state']!='deleted' else None
                if key:
                    owner=record['owner_id'];path=storage.path(owner,key)
                    if not path.is_file():raise AppError('BACKUP_MISSING','备份所需原文件缺失。',409)
                    relative='users/'+owner+'/'+key;raw=path.read_bytes();archive.writestr(relative,raw)
                    files[relative]={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            archive.writestr('manifest.json',json.dumps({'format':'lkq-platform','version':2,'schema':'mu0003',
                 'files':files,'master_key':'excluded; preserve independently'},ensure_ascii=False))
        temporary.replace(destination);destination.chmod(0o600)
    finally:temporary.unlink(missing_ok=True)
    return {'files':len(files),'path':str(destination)}


def backup(store,destination):
    with maintenance(store) as conn:return write_snapshot(store,conn,destination)


def decoded_rows(rows):
    if set(rows)!={table.name for table in Base.metadata.sorted_tables}:raise AppError('BACKUP_SCHEMA','数据库清单不兼容。',422)
    for table in Base.metadata.sorted_tables:
        if not isinstance(rows[table.name],list):raise AppError('BACKUP_SCHEMA','数据库记录格式错误。',422)
        for row in rows[table.name]:
            if set(row)!=set(table.columns.keys()):raise AppError('BACKUP_SCHEMA','数据库字段不兼容。',422)
            for col in table.columns:
                if isinstance(col.type,DateTime) and row[col.name] is not None:row[col.name]=datetime.fromisoformat(row[col.name])
    return rows


def insert_rows(conn,rows):
    for table in Base.metadata.sorted_tables:
        values=rows[table.name]
        if table.name=='records':
            remaining={r['id']:r for r in values};inserted=set()
            while remaining:
                ready=[r for r in remaining.values() if not r['parent_id'] or r['parent_id'] in inserted]
                if not ready:raise AppError('BACKUP_RELATION','记录父子关系断开或包含循环。',422)
                conn.execute(insert(table),ready)
                for row in ready:inserted.add(row['id']);remaining.pop(row['id'])
        elif values:conn.execute(insert(table),values)
        if table.name in ('audit_events','stream_events'):
            conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), COALESCE((SELECT MAX(id) FROM {table.name}),1), EXISTS(SELECT 1 FROM {table.name}))"))


def restore_preflight(store,path):
    manifest=validate_archive(path,'lkq-platform')
    if manifest.get('schema') not in ('mu0002', 'mu0003'):raise AppError('BACKUP_SCHEMA','迁移版本不兼容。',422)
    with zipfile.ZipFile(path) as archive:rows=json.loads(archive.read('database.json'))
    if manifest.get('schema') == 'mu0002':
        # Upgrade a verified pre-registration archive without touching its private data.
        for user in rows.get('users', []):
            user['email'] = None
        rows['email_challenges'] = []
    rows=decoded_rows(rows)
    for record in rows['records']:
        if record['kind'] in ('version','export') and record['state']!='deleted':
            relative='users/'+record['owner_id']+'/'+str(record['payload'].get('file_key',''))
            if relative not in manifest['files']:raise AppError('BACKUP_FILE','数据库引用的原文件不在备份清单中。',422)
            Storage(store.config.data_root).path(record['owner_id'],record['payload']['file_key'])
    from .secrets import Vault
    for model in rows['model_configs']:
        if model['secret']:Vault(store.config.master_key).decrypt(model['secret'],model['id'],model['key_version'])
    free=shutil.disk_usage(store.config.data_root.parent).free
    if free<sum(meta['size'] for meta in manifest['files'].values())*2:raise AppError('RESTORE_SPACE','恢复所需空间不足。',507)
    # Validate every row in an isolated PG schema including ownership triggers.
    schema='restore_'+uid()
    with store.engine.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    try:
        with store.engine.begin() as conn:
            conn.execute(text('SET LOCAL search_path TO '+schema));Base.metadata.create_all(conn)
            # Same constraint as the migration, scoped to the validation schema.
            conn.execute(text('''CREATE FUNCTION validate_record_model_owner() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.model_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM model_configs WHERE id=NEW.model_id AND (scope='platform' OR owner_id=NEW.owner_id)) THEN
                RAISE EXCEPTION 'Model ownership mismatch' USING ERRCODE='23514'; END IF; RETURN NEW; END $$'''))
            conn.execute(text('CREATE TRIGGER record_model_owner BEFORE INSERT OR UPDATE ON records FOR EACH ROW EXECUTE FUNCTION validate_record_model_owner()'))
            insert_rows(conn,rows)
    finally:
        with store.engine.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
    return manifest,rows


def restore(store,path,confirm_checksum=None,failpoint=None):
    path=Path(path).resolve();checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    if confirm_checksum!=checksum:raise AppError('CONFIRM','请预检备份并以 SHA256 明确确认恢复。',409)
    manifest,rows=restore_preflight(store,path)
    root=store.config.data_root;root.mkdir(parents=True,exist_ok=True)
    staged=root/('restore-stage-'+uid());staged.mkdir();rollback=root/('restore-rollback-'+uid())
    current=root/'users';swapped=False;committed=False
    restore_id=uid();journal=root/'restore-journal.json'
    try:
        with zipfile.ZipFile(path) as archive:
            for relative in manifest['files']:
                if relative.startswith('users/'):
                    target=staged/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(relative))
        with maintenance(store) as conn:
            safety=root/'platform-backups'/('before-restore-'+uid()+'.zip');write_snapshot(store,conn,safety)
            with conn.begin():
                for table in reversed(Base.metadata.sorted_tables):conn.execute(delete(table))
                insert_rows(conn,rows)
                marker=Base.metadata.tables['platform_state']
                conn.execute(delete(marker).where(marker.c.key=='restore_commit'))
                conn.execute(insert(marker).values(key='restore_commit',payload={'id':restore_id}))
                journal.write_text(json.dumps({'id':restore_id,'current':str(current),'rollback':str(rollback),'stage':str(staged)}),encoding='utf8')
                if current.exists():current.rename(rollback)
                (staged/'users').mkdir(exist_ok=True)
                (staged/'users').rename(current);swapped=True
                if failpoint:failpoint()
            committed=True
            journal.unlink(missing_ok=True)
        return {'restored':True,'rollback_backup':str(safety),'checksum':checksum}
    finally:
        if swapped and not committed:
            failed=root/('failed-restore-'+uid());current.rename(failed)
            if rollback.exists():rollback.rename(current)
            journal.unlink(missing_ok=True)
        # Keep rollback file set for operator diagnosis; no recursive deletion of live directories.


def recover_restore(store):
    journal=store.config.data_root/'restore-journal.json'
    if not journal.exists():return
    data=json.loads(journal.read_text(encoding='utf8'));root=store.config.data_root.resolve()
    current=Path(data['current']);rollback=Path(data['rollback']);stage=Path(data['stage'])
    if current!=root/'users' or rollback.parent!=root or stage.parent!=root:
        raise AppError('RESTORE_JOURNAL','恢复日志路径异常，需管理员处理。',503)
    with store.engine.begin() as conn:
        row=conn.execute(select(Base.metadata.tables['platform_state']).where(Base.metadata.tables['platform_state'].c.key=='restore_commit')).mappings().first()
    if not row or row['payload'].get('id')!=data['id']:
        if rollback.exists():
            if current.exists():current.rename(root/('failed-restore-'+uid()))
            rollback.rename(current)
    elif not current.exists():raise AppError('RESTORE_JOURNAL','数据库已恢复但文件缺失，需保留备份并人工修复。',503)
    journal.unlink(missing_ok=True)
