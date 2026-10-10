"""Isolated deterministic protocol server, never used by the shipped application."""
import sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx
from app.main import create_app
app=create_app(tempfile.mkdtemp(prefix='lkq-e2e-'))
calls=0
def fixture(req):
    global calls
    b=json.loads(req.content)
    if req.url.path.endswith('embeddings'):return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.,0.5,.1]} for i in range(len(b['input']))]})
    calls+=1
    return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'资料说明：退款期限七天。[1]'}}]})+'\n\ndata: [DONE]\n\n')
app.state.store.provider_transport=httpx.MockTransport(fixture)
@app.get('/api/test-calls')
def counts():return {'calls':calls}
