// JDIA minimal local CDP bridge. Own task tabs only; no profile discovery or guards.
import http from 'node:http';
import {timingSafeEqual} from 'node:crypto';
const port = Number(process.env.JDIA_BRIDGE_PORT || 3458), chromePort = Number(process.env.JDIA_CHROME_PORT || 9422);
if (![port,chromePort].every(p=>Number.isInteger(p)&&p>=1024&&p<=65535) || port===chromePort) throw new Error('Invalid loopback ports');
const token = process.env.JDIA_BRIDGE_SESSION || '';
if (token.length < 32 || typeof WebSocket === 'undefined') throw new Error('Bridge requires a local session and Node with native WebSocket');
let socket, connecting, nextId = 0;
const pending = new Map(), owned = new Set(), sessions = new Map();
function sameSecret(value) { const a=Buffer.from(value||''),b=Buffer.from(token); return a.length===b.length && timingSafeEqual(a,b); }
function rpc(method, params={}) {
  if (!socket || socket.readyState!==WebSocket.OPEN) return Promise.reject(new Error('专用 Chrome 未连接'));
  const id=++nextId;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error('浏览器响应超时'));},15000);
    pending.set(id,{resolve,reject,timer});
    socket.send(JSON.stringify({id,method,params}));
  });
}
async function connect() {
  if (socket?.readyState===WebSocket.OPEN) return;
  if (connecting) return connecting;
  connecting=(async()=>{
    const reply=await fetch(`http://127.0.0.1:${chromePort}/json/version`,{signal:AbortSignal.timeout(3000)});
    if (!reply.ok) throw new Error('专用 Chrome 未启动');
    const data=await reply.json(), u=new URL(data.webSocketDebuggerUrl);
    if (u.protocol!=='ws:' || u.hostname!=='127.0.0.1' || Number(u.port)!==chromePort || !u.pathname.startsWith('/devtools/browser/')) throw new Error('Chrome连接身份不匹配');
    socket=new WebSocket(u.href);
    socket.addEventListener('message',event=>{
      const msg=JSON.parse(String(event.data)), item=pending.get(msg.id);
      if (!item) return; clearTimeout(item.timer); pending.delete(msg.id);
      if (msg.error) item.reject(new Error('浏览器命令失败')); else item.resolve(msg.result);
    });
    socket.addEventListener('close',()=>{
      for (const item of pending.values()) {clearTimeout(item.timer);item.reject(new Error('专用 Chrome 已断开'));}
      pending.clear(); sessions.clear();
    });
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Chrome连接超时')),5000);
      socket.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});
      socket.addEventListener('error',()=>{clearTimeout(timer);reject(new Error('Chrome连接失败'));},{once:true});
    });
  })();
  try {await connecting;} finally {connecting=null;}
}
async function session(target) {
  if (!owned.has(target)) throw new Error('仅允许操作本程序创建的任务页');
  if (!sessions.has(target)) {
    const result=await rpc('Target.attachToTarget',{targetId:target,flatten:true}); sessions.set(target,result.sessionId);
    await command('Page.enable',{},target);
  }
  return sessions.get(target);
}
async function command(method,params,target) {
  const sessionId=await session(target);
  // Flattened CDP uses sessionId on the message envelope.
  const id=++nextId;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error('浏览器任务页响应超时：'+method));},15000);
    pending.set(id,{resolve,reject,timer}); socket.send(JSON.stringify({id,method,params,sessionId}));
  });
}
function publicUrl(raw) {
  const u=new URL(raw);
  if (u.protocol!=='https:' || u.hostname!=='www.zhipin.com' || u.username || u.password || (u.port && u.port!=='443')
      || !(u.pathname==='/web/geek/jobs' || /^\/job_detail\/[A-Za-z0-9_-]+\.html$/.test(u.pathname))) throw new Error('不允许的来源地址');
  return u.href;
}
async function dispatch(data) {
  await connect();
  if (data.action==='new') {
    const result=await rpc('Target.createTarget',{url:'about:blank',background:true}); owned.add(result.targetId);return {target:result.targetId};
  }
  if (!owned.has(data.target)) throw new Error('任务页不属于本程序');
  if (data.action==='activate') {
    await rpc('Target.activateTarget',{targetId:data.target}); return {activated:true};
  }
  if (data.action==='close') {
    const result=await rpc('Target.closeTarget',{targetId:data.target}); owned.delete(data.target);sessions.delete(data.target);return result;
  }
  if (data.action==='navigate') {
    const result=await command('Page.navigate',{url:publicUrl(data.url)},data.target);
    if (result.errorText) throw new Error('来源页面导航失败'); return {navigated:true};
  }
  if (data.action==='eval') {
    if (typeof data.code!=='string' || data.code.length>50000) throw new Error('任务表达式无效');
    const result=await command('Runtime.evaluate',{expression:data.code,returnByValue:true,awaitPromise:true},data.target);
    if (result.exceptionDetails) throw new Error('来源页面表达式失败'); return {value:result.result?.value};
  }
  if (data.action==='screenshot') {
    if (!['.job-banner','.location-address'].includes(data.selector)) throw new Error('仅允许岗位字段截图');
    await rpc('Target.activateTarget',{targetId:data.target});
    const geo=await command('Runtime.evaluate',{expression:`(() => {const e=document.querySelector(${JSON.stringify(data.selector)});if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height};})()`,returnByValue:true},data.target);
    const clip=geo.result?.value; if(!clip?.width || !clip?.height)throw new Error('岗位截图区域缺失');
    return await command('Page.captureScreenshot',{format:'png',clip:{...clip,scale:1},captureBeyondViewport:true},data.target);
  }
  throw new Error('不支持的命令');
}
function json(res,status,value) {res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));}
const server=http.createServer(async(req,res)=>{
  if (![ `127.0.0.1:${port}`, `localhost:${port}` ].includes(req.headers.host) || req.headers.origin || !sameSecret(req.headers['x-jdia-bridge'])) return json(res,403,{error:'本机桥接访问被拒绝'});
  if (req.method==='GET' && req.url==='/health') {
    let connected=false; try {await connect();connected=true;} catch {}
    return json(res,200,{service:'jdia-source-bridge',chrome_port:9422,connected});
  }
  if (req.method!=='POST' || req.url!=='/command') return json(res,404,{error:'不支持的入口'});
  let raw='';
  try {
    for await (const part of req) {raw+=part.toString('utf8');if(raw.length>65536)throw new Error('命令过大');}
    const data=JSON.parse(raw);return json(res,200,await dispatch(data));
  } catch(err) {return json(res,400,{error:err.message});}
});
server.listen(port,'127.0.0.1',()=>process.stdout.write(`JDIA source bridge listening on loopback ${port}\n`));
