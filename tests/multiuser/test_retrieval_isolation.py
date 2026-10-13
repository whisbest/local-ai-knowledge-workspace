from app.multiuser.documents import process_document
from test_documents_isolation import upload


def test_candidates_model_context_and_deleted_index_are_owner_scoped(store,clients,identities,embedding_fixture):
    model_id,requests=embedding_fixture
    docs={};kbs={}
    for name in ('alice','bob'):
        c=clients[name];kb=c.post('/api/knowledge-bases',json={'name':name,'embedding_id':model_id}).json()['id'];kbs[name]=kb
        response=upload(c,kb,identities[name]['marker'].encode());assert response.status_code==200,response.text
        docs[name]=response.json()[0]['document_id'];process_document(store,c.get('/api/jobs').json()[0]['id'])
        assert c.get('/api/dashboard').json()['ready']==1
    r=clients['bob'].post('/api/retrieval',json={'question':'private','scope':{'type':'all'}})
    assert r.status_code==200,r.text
    assert identities['bob']['marker'] in r.text and identities['alice']['marker'] not in r.text
    assert {item['document_id'] for item in r.json()['items']}=={docs['bob']}
    assert clients['alice'].put('/api/settings',json={'chunk_length':100,'chunk_overlap':10}).status_code==200
    assert clients['alice'].get('/api/dashboard').json()['ready']==1
    assert clients['bob'].delete('/api/documents/'+docs['bob']).status_code==200
    assert clients['bob'].post('/api/retrieval',json={'question':'private'}).json()['items']==[]
    assert clients['alice'].post('/api/retrieval',json={'question':'private'}).json()['items']
