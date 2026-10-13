import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.multiuser.config import Config
from app.multiuser.db import Store
from app.multiuser.migration import migrate,rollback
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source');parser.add_argument('--owner',required=True)
    parser.add_argument('--document',action='append',default=[]);parser.add_argument('--include-conversations',action='store_true')
    parser.add_argument('--apply',action='store_true');parser.add_argument('--rollback-batch');args=parser.parse_args()
    store=Store(Config.from_env())
    result=rollback(store,args.owner,args.rollback_batch) if args.rollback_batch else migrate(store,args.source,args.owner,args.document,args.include_conversations,dry_run=not args.apply)
    print(json.dumps(result,ensure_ascii=False))
