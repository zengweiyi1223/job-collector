"""BOSS public DOM adapter: own tabs, stable filters, font-derived digits.

Selectors/risk/loading approach reference BossHunter fixed 67a17420; see notices.
No account/chat endpoints, hidden application state or bypass actions.
"""
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
import json
import re
import time
from datetime import datetime, timezone

from boss_font import JS_COLLECT_FONT_SOURCES, collect_font_sources, parse_font_face_urls, build_boss_digit_map
from core import job_identity, public_job_url


class SourceBlocked(RuntimeError):
    pass


class Bridge:
    def __init__(self, token, port=3458):
        if not 1024 <= port <= 65535: raise ValueError('无效本机桥接端口')
        self.token, self.port = token, port

    def call(self, action=None, **params):
        url = f'http://127.0.0.1:{self.port}/' + ('command' if action else 'health')
        raw = json.dumps({'action':action, **params}, ensure_ascii=False).encode() if action else None
        req = Request(url, data=raw, headers={'X-JDIA-Bridge':self.token,'Content-Type':'application/json'}, method='POST' if action else 'GET')
        try:
            with urlopen(req,timeout=22) as reply: return json.load(reply)
        except HTTPError as exc:
            try: reason=json.load(exc).get('error','浏览器命令失败')
            except Exception: reason='浏览器命令失败'
            raise SourceBlocked('bridge_error: '+reason) from exc
        except Exception as exc:
            raise SourceBlocked('浏览器桥接未就绪或响应失败') from exc

    def evaluate(self, target, code): return self.call('eval',target=target,code=code)['value']


RISK_JS = r'''(() => {
 const url=new URL(location.href), title=document.title.trim();
 const expected=document.querySelector('.job-card-wrap,.job-sec-text,.job-detail,.job-primary');
 const kind=url.pathname==='/web/geek/jobs'?'search':/^\/job_detail\/[^/]+\.html$/.test(url.pathname)?'detail':'other';
 const route=/\/web\/passport\//i.test(url.pathname)?'passport':/(?:^|\/)security\.html$/i.test(url.pathname)?'security_html':/(?:^|\/)verify(?:[/.]|$)/i.test(url.pathname)?'verify':/(?:^|\/)captcha(?:[/.]|$)/i.test(url.pathname)?'captcha':/(?:^|\/)security-check(?:[/.]|$)/i.test(url.pathname)?'security_check':'other';
 const result=(reason,signal)=>({reason,signal,page_kind:kind,expected_job_dom:!!expected,route_category:route,ready_state:document.readyState});
 const visible=el=>{
  for(let p=el;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
  const r=el.getBoundingClientRect();return r.width>0&&r.height>0;
 };
 if([...document.querySelectorAll('.geetest_panel,.captcha,[class*="captcha"],[id*="captcha"],iframe[src*="captcha"],iframe[src*="verify"]')].some(visible)) return result('captcha','visible_verification_widget');
 // Route identity, not unrelated query values such as tracking/security tokens.
 if(/(?:^|\/)(?:captcha|security-check|verify)(?:[/.]|$)|\/web\/passport\/|(?:^|\/)security\.html$/i.test(url.pathname)) return result('captcha','verification_path');
 if(/\/web\/user\/login/i.test(url.pathname)) return result('login_required','login_path');
 if(/(?:^|\/)(?:403|forbidden|access-denied)(?:\/|$)/i.test(url.pathname) || /^(403|forbidden|access denied|访问被拒绝|账号异常|账号受限)/i.test(title)) return result('blocked','blocked_path_or_title');
 const status=[...url.searchParams].some(([k,v])=>/^(code|status|error|error_code|status_code)$/.test(k)&&/^(403|forbidden|access-denied)$/i.test(v));if(status)return result('blocked','blocked_status');
 if(!expected){const text=(document.body?.innerText||'').slice(0,10000);
  if(/验证码|安全验证|完成验证/.test(text))return result('captcha','visible_verification_text');
  if(/访问被拒绝|账号异常|账号受限/.test(text))return result('blocked','blocked_text');
  if(/操作频繁|访问频繁|请求频繁|稍后再试|频率限制/.test(text))return result('rate_limit','rate_limit_text');
 }
 return null;
})()'''
LOAD_JS = "JSON.stringify({state:document.readyState,protocol:location.protocol,host:location.hostname,path:location.pathname})"
LIST_JS = r'''(() => JSON.stringify([...document.querySelectorAll('.job-card-wrap')].map(c=>{
 const a=c.querySelector('.job-name'), tags=[...c.querySelectorAll('.tag-list li')].map(x=>x.textContent.trim());
 return {url:a?.getAttribute('href')||'',title:a?.textContent.trim()||'',company:(c.querySelector('.boss-name')||c.querySelector('.company-name'))?.textContent.trim()||'',
 salary:c.querySelector('.job-salary')?.textContent.trim()||'',location:c.querySelector('.company-location')?.textContent.trim()||'',experience:tags[0]||'',education:tags[1]||''};
})))()'''
FILTER_JS = r'''(() => {
 const group=[...document.querySelectorAll('.condition-filter-select')].find(el=>el.querySelector('[ka*="jobType-"]'));
 const active=[...(group?.querySelectorAll('li.active')||[])].map(el=>({label:el.textContent.trim(),key:el.getAttribute('ka')}));
 return JSON.stringify({city:document.querySelector('.cur-city-label')?.textContent.trim()||'',keyword:[...document.querySelectorAll('input')].find(el=>el.placeholder.includes('职位'))?.value||'',active});
})()'''
DETAIL_JS = r'''(() => {
 const text=s=>document.querySelector(s)?.textContent.trim()||'';
 const tags=[...document.querySelectorAll('.info-primary .tag-list span')].map(el=>el.textContent.trim());
 let company=''; for(const a of document.querySelectorAll('.sider-company .company-info a')){const s=a.textContent.trim();if(s&&!s.includes('http')){company=s;break;}}
 // The visible company card uses <p><i ...></i>500-999人</p>.
 // Icon children are valid; keep the scope to this job's own company card.
 const sizes=[...new Set([...document.querySelectorAll('.sider-company p,.sider-company .res-industry-item,.sider-company .company-info-item')].map(el=>el.textContent.trim()).filter(s=>/^[\d\ue000-\uf8ff]+(?:[-－][\d\ue000-\uf8ff]+)?人(?:以上)?$/.test(s)))];
 const employment=[...document.querySelectorAll('.job-banner .job-type,.info-primary .tag-list span')].map(el=>el.textContent.trim()).find(s=>['全职','兼职','实习'].includes(s));
 return JSON.stringify({url:location.pathname,title:text('.info-primary .name h1')||text('.name h1'),salary:text('.info-primary .salary')||text('.salary'),
 company,company_page_title:document.title,company_size:sizes.length===1?sizes[0]:'',experience:tags[0]||'',education:tags[1]||'',employment_type:employment||null,
 work_location:document.querySelector('.location-address')?.innerText.trim()||text('.text-city'),jd:text('.job-sec-text')});
})()'''


def place_fields(text, list_location=''):
    # Actual source location only; no default city copied from query.
    combined = text + ' ' + list_location
    # Only an address-leading municipality can contradict the list city.
    # Do not consume district/street text or a company name ending in 市:
    # 广州南沙区广州市嘉诚... is Guangzhou, not "广州南沙区广州"市.
    explicit=re.match(r'^(?:[\u4e00-\u9fff]{2,5}省)?((?:(?![省市区县镇乡街路村])[\u4e00-\u9fff]){2,7})市',text.strip())
    other_city=explicit and explicit.group(1)!='广州'
    city = '广州' if '广州' in combined and not other_city else None
    m = re.search(r'(天河|海珠|番禺|越秀|荔湾|白云|黄埔|花都|南沙|增城|从化)区', combined)
    return city, m.group(0) if m else None


class BossSource:
    def __init__(self, bridge, stop_event, wait=None):
        self.bridge, self.stop_event = bridge, stop_event
        self.wait = wait or stop_event.wait
        self.owned = []
        self.cleanup_errors = []
        self.manual_handler = None
        self.expected_paths = {}
        self.search_target = None
        self.config = None
        self.risk_events = []
        self.load_events = []

    def pause(self, seconds):
        if self.wait(seconds): raise SourceBlocked('human_stop')

    def risk(self, target):
        if self.stop_event.is_set(): raise SourceBlocked('human_stop')
        def inspect():
            finding = self.bridge.evaluate(target,RISK_JS)
            if isinstance(finding, dict):
                # Categorical evidence only: no full URL, page text, account or token.
                self.risk_events.append({key:finding[key] for key in
                    ('reason','signal','page_kind','expected_job_dom','route_category','ready_state') if key in finding})
                self.risk_events[-1]['observed_at'] = datetime.now(timezone.utc).isoformat()
                self.risk_events = self.risk_events[-20:]
                return finding.get('reason')
            return finding  # Controlled older adapters may return a reason string.
        reason = inspect()
        manually_resumed = False
        while reason == 'captcha' and self.manual_handler:
            self.manual_handler(target)  # Blocks locally: no browser polling.
            if self.stop_event.is_set(): raise SourceBlocked('human_stop')
            reason = inspect()  # One human-requested check.
            manually_resumed = True
        if reason: raise SourceBlocked(str(reason))
        if manually_resumed:
            position = json.loads(self.bridge.evaluate(target,
                "JSON.stringify({protocol:location.protocol,host:location.hostname,path:location.pathname})"))
            if (position.get('protocol') != 'https:' or position.get('host') != 'www.zhipin.com'
                    or position.get('path') != self.expected_paths.get(target)):
                raise SourceBlocked('manual_task_page_changed')
            if self.search_target:
                if target != self.search_target: self.risk(self.search_target)
                facts = json.loads(self.bridge.evaluate(self.search_target,FILTER_JS))
                if not self.filters_match(facts): raise SourceBlocked('manual_search_filters_changed')

    def new(self, url):
        target=self.bridge.call('new')['target']; self.owned.append(target)
        self.expected_paths[target] = urlsplit(url).path
        self.bridge.call('navigate',target=target,url=url)
        self.wait_for_load(target)
        return target

    def wait_for_load(self, target):
        # Normal navigation readiness only, like the fixed reference runtime.
        # No reload, redirect replay, challenge interaction or job-field reads.
        started=time.monotonic();position={}
        def record(outcome, checks):
            path=position.get('path','')
            self.load_events.append({'outcome':outcome,'checks':checks,
                'elapsed_ms':round((time.monotonic()-started)*1000),
                'ready_state':position.get('state','unknown'),
                'page_kind':'detail' if re.fullmatch(r'/job_detail/[A-Za-z0-9_-]+\.html',path) else 'search' if path=='/web/geek/jobs' else 'other',
                'observed_at':datetime.now(timezone.utc).isoformat()})
            self.load_events=self.load_events[-24:]
        # Cold/variable loads can exceed ten seconds. Still bounded: no reload,
        # no job-field reads before complete; explicit restrictions still stop.
        for check in range(1,61):
            self.pause(.5)
            position = json.loads(self.bridge.evaluate(target,LOAD_JS))
            if position.get('protocol') == 'about:' and position.get('path') == 'blank':
                continue  # A newly created tab has not committed navigation yet.
            if position.get('protocol') != 'https:' or position.get('host') != 'www.zhipin.com':
                record('unexpected_origin',check)
                raise SourceBlocked('unexpected_source_origin')
            if position.get('state') == 'complete':
                record('ready',check)
                return  # A loaded verification page goes through risk normally.
        record('timeout',60)
        raise SourceBlocked('source_page_load_timeout')

    def close(self, target):
        self.bridge.call('close',target=target); self.owned.remove(target)
        self.expected_paths.pop(target, None)

    def cleanup(self):
        for target in self.owned[:]:
            try: self.close(target)
            except Exception: self.cleanup_errors.append('owned_tab_close_failed')

    def rows(self, target):
        self.risk(target)
        return json.loads(self.bridge.evaluate(target,LIST_JS))

    def search(self, config):
        self.config = config
        url='https://www.zhipin.com/web/geek/jobs?'+urlencode({'query':config['keyword'],'city':config['city'],'jobType':config['job_type']})
        target=self.new(url); previous=None
        self.search_target = target
        for _ in range(15):
            self.pause(2); self.risk(target)
            facts=json.loads(self.bridge.evaluate(target,FILTER_JS)); rows=self.rows(target)
            good=self.filters_match(facts) and bool(rows)
            signature=[r['url'].split('?')[0] for r in rows]
            if good and previous==signature: return target,rows,facts
            previous=signature if good else None
        raise SourceBlocked('search_filters_or_list_not_ready')

    def filters_match(self, facts):
        return (self.config and facts.get('city') == '广州' and facts.get('keyword') == self.config['keyword']
                and any(v.get('label') == '全职' and v.get('key','').endswith('jobType-1901')
                        for v in facts.get('active',[])))

    def next_rows(self, target, seen):
        self.pause(3); self.risk(target)
        self.bridge.evaluate(target,"window.scrollTo(0,0); window.scrollTo(0,document.body.scrollHeight); window.dispatchEvent(new Event('scroll')); true")
        for _ in range(10):
            self.pause(1); rows=self.rows(target)
            added=[r for r in rows if r['url'].split('?')[0] not in seen]
            if added: return added
        return []

    def decode(self, target, fields):
        wanted={ch for v in fields.values() if isinstance(v,str) for ch in v if '\ue000'<=ch<='\uf8ff'}
        if not wanted: return fields
        urls,sources=collect_font_sources(self.bridge.evaluate(target,JS_COLLECT_FONT_SOURCES))
        def fetch_asset(url):
            u=urlsplit(url)
            if u.scheme!='https' or u.username or u.password or not u.hostname or not any(u.hostname==h or u.hostname.endswith('.'+h) for h in ('zhipin.com','zpimg.com','zhipin.cn')):
                raise SourceBlocked('font_asset_origin_not_allowed')
            req=Request(url,headers={'Referer':'https://www.zhipin.com/'})
            try:
                with urlopen(req,timeout=10) as res:
                    if urlsplit(res.url).hostname!=u.hostname: raise SourceBlocked('font_asset_redirect_not_allowed')
                    body=res.read(3000001)
            except OSError as exc:
                raise SourceBlocked('font_asset_unavailable') from exc
            if len(body)>3000000: raise SourceBlocked('font_asset_too_large')
            return body
        for css in sources[:3]:
            if css.startswith('https://'):
                text=fetch_asset(css).decode('utf-8',errors='replace'); origin=css
            else: text=css;origin='https://www.zhipin.com/'
            urls.extend(urljoin(origin,u) for u in parse_font_face_urls(text))
        for url in list(dict.fromkeys(urls))[:3]:
            mapping=build_boss_digit_map(fetch_asset(url))
            if wanted<=mapping.keys():
                table=str.maketrans(mapping)
                return {k:v.translate(table) if isinstance(v,str) else v for k,v in fields.items()}
        raise SourceBlocked('unknown_source_font')

    def detail(self, row, observer=None):
        self.pause(3); target=self.new(public_job_url(row['url']))
        try:
            raw=None
            for _ in range(12):
                self.pause(2); self.risk(target)
                raw=json.loads(self.bridge.evaluate(target,DETAIL_JS))
                if raw.get('jd') and raw.get('salary') and raw.get('title'): break
            if job_identity(raw.get('url',''))!=job_identity(row['url']): raise SourceBlocked('detail_identity_mismatch')
            raw['salary_raw']=raw.get('salary','')
            decoded=self.decode(target,{k:v for k,v in raw.items() if k!='salary_raw'})
            decoded['salary_raw']=raw['salary_raw']
            if not decoded.get('company'):
                # Page title delimiter matches this same detail; keep list name
                # only if no detail company can be read, never strip nested titles.
                m=re.search(r'」_(.+)招聘-BOSS直聘$',decoded.get('company_page_title',''))
                decoded['company']=m.group(1) if m else row.get('company','')
            decoded['experience']=decoded.get('experience') or row.get('experience')
            decoded['education']=decoded.get('education') or row.get('education')
            decoded['city'],decoded['district']=place_fields(decoded.get('work_location',''),row.get('location',''))
            if decoded['city']!='广州': raise ValueError('实际工作地点无法核验为广州')
            if observer: observer(self.bridge,target,row,decoded)
            return decoded
        finally:
            try: self.close(target)
            except Exception: self.cleanup_errors.append('detail_tab_close_failed')
