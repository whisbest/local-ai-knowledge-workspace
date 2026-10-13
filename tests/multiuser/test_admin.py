from test_documents_isolation import upload


def test_admin_routes_all_reject_users_and_operations_hide_private_content(store,clients,accounts):
    a=clients['alice'];admin=clients['admin'];kb=a.post('/api/knowledge-bases',json={'name':'PRIVATE-NAME'}).json()['id']
    upload(a,kb,b'PRIVATE-BODY-DO-NOT-SHOW')
    matrix=[('get','/api/admin/users',None),('post','/api/admin/users',{'username':'new','password':'Temporary-password'}),
            ('patch','/api/admin/users/'+accounts['bob'],{'enabled':False}),('get','/api/admin/models',None),
            ('post','/api/admin/models',{}),('patch','/api/admin/models/unknown',{'enabled':False}),
            ('post','/api/admin/models/unknown/test',None),('get','/api/admin/models/unknown/impact',None),
            ('get','/api/admin/status',None),('get','/api/admin/usage',None),('get','/api/admin/audit',None)]
    for method,path,body in matrix:
        r=getattr(a,method)(path,**({'json':body} if body is not None else {}));assert r.status_code==403,(method,path,r.text)
    for path in ('/api/admin/users','/api/admin/status','/api/admin/usage','/api/admin/audit'):
        response=admin.get(path);assert response.status_code==200
        assert 'PRIVATE-NAME' not in response.text and 'PRIVATE-BODY' not in response.text
    usage=admin.get('/api/admin/usage').json();assert next(r for r in usage if r['id']==accounts['alice'])['documents']==1
    assert clients['bob'].get('/api/dashboard').json()['documents']==0
