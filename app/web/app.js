'use strict';
const API = location.protocol==='http:' && ['127.0.0.1','localhost'].includes(location.hostname)
  ? location.origin : 'http://127.0.0.1:8688';
let sessionToken = '', connected = false, collectorReady = false, timer = null;
const $ = id => document.getElementById(id);
$('pair-link').href = API + '/pair';
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').className = error ? 'error' : 'success';
}
async function api(path, options = {}) {
  const headers = {...options.headers};
  if (sessionToken) headers['X-JDIA-Session'] = sessionToken;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  let reply;
  try { reply = await fetch(API + path, {...options, headers, cache: 'no-store'}); }
  catch { throw new Error('无法访问本机程序。请检查程序是否启动，以及浏览器的本地网络访问权限。'); }
  if (!reply.ok) {
    if (reply.status === 401) throw new Error('配对码已失效，请重新配对。');
    if (reply.status === 403) throw new Error('网页来源未获本机程序允许，请核对配套程序的公开网页配置。');
    throw new Error(`请求失败（${reply.status}）。请检查输入或采集器状态。`);
  }
  return reply.json();
}
function displayTime(raw) {
  if (!raw) return '未知';
  const value = new Date(raw);
  return Number.isNaN(value.getTime()) ? raw : value.toLocaleString('zh-CN');
}
function safeJobLink(raw) {
  try {
    const u = new URL(raw);
    if (u.protocol !== 'https:' || u.hostname !== 'www.zhipin.com' || u.username || u.password || !/^\/job_detail\/[A-Za-z0-9_-]+\.html$/.test(u.pathname)) return null;
    return 'https://www.zhipin.com' + u.pathname;
  } catch { return null; }
}
function renderJob(job) {
  const card = node('article', undefined, 'job');
  card.dataset.jobId = job.source_job_id;
  const top = node('div', undefined, 'job-top');
  const title = node('div');
  title.append(node('h3', job.title), node('p', job.company, 'company'));
  top.append(title, node('div', job.salary, 'salary')); card.append(top);
  const tags = node('div', undefined, 'tags');
  [job.work_location, job.experience, job.education, job.company_size || '公司规模未知', job.employment_type].filter(Boolean).forEach(text => tags.append(node('span', text, 'tag')));
  card.append(tags);
  card.append(node('p', `首次采集：${displayTime(job.first_seen_at)}`, 'time'));
  const actions = node('div', undefined, 'job-actions');
  const label = node('label', '关注状态');
  const select = node('select'); select.setAttribute('aria-label', `${job.title}关注状态`);
  [['unmarked', '未标记'], ['follow', '关注'], ['unfollow', '不关注']].forEach(([value, text]) => {const o = node('option', text); o.value = value; select.append(o);});
  select.value = job.follow_state;
  select.addEventListener('change', async () => {
    select.disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(job.source_job_id)}/follow`, {method:'PATCH', body:JSON.stringify({state:select.value})});
      notice('关注状态已保存在本机。'); await queryJobs();
    } catch (e) {select.value = job.follow_state; notice(e.message, true);}
    finally {select.disabled = false;}
  });
  label.append(select); actions.append(label);
  const url = safeJobLink(job.url);
  if (url) {const a = node('a', '查看原岗位 ↗'); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; actions.append(a);}
  card.append(actions);
  const details = node('details'); details.append(node('summary', '展开 JD · 采集时快照'), node('div', job.jd, 'jd')); card.append(details);
  return card;
}
async function queryJobs() {
  if (!connected) throw new Error('请先连接本机程序。');
  const params = new URLSearchParams({q:$('q').value.trim(), follow:$('follow').value});
  document.querySelectorAll('input[name="district"]:checked').forEach(el => params.append('district', el.value));
  [['salary-low','salary_low'], ['salary-high','salary_high']].forEach(([id,key]) => {if ($(id).value !== '') params.set(key, String(Number($(id).value)*1000));});
  if ($('size').value) params.set('size_min', $('size').value);
  const data = await api('/api/jobs?' + params);
  $('jobs').replaceChildren(); $('total').textContent = `${data.total} 条`;
  if (!data.jobs.length) $('jobs').append(node('p', '没有符合条件的岗位。可放宽本地筛选，或完成一次采集。', 'empty'));
  data.jobs.forEach(job => $('jobs').append(renderJob(job)));
}
const statuses = {running:'采集中',paused:'等待本人验证',completed:'完成',stopped:'已停止',blocked:'来源受限',failed:'失败',interrupted:'程序中断'};
const outcomes = {inserted:'新增',already_exists_not_checked:'已入库，未复查',duplicate_in_run:'本轮重复',detail_budget_reached:'达到详情上限',filtered:'未入库',failed:'处理失败',blocked:'来源受限'};
const reasons = {captcha:'来源出现验证，本轮已结束',captcha_manual_verification:'请检查专用 Chrome 任务页；页面正常或本人验证完成后可点击继续',manual_verification_timeout:'等待本人验证超时，本轮已结束',manual_task_page_changed:'验证后未回到原任务页，本轮已结束',manual_search_filters_changed:'搜索条件已改变，本轮已结束',new_detail_limit:'达到本轮新详情预算',three_batches:'达到三批列表上限',no_more_new_ids:'未发现更多新列表 ID',human_stop:'本人停止',dedicated_chrome_not_connected:'专用 Chrome 未连接',unknown_source_font:'来源字体无法解释，已停止',font_asset_unavailable:'来源字体资源不可用，已停止'};
reasons.source_page_load_timeout='任务页加载超时，本轮已结束';
reasons.unexpected_source_origin='任务页进入未知来源，本轮已结束';
async function refreshRuns() {
  const data = await api('/api/runs');
  $('runs').replaceChildren();
  for (const run of data.runs.slice(0,3)) {
    const details = node('details');
    const statusLabel=run.reason==='source_page_load_timeout'?'页面加载超时':statuses[run.status] || run.status;
    details.append(node('summary', `${displayTime(run.started_at)} · ${statusLabel}`));
    let counts = {}; try { counts = JSON.parse(run.counts_json); } catch { /* old/unknown counts stay unknown */ }
    details.append(node('p', `新增 ${counts.inserted ?? '未知'} · 新详情尝试 ${counts.detail_attempts ?? '未知'} · 列表批次 ${counts.batches ?? '未知'}${run.reason ? ' · '+(reasons[run.reason] || run.reason) : ''}`));
    if(run.reason==='source_page_load_timeout'){
      const load=counts.load_events?.at(-1);
      details.append(node('p',load?`最后加载状态：${load.ready_state} · 等待约${Math.round(load.elapsed_ms/1000)}秒。页面尚未加载完成；本条未入库，尚不能据此认定访问限制。`:'这轮未保存加载状态。页面等待超时，本条未入库；尚不能据此认定访问限制。'));
    }
    const risk = counts.risk_events?.at(-1);
    if (risk) {
      const signals = {visible_verification_widget:'检测到可见验证组件',verification_path:'任务页进入验证地址',visible_verification_text:'页面显示验证提示',login_path:'任务页进入登录地址',blocked_path_or_title:'页面提示访问受限',blocked_status:'来源返回访问受限状态',blocked_text:'页面显示访问受限提示',rate_limit_text:'页面显示频率限制'};
      details.append(node('p', `最近触发依据：${signals[risk.signal] || '来源风险提示'}。这是触发时的记录，页面随后可能改变。`));
      if (risk.route_category || risk.ready_state) {
        const routes = {passport:'账号入口',security_html:'安全页面',verify:'验证页面',captcha:'验证码页面',security_check:'安全检查页面',other:'其他页面'};
        details.append(node('p', `触发页面：${routes[risk.route_category] || '未知'} · 加载状态：${risk.ready_state || '未记录'}${risk.observed_at ? ' · '+displayTime(risk.observed_at) : ''}`));
      }
    }
    details.addEventListener('toggle', async () => {
      if (!details.open || details.dataset.loaded) return;
      try {
        const result = await api(`/api/runs/${encodeURIComponent(run.id)}/items`);
        const table = node('table'); const heading = node('tr');
        ['批次','岗位 / 公司','结果 / 原因'].forEach(text => heading.append(node('th',text))); table.append(heading);
        result.items.forEach(item => {const tr=node('tr');const label=item.reason==='source_page_load_timeout'?'页面加载超时':outcomes[item.outcome] || item.outcome; tr.append(node('td',String(item.batch)),node('td',`${item.title || '未知'} / ${item.company || '未知'}`),node('td',`${label} · ${reasons[item.reason] || item.reason || ''}`)); table.append(tr);});
        details.append(table); details.dataset.loaded = '1';
      } catch(e) {notice(e.message,true);}
    });
    $('runs').append(details);
  }
  const paused = data.runs.find(run => run.status === 'paused');
  $('verification-notice').hidden = !paused;
  $('resume-button').disabled = !paused;
  $('resume-button').dataset.runId = paused?.id || '';
  const running = data.runs.some(run => ['running','paused'].includes(run.status));
  $('collect-button').disabled = !collectorReady || running; $('stop-button').disabled = !running;
  return running;
}
$('pair-form').addEventListener('submit', async e => {
  e.preventDefault(); sessionToken = $('token').value.trim();
  try {
    const health = await api('/api/health');
    if (health.service !== 'jdia-collector' || health.api_version !== 1) throw new Error('本机服务身份不匹配。');
    connected = true; collectorReady = health.collector_ready === true;
    await queryJobs(); if (await refreshRuns()) watchRun();
    $('token').value = ''; $('connection').textContent = '本机已连接';
    notice(collectorReady ? '已连接，可以采集和查询。' : '已连接查询接口。新采集器尚未接入，当前为开发预览。');
  } catch (err) {connected=false; sessionToken=''; collectorReady=false; $('collect-button').disabled=true; $('connection').textContent='未连接'; notice(err.message,true);}
});
$('query-form').addEventListener('submit', async e => {e.preventDefault(); try {await queryJobs(); await refreshRuns();} catch(err) {notice(err.message,true);}});
$('collect-form').addEventListener('submit', async e => {
  e.preventDefault(); if (!connected || !collectorReady) return;
  $('collect-button').disabled=true;
  try {
    await api('/api/collect',{method:'POST',body:JSON.stringify({keyword:$('keyword').value.trim(),city:$('city').value,job_type:$('job-type').value})});
    notice('采集已启动。请保持专用 Chrome 登录，不要切换任务页条件。');
    watchRun();
    await refreshRuns();
  } catch(err) {notice(err.message,true); $('collect-button').disabled=!collectorReady;}
});
$('stop-button').addEventListener('click', async()=>{try {await api('/api/collect/stop',{method:'POST',body:'{}'}); notice('已请求停止，等待当前读取结束。');} catch(err){notice(err.message,true);}});
function watchRun() {
  if (timer) return;
  timer=setInterval(async()=>{try {const running=await refreshRuns(); if(!running){clearInterval(timer); timer=null; await queryJobs(); notice('本轮已结束，请查看采集记录。');}} catch(err) {clearInterval(timer); timer=null; notice(err.message,true);}},3000);
}
$('resume-button').addEventListener('click', async()=>{
  const runId = $('resume-button').dataset.runId;
  $('resume-button').disabled=true;
  try {
    await api('/api/collect/resume',{method:'POST',body:JSON.stringify({run_id:runId})});
    notice('已请求检查验证结果；尚未通过时会继续等待。');
    watchRun(); await refreshRuns();
  } catch(err) {notice(err.message,true); await refreshRuns();}
});
