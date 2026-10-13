import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath
from datetime import timedelta
from fastapi import Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from ..core import AppError
from .auth import identity
from .db import Record, Preferences, now, uid
from .tenancy import require_owned
from .storage import Storage
from .documents import lock_owner


def strip_private(payload):
    # Model selection IDs are references, never connection parameters or credentials.
    forbidden={'secret','secret_ref','api_key','key','file_key','source_path','parsed_path','endpoint','host_path','container_path'}
    if isinstance(payload,dict):return {k:strip_private(v) for k,v in payload.items() if k not in forbidden}
    if isinstance(payload,list):return [strip_private(v) for v in payload]
    return payload


def create_export(store,owner):
    storage=Storage(store.config.data_root);export_id=uid();file_key=export_id+'.zip'
    # Serialization under the per-user lock keeps mutation of this user's files out.
    with store.session() as s:
        lock_owner(s,owner)
        records=list(s.scalars(select(Record).where(Record.owner_id==owner,Record.kind.in_(['kb','document','version','chunk','conversation','message','generation','source']),Record.state!='deleted').order_by(Record.created_at)))
        files={};output=io.BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            rows=[]
            for record in records:
                data={'id':record.id,'kind':record.kind,'parent_id':record.parent_id,'state':record.state,
                      'created_at':record.created_at.isoformat(),'updated_at':record.updated_at.isoformat(),'payload':strip_private(record.payload)}
                if record.kind=='version':
                    path=storage.path(owner,record.payload['file_key'])
                    if not path.is_file():raise AppError('EXPORT_MISSING','部分原文件缺失，导出未完成。',409)
                    content=path.read_bytes();relative='files/'+record.id+'.'+record.payload['format']
                    files[relative]={'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}
                    archive.writestr(relative,content);data['payload']['export_file']=relative
                rows.append(data)
            raw=json.dumps({'records':rows,'preferences':strip_private(s.get(Preferences,owner).payload)},ensure_ascii=False).encode()
            archive.writestr('workspace.json',raw);files['workspace.json']={'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
            archive.writestr('manifest.json',json.dumps({'format':'lkq-personal','version':2,'created_at':now().isoformat(),
                 'files':files,'excluded':['API keys','account credentials','platform settings','other users','vectors']},ensure_ascii=False))
        raw=output.getvalue()
        storage.write(owner,file_key,[raw],2*1024**3)
        storage.compensate(s,owner,[file_key])
        row=Record(id=export_id,owner_id=owner,kind='export',state='completed',payload={
            'file_key':file_key,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'expires_at':(now()+timedelta(days=7)).isoformat()})
        s.add(row)
    return {'id':export_id,'size':len(raw),'expires_at':row.payload['expires_at']}


def validate_archive(path,expected):
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if len(entries)>50000 or sum(e.file_size for e in entries)>4*1024**3:
            raise AppError('ARCHIVE_LIMIT','备份展开量超过限制。',413)
        names=[entry.filename for entry in entries]
        if len(names)!=len(set(names)):raise AppError('ARCHIVE','备份包含重复路径。',422)
        for entry in entries:
            relative=PurePosixPath(entry.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename or ':' in entry.filename or (entry.external_attr>>16)&0o170000==0o120000:
                raise AppError('ARCHIVE','备份包含不安全路径。',422)
        try:manifest=json.loads(archive.read('manifest.json'))
        except (KeyError,ValueError):raise AppError('ARCHIVE','备份清单缺失或损坏。',422)
        if manifest.get('format')!=expected or manifest.get('version')!=2:raise AppError('ARCHIVE_VERSION','备份格式或版本不兼容。',422)
        files=manifest.get('files')
        if not isinstance(files,dict) or set(names)!=set(files)|{'manifest.json'}:raise AppError('ARCHIVE','备份文件清单不一致。',422)
        for name,meta in files.items():
            raw=archive.read(name)
            if len(raw)!=meta['size'] or hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise AppError('CHECKSUM','备份校验失败。',422)
        return manifest


def mount(app,store):
    storage=Storage(store.config.data_root)
    @app.post('/api/exports')
    def export(request:Request):return create_export(store,identity(request).id)

    @app.get('/api/exports')
    def exports(request:Request):
        with store.session() as s:return [{'id':r.id,'size':r.payload['size'],'expires_at':r.payload['expires_at']} for r in s.scalars(select(Record).where(Record.owner_id==identity(request).id,Record.kind=='export').order_by(Record.created_at.desc()))]

    @app.get('/api/exports/{id}/file')
    def file(id:str,request:Request):
        from datetime import datetime
        owner=identity(request).id
        with store.session() as s:
            row=require_owned(s,owner,id,'export')
            if datetime.fromisoformat(row.payload['expires_at'])<now():raise AppError('EXPORT_EXPIRED','导出已过期，请重新导出。',410)
            path=storage.path(owner,row.payload['file_key'])
            if not path.is_file() or not storage.verify(owner,row.payload['file_key'],row.payload['sha256']):raise AppError('EXPORT_FILE','导出文件缺失或校验失败。',409)
            return FileResponse(path,filename='knowledge-workspace-'+id+'.zip',headers={'Cache-Control':'no-store'})
