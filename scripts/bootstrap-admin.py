"""Create the first administrator; accepts no password on the command line."""
import argparse
import getpass
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import select
from app.multiuser.config import Config
from app.multiuser.db import Store, User, Audit
from app.multiuser.auth import create_user


def bootstrap(store, username, password):
    from sqlalchemy import text
    with store.session() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(76241012)'))
        if s.scalar(select(User).where(User.role == 'admin')):
            raise ValueError('Administrator already exists; bootstrap never overwrites credentials')
        user = create_user(s, username, password, role='admin')
        s.add(Audit(actor_id=user.id, event='admin_bootstrapped'))
        return user.id


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--username', required=True)
    args = parser.parse_args()
    password = getpass.getpass('Initial administrator password (12+ characters): ')
    if password != getpass.getpass('Confirm password: '): raise SystemExit('Passwords differ')
    store = Store(Config.from_env())
    bootstrap(store, args.username, password)
    print('Administrator created. First login requires a new password.')
