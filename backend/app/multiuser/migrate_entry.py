from .config import Config
from .db import Store

if __name__=='__main__':
    store=Store(Config.from_env())
    try:store.migrate();print('V2 PostgreSQL migrations complete.')
    finally:store.engine.dispose()
