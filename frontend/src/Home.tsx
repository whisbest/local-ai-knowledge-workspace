import {useState} from 'react';
import {Link,useNavigate} from 'react-router-dom';
import {useQuery} from '@tanstack/react-query';
import {api,multiuser} from './api';
import {states} from './Documents';
import Icon from './Icons';

const stats = [
  {label:'全部资料',key:'documents',to:'/knowledge',icon:'folder',hint:'收集的知识与笔记'},
  {label:'可检索',key:'ready',to:'/knowledge?status=ready',icon:'check',hint:'已完成向量索引'},
  {label:'处理中',key:'processing',to:'/knowledge?status=queued,parsing,chunking,embedding',icon:'clock',hint:'正在准备资料'},
  {label:'失败或中断',key:'failed',to:'/knowledge?status=failed,interrupted',icon:'alert',hint:'查看原因并重试'},
  {label:'知识库',key:'knowledge_bases',to:'/knowledge',icon:'grid',hint:'按主题组织资料'},
] as const;
export default function Home(){
  const nav=useNavigate();const [question,setQuestion]=useState('');const [kb,setKb]=useState<string|undefined>();const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
  const q=useQuery({queryKey:['dashboard'],queryFn:()=>api('/dashboard'),refetchInterval:2000});
  const settings=useQuery({queryKey:['settings'],queryFn:()=>api('/settings')});
  const models=useQuery({queryKey:['models'],queryFn:()=>api('/model-configs')});
  const kbs=useQuery({queryKey:['kbs'],queryFn:()=>api('/knowledge-bases')});
  const selected=kb??settings.data?.default_kb??'';
  const chatModel=models.data?.find((m:any)=>m.id===settings.data?.default_chat);
  if(q.isError)return <section className="empty"><Icon name="alert"/><h1>{multiuser?'无法连接服务':'无法连接本地服务'}</h1><p>请运行启动脚本，然后刷新页面。</p><button onClick={()=>q.refetch()}>重新连接</button></section>;
  return <>
    <header className="page-heading home-heading"><div><h1>让知识，随问随得。</h1><p>整理你的资料，从每一次提问开始连接知识。</p></div><Link className="button secondary" to="/knowledge"><Icon name="plus"/>导入资料</Link></header>
    <section className="panel quick">
      <h2><Icon name="spark"/>今天想了解什么？</h2><p>基于你的资料回答，让每一个结论都有来源。</p>
      {notice&&<div className="notice" role="status">{notice}</div>}
      <form className="question-box" onSubmit={async e=>{e.preventDefault();if(busy)return;setBusy(true);setNotice('');const key=crypto.randomUUID();try{const scope=selected?{type:'kb',ids:[selected]}:{type:'all'};const conv=await api('/conversations',{method:'POST',body:JSON.stringify({request_key:key,scope})});const g=await api('/conversations/'+conv.id+'/generations',{method:'POST',body:JSON.stringify({question,scope,model_id:settings.data?.default_chat,request_key:key})});nav('/chat/'+conv.id+'?generationId='+g.id)}catch(e){setNotice((e as Error).message)}finally{setBusy(false)}}}>
        <textarea aria-label="首页问题" required maxLength={4000} placeholder="例如：帮我梳理资料中的核心知识，并引用相关笔记…" value={question} onChange={e=>setQuestion(e.target.value)}/>
        <div className="question-actions"><select aria-label="首页知识库" value={selected} onChange={e=>setKb(e.target.value)}><option value="">全部知识库</option>{kbs.data?.map((r:any)=><option key={r.id} value={r.id}>{r.name}</option>)}</select><button disabled={busy||!question.trim()||!settings.data?.default_chat||!(q.data?.ready>0)}>{busy?'正在提交…':'开始提问'}<Icon name="arrow"/></button></div>
      </form>
      <div className="model-note">{chatModel&&<span className={'tag '+(chatModel.mode==='local'?'tag-success':'tag-accent')}>{chatModel.mode==='local'?'本地回答模型':'API 回答模型'}</span>}{!settings.data?.default_chat?<Link to="/settings?section=models&purpose=chat">先配置回答模型 →</Link>:!settings.data?.default_embedding?<Link to="/settings?section=models&purpose=embedding">先配置向量模型 →</Link>:q.data?.documents===0?<Link to="/knowledge">导入第一份资料 →</Link>:!q.data?.ready?<Link to="/processing">完成索引或重建后提问 →</Link>:<><span>回答：{chatModel?.name}</span><span>可检索资料 {q.data.ready} 份</span></>}</div>
    </section>
    <div className="stats">{stats.map(({label,key,to,icon,hint})=><Link key={key} className={'panel stat stat-'+key} to={to}><div className="stat-label"><span>{label}</span><Icon name={icon}/></div><strong>{q.isPending?'…':q.data?.[key]??0}</strong><small>{hint}</small></Link>)}</div>
    <div className="home-columns">
      <section className="panel"><div className="panel-heading"><h2>最近对话</h2><Link className="more-link" to="/chat">查看全部 <Icon name="arrow"/></Link></div>
        {q.data?.recent_conversations.length?q.data.recent_conversations.map((r:any)=><Link className="recent" key={r.id} to={'/chat/'+r.id}><span className="file-icon"><Icon name="chat"/></span><div className="recent-body"><strong>{r.title}</strong><small>{new Date(r.updated_at).toLocaleString()}</small></div><Icon className="recent-arrow" name="arrow"/></Link>):<div className="inline-empty"><Icon name="chat"/><h3>从一个好问题开始</h3><p>还没有对话。从上方输入第一个问题。</p></div>}
      </section>
      <section className="panel"><div className="panel-heading"><h2>最近资料</h2><Link className="more-link" to="/knowledge">管理资料 <Icon name="arrow"/></Link></div>
        {q.data?.recent_documents.length?q.data.recent_documents.map((d:any)=><Link className="recent" key={d.id} to={'/documents/'+d.id}><span className={'file-icon format-'+d.format}>{d.format.toUpperCase()}</span><div className="recent-body"><strong>{d.name}</strong><small>{d.format.toUpperCase()}</small></div><span className={'tag status-'+d.state}>{states[d.state]}</span></Link>):<div className="inline-empty"><Icon name="folder"/><h3>为知识留一个空间</h3><p>导入笔记、文档或文字型 PDF，开始积累资料。</p><Link className="button secondary" to="/knowledge">导入第一份资料</Link></div>}
      </section>
    </div>
  </>;
}
