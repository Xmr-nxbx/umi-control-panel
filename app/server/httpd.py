"""本地 HTTP 服务：只绑 127.0.0.1，静态文件走白名单，不做任何路径拼接。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.paths import WEB_DIR

MAX_BODY = 64 * 1024
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8'),
          '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json')}
SENSITIVE_KEYS = {'password', 'token', 'secret'}


def _scrub(node):
    if isinstance(node, dict):
        return {k: ('***' if k.lower() in SENSITIVE_KEYS else _scrub(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(x) for x in node]
    return node


def make_handler(daemon, cfg, on_shutdown):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'UmiControlPanel/0.1'

        def log_message(self, fmt, *args):
            daemon.log.info('[http] ' + (fmt % args))

        # ---- 工具 ----
        def _send(self, code, body, ctype='application/json; charset=utf-8'):
            data = body if isinstance(body, bytes) else json.dumps(
                body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_json(self):
            length = int(self.headers.get('Content-Length') or 0)
            if length <= 0 or length > MAX_BODY:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode('utf-8'))
                return parsed if isinstance(parsed, dict) else {}
            except (ValueError, UnicodeDecodeError):
                return {}

        # ---- 路由 ----
        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path in STATIC:
                fname, ctype = STATIC[path]
                try:
                    with open('%s/%s' % (WEB_DIR, fname), 'rb') as f:
                        self._send(200, f.read(), ctype)
                except OSError:
                    self._send(500, {'error': '静态文件缺失：%s' % fname})
                return
            if path == '/api/state':
                self._send(200, daemon.state())
            elif path == '/api/config':
                self._send(200, _scrub(cfg.data))
            elif path == '/api/logs':
                n = 100
                try:
                    n = min(500, int(self.path.split('n=')[-1]))
                except ValueError:
                    pass
                self._send(200, {'lines': daemon.logs(n)})
            elif path == '/api/hardware':
                self._send(200, {'channels': [c.status() for c in daemon.hw.channels],
                                 'state': daemon.hw.snapshot(),
                                 'caps': daemon.hw.capability_map()})
            elif path == '/api/health':
                # 机主不是开发者：出问题时要能一行字讲清楚现状，所以体检结果直接给面板
                from app.cli import health_rows, health_text    # 延迟导入：cli 也 import 了本模块
                rows = health_rows(cfg, daemon.log, state=daemon.state())
                self._send(200, {'text': health_text(rows),
                                 'rows': [{'name': n, 'ok': o, 'detail': d, 'optional': p}
                                          for n, o, d, p in rows]})
            elif path == '/api/history':
                minutes = None
                try:
                    minutes = min(60.0, float(self.path.split('minutes=')[-1]))
                except ValueError:
                    pass
                self._send(200, daemon.history_view(minutes))
            elif path == '/api/ec/probe':
                self._send(200, daemon.hw.ec.probe())
            elif path == '/api/bench':
                self._send(200, daemon.bench_view())
            elif path == '/api/ping':
                # 守护进程靠这个判活：不光看 HTTP 有没有回，还要看调度节拍多久没走
                self._send(200, {'pong': True, 'tick_age_s': daemon.tick_age_s(),
                                 'uptime_s': daemon.state().get('uptime_s')})
            else:
                self._send(404, {'error': 'not found'})

        def do_POST(self):
            path = self.path.split('?', 1)[0]
            body = self._read_json()
            if path == '/api/intent':
                ok, detail = daemon.set_intent(str(body.get('intent', '')))
                self._send(200 if ok else 400, {'ok': ok, 'detail': detail})
            elif path == '/api/sched-profile':
                ok, detail = daemon.set_sched_profile(str(body.get('name', '')))
                self._send(200 if ok else 400, {'ok': ok, 'detail': detail})
            elif path == '/api/mode':
                ok, detail = daemon.set_mode_now(str(body.get('mode', '')))
                self._send(200 if ok else 409, {'ok': ok, 'detail': detail})
            elif path == '/api/tier':
                ok, detail = daemon.set_tier_now(str(body.get('tier', '')))
                self._send(200 if ok else 400, {'ok': ok, 'detail': detail})
            elif path == '/api/action':
                action = str(body.get('action', ''))
                extra = body.get('extra') if isinstance(body.get('extra'), dict) else None
                ok, detail = daemon.hw.send_action(action, extra)
                self._send(200 if ok else 409, {'ok': ok, 'detail': detail})
            elif path == '/api/fan-mode':
                ok, detail = daemon.set_fan_mode(str(body.get('flag', '')))
                self._send(200 if ok else 409, {'ok': ok, 'detail': detail})
            elif path == '/api/bench':
                ok, detail = daemon.start_bench(str(body.get('mode', 'current')))
                self._send(200 if ok else 409, {'ok': ok, 'detail': detail})
            elif path == '/api/sleep-guard':
                daemon.cfg.set('scheduler', 'sleep_guard_idle_s', float(body.get('idle_s', 60)))
                daemon.cfg.save()
                self._send(200, {'ok': True})
            elif path == '/api/shutdown':
                self._send(200, {'ok': True, 'detail': '正在退出'})
                threading.Thread(target=on_shutdown, daemon=True).start()
            else:
                self._send(404, {'error': 'not found'})

        def do_OPTIONS(self):
            self._send(405, {'error': 'method not allowed'})

    return Handler


class Server:
    def __init__(self, bind, port, daemon, cfg, on_shutdown):
        self.httpd = ThreadingHTTPServer((bind, port),
                                         make_handler(daemon, cfg, on_shutdown))
        self.httpd.daemon_threads = True
        self.thread = None
        self.port = port

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       name='http', daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
