import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter,NavLink,Routes,Route} from 'react-router-dom';
import {QueryClient,QueryClientProvider,useQuery} from '@tanstack/react-query';
import './style.css';
const client=new QueryClient();
export async function api(path:string,options:RequestInit={}) {const r=await fetch('/api'+path,{...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers}});if(!r.ok){let e;try{e=await r.json()}catch{e={message:'服务暂不可用'}}throw new Error(e.message||e.detail||'请求失败')}return r.status===204?null:r.json()}
function Empty({title,detail}:{title:string,detail:string}){return <div className="empty"><h2>{title}</h2><p>{detail}</p></div>}
function Home(){const q=useQuery({queryKey:['dashboard'],queryFn:()=>api('/dashboard')});if(q.isError)return <Empty title="无法连接本地服务" detail="请检查应用是否启动，再刷新页面。"/>;return <><header><span className="eyebrow">你的资料，你的知识</span><h1>个人知识库</h1><p>导入资料，找到答案，核对来源。</p></header><section className="panel"><h2>开始使用</h2><p>当前已导入 {q.data?.documents??0} 份文档。</p><div className="actions"><NavLink className="button" to="/knowledge">导入第一份资料</NavLink><NavLink className="button secondary" to="/settings">配置模型</NavLink></div></section></>}
function App(){return <div className="shell"><aside><div className="brand">◈ 知识空间</div>{[['/','首页'],['/knowledge','知识库'],['/processing','文档处理'],['/chat','问答对话'],['/settings','数据与设置']].map(([to,label])=><NavLink key={to} to={to} end={to==='/'}>{label}</NavLink>)}<div className="local">本机数据 · 个人使用</div></aside><main><Routes><Route path="/" element={<Home/>}/>{['knowledge','processing','chat','settings'].map(p=><Route key={p} path={'/'+p+'/*'} element={<Empty title={{knowledge:'知识库',processing:'文档处理',chat:'问答对话',settings:'数据与设置'}[p]||p} detail="暂无数据"/>}/>)}</Routes></main></div>}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><BrowserRouter><App/></BrowserRouter></QueryClientProvider>);
