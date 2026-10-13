export const multiuser=import.meta.env.VITE_MULTIUSER==='true';
let csrf='';let epoch=0;const pending=new Set<AbortController>();
export function setSession(token:string){csrf=token;epoch++}
export function clearSession(){csrf='';epoch++;pending.forEach(controller=>controller.abort());pending.clear()}
export async function api(path:string,options:RequestInit={}) {
 const controller=new AbortController();pending.add(controller);const current=epoch;
 try{
  const headers=new Headers(options.headers);if(!(options.body instanceof FormData))headers.set('Content-Type','application/json');
  if(multiuser&&csrf)headers.set('X-CSRF-Token',csrf);
  const r=await fetch('/api'+path,{...options,headers,credentials:'same-origin',signal:options.signal||controller.signal});
  if(current!==epoch&&path!=='/auth/login')throw new Error('账号已切换，请重新操作');
  if(!r.ok){let e;try{e=await r.json()}catch{e={message:'服务暂不可用'}}
   if(multiuser&&r.status===401&&path!=='/auth/login')window.dispatchEvent(new Event('workspace-session-expired'));
   throw new Error(e.message||e.detail||'请求失败')
  }
  return r.status===204?null:r.json();
 }finally{pending.delete(controller)}
}
