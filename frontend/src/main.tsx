import {createRoot} from 'react-dom/client';
import {BrowserRouter,NavLink,Routes,Route,useLocation} from 'react-router-dom';
import {QueryClient,QueryClientProvider,useQuery} from '@tanstack/react-query';
import './style.css';
import Settings from './Settings';
import {Knowledge,DocumentDetail,Processing} from './Documents';
import Chat from './Chat';
import Home from './Home';
import {api} from './api';
import Icon from './Icons';
const client=new QueryClient();
const navigation=[{to:'/',label:'首页',icon:'home'},{to:'/knowledge',label:'知识库',icon:'folder'},{to:'/processing',label:'文档处理',icon:'document'},{to:'/chat',label:'问答对话',icon:'chat'},{to:'/settings',label:'数据与设置',icon:'settings'}] as const;
function ServiceStatus(){const q=useQuery({queryKey:['health'],queryFn:()=>api('/health'),refetchInterval:5000,retry:0});return <div className={'service-status '+(q.isError?'service-error':'')} role="status"><span className={'status-dot '+(q.isError?'error':q.data?.maintenance||q.isPending?'waiting':'')}/>{q.isError?'无法连接本地服务，请运行启动脚本。':q.data?.maintenance?'数据维护中，请稍候。':q.isPending?'连接本地服务…':'本地服务运行中'}</div>}
function App(){const location=useLocation();const chatPage=location.pathname.startsWith('/chat');const pageTitle=location.pathname.startsWith('/documents/')?'资料详情':navigation.find(n=>n.to==='/'?location.pathname==='/':location.pathname.startsWith(n.to))?.label||'首页';return <div className="shell"><aside className="sidebar"><div className="brand"><span className="brand-mark"><Icon name="spark"/></span>知识空间</div><div className="brand-subtitle">个人 AI 知识工作区</div><div className="nav-label">我的工作区</div><nav aria-label="主导航">{navigation.map(({to,label,icon})=><NavLink key={to} to={to} end={to==='/'}><Icon name={icon}/><span>{label}</span></NavLink>)}</nav><div className="sidebar-footer"><span className="local-dot"/>本机数据 · 个人使用</div></aside><div className="workspace"><div className="topbar"><div className="breadcrumb"><span>我的工作区</span><span className="breadcrumb-divider">/</span><strong>{pageTitle}</strong></div><ServiceStatus/></div><main className={chatPage?'page-content chat-page':'page-content'}><Routes><Route path="/" element={<Home/>}/><Route path="/settings" element={<Settings/>}/><Route path="/knowledge" element={<Knowledge/>}/><Route path="/processing" element={<Processing/>}/><Route path="/documents/:id" element={<DocumentDetail/>}/><Route path="/chat" element={<Chat/>}/><Route path="/chat/:id" element={<Chat/>}/></Routes></main></div></div>}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><BrowserRouter><App/></BrowserRouter></QueryClientProvider>);
