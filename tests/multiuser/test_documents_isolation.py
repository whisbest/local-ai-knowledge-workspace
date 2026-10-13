from pathlib import Path
import pytest
from sqlalchemy import select
from app.multiuser.db import Job, Record
from app.multiuser.documents import process_document


def upload(client,kb,content=b'private',filename='same.txt',key='same',decision='ask'):
    return client.post('/api/documents/upload',data={'kb_id':kb,'request_key':key,'decision':decision},
                       files={'files':(filename,content)})


def test_document_task_id_matrix_same_names_and_idempotency(store,clients,identities):
    a,b=clients['alice'],clients['bob']
    ka=a.post('/api/knowledge-bases',json={'name':'A-only'}).json()['id']
    kb=b.post('/api/knowledge-bases',json={'name':'B-only'}).json()['id']
    ra=upload(a,ka,identities['alice']['marker'].encode());rb=upload(b,kb,identities['bob']['marker'].encode())
    assert ra.status_code==rb.status_code==200,ra.text+rb.text
    da,db=ra.json()[0]['document_id'],rb.json()[0]['document_id'];assert da!=db
    assert a.get('/api/dashboard').json()['documents']==b.get('/api/dashboard').json()['documents']==1
    assert 'A-only' not in b.get('/api/knowledge-bases').text
    assert upload(a,ka,identities['alice']['marker'].encode()).json()==ra.json()
    assert upload(a,ka,b'different').status_code==409
    assert a.get('/api/documents').json()['total']==1
    ja=a.get('/api/jobs').json()[0]['id']
    for path in [f'/api/documents/{da}',f'/api/documents/{da}/file',f'/api/documents/{da}/parsed',
                 f'/api/documents/{da}/chunks',f'/api/documents/{da}/versions',f'/api/knowledge-bases/{ka}/impact']:
        response=b.get(path);assert response.status_code==404,(path,response.text)
        assert identities['alice']['marker'] not in response.text
    for method,path,kwargs in [('delete',f'/api/documents/{da}',{}),('post',f'/api/documents/{da}/reprocess',{}),
       ('post',f'/api/jobs/{ja}/cancel',{}),('post',f'/api/jobs/{ja}/retry',{}),('delete',f'/api/jobs/{ja}',{}),
       ('patch',f'/api/knowledge-bases/{ka}',{'json':{'name':'hack'}}),
       ('post','/api/indexes/rebuild',{'json':{'document_ids':[db,da]}})]:
        assert getattr(b,method)(path,**kwargs).status_code==404,path
    assert upload(b,ka,b'hack',key='cross').status_code==404
    assert b.post('/api/retrieval',json={'question':'test','scope':{'type':'documents','ids':[db,da]}}).status_code==404
    assert upload(a,ka,b'changed',key='replace',decision='replace').status_code==200
    assert a.get(f'/api/documents/{da}/file').content==b'changed'
    assert b.get(f'/api/documents/{db}/file').content==identities['bob']['marker'].encode()
    assert a.delete(f'/api/documents/{da}').status_code==200
    assert a.get(f'/api/documents/{da}/file').status_code==404
    assert b.get(f'/api/documents/{db}/file').status_code==200
    assert a.post('/api/data/clear',json={'confirm':True,'scope':'all'}).status_code==200
    assert b.get(f'/api/documents/{db}/file').content==identities['bob']['marker'].encode()


@pytest.mark.parametrize('filename',['policy-a.txt','notes.docx','two-pages.pdf','study.md'])
def test_all_formats_private_processing_and_source_locations(store,clients,filename,embedding_fixture):
    fixtures=Path(__file__).resolve().parents[1]/'fixtures'
    content=b'# Study\n\nA private note.' if filename=='study.md' else (fixtures/filename).read_bytes()
    model,_=embedding_fixture
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'Formats','embedding_id':model}).json()['id']
    response=upload(a,kb,content,filename);assert response.status_code==200,response.text
    id=response.json()[0]['document_id'];job=a.get('/api/jobs').json()[0]['id']
    process_document(store,job)
    parsed=a.get(f'/api/documents/{id}/parsed');chunks=a.get(f'/api/documents/{id}/chunks')
    assert parsed.status_code==200 and parsed.json()['spans']
    assert chunks.json()['items'][0]['source_spans']
    assert a.get(f'/api/documents/{id}/file').content==content
    assert clients['bob'].get('/api/documents').json()['total']==0
    assert a.get(f'/api/documents/{id}').json()['state']=='ready'
    assert a.post('/api/retrieval',json={'question':'private','scope':{'type':'documents','ids':[id]}}).json()['items']
    from test_chat_isolation import chat_model
    from app.multiuser.chat import execute_generation
    chat=chat_model(store);conv=a.post('/api/conversations',json={}).json()['id']
    generation=a.post(f'/api/conversations/{conv}/generations',json={'question':'private','request_key':'format-answer','model_id':chat,'scope':{'type':'documents','ids':[id]}}).json()['id']
    execute_generation(store,next(j['id'] for j in a.get('/api/jobs').json() if j['target']==generation))
    assert a.get('/api/generations/'+generation).json()['state']=='completed'
    source=a.get('/api/generations/'+generation+'/sources').json()[0]
    assert source['source_spans'] and source['format']==Path(filename).suffix[1:]
    # A second version must invalidate the first index and keep its historical citation.
    replacement=upload(a,kb,content+b'\n' if filename.endswith(('.txt','.md')) else content,filename,key='replacement',decision='replace')
    assert replacement.status_code==200,replacement.text
    assert a.post('/api/retrieval',json={'question':'private','scope':{'type':'documents','ids':[id]}}).json()['items']==[]
    process_document(store,a.get('/api/jobs').json()[0]['id'])
    assert a.get(f'/api/documents/{id}').json()['state']=='ready'
    assert len(a.get(f'/api/documents/{id}/versions').json())==2
    assert a.get('/api/generations/'+generation+'/sources').json()[0]['source_status']=='replaced'
    assert a.delete(f'/api/documents/{id}').status_code==200
    assert a.get(f'/api/documents/{id}/file').status_code==404
    assert a.post('/api/retrieval',json={'question':'private'}).json()['items']==[]
    assert a.get('/api/generations/'+generation+'/sources').json()[0]['source_status']=='deleted'


def test_preferences_do_not_change_kb_binding_or_other_user(store,clients):
    a,b=clients['alice'],clients['bob'];prefs=b.get('/api/settings').json()
    kb=a.post('/api/knowledge-bases',json={'name':'Stable profile'}).json()
    assert a.put('/api/settings',json={'chunk_length':100,'chunk_overlap':10,'answer_length':'long'}).status_code==200
    assert b.get('/api/settings').json()==prefs
    assert a.get('/api/knowledge-bases').json()[0]['profile']==kb['profile']
    assert a.put('/api/settings',json={'default_kb':b.post('/api/knowledge-bases',json={'name':'B'}).json()['id']}).status_code==404


def test_bad_format_bounded_upload_and_atomic_failure(store,clients):
    from dataclasses import replace
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'limits'}).json()['id']
    store.config=replace(store.config,upload_bytes=10)
    assert upload(a,kb,b'x'*11).status_code==413
    assert upload(a,kb,b'bad',filename='exploit.exe').status_code==422
    assert a.get('/api/dashboard').json()['documents']==0
    assert not list(store.config.data_root.rglob('*.txt'))
    assert upload(a,kb,b'a',filename='../../same.txt',key='safe').status_code==200
    assert a.get('/api/documents').json()['items'][0]['name']=='same.txt'
