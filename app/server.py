"""Loopback companion API and portable static UI.

The public UI receives only allowlisted API endpoints. Browser/CDP commands are
never exposed. Pairing credentials are generated per start, not saved/logged.
"""
from pathlib import Path
import json
import secrets
from urllib.parse import urlsplit

from bottle import Bottle, abort, request, response, static_file

from store import Store


def approved_origins(port, public_origin=None):
    origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
    if public_origin:
        parts = urlsplit(public_origin)
        if (parts.scheme != 'https' or not parts.hostname or parts.username
                or parts.password or parts.path or parts.query or parts.fragment
                or parts.port not in (None, 443)):
            raise ValueError('公开网页来源必须是准确的 HTTPS origin，不带路径')
        origins.add(public_origin)
    return origins


def create_app(store, port=8688, public_origin=None, session_token=None,
               collector=None):
    app = Bottle()
    allowed = approved_origins(port, public_origin)
    token = session_token or secrets.token_urlsafe(32)
    static_root = Path(__file__).resolve().parent / 'web'
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}

    @app.hook('before_request')
    def boundary():
        # DNS rebinding and arbitrary origin requests must fail before any reads.
        if request.get_header('Host') not in hosts:
            abort(403, '本机地址不匹配')
        origin = request.get_header('Origin')
        if origin and origin not in allowed:
            abort(403, '网页来源未配对')
        if request.path.startswith('/api/'):
            # Same-origin GET fetches usually omit Origin. Accept only browser
            # same-origin Fetch Metadata (browser-controlled). Our no-referrer
            # policy may omit Referer; if present it must match. The session
            # token is still required. Public cross-origin fetches need Origin.
            local_get = False
            if (not origin and request.method == 'GET'
                    and request.get_header('Sec-Fetch-Site') == 'same-origin'
                    and request.get_header('Sec-Fetch-Mode') in {'cors', 'same-origin'}):
                referrer = request.get_header('Referer')
                ref = urlsplit(referrer or '')
                local_get = not referrer or (ref.scheme == 'http' and ref.netloc in hosts and not ref.username)
            if origin not in allowed and not local_get:
                abort(403, '接口需要已允许的网页来源')
            if request.method != 'OPTIONS' and request.path != '/api/health':
                supplied = request.get_header('X-JDIA-Session') or ''
                if not secrets.compare_digest(supplied, token):
                    abort(401, '请重新配对本机程序')
        if request.path == '/pair' and origin and origin != f'http://127.0.0.1:{port}' and origin != f'http://localhost:{port}':
            abort(403, '配对码只在本机入口显示')

    @app.hook('after_request')
    def headers():
        response.set_header('Cache-Control', 'no-store')
        response.set_header('X-Content-Type-Options', 'nosniff')
        response.set_header('X-Frame-Options', 'DENY')
        response.set_header('Referrer-Policy', 'no-referrer')
        response.set_header('Content-Security-Policy',
                            f"default-src 'self'; connect-src 'self' http://127.0.0.1:{port} http://localhost:{port}; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        origin = request.get_header('Origin')
        if origin in allowed and request.path.startswith('/api/'):
            response.set_header('Access-Control-Allow-Origin', origin)
            response.set_header('Vary', 'Origin')

    @app.route('/api/<path:path>', method='OPTIONS')
    def preflight(path):
        method = request.get_header('Access-Control-Request-Method')
        if method not in {'GET', 'POST', 'PATCH'}:
            abort(403, '不支持此请求方法')
        requested = {v.strip().lower() for v in (request.get_header('Access-Control-Request-Headers') or '').split(',') if v.strip()}
        if not requested <= {'content-type', 'x-jdia-session'}:
            abort(403, '不支持此请求头')
        response.set_header('Access-Control-Allow-Methods', 'GET, POST, PATCH')
        response.set_header('Access-Control-Allow-Headers', 'Content-Type, X-JDIA-Session')
        if request.get_header('Access-Control-Request-Private-Network') == 'true':
            response.set_header('Access-Control-Allow-Private-Network', 'true')
        response.status = 204
        return ''

    @app.get('/api/health')
    def health():
        return {'service': 'jdia-collector', 'api_version': 1,
                'pairing_required': True, 'collector_ready': collector is not None,
                'browser_connected': collector.health() if collector is not None else False}

    def body():
        length = request.content_length
        if length is None or length < 0 or length > 65536:
            abort(413, '请求过大或长度未知')
        if request.content_type != 'application/json':
            abort(415, '需要 JSON 请求')
        try:
            parsed = json.loads(request.body.read(65537))
        except (ValueError, UnicodeDecodeError):
            abort(400, 'JSON 无效')
        if not isinstance(parsed, dict):
            abort(400, '请求应为对象')
        return parsed

    def integer(name, minimum=0, maximum=1000000):
        raw = request.query.get(name)
        if raw is None or raw == '':
            return None
        try:
            value = int(raw)
        except ValueError:
            abort(400, '筛选数值无效')
        if not minimum <= value <= maximum:
            abort(400, '筛选数值超出范围')
        return value

    @app.get('/api/jobs')
    def jobs():
        try:
            query_fields = request.query.decode('utf-8')
        except UnicodeError:
            abort(400, '筛选编码无效')
        query = query_fields.get('q', '')
        if len(query) > 100:
            abort(400, '关键词过长')
        districts = query_fields.getall('district')
        if len(districts) > 20 or any(len(v) > 20 for v in districts):
            abort(400, '区域筛选无效')
        low, high = integer('salary_low'), integer('salary_high')
        if low is not None and high is not None and low > high:
            abort(400, '薪资下限不能高于上限')
        try:
            result = store.query(query=query, districts=districts,
                                 salary_low=low, salary_high=high,
                                 size_min=integer('size_min'),
                                 follow_state=request.query.get('follow', 'all'))
        except ValueError:
            abort(400, '筛选条件无效')
        # Source publication time was removed from this version by user decision.
        return {'jobs': result, 'total': len(result)}

    @app.patch('/api/jobs/<job_id:re:[A-Za-z0-9_-]+>/follow')
    def follow(job_id):
        data = body()
        if (set(data) != {'state'} or not isinstance(data['state'], str)
                or data['state'] not in {'unmarked', 'follow', 'unfollow'}):
            abort(400, '关注值无效')
        try:
            store.follow('boss', job_id, data['state'])
        except KeyError:
            abort(404, '岗位不存在')
        return {'saved': True, 'state': data['state']}

    @app.get('/api/runs')
    def runs():
        return {'runs': store.recent_runs()}

    @app.get('/api/runs/<run_id:re:[A-Za-z0-9_-]+>/items')
    def items(run_id):
        return {'items': store.items(run_id)}

    @app.post('/api/collect')
    def collect():
        data = body()
        if collector is None:
            abort(409, '新采集器尚未接入，不会调用参考版')
        try:
            return collector.start(data)
        except ValueError as exc:
            abort(400, str(exc))
        except RuntimeError as exc:
            abort(409, str(exc))

    @app.post('/api/collect/resume')
    def resume():
        data = body()
        if collector is None: abort(409, '采集器尚未接入')
        if set(data) != {'run_id'} or not isinstance(data['run_id'], str) or not data['run_id']:
            abort(400, '需要正在等待验证的轮次')
        try: return collector.resume(data['run_id'])
        except RuntimeError as exc: abort(409, str(exc))

    @app.post('/api/collect/stop')
    def stop():
        body()
        if collector is None:
            abort(409, '没有已接入的采集器')
        return collector.stop()

    @app.get('/pair')
    def pairing():
        # token_urlsafe produces text safe for this simple text-only local page.
        response.content_type = 'text/html; charset=UTF-8'
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<title>JDIA 本机配对</title><h1>本机配对</h1>'
                '<p>将以下配对码复制到查询网页。程序重启后需要重新配对。</p>'
                f'<pre>{token}</pre><p>配对码只用于本机程序，不是 BOSS 登录凭据。</p>'
                '<a href="/">打开本机查询网页</a></html>')

    @app.get('/')
    def index():
        return static_file('index.html', root=static_root)

    @app.get(r'/<asset:re:(?:app\.js|style\.css)>')
    def asset(asset):
        return static_file(asset, root=static_root)

    return app


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='JDIA 本机查询接口（采集尚未接入）')
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8688)
    parser.add_argument('--public-origin')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('端口应为1024～65535')
    # This preparation entry does not start Chrome, collect, or install anything.
    create_app(Store(args.db), args.port, args.public_origin).run(
        host='127.0.0.1', port=args.port, quiet=True, debug=False, reloader=False)
