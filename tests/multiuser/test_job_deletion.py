import pytest
from sqlalchemy import select
from app.multiuser.db import Job, Record, Reservation, Event
from app.multiuser.chat import execute_generation
from app.multiuser.worker import Worker
from test_documents_isolation import upload
from test_chat_isolation import chat_model


def test_real_failed_document_can_delete_record_keep_file_and_reprocess(store,clients):
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'broken'}).json()['id']
    doc=upload(a,kb,b'not a PDF','broken.pdf').json()[0]['document_id']
    Worker(store).run_once()
    job=a.get('/api/jobs').json()[0];assert job['state']=='failed' and job['error']
    assert clients['bob'].delete('/api/jobs/'+job['id']).status_code==404
    before=a.get('/api/documents/'+doc).json();usage=a.get('/api/data/overview').json()
    assert a.delete('/api/jobs/'+job['id']).json()=={'deleted':True}
    assert a.get('/api/jobs').json()==[]
    after=a.get('/api/documents/'+doc).json()
    assert after['jobs']==[] and before['versions']==after['versions'] and after['state']=='failed'
    assert a.get('/api/documents/'+doc+'/file').content==b'not a PDF'
    assert a.get('/api/data/overview').json()['storage_bytes']==usage['storage_bytes']
    assert a.delete('/api/jobs/'+job['id']).status_code==404
    assert a.post('/api/documents/'+doc+'/reprocess').status_code==200


@pytest.mark.parametrize('state',['completed','failed','interrupted','cancelled'])
def test_terminal_answer_job_deletion_preserves_history_events_sources_and_usage(store,clients,embedding_fixture,state):
    a=clients['alice'];model,_=embedding_fixture;kb=a.post('/api/knowledge-bases',json={'name':'history','embedding_id':model}).json()['id']
    doc=upload(a,kb,b'Private knowledge for citations').json()[0]['document_id']
    from app.multiuser.documents import process_document
    process_document(store,a.get('/api/jobs').json()[0]['id'])
    conv=a.post('/api/conversations',json={}).json()['id']
    chat=chat_model(store)
    generation=a.post('/api/conversations/'+conv+'/generations',json={'question':'private','model_id':chat,'request_key':'answer'}).json()['id']
    job=next(j for j in a.get('/api/jobs').json() if j['target']==generation)
    execute_generation(store,job['id'])
    with store.session() as s:
        s.get(Job,job['id']).state=state
        s.add(Reservation(owner_id=s.get(Job,job['id']).owner_id,resource='platform_calls',request_id='delete-regression',amount=3,state='committed'))
    history=a.get('/api/conversations/'+conv+'/messages').json();sources=a.get('/api/generations/'+generation+'/sources').json()
    assert sources
    with store.session() as s:events=[(e.id,e.kind,e.payload) for e in s.scalars(select(Event).where(Event.generation_id==generation).order_by(Event.id))]
    usage=a.get('/api/data/overview').json()
    assert clients['bob'].delete('/api/jobs/'+job['id']).status_code==404
    assert a.delete('/api/jobs/'+job['id']).status_code==200
    assert a.get('/api/conversations/'+conv+'/messages').json()==history
    assert a.get('/api/generations/'+generation+'/sources').json()==sources
    assert a.get('/api/data/overview').json()['platform_calls']==usage['platform_calls']
    with store.session() as s:assert events==[(e.id,e.kind,e.payload) for e in s.scalars(select(Event).where(Event.generation_id==generation).order_by(Event.id))]
    assert a.post('/api/generations/'+generation+'/regenerate',json={'request_key':'regenerate','model_id':chat}).status_code==200


@pytest.mark.parametrize('state',['queued','running','unrecognized'])
def test_nonterminal_jobs_cannot_be_deleted(store,clients,state):
    a=clients['alice'];kb=a.post('/api/knowledge-bases',json={'name':'active'}).json()['id']
    upload(a,kb);job=a.get('/api/jobs').json()[0]
    with store.session() as s:s.get(Job,job['id']).state=state
    response=a.delete('/api/jobs/'+job['id'])
    assert response.status_code==409 and response.json()['code']=='JOB_ACTIVE'
    with store.session() as s:assert s.get(Job,job['id']).state==state
