import asyncio
from sqlalchemy import select
from starlette.requests import Request
from app.multiuser.db import Model, Job, Record, Session
from app.multiuser.chat import execute_generation
from app.multiuser.auth import authenticate, COOKIE, token_hash
from app.multiuser.main import create_app
from app.multiuser.documents import process_document
from test_documents_isolation import upload


def chat_model(store):
    with store.session() as s:
        row=Model(scope='platform',purpose='chat',name='Chat fixture',mode='api',endpoint='https://example.com/v1',model='fixture',status='available')
        s.add(row);s.flush();return row.id


def test_conversation_generation_idempotency_sources_events_and_context(store,clients,identities,embedding_fixture):
    embedding,requests=embedding_fixture;chat=chat_model(store);generated={};docs={};convs={}
    for name in ('alice','bob'):
        c=clients[name];kb=c.post('/api/knowledge-bases',json={'name':name,'embedding_id':embedding}).json()['id']
        docs[name]=upload(c,kb,identities[name]['marker'].encode()).json()[0]['document_id']
        process_document(store,c.get('/api/jobs').json()[0]['id'])
        conv=c.post('/api/conversations',json={'request_key':'same','title':name}).json()['id'];convs[name]=conv
        r=c.post(f'/api/conversations/{conv}/generations',json={'question':'private','model_id':chat,'request_key':'same','scope':{'type':'all'}})
        assert r.status_code==200,r.text;generated[name]=r.json()['id']
        assert c.post(f'/api/conversations/{conv}/generations',json={'question':'private','model_id':chat,'request_key':'same','scope':{'type':'all'}}).json()==r.json()
        assert c.post(f'/api/conversations/{conv}/generations',json={'question':'changed','model_id':chat,'request_key':'same'}).status_code==409
        job=next(j for j in c.get('/api/jobs').json() if j['target']==generated[name])
        execute_generation(store,job['id'])
        assert c.get('/api/generations/'+generated[name]).json()['state']=='completed'
        sources=c.get('/api/generations/'+generated[name]+'/sources').json()
        assert identities[name]['marker'] in str(sources)
    a,b=clients['alice'],clients['bob'];ga=generated['alice'];ca=convs['alice']
    for path in [f'/api/conversations/{ca}/messages',f'/api/generations/{ga}',f'/api/generations/{ga}/sources',f'/api/generations/{ga}/events?stream=false']:
        r=b.get(path);assert r.status_code==404,(path,r.text)
        assert identities['alice']['marker'] not in r.text
    for method,path,body in [('post',f'/api/generations/{ga}/stop',{}),('post',f'/api/generations/{ga}/regenerate',{'request_key':'x','model_id':chat}),
                            ('delete',f'/api/conversations/{ca}',{}),('patch',f'/api/conversations/{ca}',{'title':'hacked'})]:
        assert getattr(b,method)(path,json=body).status_code==404 if method!='delete' else b.delete(path).status_code==404
    events=a.get(f'/api/generations/{ga}/events?stream=false').text
    assert 'event: sources' in events and 'event: done' in events
    later=a.get(f'/api/generations/{ga}/events?stream=false&after=1').text
    assert 'id: 1\n' not in later
    chats=[body for path,body in requests if path.endswith('chat/completions')]
    assert len(chats)==2
    assert identities['alice']['marker'] not in str(chats[-1])
    assert identities['bob']['marker'] in str(chats[-1])
    assert a.delete('/api/documents/'+docs['alice']).status_code==200
    historic=a.get(f'/api/generations/{ga}/sources').json()
    assert historic[0]['source_status']=='deleted' and identities['alice']['marker'] in str(historic)
    assert a.get('/api/documents/'+docs['alice']+'/file').status_code==404


def test_no_sources_stop_regenerate_and_queue_snapshot(store,clients,embedding_fixture):
    chat=chat_model(store);a=clients['alice'];conv=a.post('/api/conversations',json={}).json()['id']
    generated=a.post(f'/api/conversations/{conv}/generations',json={'question':'unavailable','model_id':chat,'request_key':'one'}).json()['id']
    job=a.get('/api/jobs').json()[0]['id'];execute_generation(store,job)
    history=a.get(f'/api/conversations/{conv}/messages').json()
    assert '不足' in history['messages'][-1]['content']
    retry=a.post(f'/api/generations/{generated}/regenerate',json={'request_key':'two','model_id':chat})
    assert retry.status_code==200,retry.text
    id=retry.json()['id'];assert a.post(f'/api/generations/{id}/stop').json()['state']=='stopped'
    execute_generation(store,a.get('/api/jobs').json()[0]['id'])
    assert a.get(f'/api/generations/{id}').json()['state']=='stopped'


def test_sse_rechecks_session_between_events(store,clients,accounts):
    a=clients['alice'];chat=chat_model(store);conv=a.post('/api/conversations',json={}).json()['id']
    generation=a.post(f'/api/conversations/{conv}/generations',json={'question':'test','request_key':'stream','model_id':chat}).json()['id']
    cookie=a.cookies.get(COOKIE);app=create_app(store=store)
    endpoint=next(route.endpoint for route in app.routes if getattr(route,'path',None)=='/api/generations/{id}/events')
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    request=Request({'type':'http','method':'GET','path':f'/api/generations/{generation}/events',
                     'headers':[(b'cookie',f'{COOKIE}={cookie}'.encode())],'query_string':b'','server':('testserver',80)},receive)
    request.state.identity=authenticate(store,cookie)
    response=endpoint(generation,request)
    async def check():
        iterator=response.body_iterator
        first=await anext(iterator);assert 'queued' in first
        with store.session() as s:s.get(Session,token_hash(cookie)).revoked=True
        tail=[]
        async for item in iterator:tail.append(item)
        assert '"code":"AUTH"' in ''.join(tail)
    asyncio.run(check())


def test_stop_waits_for_worker_job_lock_without_generation_deadlock(store,clients):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    a=clients['alice'];chat=chat_model(store);conv=a.post('/api/conversations',json={}).json()['id']
    generation=a.post(f'/api/conversations/{conv}/generations',json={'question':'test','request_key':'lock-order','model_id':chat}).json()['id']
    locked=threading.Event();release=threading.Event()
    def worker_transaction():
        with store.session() as s:
            job=s.scalar(select(Job).where(Job.target==generation).with_for_update());locked.set()
            assert release.wait(5)
            g=s.scalar(select(Record).where(Record.id==generation).with_for_update())
            job.state='running';g.state='generating'
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing=pool.submit(worker_transaction);assert locked.wait(5)
        stopping=pool.submit(lambda:a.post('/api/generations/'+generation+'/stop'))
        time.sleep(.2);release.set();writing.result(timeout=5)
        response=stopping.result(timeout=5)
    assert response.status_code==200 and response.json()['state']=='stopped'
    with store.session() as s:assert s.scalar(select(Job).where(Job.target==generation)).state=='cancelled'


def test_job_cancel_and_account_disable_finish_generation_and_allow_followup(store,clients,accounts):
    a=clients['alice'];chat=chat_model(store);conv=a.post('/api/conversations',json={}).json()['id']
    def begin(key):return a.post(f'/api/conversations/{conv}/generations',json={'question':'test','request_key':key,'model_id':chat}).json()['id']
    g=begin('cancel');job=next(j for j in a.get('/api/jobs').json() if j['target']==g)
    assert a.post('/api/jobs/'+job['id']+'/cancel').status_code==200
    assert a.get('/api/generations/'+g).json()['state']=='stopped'
    assert 'event: done' in a.get('/api/generations/'+g+'/events?stream=false').text
    next_g=begin('disable')
    assert clients['admin'].patch('/api/admin/users/'+accounts['alice'],json={'enabled':False}).status_code==200
    with store.session() as s:
        generation=s.get(Record,next_g)
        assert generation.state=='stopped'
        assert s.get(Record,generation.payload['assistant_message_id']).state=='stopped'
