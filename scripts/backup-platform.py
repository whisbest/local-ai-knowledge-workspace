import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.multiuser.config import Config
from app.multiuser.db import Store
from app.multiuser.backups import backup
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--destination',required=True);args=parser.parse_args()
    result=backup(Store(Config.from_env()),args.destination)
    print(f"Platform backup created ({result['files']} files). Master key excluded.")
