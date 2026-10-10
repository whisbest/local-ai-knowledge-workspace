import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter,NavLink,Routes,Route} from 'react-router-dom';
import {QueryClient,QueryClientProvider,useQuery} from '@tanstack/react-query';
import './style.css';
import Settings from './Settings';
import {Knowledge,DocumentDetail,Processing} from './Documents';
import Chat from './Chat';
import Home from './Home';
import {api} from './api';
const client=new QueryClient();
function ServiceStatus(){const q=useQuery({queryKey:['health'],queryFn:()=>api('/health'),refetchInterval:5000,retry:0});return <div className={'service-status '+(q.isError?'notice':'')} role="status">{q.isError?'无法连接本地服务，请运行启动脚本。':q.data?.maintenance?'数据维护中，请稍候。':q.isPending?'连接本地服务…':'● 本地服务运行中'}</div>}
function App(){return <div className="shell"><aside><div className="brand">◈ 知识空间</div>{[['/','首页'],['/knowledge','知识库'],['/processing','文档处理'],['/chat','问答对话'],['/settings','数据与设置']].map(([to,label])=><NavLink key={to} to={to} end={to==='/'}>{label}</NavLink>)}<div className="local">本机数据 · 个人使用</div></aside><main><ServiceStatus/><Routes><Route path="/" element={<Home/>}/><Route path="/settings" element={<Settings/>}/><Route path="/knowledge" element={<Knowledge/>}/><Route path="/processing" element={<Processing/>}/><Route path="/documents/:id" element={<DocumentDetail/>}/><Route path="/chat" element={<Chat/>}/><Route path="/chat/:id" element={<Chat/>}/></Routes></main></div>}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><BrowserRouter><App/></BrowserRouter></QueryClientProvider>);
