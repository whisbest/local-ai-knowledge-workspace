"""Operator backup/restore entry point shipped in the production image."""
import argparse
import hashlib
from pathlib import Path
from .config import Config
from .db import Store
from .backups import backup,restore_preflight,restore


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('backup').add_argument('--destination',required=True)
    restore_parser=sub.add_parser('restore')
    restore_parser.add_argument('--archive',required=True)
    restore_parser.add_argument('--confirm-sha256')
    args=parser.parse_args();store=Store(Config.from_env())
    try:
        if args.action=='backup':
            result=backup(store,args.destination)
            print(f"Platform backup created ({result['files']} files). Master key excluded.")
        elif args.confirm_sha256:
            restore(store,args.archive,args.confirm_sha256)
            print('Platform restored; prior safety backup retained.')
        else:
            path=Path(args.archive);restore_preflight(store,path)
            print('Preflight passed. Stop API/worker before restore. Confirm SHA256: '+hashlib.sha256(path.read_bytes()).hexdigest())
    finally:store.engine.dispose()


if __name__=='__main__':main()
