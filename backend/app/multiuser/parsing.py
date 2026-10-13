import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from uuid import uuid4
import psutil
from ..core import AppError


def bounded_parse(path,format,timeout=30,cancelled=lambda:False):
    if format=='docx':
        try:
            with zipfile.ZipFile(path) as archive:
                entries=archive.infolist()
                if len(entries)>10000 or sum(e.file_size for e in entries)>128*1024*1024 or any(e.file_size>max(1,e.compress_size)*1000 for e in entries):
                    raise AppError('ARCHIVE_LIMIT','文档解压展开量超过限制。',413)
        except zipfile.BadZipFile:raise AppError('PARSE_FAILED','DOCX 文件已损坏。',422)
    result=Path(path).parent/('.parse-'+uuid4().hex+'.json')
    env=dict(os.environ);backend=Path(__file__).resolve().parents[2]
    env['PYTHONPATH']=str(backend)
    process=subprocess.Popen([sys.executable,'-m','app.multiuser.parse_entry',str(path),format,str(result)],
                             env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    started=time.monotonic()
    try:
        while process.poll() is None:
            if cancelled():raise AppError('CANCELLED','任务已取消。',409)
            if time.monotonic()-started>timeout:raise AppError('PARSE_TIMEOUT','文件解析超过时间限制。',408)
            try:
                if psutil.Process(process.pid).memory_info().rss>768*1024*1024:raise AppError('PARSE_MEMORY','文件解析超过内存限制。',413)
            except psutil.NoSuchProcess:pass
            time.sleep(.03)
        if process.returncode or not result.exists() or result.stat().st_size>32*1024*1024:raise AppError('PARSE_FAILED','解析进程未能完成文件处理。',422)
        data=json.loads(result.read_text(encoding='utf8'))
        if not data['ok']:raise AppError('PARSE_FAILED',data['message'],422)
        return data['result']
    finally:
        if process.poll() is None:process.kill()  # only the child process created by this call
        process.wait(timeout=5);result.unlink(missing_ok=True)
