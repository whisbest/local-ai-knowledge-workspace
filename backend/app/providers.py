"""Bounded model calls. There is deliberately no provider fallback."""
from __future__ import annotations
import json
import math
import hashlib
from urllib.parse import urlsplit, urlunsplit
import httpx
from .core import AppError, dump


def normalize_endpoint(value, mode):
    try:
        u = urlsplit(value.strip())
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError()
        if mode == 'local' and u.hostname not in ('localhost', '127.0.0.1', '::1', 'host.docker.internal'):
            raise ValueError()
        return urlunsplit((u.scheme, u.netloc.lower(), u.path.rstrip('/'), '', ''))
    except ValueError:
        raise AppError('ENDPOINT', '请输入不含密钥、查询参数的 HTTP 地址；本地模式只连接本机 Ollama。')


def fingerprint(config, dimension):
    semantic = {k: config[k] for k in ('purpose', 'mode', 'protocol', 'endpoint', 'model')}
    semantic.update(dimension=dimension, parameters=config.get('parameters', {}), model_digest=config.get('model_digest'))
    return hashlib.sha256(dump(semantic).encode()).hexdigest()


class Provider:
    def __init__(self, config, secret=None, transport=None):
        self.config, self.secret = config, secret
        self.client = httpx.Client(base_url=config['endpoint']+'/', timeout=httpx.Timeout(180, connect=8), trust_env=False, transport=transport,
                                   headers={'Authorization':'Bearer '+secret} if config['mode']=='api' and secret else {})

    def close(self): self.client.close()

    def failure(self, response=None):
        status = response.status_code if response else None
        # Never surface provider bodies, exception strings, URL credentials or authorization.
        reason = {401:'密钥无效或已失效。', 403:'服务拒绝访问，请检查密钥权限。', 404:'模型或接口不存在，请检查模型名和地址。', 429:'请求额度或频率受限，请稍后重试。'}.get(status, '模型服务无法连接或响应异常，请检查服务、网络和配置。')
        raise AppError('MODEL_SERVICE', reason, 502, status in (429,502,503,504) or status is None)

    def post(self, path, body):
        try:
            r=self.client.post(path, json=body)
            if r.status_code >= 400: self.failure(r)
            if len(r.content)>16*1024*1024: raise ValueError()
            return r.json()
        except AppError: raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError): self.failure()

    def embed(self, texts):
        c=self.config
        if c['mode']=='api' and not self.secret: raise AppError('MISSING_KEY','请填写此向量配置的 API 密钥。')
        data=self.post('api/embed', {'model':c['model'],'input':texts,'keep_alive':0,'truncate':False}) if c['mode']=='local' else self.post('embeddings', {'model':c['model'],'input':texts})
        try:
            vectors=data['embeddings'] if c['mode']=='local' else [v['embedding'] for v in sorted(data['data'],key=lambda x:x['index'])]
            if len(vectors)!=len(texts) or not vectors: raise ValueError()
            dimension=len(vectors[0])
            if not 1<=dimension<=16384: raise ValueError()
            if any(len(v)!=dimension or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in v) or sum(x*x for x in v)==0 for v in vectors): raise ValueError()
            return vectors
        except (KeyError,TypeError,ValueError): raise AppError('EMBEDDING_RESPONSE','向量响应数量、维度或数值不合法。',502)

    def chat(self, messages, cancelled=lambda:False, length='normal'):
        c=self.config
        if c['mode']=='api' and not self.secret: raise AppError('MISSING_KEY','请填写此回答配置的 API 密钥。')
        tokens={'short':256,'normal':512,'long':1024}.get(length,512)
        body={'model':c['model'],'messages':messages,'stream':True}
        if c['mode']=='local': body.update(keep_alive=0,options={'temperature':0.1,'num_ctx':4096,'num_predict':tokens})
        else: body.update(temperature=0.1,max_tokens=tokens)
        count=0
        try:
            with self.client.stream('POST','api/chat' if c['mode']=='local' else 'chat/completions',json=body) as response:
                if response.status_code>=400: self.failure(response)
                for line in response.iter_lines():
                    if cancelled(): return
                    if not line: continue
                    if c['mode']=='api':
                        if not line.startswith('data:'): continue
                        line=line[5:].strip()
                        if line=='[DONE]': return
                    count+=len(line)
                    if count>2*1024*1024: raise ValueError()
                    event=json.loads(line)
                    if event.get('error'): self.failure()
                    text=event.get('message',{}).get('content','') if c['mode']=='local' else event['choices'][0].get('delta',{}).get('content','')
                    if text: yield text
                    if c['mode']=='local' and event.get('done'): return
        except AppError: raise
        except (httpx.HTTPError,ValueError,KeyError,TypeError): self.failure()

    def models(self):
        try:
            r=self.client.get('api/tags')
            if r.status_code>=400: self.failure(r)
            return r.json()['models']
        except AppError: raise
        except (httpx.HTTPError,ValueError,KeyError,TypeError): self.failure()

    def pull(self, model, cancelled=lambda:False):
        try:
            with self.client.stream('POST','api/pull',json={'name':model,'stream':True}) as r:
                if r.status_code>=400: self.failure(r)
                for line in r.iter_lines():
                    if cancelled(): return
                    if line:
                        item=json.loads(line)
                        if item.get('error'): self.failure()
                        yield {k:item[k] for k in ('status','total','completed') if k in item}
        except AppError: raise
        except (httpx.HTTPError,ValueError,KeyError,TypeError): self.failure()
