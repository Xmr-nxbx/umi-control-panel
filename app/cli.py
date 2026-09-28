"""命令行入口与进程生命周期。

可恢复性是硬要求（open-revo 就是栽在「一次异常后永久起不来」）：
  * 单实例用内核互斥，进程死了自动释放，绝不留锁文件；
  * 端口被占就顺延 10 个，并把实际端口写进 data/port；
  * 配置坏掉就改名备份 + 用默认值继续跑；
  * 任何启动期致命异常都落到 data/fatal-*.log，窗口不闪退。
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

from app.config import Config
from app.daemon import Daemon
from app.logx import Log
from app.paths import data_path, ensure_data_dir
from app.server.httpd import Server
from app.singleton import SingleInstance


def _find_free_port(httpd_factory, bind, port):
    for candidate in range(port, port + 10):
        try:
            srv = httpd_factory(candidate)
            return srv, candidate
        except OSError:
            continue
    return None, None


def cmd_probe_acpi(cfg, log):
    """只读枚举 ACPI 命名空间并退出。需要管理员，否则如实报错。"""
    from app.act.channels.ec_acpi import AcpiHandle, EC_METHOD_CANDIDATES
    acpi = AcpiHandle()
    if not acpi.open():
        print('[失败] %s' % acpi.error)
        print('提示：用管理员身份运行  tools\\acpi_probe.bat')
        return 2
    names, err = acpi.enumerate_names()
    if err:
        print('[失败] 枚举出错：%s' % err)
        return 2
    print('命名空间对象数：%d' % len(names))
    hits = [n for n in names if any(m in n.upper() for m in EC_METHOD_CANDIDATES)]
    print('疑似 EC 方法（%d 个）：' % len(hits))
    for h in hits[:60]:
        print('   ', h)
    print('全部方法名（前 120 个）：')
    for n in names[:120]:
        print('   ', n)
    acpi.close()
    return 0


def _anchor_loop():
    """身份锚点进程：只为让 broker 的「同名进程是否存在」校验通过而空转。"""
    import signal
    stop = threading.Event()
    signal.signal(signal.SIGBREAK, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    while not stop.is_set():
        stop.wait(5.0)
    return 0


def _port_open(host, port):
    import socket
    try:
        with socket.create_connection((host, port), 1.0):
            return True
    except OSError:
        return False


def _process_exists(name):
    """用 tasklist 查同名进程，仅在 broker 存活时调用。"""
    import subprocess
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq %s' % name],
                             capture_output=True, text=True, timeout=5,
                             creationflags=0x08000000).stdout or ''
    except Exception:                                     # noqa: BLE001
        return False
    return name.lower() in out.lower()


def ensure_identity_anchor(cfg, log):
    """GCUBridge 会周期性检查「clientId 同名进程」是否存在，不在就踢线。

    兜底通道要用 OcTool 这个 clientId，所以必须有一个叫 OcTool.exe 的进程活着。
    做法：把 python.exe 复制一份改名 OcTool.exe（setup_runtime.ps1 负责生成），
    以 --anchor 空转模式常驻。只在 broker 端口真的活着时才启动，避免白费一个进程。
    """
    if not cfg.get('hardware', 'mqtt', 'enabled', default=True):
        return None
    host = cfg.get('hardware', 'mqtt', 'host', default='127.0.0.1')
    port = int(cfg.get('hardware', 'mqtt', 'port', default=13688))
    if not _port_open(host, port):
        return None
    if _process_exists('OcTool.exe'):
        return None
    from app.paths import RUNTIME_DIR, ROOT_DIR
    anchor = os.path.join(RUNTIME_DIR, 'OcTool.exe')
    if not os.path.exists(anchor):
        log.warn('[兜底通道] 缺少 runtime\\OcTool.exe，GCUBridge 会每 ~26 秒踢线；'
                 '请重跑 scripts\\setup_runtime.ps1')
        return None
    import subprocess
    proc = subprocess.Popen(
        [anchor, os.path.join(ROOT_DIR, 'main.py'), '--anchor'],
        cwd=ROOT_DIR, creationflags=0x08000000)
    log.info('[兜底通道] 已启动身份锚点进程 OcTool.exe (pid=%s)' % proc.pid)
    return proc


def main(argv=None):
    ap = argparse.ArgumentParser(description='Umi Control Panel 常驻服务')
    ap.add_argument('--no-tray', action='store_true', help='不加载托盘')
    ap.add_argument('--no-browser', action='store_true', help='启动后不自动打开面板')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--probe-acpi', action='store_true', help='只读枚举 ACPI 命名空间后退出')
    ap.add_argument('--one-shot', action='store_true', help='打印一次状态 JSON 后退出')
    ap.add_argument('--stop', action='store_true', help='请求正在运行的实例优雅退出')
    ap.add_argument('--anchor', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    ensure_data_dir()
    cfg, cfg_error, cfg_broken = Config.load()
    log = Log('umi-control-panel')
    if cfg_error:
        log.error('配置文件损坏（%s），已改名为 %s 并使用默认值' % (cfg_error, cfg_broken))

    if args.anchor:
        return _anchor_loop()

    if args.probe_acpi:
        return cmd_probe_acpi(cfg, log)

    if args.stop:
        import urllib.request
        port = _current_port(cfg)
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/api/shutdown' % port,
                                        data=b'{}', timeout=3) as r:
                print('已请求停止：%s' % r.read().decode('utf-8', 'replace'))
            return 0
        except Exception as exc:                           # noqa: BLE001
            print('没有找到运行中的实例（%s）' % exc)
            return 1

    inst = SingleInstance()
    if not inst.acquire():
        port = _current_port(cfg)
        print('已有一个实例在运行（端口 %s），为你打开面板。' % port)
        if not args.no_browser:
            webbrowser.open('http://127.0.0.1:%d/' % port)
        return 0

    bind = cfg.get('server', 'bind', default='127.0.0.1')
    want_port = args.port or int(cfg.get('server', 'port', default=8747))
    daemon = Daemon(cfg, log)
    state = {'srv': None, 'tray': None}
    quit_evt = threading.Event()

    def build(port):
        return Server(bind, port, daemon, cfg, lambda: quit_evt.set())

    srv, port = _find_free_port(build, bind, want_port)
    if srv is None:
        log.error('端口 %s~%s 都被占用，无法启动' % (want_port, want_port + 9))
        inst.release()
        return 1
    state['srv'] = srv
    with open(data_path('port'), 'w') as f:
        f.write(str(port))
    cfg.set('server', 'port', port)
    cfg.save()

    if args.one_shot:
        import json
        daemon.tick()
        time.sleep(1.2)
        daemon.tick()
        print(json.dumps(daemon.state(), ensure_ascii=False, indent=2, default=str))
        daemon.stop(restore=False)
        inst.release()
        return 0

    daemon.start()
    srv.start()
    anchor = ensure_identity_anchor(cfg, log)
    url = 'http://%s:%d/' % (bind, port)
    log.info('[面板] %s' % url)

    if cfg.get('tray', 'enabled', default=True) and not args.no_tray:
        try:
            from app.tray.tray import Tray

            def open_panel():
                webbrowser.open(url)

            def set_intent(intent):
                daemon.set_intent(intent)

            tray = Tray(cfg, log, open_panel, set_intent,
                        lambda: quit_evt.set(), lambda m: daemon.set_mode_now(m))
            tray._intent = cfg.get('intent')
            tray.start()
            state['tray'] = tray
            threading.Thread(target=_sync_tray, args=(daemon, tray, quit_evt),
                             name='tray-sync', daemon=True).start()
        except Exception as exc:                           # noqa: BLE001
            log.error('托盘启动失败（面板仍可用）：%r' % (exc,))

    if cfg.get('server', 'open_browser_on_start', default=True) and not args.no_browser:
        webbrowser.open(url)

    def _sig(signum, frame):
        quit_evt.set()

    for sig_name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        import signal
        sig = getattr(signal, sig_name, None)
        if sig:
            try:
                signal.signal(sig, _sig)
            except (ValueError, OSError):
                pass

    log.info('[运行] 按 Ctrl+C 或在面板点「停止面板服务」退出')
    while not quit_evt.is_set():
        quit_evt.wait(0.5)

    log.info('[退出] 开始收尾')
    if anchor:
        try:
            anchor.terminate()
        except OSError:
            pass
    if state['tray']:
        state['tray'].remove()
    daemon.stop()
    srv.stop()
    inst.release()
    log.close()
    return 0


def _current_port(cfg):
    try:
        with open(data_path('port')) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return int(cfg.get('server', 'port', default=8747))


def _sync_tray(daemon, tray, quit_evt):
    last = None
    while not quit_evt.is_set():
        st = daemon.state()
        key = (st.get('tier'), st.get('intent'))
        if key != last:
            tray.update_state(st.get('tier') or 'bal',
                              '%s档 · %s' % (st.get('tier_label') or '?',
                                             st.get('intent') or 'auto'),
                              intent=st.get('intent'))
            last = key
        quit_evt.wait(2.0)
