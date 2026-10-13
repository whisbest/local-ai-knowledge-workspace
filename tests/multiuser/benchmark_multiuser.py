"""Three-user real PostgreSQL retrieval benchmark, excluding model HTTP latency."""
import json
import platform
import sys
import time
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import psutil
from sqlalchemy import create_engine,text,select,insert
from sqlalchemy.orm import sessionmaker
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from app.multiuser.config import Config
from app.multiuser.db import Store,User,Record,Model,Vector,uid
from app.multiuser.indexes import search_vectors


def main():
    values=json.loads((ROOT/'.runtime/multiuser-test.json').read_text(encoding='utf8'));url=values['database_url']
    schema='benchmark_'+uid();admin=create_engine(url,hide_parameters=True)
    with admin.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
    temp=tempfile.TemporaryDirectory(prefix='lkq-benchmark-');store=Store(Config(url,Path(temp.name)))
    store.engine.dispose();store.engine=create_engine(url,hide_parameters=True,connect_args={'options':'-csearch_path='+schema})
    store.Session=sessionmaker(store.engine,expire_on_commit=False);store.migrate()
    report={'environment':{'platform':platform.platform(),'python':platform.python_version(),'memory_gib':psutil.virtual_memory().total/1024**3,
                           'database':'PostgreSQL 17','dimensions':128,'batch':128},'runs':[]}
    try:
        for count in (1000,10000):
            setups=[]
            with store.session() as s:
                model=Model(scope='platform',purpose='embedding',name='benchmark',mode='api',endpoint='https://example.com',model='fixed',status='available',dimension=128,fingerprint='b'*64)
                s.add(model);s.flush()
                for n in range(3):
                    user=User(username=f'bench-{count}-{n}',password_hash='benchmark-not-login');s.add(user);s.flush()
                    doc=Record(owner_id=user.id,kind='document',state='ready',payload={'name':f'PRIVATE-{n}','format':'txt'});s.add(doc);s.flush()
                    version=Record(owner_id=user.id,kind='version',parent_id=doc.id);s.add(version);s.flush()
                    doc.payload={**doc.payload,'current_version_id':version.id}
                    index=Record(owner_id=user.id,kind='index',parent_id=doc.id,model_id=model.id,state='active',payload={'dimension':128,'fingerprint':'b'*64,'version_id':version.id});s.add(index);s.flush()
                    chunks=[{'id':uid(),'owner_id':user.id,'kind':'chunk','parent_id':version.id,'state':'active',
                             'payload':{'text':f'PRIVATE-{n}-{i}','source_spans':[{'type':'lines','start_line':i+1,'end_line':i+1}]}} for i in range(count)]
                    s.execute(insert(Record),chunks)
                    random=np.random.default_rng(42+n);vectors=random.normal(size=(count,128)).astype(np.float32);vectors/=np.linalg.norm(vectors,axis=1,keepdims=True)
                    rows=[{'id':uid(),'owner_id':user.id,'document_id':doc.id,'version_id':version.id,'index_id':index.id,'chunk_id':chunk['id'],
                           'fingerprint':'b'*64,'dimension':128,'values':vector.tolist()} for chunk,vector in zip(chunks,vectors)]
                    for offset in range(0,len(rows),500):s.execute(insert(Vector),rows[offset:offset+500])
                    setups.append((user.id,index.id,n))
            query=np.ones(128,dtype=np.float32);measurements=[]
            def retrieve(setup):
                owner,index_id,n=setup
                started=time.perf_counter()
                with store.session() as s:
                    index=s.get(Record,index_id);items=search_vectors(s,owner,[index],query,5)
                duration=time.perf_counter()-started
                assert len(items)==5 and all(item['file_name']==f'PRIVATE-{n}' for item in items)
                return duration
            with ThreadPoolExecutor(max_workers=3) as pool:
                for _ in range(10):measurements.extend(pool.map(retrieve,setups))
            p95=float(np.percentile(measurements,95))
            report['runs'].append({'chunks_per_user':count,'users':3,'samples':len(measurements),'p95_seconds':p95,
                    'max_seconds':max(measurements),'process_rss_mb':psutil.Process().memory_info().rss/1024**2,'target_seconds':2,'passed':p95<=2})
            print(json.dumps(report['runs'][-1]),flush=True)
        report['passed']=all(run['passed'] for run in report['runs'])
        path=ROOT/'.reports/multiuser/P11/benchmark.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2),encoding='utf8')
        if not report['passed']:raise SystemExit('Retrieval p95 exceeded 2 seconds; optimize before acceptance')
    finally:
        store.engine.dispose()
        with admin.begin() as c:c.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose();temp.cleanup()


if __name__=='__main__':main()
