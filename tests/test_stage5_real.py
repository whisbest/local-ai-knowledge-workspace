import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
from app.main import create_app
from test_stage3 import upload,drain

def test_real_grounded_answer_outside_question_and_continuation(tmp_path):
    app=create_app(tmp_path,worker=False)
    with TestClient(app) as c:
        for purpose,model in [('embedding','qwen3-embedding:0.6b'),('chat','qwen2.5:3b')]:
            id=c.post('/api/model-configs',json={'purpose':purpose,'mode':'local','endpoint':'http://127.0.0.1:11434','model':model}).json()['id'];assert c.post('/api/model-configs/'+id+'/test').status_code==200;c.put('/api/settings',json={'default_'+purpose:id})
        kb=c.post('/api/knowledge-bases',json={'name':'本地问答实测'}).json()['id'];doc=upload(c,kb,['policy-a.txt']).json()[0]['document_id'];drain(app.state.store)
        conv=c.post('/api/conversations',json={}).json()['id'];records=[]
        for i,question in enumerate(['青竹项目的退款期限是多少天？','你刚说的期限适用于哪个项目？','资料中提到的火星人口是多少？']):
            start=time.perf_counter();g=c.post('/api/conversations/'+conv+'/generations',json={'question':question,'request_key':'real-'+str(i),'scope':{'type':'documents','ids':[doc]}}).json()['id'];drain(app.state.store)
            gen=c.get('/api/generations/'+g).json();assert gen['state']=='completed',gen
            answer=c.get('/api/conversations/'+conv+'/messages').json()['messages'][-1]['content'];records.append({'question':question,'answer':answer,'seconds':time.perf_counter()-start})
            if i==0:assert ('七' in answer or '7' in answer) and '[1]' in answer
            if i==2:assert any(s in answer for s in ['不足','未提及','没有','无法','未提供'])
        Path('.reports/stage5-real.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')

