"""Operator recovery; requires local deployment configuration, never a public API."""
import argparse
import getpass
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from sqlalchemy import select
from app.multiuser.config import Config
from app.multiuser.db import Store,User,Audit
from app.multiuser.auth import HASHER,check_password,revoke
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--username',required=True);args=parser.parse_args()
    password=getpass.getpass('New temporary administrator password: ');check_password(password)
    if password!=getpass.getpass('Confirm: '):raise SystemExit('Passwords differ')
    store=Store(Config.from_env())
    with store.session() as s:
        user=s.scalar(select(User).where(User.username==args.username.casefold(),User.role=='admin').with_for_update())
        if not user:raise SystemExit('Administrator not found')
        user.password_hash=HASHER.hash(password);user.must_change=True;user.enabled=True;revoke(s,user.id)
        s.add(Audit(actor_id=user.id,event='operator_password_reset'))
    print('Temporary password reset; all old sessions revoked.')
