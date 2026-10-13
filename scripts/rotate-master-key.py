import getpass
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from sqlalchemy import select
from app.multiuser.config import Config
from app.multiuser.db import Store,Model,Audit
from app.multiuser.secrets import Vault
from app.multiuser.backups import maintenance
if __name__=='__main__':
    store=Store(Config.from_env());old=Vault(store.config.master_key)
    key=getpass.getpass('New fixed 32-byte base64 master key: ');new=Vault(key)
    if key!=getpass.getpass('Confirm: '):raise SystemExit('Keys differ')
    with maintenance(store) as conn:
        from sqlalchemy.orm import Session
        with Session(bind=conn) as s,s.begin():
            for model in s.scalars(select(Model).with_for_update()):
                if model.secret:
                    value=old.decrypt(model.secret,model.id,model.key_version);model.key_version+=1
                    model.secret=new.encrypt(value,model.id,model.key_version)
            s.add(Audit(event='master_key_rotated'))
    print('Ciphertexts rotated. Update private configuration to the new key before starting API/worker; preserve old key backup.')
