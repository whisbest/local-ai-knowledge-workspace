"""Private storage with bounded writes and transaction compensation."""
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4
from ..core import AppError


class Storage:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, owner, key):
        if not re.fullmatch(r'[a-f0-9]{32}', owner) or not re.fullmatch(r'[a-f0-9]{32}(?:\.[a-z0-9]{1,5})?', key):
            raise AppError('FILE_PATH', '文件标识不合法。')
        base = self.root / 'users' / owner
        candidate = base / key
        if candidate.resolve() != candidate or self.root not in candidate.resolve().parents:
            raise AppError('FILE_PATH', '文件路径越界。')
        # resolve() also rejects a linked user directory, including links within root.
        for parent in (self.root / 'users', base, candidate):
            if parent.is_symlink():
                raise AppError('FILE_PATH', '不能通过链接访问文件。')
        if candidate.is_file() and candidate.stat().st_nlink > 1:
            raise AppError('FILE_PATH', '不能通过文件别名访问文件。')
        return candidate

    def write(self, owner, key, chunks, limit):
        target = self.path(owner, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise AppError('FILE_EXISTS', '文件标识已存在。', 409)
        temporary = target.parent / ('.pending-' + uuid4().hex)
        size = 0
        digest = hashlib.sha256()
        try:
            with temporary.open('xb') as output:
                for chunk in chunks:
                    size += len(chunk)
                    if size > limit:
                        raise AppError('UPLOAD_LIMIT', '文件超过大小限制。', 413)
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            self.path(owner, key)  # recheck before committing a temporary file
            os.replace(temporary, target)
            target.chmod(0o600)
            return {'size': size, 'sha256': digest.hexdigest(), 'key': key}
        except OSError:
            raise AppError('STORAGE_WRITE','文件存储失败，请检查服务端空间与写入权限。',507)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self, owner, key):
        self.path(owner, key).unlink(missing_ok=True)

    def verify(self, owner, key, digest):
        return hashlib.sha256(self.path(owner, key).read_bytes()).hexdigest() == digest

    def compensate(self, session, owner, keys):
        from sqlalchemy import event
        @event.listens_for(session, 'after_rollback', once=True)
        def rollback(_):
            for key in keys:
                self.delete(owner, key)
