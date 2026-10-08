"""One manual, durable bounded run. Existing source snapshots stay unchanged."""
from threading import Event, Lock, Thread
from uuid import uuid4
import json
import time

from core import CollectionBudget, InvalidJobLink, job_identity
from source import BossSource, SourceBlocked


def normalize_config(data):
    if not isinstance(data,dict) or set(data)-{'keyword','city','job_type'}:
        raise ValueError('只支持关键词、城市、求职类型配置')
    keyword=data.get('keyword','')
    if not isinstance(keyword,str) or not 1<=len(keyword.strip())<=80:
        raise ValueError('请输入1～80字的岗位关键词')
    if data.get('city')!='101280100' or data.get('job_type')!='1901':
        raise ValueError('首版只支持已验证的广州/全职')
    return {'keyword':keyword.strip(),'city':'101280100','job_type':'1901'}


class Collector:
    def __init__(self,store,bridge,source_factory=BossSource,observer=None,reference_idle=None,detail_limit=20,manual_timeout=600):
        self.store,self.bridge,self.source_factory=store,bridge,source_factory
        self.observer,self.reference_idle=observer,reference_idle or (lambda:True)
        self.detail_limit=CollectionBudget(detail_limit=detail_limit).detail_limit
        self.lock,self.stop_event=Lock(),Event()
        self.thread=None
        self.resume_event, self.state_lock = Event(), Lock()
        self.paused_run_id = None
        self.manual_timeout = manual_timeout
        # Interrupted process runs remain discoverable after a service restart.
        for run in store.recent_runs():
            if run['status'] in {'running','paused'}:
                store.finish_run(run['id'],'interrupted','服务重启，未完成的读取不自动继续',json.loads(run['counts_json']))

    def health(self):
        try: return self.bridge.call().get('connected',False)
        except SourceBlocked: return False

    def start(self,data):
        config=normalize_config(data)
        if not self.lock.acquire(blocking=False): raise RuntimeError('已有采集任务，请等待或停止')
        try:
            if not self.reference_idle(): raise RuntimeError('参考版正在采集，请先结束该任务')
            run_id=uuid4().hex
            self.stop_event.clear();self.resume_event.clear();self.store.start_run(run_id,{**config,'detail_limit':self.detail_limit})
            self.thread=Thread(target=self._run,args=(run_id,config),daemon=True)
            self.thread.start()
            return {'run_id':run_id,'status':'running','limits':{'batches':3,'new_details':self.detail_limit}}
        except Exception:
            self.lock.release();raise

    def stop(self):
        self.stop_event.set();self.resume_event.set();return {'stop_requested':True}

    def resume(self, run_id):
        with self.state_lock:
            if self.paused_run_id != run_id or self.stop_event.is_set():
                raise RuntimeError('该轮没有等待人工验证，请刷新任务记录')
            self.paused_run_id = None
            self.resume_event.set()
        return {'run_id':run_id,'continue_requested':True}

    def wait_for_manual(self, run_id, target, counts):
        with self.state_lock:
            self.resume_event.clear()
            self.paused_run_id = run_id
        self.store.update_counts(run_id, counts)
        self.store.run_state(run_id,'paused','captcha_manual_verification')
        self.bridge.call('activate',target=target)  # Show only this owned task tab.
        deadline = time.monotonic() + self.manual_timeout
        try:
            while not self.resume_event.wait(.2):
                if self.stop_event.is_set(): raise SourceBlocked('human_stop')
                if time.monotonic() >= deadline: raise SourceBlocked('manual_verification_timeout')
            if self.stop_event.is_set(): raise SourceBlocked('human_stop')
        finally:
            with self.state_lock:
                self.paused_run_id = None
        if not self.reference_idle(): raise SourceBlocked('reference_collection_active')
        self.store.run_state(run_id,'running','')

    def _run(self,run_id,config):
        budget=CollectionBudget(detail_limit=self.detail_limit);source=self.source_factory(self.bridge,self.stop_event)
        counters={'skipped_existing':0,'duplicate':0,'filtered':0,'failed':0}
        ordinal=0;status='completed';reason='three_batches';seen_links=set()
        def snapshot():
            return {**counters,'batches':budget.batches,'detail_attempts':budget.detail_attempts,
                    'inserted':budget.inserted,'observed':ordinal,
                    **({'risk_events':source.risk_events} if getattr(source,'risk_events',[]) else {}),
                    **({'load_events':source.load_events} if getattr(source,'load_events',[]) else {})}
        source.manual_handler = lambda target: self.wait_for_manual(run_id,target,snapshot())
        try:
            if not self.health(): raise SourceBlocked('dedicated_chrome_not_connected')
            worker,rows,filters=source.search(config)
            while budget.begin_batch():
                for row in rows:
                    ordinal+=1
                    identity=None;outcome='failed';why=''
                    try:
                        if self.stop_event.is_set(): raise SourceBlocked('human_stop')
                        if not self.reference_idle(): raise SourceBlocked('reference_collection_active')
                        identity=job_identity(row['url']);seen_links.add(row['url'].split('?')[0])
                        decision=budget.decide(row['url'],self.store.exists(*identity))
                        if decision=='already_exists_not_checked':
                            self.store.seen_again(*identity);counters['skipped_existing']+=1
                            outcome=decision;why='已入库，未读取旧详情或更新旧内容'
                        elif decision=='duplicate_in_run':
                            counters['duplicate']+=1;outcome=decision;why='本轮相同来源ID'
                        elif decision=='detail_budget_reached':
                            outcome=decision;why=f'{self.detail_limit}次新详情尝试预算已达到'
                        else:
                            try:
                                facts=source.detail(row,self.observer)
                                saved=self.store.add_job(facts)
                                budget.detail_finished(row['url'],saved)
                                outcome='inserted' if saved else 'already_exists_not_checked'
                                why='新岗位采集时快照' if saved else '并发已入库，不覆盖已有内容'
                            except ValueError as exc:
                                budget.detail_finished(row['url'],False)
                                counters['filtered']+=1;outcome='filtered';why=str(exc)
                            except SourceBlocked:
                                budget.detail_finished(row['url'],False);raise
                            except Exception:
                                budget.detail_finished(row['url'],False)
                                counters['failed']+=1;outcome='failed';why='详情处理或保存失败，未覆盖有效数据'
                    except InvalidJobLink:
                        counters['filtered']+=1;outcome='filtered';why='无法识别允许的公开岗位链接'
                    except SourceBlocked as exc:
                        outcome='blocked';why=str(exc);raise
                    finally:
                        self.store.run_item(run_id,ordinal,budget.batches,identity[1] if identity else None,
                                            row.get('title',''),row.get('company',''),outcome,why)
                        self.store.update_counts(run_id,snapshot())
                if budget.detail_attempts>=self.detail_limit:
                    reason='new_detail_limit';break
                if budget.batches>=3: break
                rows=source.next_rows(worker,seen_links)
                if not rows: reason='no_more_new_ids';break
        except SourceBlocked as exc:
            reason=str(exc);status='stopped' if reason=='human_stop' else 'blocked'
        except Exception:
            status='failed';reason='采集任务失败，已保存记录保留；查看本机连接与任务记录'
        finally:
            source.cleanup()
            counts=snapshot()
            if source.cleanup_errors: counts['cleanup_errors']=len(source.cleanup_errors)
            self.store.finish_run(run_id,status,reason,counts)
            with self.state_lock: self.paused_run_id = None
            self.lock.release()
