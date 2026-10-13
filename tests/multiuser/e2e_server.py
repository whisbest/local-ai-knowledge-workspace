"""V2 browser fixture on a private PostgreSQL schema, with an actual worker."""
import base64
import json
import secrets
import sys
import uuid
import tempfile
import threading
import socket
from pathlib import Path
from contextlib import asynccontextmanager
import httpx
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.multiuser.config import Config
from app.multiuser.db import Store,Model
from app.multiuser.auth import create_user
from app.multiuser.main import create_app
from app.multiuser.worker import Worker
from app.multiuser.secrets import Vault

values=json.loads((ROOT/'.runtime/multiuser-test.json').read_text(encoding='utf8'))
url=values['database_url'];schema='browser_'+uuid.uuid4().hex
engine=create_engine(url,hide_parameters=True)
with engine.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
temporary=tempfile.TemporaryDirectory(prefix='lkq-multiuser-e2e-')
config=Config(url,Path(temporary.name),master_key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
              hosts=('127.0.0.1','localhost'),origins=('http://127.0.0.1:18771',),
              registration_enabled=True, mail_backend='local')
store=Store(config);store.engine.dispose()
store.engine=create_engine(url,hide_parameters=True,connect_args={'options':'-csearch_path='+schema})
store.Session=sessionmaker(store.engine,expire_on_commit=False);store.migrate()
with store.session() as s:
    for name in ('alice','bob','carol','admin','deletion','model-choice'):
        create_user(s,name,'Browser-'+name+'-2026!',role='admin' if name=='admin' else 'user',must_change=False)
    for purpose in ('chat','embedding'):
        model=Model(scope='platform',purpose=purpose,name='平台测试'+purpose,mode='api',endpoint='https://api.example.com/v1',model='fixture',
                    status='available',dimension=3 if purpose=='embedding' else None,fingerprint='a'*64 if purpose=='embedding' else None)
        s.add(model);s.flush();model.secret=Vault(config.master_key).encrypt('browser-fixture-only',model.id)
store.resolver=lambda host,port,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',port))]
def model_response(request):
    body=json.loads(request.content)
    if request.url.path.endswith('embeddings'):return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.,2.,3.]} for i,_ in enumerate(body['input'])]})
    if 'TASK-DELETION-FAILURE' in str(body.get('messages', [])):
        return httpx.Response(503,json={'error':{'message':'Intentional model failure for task deletion regression'}})
    return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'**私有知识**的结论来自资料 [1]。'}}]})+'\n\ndata: [DONE]\n\n')
store.model_transport=httpx.MockTransport(model_response)
worker=Worker(store);thread=threading.Thread(target=worker.run,daemon=True)
@asynccontextmanager
async def lifespan(app):
    thread.start()
    yield
    worker.stop_event.set();thread.join(timeout=10);store.engine.dispose()
    with engine.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
    engine.dispose();temporary.cleanup()
app=create_app(store=store);app.router.lifespan_context=lifespan
