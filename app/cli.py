"""命令行入口与进程生命周期。

可恢复性是硬要求（open-revo 就是栽在「一次异常后永久起不来」）：
  * 单实例用内核互斥，进程死了自动释放，绝不留锁文件；
  * 端口被占就顺延 10 个，并把实际端口写进 data/port；
  * 配置坏掉就改名备份 + 用默认值继续跑；
  * 任何启动期致命异常都落到 data/fatal-*.log，窗口不闪退。
"""
import argparse
import collections
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


def cmd_ec_test(cfg, log):
    """EC 只读自检：验证通道、打印关键寄存器读数（不写任何东西）。"""
    from app.act.channels.ec_gpd import EcChannel
    ch = EcChannel(cfg, log)
    detail = ch.probe()
    print('通道状态：%s' % detail.get('state'))
    print('说明：%s' % detail.get('reason'))
    if not ch.alive:
        ch.close()
        return 2
    ch.tick()
    derived = ch.read()
    print('\n--- 语义值 ---')
    for key in ('fan_rpm', 'fan2_rpm', 'fan_duty_l', 'fan_duty_r', 'fan_ctl_byte',
                'fan_mode_flag', 'fan_alert', 'pl1', 'pl2', 'pl1_setting', 'pl2_setting',
                'pl4_setting', 'vrm_limit', 'mode', 'mode_index', 'silent_mode',
                'battery_pct_ec', 'battery_temp_c', 'battery_temp_raw', 'battery_cycles',
                'charge_limit_up', 'charge_limit_down', 'project_id', 'module_id',
                'ec_power_source'):
        if derived.get(key) is not None:
            print('  %-18s = %s' % (key, derived[key]))
    print('\n--- 原始寄存器读数（按名字，地址来自本机生成的表）---')
    for group, values in sorted(ch.raw_values().items()):
        print('  [%s]' % group)
        for name, value in sorted(values.items()):
            print('    %-44s = %3d  (0x%02X)' % (name, value, value))
    print('\n共发送 %d 次 ECREAD 请求；错误=%s' % (ch.dev.reads, ch.dev.error))
    ch.close()
    return 0


def _http_json(port, path, body=None, timeout=10):
    import json
    import urllib.request
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _print_bench_table(view):
    rows = view.get('tiers') or []
    base = view.get('baseline') or {}
    print('\n基准（=100 分）：单线程 %s Mops/s，多线程 %s Mops/s，短任务 %s ms，内存 %s MB/s' % (
        base.get('single_mops'), base.get('multi_mops'), base.get('burst_ms'),
        base.get('mem_mb_s')))
    print('%-6s %-7s %-9s %-9s %-9s %-9s %-8s %-6s' % (
        '档位', '总分', '单线程', '多线程', '短任务ms', '内存MB/s', '频率MHz', '最高温'))
    for row in rows:
        r = row.get('record') or {}
        s = row.get('score') or {}
        print('%-6s %-7s %-9s %-9s %-9s %-9s %-8s %-6s' % (
            row.get('label') or row.get('tier'),
            s.get('overall') if s.get('overall') is not None else '-',
            r.get('single_mops'), r.get('multi_mops'), r.get('burst_ms'), r.get('mem_mb_s'),
            r.get('clock_mhz') or '-', r.get('temp_after_c') or '-'))
    verdict = view.get('verdict')
    if verdict:
        print('\n[结论] %s' % verdict.get('reason'))


def cmd_bench(cfg, log, mode):
    """跑分对比。面板在跑就交给面板（避免两个进程抢电源设置），否则本机独立跑。"""
    port = _current_port(cfg)
    if _port_open('127.0.0.1', port):
        print('检测到面板在运行（端口 %d），由它执行跑分。' % port)
        try:
            _http_json(port, '/api/bench', {'mode': mode})
        except Exception as exc:                           # noqa: BLE001
            print('[失败] 无法启动跑分：%s' % exc)
            return 1
        last = ''
        while True:
            time.sleep(2.0)
            try:
                view = _http_json(port, '/api/bench')
            except Exception as exc:                       # noqa: BLE001
                print('[失败] 读取跑分状态失败：%s' % exc)
                return 1
            job = view.get('job') or {}
            line = '%s %s%% %s' % (job.get('label') or '', job.get('pct') or 0,
                                   job.get('step') or '')
            if line != last:
                print('  ' + line)
                last = line
            if not job.get('running'):
                if job.get('error'):
                    print('[失败] %s' % job['error'])
                    return 1
                _print_bench_table(view)
                return 0
    return cmd_bench_local(cfg, log, mode)


def cmd_bench_local(cfg, log, mode):
    from app.act.power import PowerExecutor
    from app.bench import Bench, best_of_each_tier, power_verdict, save_record, set_baseline
    from app.daemon import BENCH_COOLDOWN_S, BENCH_SETTLE_S, COMPARE_TIERS
    from app.policy.scheduler import TIER_LABELS
    from app.sense.clock import ClockSense
    from app.sense.thermal import Thermal

    tiers = list(COMPARE_TIERS) if mode == 'compare' else [None]
    power = PowerExecutor(log)
    thermal, clock = Thermal(), ClockSense()
    bench = Bench(log=log, thermal=thermal, clock=clock)
    power.capture_baseline(['balanced', 'high_perf'])
    before = power.external_scheme()
    results = []
    run_id = int(time.time())
    try:
        for i, tier in enumerate(tiers):
            if tier:
                label = TIER_LABELS.get(tier, tier)
                print('[%d/%d] 应用 %s 档并稳定 %ds…' % (i + 1, len(tiers), label, BENCH_SETTLE_S))
                power.apply(tier, cfg['tiers'][tier])
                time.sleep(BENCH_SETTLE_S)
            else:
                label = '当前设置'
                print('[%d/%d] 保持当前电源设置，稳定 %ds…' % (i + 1, len(tiers), BENCH_SETTLE_S))
                time.sleep(BENCH_SETTLE_S)
            record = bench.run(label=label, tier=tier or 'current',
                               progress=lambda m, p: print('    %3d%% %s' % (p, m)))
            record['profile'] = dict(cfg['tiers'].get(tier) or {})
            record['run_id'] = run_id
            save_record(record)
            results.append(record)
            print('    → 单线程 %s Mops/s，多线程 %s Mops/s，短任务 %s ms，内存 %s MB/s，'
                  '频率 %s MHz，最高 %s°C'
                  % (record['single_mops'], record['multi_mops'], record['burst_ms'],
                     record['mem_mb_s'], record.get('clock_mhz'), record.get('temp_after_c')))
            if i + 1 < len(tiers):
                print('    降温 %ds…' % BENCH_COOLDOWN_S)
                time.sleep(BENCH_COOLDOWN_S)
        if mode == 'compare':
            anchor = next((r for r in results if r.get('tier') == 'bal'), results[0])
            set_baseline(anchor)
    finally:
        thermal.close()
        clock.close()
        power.restore(cfg['tiers'])
        if before:
            import subprocess
            subprocess.run(['powercfg', '/setactive', before], capture_output=True,
                           creationflags=0x08000000)
        print('已还原电源设置（方案 %s）' % (before or '未知'))
    view = best_of_each_tier()
    view['verdict'] = power_verdict()
    _print_bench_table(view)
    return 0


def _supervise(pass_through):
    """守护模式：子进程异常退出就自动拉起，用户主动停止则不复活。

    针对 open-revo 的教训——它一次僵死之后重启再也没起来，用户只能手动折腾。
    这里区分「正常退出（面板里点了停止 / --stop）」和「崩了/被杀」：
    只有后者才重启，且 10 分钟窗口内最多重启 5 次，避免启动即崩时刷屏空转。
    """
    import subprocess
    ensure_data_dir()
    log = Log('umi-supervisor')
    root = os.path.dirname(os.path.abspath(sys.argv[0]))
    child_args = [sys.executable, os.path.join(root, 'main.py')] + list(pass_through)
    window = collections.deque(maxlen=8)
    restarts = 0
    backoff = 3.0
    log.info('[守护] 启动被管进程：%s' % ' '.join(child_args[-2:]))
    while True:
        proc = subprocess.Popen(child_args, cwd=root, creationflags=0x08000000)
        code = proc.wait()
        now = time.time()
        window.append(now)
        if code == 0:
            log.info('[守护] 子进程正常退出（code=0），不再拉起')
            return 0
        recent = sum(1 for t in window if now - t < 600.0)
        if recent > 5:
            log.error('[守护] 10 分钟内异常退出 %d 次，停止自动重启以免空转；'
                      '请查看 data/fatal-*.log 与 umi-control-panel.log' % recent)
            return 1
        restarts += 1
        wait = min(backoff * (2 ** min(restarts, 3)), 30.0)
        log.warn('[守护] 子进程异常退出 code=%s，%.0f 秒后第 %d 次重启' % (code, wait, restarts))
        time.sleep(wait)


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
    ap.add_argument('--ec-test', action='store_true',
                    help='EC 只读自检：验证通道并打印寄存器读数后退出')
    ap.add_argument('--bench', nargs='?', const='compare', default=None,
                    choices=('current', 'compare'),
                    help='跑分：compare=四档逐一对比（默认），current=只测当前档位')
    ap.add_argument('--one-shot', action='store_true', help='打印一次状态 JSON 后退出')
    ap.add_argument('--stop', action='store_true', help='请求正在运行的实例优雅退出')
    ap.add_argument('--anchor', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--supervise', action='store_true',
                    help='守护模式：子进程崩溃自动重启（开机自启推荐用这个）')
    args, extra = ap.parse_known_args(argv)

    if args.supervise:
        passthrough = []
        if args.no_tray:
            passthrough.append('--no-tray')
        if args.no_browser:
            passthrough.append('--no-browser')
        passthrough.extend(extra)
        return _supervise(passthrough)

    ensure_data_dir()
    cfg, cfg_error, cfg_broken = Config.load()
    log = Log('umi-control-panel')
    if cfg_error:
        log.error('配置文件损坏（%s），已改名为 %s 并使用默认值' % (cfg_error, cfg_broken))

    if args.anchor:
        return _anchor_loop()

    if args.ec_test:
        return cmd_ec_test(cfg, log)

    if args.bench:
        return cmd_bench(cfg, log, args.bench)

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
