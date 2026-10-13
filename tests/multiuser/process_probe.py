"""Isolated process probe used by cross-process queue tests."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.multiuser.config import Config
from app.multiuser.db import Store
from app.multiuser.worker import claim
from app.multiuser.quotas import reserve

values=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
store=Store(Config(values['url'],Path(values['root'])))
store.engine.dispose()
store.engine=create_engine(values['url'],hide_parameters=True,connect_args={'options':'-csearch_path='+values['schema']})
store.Session=sessionmaker(store.engine,expire_on_commit=False)
if sys.argv[2]=='claim':
    print(json.dumps(claim(store)))
elif sys.argv[2]=='claim-and-exit':
    # Simulate a process disappearing after claiming, before any external request.
    print(json.dumps(claim(store)),flush=True)
elif sys.argv[2]=='claim-external-and-exit':
    from app.multiuser.worker import mark_external
    pair=claim(store)
    if pair:mark_external(store,*pair)
    print(json.dumps(pair),flush=True)
elif sys.argv[2]=='run-once':
    from contextlib import redirect_stdout
    from app.multiuser.worker import Worker
    # Parser dependency diagnostics belong on stderr, leaving stdout a JSON protocol.
    with redirect_stdout(sys.stderr):result=Worker(store).run_once()
    print(json.dumps(result),flush=True)
else:
    from app.core import AppError
    try:
        with store.session() as s:reserve(s,values['owner'],'parallel','process-'+sys.argv[3],1,1)
        print('reserved')
    except AppError:print('limited')
store.engine.dispose()
