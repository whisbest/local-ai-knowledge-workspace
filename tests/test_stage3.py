import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
from app.main import create_app
from app.worker import Worker
from app.core import Record,Job,load
from app.parsers import chunk_spans
FIX=Path(__file__).parent/'fixtures'

def drain(store):
    worker=Worker(store)
    while id:=store.claim():worker.execute(id)

def upload(c,kb,names,decision='ask',key='first'):
    return c.post('/api/documents/upload',data={'kb_id':kb,'decision':decision,'request_key':key},files=[('files',(n,(FIX/n).read_bytes())) for n in names])

def test_batch_parsing_failure_isolation_duplicates_and_file_copy(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        kb=c.post('/api/knowledge-bases',json={'name':'样本'}).json()['id']
        r=upload(c,kb,['policy-a.txt','policy-b.md','notes.docx','two-pages.pdf','broken.pdf','scan-no-text.pdf']);assert r.status_code==200,r.text
        results=r.json();assert len(results)==6;drain(app.state.store)
        docs=c.get('/api/documents').json()['items'];assert len(docs)==6
        assert sum(d['state']=='parsed' for d in docs)==4;assert sum(d['state']=='failed' for d in docs)==2
        pdf=next(d for d in docs if d['format']=='pdf' and d['state']=='parsed')
        assert c.get('/api/documents/'+pdf['id']+'/file').content==(FIX/'two-pages.pdf').read_bytes()
        parsed=c.get('/api/documents/'+pdf['id']+'/parsed').json();assert [s['location']['page'] for s in parsed['spans']]==[1,2]
        chunks=c.get('/api/documents/'+pdf['id']+'/chunks').json()['items'];assert chunks and all(d['text'].strip() for d in chunks);assert {s['page'] for d in chunks for s in d['source_spans']}=={1,2}
        duplicate=upload(c,kb,['duplicate.txt'],key='duplicate').json();assert duplicate[0]['state']=='duplicate'
        assert upload(c,kb,['duplicate.txt'],'skip','skip').json()[0]['state']=='skipped'
        assert c.get('/api/documents').json()['total']==6
        assert upload(c,kb,['policy-a.txt'],'ask','first').json()==results # idempotent batch
        assert c.put('/api/settings',json={'chunk_length':30,'chunk_overlap':5}).status_code==200
        assert c.post('/api/documents/'+pdf['id']+'/reprocess').status_code==200;drain(app.state.store)
        assert c.get('/api/documents/'+pdf['id']+'/chunks').json()['total']>1
        failed=next(j for j in c.get('/api/jobs').json() if j['state']=='failed')
        assert failed['error'];assert c.post('/api/jobs/'+failed['id']+'/retry').json()['attempt']==2

def test_replace_delete_search_and_impact_confirmation(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        kb=c.post('/api/knowledge-bases',json={'name':'资料'}).json()['id'];id=upload(c,kb,['policy-a.txt']).json()[0]['document_id'];drain(app.state.store)
        result=upload(c,kb,['policy-a.txt'],'replace','replace').json()[0];assert result['document_id']==id;drain(app.state.store)
        assert c.get('/api/documents/'+id).json()['version']==2
        assert c.get('/api/documents?q=不存在').json()['total']==0
        p=c.get('/api/knowledge-bases/'+kb+'/impact').json();assert p['documents']==1
        assert c.delete('/api/knowledge-bases/'+kb+'?token=wrong').status_code==409
        assert c.delete('/api/knowledge-bases/'+kb+'?token='+p['token']).status_code==200
        assert c.get('/api/documents/'+id).status_code==404;assert c.get('/api/documents').json()['total']==0

def test_cross_page_chunk_overlap_is_deterministic():
    spans=[{'text':'abcdefghij','location':{'type':'page','page':1}},{'text':'klmnopqrst','location':{'type':'page','page':2}}]
    chunks=chunk_spans(spans,15,3);assert chunks==chunk_spans(spans,15,3);assert chunks[0]['text'][-3:]==chunks[1]['text'][:3]
    assert len(chunks[0]['source_spans'])==2
