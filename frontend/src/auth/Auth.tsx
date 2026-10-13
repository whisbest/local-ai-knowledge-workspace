import {useEffect,useState,createContext,useContext,type ReactNode} from 'react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {api,setSession,clearSession} from '../api';
import Icon from '../Icons';

type Account={id:string;username:string;role:string;must_change:boolean;csrf:string};
const Auth=createContext<{user:Account;logout:()=>Promise<void>}>({} as any);
export function useAccount(){return useContext(Auth)}

export default function AuthGate({children}:{children:ReactNode}){
 const [user,setUser]=useState<Account|null>(null);const [loading,setLoading]=useState(true);
 const [client,setClient]=useState(()=>new QueryClient({defaultOptions:{queries:{retry:0}}}));const [notice,setNotice]=useState('');
 function reset(){clearSession();client.cancelQueries();client.clear();setUser(null);setClient(new QueryClient({defaultOptions:{queries:{retry:0}}}));setLoading(false)}
 useEffect(()=>{let active=true;api('/auth/me').then(value=>{if(active){setSession(value.csrf);setUser(value)}}).catch(()=>{}).finally(()=>{if(active)setLoading(false)});
  const expire=()=>{reset();setNotice('登录已失效，请重新登录。')};window.addEventListener('workspace-session-expired',expire);
  return()=>{active=false;window.removeEventListener('workspace-session-expired',expire)}
 },[]);
 async function logout(){try{await api('/auth/logout',{method:'POST'})}finally{reset();window.history.replaceState(null,'','/');window.dispatchEvent(new PopStateEvent('popstate'))}}
 if(loading)return <div className="auth-page"><p>正在验证登录…</p></div>;
 if(!user)return <Login notice={notice} onLogin={account=>{client.clear();setSession(account.csrf);setUser(account);setNotice('')}}/>;
 if(user.must_change)return <PasswordChange onChanged={()=>{reset();setNotice('密码已更新，请使用新密码登录。')}} onLogout={logout}/>;
 return <Auth.Provider value={{user,logout}}><QueryClientProvider client={client} key={user.id}>{children}</QueryClientProvider></Auth.Provider>
}

function Login({notice,onLogin}:{notice:string;onLogin:(value:Account)=>void}){
 const [username,setUsername]=useState('');const [password,setPassword]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const [options,setOptions]=useState<any>(null);const [register,setRegister]=useState(false);
 useEffect(()=>{let active=true;api('/auth/options').then(v=>{if(active)setOptions(v)}).catch(()=>{});return()=>{active=false}},[]);
 if(register)return <Register options={options} onBack={email=>{setRegister(false);setUsername(email||'');setPassword('');setError(email?'注册成功，请用邮箱和密码登录。':'')}}/>;
 return <div className="auth-page"><section className="panel auth-card"><span className="brand-mark"><Icon name="spark"/></span><h1>登录知识空间</h1><p>你的资料、对话与设置保存在自己的账号中。</p>{(error||notice)&&<div className="notice" role="status">{error||notice}</div>}<form onSubmit={async e=>{e.preventDefault();if(busy)return;setBusy(true);setError('');try{const account=await api('/auth/login',{method:'POST',body:JSON.stringify({username,password})});setPassword('');onLogin(account)}catch(e){setPassword('');setError((e as Error).message)}finally{setBusy(false)}}}><label>邮箱或账号<input aria-label="登录账号" autoComplete="username" required value={username} onChange={e=>setUsername(e.target.value)}/></label><label>密码<input aria-label="登录密码" type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label><button disabled={busy}>{busy?'正在登录…':'登录'}</button></form>{options?.registration_enabled&&<button className="secondary" onClick={()=>setRegister(true)}>用邮箱注册</button>}<p>已有账号也可直接登录。忘记密码时，请联系管理员重置。</p></section></div>
}

function Register({options,onBack}:{options:any;onBack:(email?:string)=>void}){
 const [email,setEmail]=useState('');const [code,setCode]=useState('');const [password,setPassword]=useState('');const [confirm,setConfirm]=useState('');
 const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);const [sending,setSending]=useState(false);const [cooldown,setCooldown]=useState(0);
 useEffect(()=>{if(cooldown<=0)return;const timer=window.setTimeout(()=>setCooldown(cooldown-1),1000);return()=>window.clearTimeout(timer)},[cooldown]);
 async function send(){if(sending||cooldown)return;setSending(true);setNotice('');try{const r=await api('/auth/registration-code',{method:'POST',body:JSON.stringify({email})});setCooldown(r.retry_after);setNotice(options?.local_mail_preview?'当前为本地测试，验证码可由管理员在平台管理的本地验证邮件中查看。':r.message)}catch(e){setNotice((e as Error).message)}finally{setSending(false)}}
 return <div className="auth-page"><section className="panel auth-card"><h1>邮箱注册</h1><p>验证邮箱后，即可创建自己的知识空间。</p>{options?.local_mail_preview&&<p className="notice">本地测试模式：邮件不会发送到真实邮箱。上线使用前需配置发信邮箱。</p>}{options&&!options.mail_ready&&<p className="notice">注册邮件服务尚未配置，请联系管理员。</p>}{notice&&<div className="notice" role="status">{notice}</div>}<form onSubmit={async e=>{e.preventDefault();if(busy)return;if(password!==confirm){setNotice('两次密码不一致');return}setBusy(true);setNotice('');try{await api('/auth/register',{method:'POST',body:JSON.stringify({email,code,password})});setPassword('');setConfirm('');onBack(email.trim().toLowerCase())}catch(e){setPassword('');setConfirm('');setNotice((e as Error).message)}finally{setBusy(false)}}}><label>邮箱<input type="email" required aria-label="注册邮箱" autoComplete="email" maxLength={254} value={email} onChange={e=>{setEmail(e.target.value);setCode('')}}/></label><button type="button" className="secondary" disabled={sending||cooldown>0||!email||!options?.mail_ready} onClick={send}>{sending?'正在发送…':cooldown?`${cooldown}秒后重发`:'发送验证码'}</button><label>验证码<input required aria-label="注册验证码" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={e=>setCode(e.target.value)}/></label><label>密码<input required aria-label="注册密码" type="password" autoComplete="new-password" minLength={12} maxLength={128} value={password} onChange={e=>setPassword(e.target.value)}/></label><label>确认密码<input required aria-label="确认注册密码" type="password" autoComplete="new-password" minLength={12} maxLength={128} value={confirm} onChange={e=>setConfirm(e.target.value)}/></label><p>密码至少12个字符。验证码10分钟有效。</p><button disabled={busy||!options?.mail_ready}>{busy?'正在注册…':'创建账号'}</button><button type="button" className="secondary" disabled={busy} onClick={()=>onBack()}>返回登录</button></form></section></div>
}

export function PasswordChange({onChanged,onLogout}:{onChanged:()=>void;onLogout?:()=>Promise<void>}){
 const [current,setCurrent]=useState('');const [password,setPassword]=useState('');const [confirm,setConfirm]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
 return <section className="panel auth-card"><h2>修改密码</h2><p>首次登录或凭据重置后，需要修改临时密码。至少 12 个字符。</p>{notice&&<p className="notice" role="status">{notice}</p>}<form onSubmit={async e=>{e.preventDefault();if(password!==confirm){setNotice('两次新密码不一致');return}setBusy(true);try{await api('/auth/password',{method:'POST',body:JSON.stringify({current_password:current,new_password:password})});setCurrent('');setPassword('');setConfirm('');onChanged()}catch(e){setNotice((e as Error).message)}finally{setBusy(false)}}}><label>当前密码<input required type="password" autoComplete="current-password" value={current} onChange={e=>setCurrent(e.target.value)}/></label><label>新密码<input required minLength={12} type="password" autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)}/></label><label>确认新密码<input required type="password" autoComplete="new-password" value={confirm} onChange={e=>setConfirm(e.target.value)}/></label><button disabled={busy}>更新密码</button>{onLogout&&<button className="secondary" type="button" onClick={onLogout}>退出</button>}</form></section>
}
