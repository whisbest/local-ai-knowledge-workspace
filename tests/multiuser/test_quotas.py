from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from sqlalchemy import select,func
import pytest
from app.multiuser.db import Reservation, User, Model
from app.multiuser.quotas import reserve,settle,MeteredProvider,slot
from app.core import AppError
from test_models import model_setup
from test_documents_isolation import upload


def test_atomic_parallel_reservations_idempotent_settlement_and_release(store,accounts):
    owner=accounts['alice']
    def compete(i):
        try:
            with store.session() as s:return reserve(s,owner,'test',str(i),1,2).id
        except AppError:return None
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(compete,range(5)))
    assert sum(r is not None for r in results)==2
    with store.session() as s:
        row=s.scalar(select(Reservation).where(Reservation.owner_id==owner));request_id=row.request_id
        assert reserve(s,owner,'test',request_id,1,2).id==row.id
        settle(s,owner,'test',request_id,'released');settle(s,owner,'test',request_id,'released')
        assert row.state=='released';reserve(s,owner,'test','new',1,2)


def test_platform_limit_personal_still_works_and_uncertain_calls_metered(store,clients,accounts):
    model_setup(store);admin=clients['admin'];a=clients['alice'];models=[]
    for client,prefix in [(admin,'/api/admin/models'),(a,'/api/model-configs')]:
        response=client.post(prefix,json={'name':'quota','purpose':'embedding','endpoint':'https://api.example.com/v1','model':'fixture','key':'fixture'})
        id=response.json()['id'];assert client.post(prefix+'/'+id+'/test').status_code==200;models.append(id)
    assert admin.patch('/api/admin/users/'+accounts['alice'],json={'quota':{'platform_calls':0}}).status_code==200
    from app.multiuser.indexes import provider
    p=provider(store,accounts['alice'],models[0],'embedding')
    with pytest.raises(AppError):p.embed(['sample'])
    p.close();p=provider(store,accounts['alice'],models[1],'embedding');assert p.embed(['sample']);p.close()
    assert admin.patch('/api/admin/users/'+accounts['alice'],json={'quota':{'platform_calls':2}}).status_code==200
    class Timeout:
        def embed(self,texts):raise AppError('TIMEOUT','服务超时。',502)
        def close(self):pass
    p=MeteredProvider(store,accounts['alice'],models[0],Timeout())
    with pytest.raises(AppError):p.embed(['sample'])
    with store.session() as s:
        row=s.scalar(select(Reservation).where(Reservation.owner_id==accounts['alice'],Reservation.resource=='platform_calls'))
        assert row.state=='unknown'


def test_parallel_upload_storage_and_queue_limits_do_not_lose_existing_data(store,clients,accounts):
    a=clients['alice'];admin=clients['admin'];kb=a.post('/api/knowledge-bases',json={'name':'limits'}).json()['id']
    assert admin.patch('/api/admin/users/'+accounts['alice'],json={'quota':{'storage_bytes':8,'pending_jobs':1}}).status_code==200
    r=upload(a,kb,b'123456',key='one');assert r.status_code==200
    assert upload(a,kb,b'abcdef',filename='other.txt',key='two').status_code==409
    assert a.get('/api/documents').json()['total']==1
    job=a.get('/api/jobs').json()[0]['id'];assert a.post('/api/jobs/'+job+'/cancel').status_code==200
    assert upload(a,kb,b'12',filename='tiny.txt',key='three').status_code==200
    assert a.get('/api/data/overview').json()['storage_bytes']==8
    assert upload(clients['bob'],clients['bob'].post('/api/knowledge-bases',json={'name':'B'}).json()['id'],b'12345678').status_code==200


def test_capability_tests_use_quota_and_rate_limit_before_sending(store,clients,accounts):
    requests=model_setup(store);a=clients['alice'];admin=clients['admin']
    body={'name':'bounded tests','purpose':'embedding','endpoint':'https://api.example.com/v1','model':'fixture','key':'test-only'}
    platform=admin.post('/api/admin/models',json=body).json()['id']
    assert admin.patch('/api/admin/users/'+accounts['admin'],json={'quota':{'platform_calls':1}}).status_code==200
    assert admin.post('/api/admin/models/'+platform+'/test').status_code==200
    sent=len(requests)
    response=admin.post('/api/admin/models/'+platform+'/test')
    assert response.status_code==409 and response.json()['code']=='QUOTA_LIMIT'
    assert len(requests)==sent
    personal=a.post('/api/model-configs',json=body).json()['id']
    for _ in range(10):assert a.post('/api/model-configs/'+personal+'/test').status_code==200
    sent=len(requests)
    response=a.post('/api/model-configs/'+personal+'/test')
    assert response.status_code==429 and response.json()['code']=='MODEL_TEST_LIMIT'
    assert len(requests)==sent
    with store.session() as s:
        assert s.scalar(select(func.sum(Reservation.amount)).where(Reservation.owner_id==accounts['admin'],Reservation.resource=='platform_calls',Reservation.state=='committed'))==1
        assert s.scalar(select(func.count()).select_from(Reservation).where(Reservation.owner_id==accounts['alice'],Reservation.resource=='platform_calls'))==0


def test_personal_usage_counts_reserved_unknown_and_only_own_quota(store,clients,accounts):
    alice, bob = accounts['alice'], accounts['bob']
    with store.session() as s:
        s.get(User, alice).quota = {'platform_calls': 6, 'pending_jobs': 3}
        for owner, request, amount, state in (
                (alice, 'completed', 2, 'committed'), (alice, 'waiting', 1, 'reserved'),
                (alice, 'uncertain', 1, 'unknown'), (alice, 'released', 50, 'released'),
                (bob, 'other-user', 500, 'committed')):
            s.add(Reservation(owner_id=owner, resource='platform_calls', request_id=request,
                              amount=amount, state=state))
    kb = clients['alice'].post('/api/knowledge-bases', json={'name': 'Usage'}).json()['id']
    assert upload(clients['alice'], kb, b'quota sample').status_code == 200
    report = clients['alice'].get('/api/data/overview').json()
    assert report['platform_calls'] == 4
    assert report['platform_call_limit'] == 6 and report['platform_calls_remaining'] == 2
    assert report['platform_calls_reserved'] == 1 and report['platform_calls_unknown'] == 1
    assert report['platform_calls_committed'] == 2
    assert report['pending_jobs'] == 1 and report['pending_job_limit'] == 3
    assert clients['bob'].get('/api/data/overview').json()['platform_calls'] == 500
    assert clients['bob'].get('/api/data/overview').json()['pending_jobs'] == 0
    assert clients['admin'].patch('/api/admin/users/'+alice, json={'quota': {'platform_calls': 1}}).status_code == 200
    report = clients['alice'].get('/api/data/overview').json()
    assert report['platform_calls_remaining'] == 0 and report['platform_calls'] == 4
