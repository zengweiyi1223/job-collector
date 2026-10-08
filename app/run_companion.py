"""Project-local companion: query API + private bridge; never auto-collects."""
from pathlib import Path
import argparse
import os
import secrets
import socket
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from collector import Collector
from server import create_app
from source import Bridge
from store import Store


def reference_idle():
    from urllib.request import urlopen
    import json
    try:
        with urlopen('http://127.0.0.1:8687/api/workbench',timeout=2) as reply:
            task=json.load(reply).get('task')
        return not task or task.get('status') not in {'running','stopping'}
    except OSError:
        return True  # No reference installed/running is normal for other users.


def main():
    parser=argparse.ArgumentParser(description='JDIA 本机配套程序')
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--node',type=Path,required=True)
    parser.add_argument('--public-origin')
    parser.add_argument('--port',type=int,default=8688)
    parser.add_argument('--bridge-port',type=int,default=3458)
    parser.add_argument('--chrome-port',type=int,default=9422)
    parser.add_argument('--validation-evidence',type=Path)
    parser.add_argument('--validation-detail-limit',type=int,default=20)
    args=parser.parse_args()
    if not args.node.is_file(): parser.error('缺少项目 Node 运行时；不会自动安装')
    ports=(args.port,args.bridge_port,args.chrome_port)
    if len(set(ports))!=3 or not all(1024<=p<=65535 for p in ports): parser.error('需要三个不同的本机端口，范围1024～65535')
    for port in (args.port,args.bridge_port):
        with socket.socket() as guard:
            guard.settimeout(.5)
            if guard.connect_ex(('127.0.0.1',port))==0:
                raise RuntimeError(f'端口{port}已占用，不终止未知进程')
    token=secrets.token_urlsafe(32)
    child_env=os.environ.copy();child_env['JDIA_BRIDGE_SESSION']=token
    child_env['JDIA_BRIDGE_PORT']=str(args.bridge_port)
    child_env['JDIA_CHROME_PORT']=str(args.chrome_port)
    bridge_path=Path(__file__).resolve().with_name('bridge.mjs')
    node=subprocess.Popen([str(args.node),str(bridge_path)],env=child_env)
    try:
        store=Store(args.db);bridge=Bridge(token,port=args.bridge_port)
        observer=None
        if args.validation_evidence:
            import base64,json
            evidence=args.validation_evidence.resolve()
            project=Path(__file__).resolve().parents[2]
            if not evidence.is_relative_to(project/'docs/evidence') or evidence.exists():
                raise ValueError('验证目录必须是项目证据区中未占用的路径')
            evidence.mkdir(parents=True)
            observed=[]
            capture_disabled=False
            def observer(client,target,row,facts):
                nonlocal capture_disabled
                if len(observed)>=3 or capture_disabled:return
                index=len(observed)+1
                try:
                    banner=client.call('screenshot',target=target,selector='.job-banner')
                    (evidence/f'job-{index}-banner.png').write_bytes(base64.b64decode(banner['data']))
                    has_address=client.evaluate(target,"Boolean(document.querySelector('.location-address'))")
                    if has_address:
                        address=client.call('screenshot',target=target,selector='.location-address')
                        (evidence/f'job-{index}-location.png').write_bytes(base64.b64decode(address['data']))
                except Exception as exc:
                    capture_disabled=True
                    (evidence/'capture-failure.json').write_text(json.dumps({'job_link':facts.get('url'),'error':str(exc),'oracle':'Not Passed; capture disabled, source data validation remains required'},ensure_ascii=False),encoding='utf-8')
                    return
                observed.append({**{k:facts.get(k) for k in ['url','title','company','salary','salary_raw','work_location','city','district','company_size']},'location_screenshot':bool(has_address)})
                (evidence/'observed-fields.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        collector=Collector(store,bridge,observer=observer,reference_idle=reference_idle,
                            detail_limit=args.validation_detail_limit)
        app=create_app(store,port=args.port,public_origin=args.public_origin,collector=collector)
        app.run(host='127.0.0.1',port=args.port,quiet=True,debug=False,reloader=False)
    finally:
        if node.poll() is None:
            node.terminate()
            try: node.wait(timeout=5)
            except subprocess.TimeoutExpired: node.kill();node.wait(timeout=5)


if __name__=='__main__': main()
