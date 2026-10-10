from __future__ import annotations
import hashlib
import os
import sqlite3
import shutil
import zipfile
from pathlib import Path,PurePosixPath
from contextlib import contextmanager
from fastapi import APIRouter,UploadFile,File
from fastapi.responses import FileResponse
from .core import Record,Job,AppError,uid,dump,load,now

FORMAT_VERSION=1
MAX_ARCHIVE=2*1024**3

def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        while b:=f.read(1024*1024):digest.update(b)
    return digest.hexdigest()


def safe_remove(store,path):
    path=Path(path).resolve()
    if not path.is_relative_to(store.root/'state') or path==store.root/'state' or path==store.current:raise RuntimeError('Invalid temporary directory')
    if path.exists():shutil.rmtree(path)


@contextmanager
def maintenance(store):
    with store.gate:
        if store.locked or store.request_count>1:raise AppError('BUSY','其他请求正在进行，请稍后再试。',409)
        store.locked=True
    try:
        with store.session() as s:
            active=s.query(Job).filter_by(state='running').count()
            if active:raise AppError('BUSY','有任务正在运行，请等待完成或主动停止后再备份/恢复。',409)
        yield
    finally:
        with store.gate:store.locked=False


def snapshot(store,label='manual'):
    id=uid();temporary=store.root/'state/staging'/('backup-'+id);temporary.mkdir()
    out=store.root/'backups'/(id+'.zip')
    try:
        source=sqlite3.connect(str(store.current/'app.sqlite'));destination=sqlite3.connect(str(temporary/'app.sqlite'))
        try:source.backup(destination)
        finally:source.close();destination.close()
        db=sqlite3.connect(str(temporary/'app.sqlite'))
        models=[]
        for id_,kind,payload in db.execute('SELECT id,kind,payload FROM records'):
            values=load(payload)
            if kind=='model':
                values.pop('secret_ref',None);models.append({k:values.get(k) for k in ('purpose','mode','model','dimension','fingerprint')})
            values.pop('key',None);values.pop('api_key',None)
            db.execute('UPDATE records SET payload=? WHERE id=?',(dump(values),id_))
        db.commit();db.close()
        for folder in ('documents','parsed'):
            shutil.copytree(store.current/folder,temporary/folder)
        files={str(p.relative_to(temporary)).replace('\\','/'):{'sha256':sha(p),'size':p.stat().st_size} for p in temporary.rglob('*') if p.is_file()}
        manifest={'format_version':FORMAT_VERSION,'schema_revision':'0001','created_at':now(),'label':label,'files':files,'models':models,'excluded':['API keys','model weights','logs','temporary files','other backups']}
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',dump(manifest))
            for relative in files:archive.write(temporary/relative,relative)
        return {'id':out.stem,'created_at':manifest['created_at'],'size':out.stat().st_size,'label':label}
    except OSError:out.unlink(missing_ok=True);raise AppError('BACKUP_IO','备份写入失败，请检查磁盘空间和目录权限。',500)
    finally:safe_remove(store,temporary)


def validate_relations(path):
    try:
        conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        conn.execute('PRAGMA trusted_schema=OFF')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError()
        if conn.execute('SELECT version_num FROM alembic_version').fetchone()[0]!='0001':raise AppError('BACKUP_VERSION','备份数据库版本不兼容。')
        rows={id:{'kind':kind,'parent':parent,'state':state,'payload':load(payload)} for id,kind,parent,state,payload in conn.execute('SELECT id,kind,parent_id,state,payload FROM records')}
        required={'records','vectors','jobs','idempotency','stream_events','alembic_version'}
        if not required.issubset({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}):raise ValueError()
        if 'preferences' not in rows:raise ValueError()
        def refer(id,kind):
            if id not in rows or rows[id]['kind']!=kind:raise ValueError()
        parents={'document':'kb','version':'document','chunk':'version','index':'document','message':'conversation','generation':'conversation','source':'generation'}
        for id,r in rows.items():
            p=r['payload'];kind=r['kind']
            if kind in parents:refer(r['parent'],parents[kind])
            if kind=='document':refer(p['current_version_id'],'version')
            if kind=='generation':refer(p['user_message_id'],'message');refer(p['assistant_message_id'],'message');refer(p['model_id'],'model')
            if kind=='version' and r['state']!='deleted':
                for key in ('source_path','parsed_path'):
                    relative=p.get(key)
                    if relative:
                        target=(path.parent/relative).resolve()
                        if not target.is_relative_to(path.parent) or not target.is_file():raise ValueError()
        for doc,version,generation,chunk,dimension,values in conn.execute('SELECT document_id,version_id,generation_id,chunk_id,dimension,"values" FROM vectors'):
            refer(doc,'document');refer(version,'version');refer(generation,'index');refer(chunk,'chunk')
            if rows[chunk]['parent']!=version or len(load(values))!=dimension:raise ValueError()
        for generation, in conn.execute('SELECT DISTINCT generation_id FROM stream_events'):refer(generation,'generation')
        return {'documents':sum(r['kind']=='document' and r['state']!='deleted' for r in rows.values()),'conversations':sum(r['kind']=='conversation' and r['state']=='active' for r in rows.values()),'models':[r['payload'] for r in rows.values() if r['kind']=='model']}
    except AppError:raise
    except Exception:raise AppError('BACKUP_RELATIONS','备份数据库损坏或资料关联不完整。')
    finally:
        if 'conn' in locals():conn.close()


def validate_archive(store,archive_path):
    id=uid();staging=store.root/'state/staging'/id;current=staging/'current';current.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos=archive.infolist();names=[i.filename for i in infos]
            if len(names)!=len(set(names)) or len(names)>10000 or sum(i.file_size for i in infos)>MAX_ARCHIVE:raise AppError('ARCHIVE_LIMIT','备份重复文件或展开大小超限（最多 2GB/10000 文件）。')
            for info in infos:
                p=PurePosixPath(info.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in info.filename or ':' in info.filename or (info.external_attr>>16)&0o170000==0o120000:raise AppError('ARCHIVE_PATH','备份包含不安全路径。')
            if archive.getinfo('manifest.json').file_size>5*1024*1024:raise ValueError()
            manifest=load(archive.read('manifest.json').decode('utf8'))
            if manifest['format_version']!=FORMAT_VERSION:raise AppError('BACKUP_VERSION','备份数据格式版本不兼容。')
            files=manifest['files']
            if set(files)|{'manifest.json'}!=set(names) or 'app.sqlite' not in files:raise ValueError()
            for relative,expected in files.items():
                if relative!='app.sqlite' and not relative.startswith(('documents/','parsed/')):raise AppError('ARCHIVE_PATH','备份包含不允许的数据目录。')
                target=current/relative;target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(relative) as source,target.open('wb') as destination:shutil.copyfileobj(source,destination,1024*1024)
                if target.stat().st_size!=expected['size'] or sha(target)!=expected['sha256']:raise AppError('BACKUP_CHECKSUM','备份文件校验失败，文件可能已损坏。')
        counts=validate_relations(current/'app.sqlite')
        token=uid();preview={'id':id,'token':token,'manifest':manifest,'counts':counts,'state':'validated','message':'覆盖当前文档、索引、对话和非敏感设置。API 密钥与模型权重不会导入，API 配置需重新填写密钥。'}
        (staging/'preview.json').write_text(dump(preview),encoding='utf8');return preview
    except AppError:safe_remove(store,staging);raise
    except Exception:safe_remove(store,staging);raise AppError('BACKUP_INVALID','备份文件损坏或格式不合法。')


def journal_write(store,id,phase):
    path=store.root/'state/recovery.json';temp=path.with_suffix('.tmp')
    with temp.open('w',encoding='utf8') as f:f.write(dump({'id':id,'phase':phase}));f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def commit_restore(store,id,token,fault=None):
    if not id.isalnum():raise AppError('RESTORE_ID','恢复标识不合法。')
    staging=store.root/'state/staging'/id;file=staging/'preview.json'
    if not file.exists():raise AppError('RESTORE_ID','请先上传并校验备份。')
    preview=load(file.read_text(encoding='utf8'))
    if preview['token']!=token or preview['state']!='validated':raise AppError('CONFIRM','恢复确认无效，请重新校验。')
    with maintenance(store):
        # Revalidate the immutable staging snapshot immediately before overwriting.
        validate_relations(staging/'current/app.sqlite')
        pre=snapshot(store,'pre-restore')
        old=store.root/'state/rollback'/id
        journal_write(store,id,'prepared');store.engine.dispose()
        try:
            store.current.rename(old);journal_write(store,id,'old_moved')
            if fault:fault('old_moved')
            (staging/'current').rename(store.current);journal_write(store,id,'new_moved')
            if fault:fault('new_moved')
            store.initialize()
            with store.session() as s:
                for model in s.query(Record).filter_by(kind='model'):
                    p=load(model.payload)
                    if p['mode']=='api':p['restored_without_key']=True;p['test_status']='missing_key';model.payload=dump(p)
            store.recover();journal_write(store,id,'committed')
            preview['state']='completed';preview['pre_restore_backup']=pre['id'];file.write_text(dump(preview),encoding='utf8')
            (store.root/'state/recovery.json').unlink();return {'state':'completed','pre_restore_backup':pre['id']}
        except Exception:
            store.engine.dispose()
            if old.exists():
                if store.current.exists():store.current.rename(store.root/'state/staging'/('failed-'+uid()))
                old.rename(store.current)
            store.initialize();(store.root/'state/recovery.json').unlink(missing_ok=True)
            raise AppError('RESTORE_FAILED','恢复失败，已回滚到原数据。恢复前备份保留。',500)


def mount_backups(app,store):
    router=APIRouter(prefix='/api')
    @router.get('/backups')
    def backups():return [{'id':p.stem,'size':p.stat().st_size,'created_at':p.stat().st_mtime} for p in sorted((store.root/'backups').glob('*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)]
    @router.post('/backups')
    def create():
        with maintenance(store):return snapshot(store)
    @router.get('/backups/{id}/download')
    def download(id:str):
        if not id.isalnum() or not (store.root/'backups'/(id+'.zip')).is_file():raise AppError('NOT_FOUND','备份不存在。',404)
        return FileResponse(store.root/'backups'/(id+'.zip'),filename='knowledge-backup-'+id+'.zip')
    @router.post('/restores/validate')
    async def validate(file:UploadFile=File(...)):
        staging=store.root/'state/staging'/('archive-'+uid());size=0
        try:
            with staging.open('wb') as out:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>MAX_ARCHIVE:raise AppError('ARCHIVE_LIMIT','备份上传最多 2GB。')
                    out.write(chunk)
            return validate_archive(store,staging)
        finally:staging.unlink(missing_ok=True);await file.close()
    @router.post('/restores/commit')
    def commit(body:dict):return commit_restore(store,body.get('id',''),body.get('token',''))
    @router.get('/restores/{id}')
    def status(id:str):
        if not id.isalnum():raise AppError('RESTORE_ID','恢复标识不合法。')
        file=store.root/'state/staging'/id/'preview.json'
        if not file.is_file():raise AppError('NOT_FOUND','恢复记录不存在。',404)
        return load(file.read_text(encoding='utf8'))
    app.include_router(router)
