import json
import sys
from pathlib import Path
from ..parsers import parse_file
from ..core import AppError


def main():
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU,(30,30))
        except ImportError:pass
        parsed=parse_file(sys.argv[1],sys.argv[2])
        Path(sys.argv[3]).write_text(json.dumps({'ok':True,'result':parsed},ensure_ascii=False),encoding='utf8')
    except Exception as exc:
        Path(sys.argv[3]).write_text(json.dumps({'ok':False,'message':exc.message if isinstance(exc,AppError) else '文件解析失败。'}),encoding='utf8')


if __name__=='__main__':main()
