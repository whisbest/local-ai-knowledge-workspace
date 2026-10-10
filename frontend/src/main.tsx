import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter,NavLink,Routes,Route} from 'react-router-dom';
import {QueryClient,QueryClientProvider,useQuery} from '@tanstack/react-query';
import './style.css';
import Settings from './Settings';
import {Knowledge,DocumentDetail,Processing} from './Documents';
import Chat from './Chat';
import Home from './Home';
const client=new QueryClient();
export async function api(path:string,options:RequestInit={}) {const r=await fetch('/api'+path,{...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers}});if(!r.ok){let e;try{e=await r.json()}catch{e={message:'服务暂不可用'}}throw new Error(e.message||e.detail||'请求失败')}return r.status===204?null:r.json()}
function Empty({title,detail}:{title:string,detail:string}){return <div className="empty"><h2>{title}</h2><p>{detail}</p></div>}
function App(){return <div className="shell"><aside><div className="brand">◈ 知识空间</div>{[['/','首页'],['/knowledge','知识库'],['/processing','文档处理'],['/chat','问答对话'],['/settings','数据与设置']].map(([to,label])=><NavLink key={to} to={to} end={to==='/'}>{label}</NavLink>)}<div className="local">本机数据 · 个人使用</div></aside><main><Routes><Route path="/" element={<Home/>}/><Route path="/settings" element={<Settings/>}/><Route path="/knowledge" element={<Knowledge/>}/><Route path="/processing" element={<Processing/>}/><Route path="/documents/:id" element={<DocumentDetail/>}/><Route path="/chat" element={<Chat/>}/><Route path="/chat/:id" element={<Chat/>}/></Routes></main></div>}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><BrowserRouter><App/></BrowserRouter></QueryClientProvider>);


