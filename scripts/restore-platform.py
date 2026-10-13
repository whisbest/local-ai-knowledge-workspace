import argparse
import hashlib
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.multiuser.config import Config
from app.multiuser.db import Store
from app.multiuser.backups import restore_preflight,restore
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--archive',required=True);parser.add_argument('--confirm-sha256');args=parser.parse_args()
    store=Store(Config.from_env());path=Path(args.archive)
    if args.confirm_sha256:restore(store,path,args.confirm_sha256);print('Platform restored; prior safety backup retained.')
    else:
        manifest,rows=restore_preflight(store,path)
        print('Preflight passed. Stop API and worker before restoring. Confirm SHA256: '+hashlib.sha256(path.read_bytes()).hexdigest())
