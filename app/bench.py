"""内置跑分：不下载任何第三方软件，纯标准库量出档位之间的真实差距。

设计取舍：
  * 单线程用纯 Python 紧循环，对频率最敏感——这正是「卡顿」的体感来源；
  * 多线程不能用 threading（GIL 会把并行吃掉），改为拉起 N 个 python 子进程，
    便携运行时的 python.exe 就够用，不依赖 multiprocessing；
  * 频率/温度用后台线程旁路采样，避免拖慢被测负载；
  * 分数是「相对基准的指数」，基准存在 data/bench.json 里，
    没有基准时第一次跑分自动成为 100，用户看到的是「办公 72 / 狂暴 118」这种能懂的数。
"""
import json
import os
import subprocess
import threading
import time

from app.paths import ROOT_DIR, data_path
from app.sense.clock import ClockSense

BENCH_NAME = 'bench.json'
HISTORY_LIMIT = 60
REF_SECONDS = {'single': 2.0, 'multi': 2.0, 'memory': 1.5}
CAL_ITERS = 300000
MEM_BLOCK = 8 * 1024 * 1024
MEM_TARGET_S = 1.5
CHILD_SPIN = (
    "import sys\n"
    "n=int(sys.argv[1]);x=123456789;m=0xFFFFFFFF\n"
    "for _ in range(n):\n"
    "    x=(x*1103515245+12345)&m\n"
    "    x^=x>>13\n"
    "print(x&7)\n"
)
NO_WINDOW = 0x08000000


def _spin(n):
    x = 123456789
    mask = 0xFFFFFFFF
    for _ in range(n):
        x = (x * 1103515245 + 12345) & mask
        x ^= x >> 13
    return x


class _Monitor(threading.Thread):
    """跑分期间旁路采样频率与温度。"""

    def __init__(self, clock, thermal, interval=0.4):
        super().__init__(daemon=True)
        self.clock = clock
        self.thermal = thermal
        self.interval = interval
        self.clocks = []
        self.temps = []
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            if self.clock is not None:
                mhz = self.clock.read()
                if mhz:
                    self.clocks.append(mhz)
            if self.thermal is not None:
                try:
                    temp, _zones = self.thermal.read()
                except Exception:                        # noqa: BLE001
                    temp = None
                if temp:
                    self.temps.append(temp)
            self._halt.wait(self.interval)

    def halt(self):
        self._halt.set()

    def result(self):
        return {
            'clock_mhz': round(sum(self.clocks) / len(self.clocks)) if self.clocks else None,
            'clock_peak_mhz': max(self.clocks) if self.clocks else None,
            'temp_c': max(self.temps) if self.temps else None,
            'samples': len(self.clocks),
        }


class Bench:
    def __init__(self, log=None, thermal=None, clock=None, python_exe=None):
        self.log = log
        self.thermal = thermal
        self.clock = clock or ClockSense()
        self.python_exe = python_exe or os.path.join(ROOT_DIR, 'runtime', 'python.exe')
        if not os.path.exists(self.python_exe):
            import sys
            self.python_exe = sys.executable
        self._ips = None

    # ---------- 负载 ----------
    def _calibrate(self):
        if self._ips:
            return self._ips
        best = 0.0
        for _ in range(2):
            t0 = time.perf_counter()
            _spin(CAL_ITERS)
            dt = time.perf_counter() - t0
            best = max(best, CAL_ITERS / max(dt, 1e-6))
        self._ips = best
        return best

    def run_single(self, target_s=None):
        ips = self._calibrate()
        n = max(50000, int(ips * (target_s or REF_SECONDS['single'])))
        mon = _Monitor(self.clock, self.thermal)
        mon.start()
        t0 = time.perf_counter()
        _spin(n)
        dt = time.perf_counter() - t0
        mon.halt()
        mon.join(timeout=1.0)
        out = {'mops': round(n / dt / 1e6, 2), 'iters': n, 'elapsed_s': round(dt, 3)}
        out.update(mon.result())
        return out

    def run_multi(self, workers=None, target_s=None):
        workers = workers or (os.cpu_count() or 4)
        ips = self._calibrate()
        per = max(50000, int(ips * (target_s or REF_SECONDS['multi'])))
        mon = _Monitor(self.clock, self.thermal)
        mon.start()
        procs = []
        t0 = time.perf_counter()
        for _ in range(workers):
            procs.append(subprocess.Popen(
                [self.python_exe, '-c', CHILD_SPIN, str(per)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW))
        for p in procs:
            try:
                p.wait(timeout=120)
            except subprocess.TimeoutExpired:
                p.kill()
        dt = time.perf_counter() - t0
        mon.halt()
        mon.join(timeout=1.0)
        out = {'mops': round(workers * per / dt / 1e6, 2), 'workers': workers,
               'iters': workers * per, 'elapsed_s': round(dt, 3)}
        out.update(mon.result())
        return out

    def run_memory(self, block=MEM_BLOCK):
        buf = bytearray(block)
        view = bytes(block)
        copies = 0
        mon = _Monitor(self.clock, self.thermal)
        mon.start()
        t0 = time.perf_counter()
        deadline = t0 + MEM_TARGET_S
        while time.perf_counter() < deadline:
            buf[:] = view
            copies += 1
        dt = time.perf_counter() - t0
        mon.halt()
        mon.join(timeout=1.0)
        out = {'mb_s': round(copies * block / dt / 1048576.0, 1), 'copies': copies,
               'elapsed_s': round(dt, 3)}
        out.update(mon.result())
        return out

    def run_burst(self, repeats=3, idle_s=2.0, work_s=0.25):
        """短任务延迟：空转降频后突然来一下活，看多久能干完。

        这正是「息屏回来点什么都卡一下」的量纲——满载吞吐看不出这个差别，
        最低处理器状态和 EPP 的作用全体现在这里。
        """
        ips = self._calibrate()
        n = max(20000, int(ips * work_s))
        lat, before, after = [], [], []
        mon = _Monitor(self.clock, self.thermal, interval=0.2)
        mon.start()
        for _ in range(repeats):
            time.sleep(idle_s)
            if self.clock is not None:
                before.append(self.clock.read())
            t0 = time.perf_counter()
            _spin(n)
            lat.append((time.perf_counter() - t0) * 1000.0)
            if self.clock is not None:
                after.append(self.clock.read())
        mon.halt()
        mon.join(timeout=1.0)
        nums = lambda xs: [x for x in xs if x]          # noqa: E731
        out = {
            'burst_ms': round(sum(lat) / len(lat), 1),
            'burst_worst_ms': round(max(lat), 1),
            'burst_idle_mhz': round(sum(nums(before)) / len(nums(before))) if nums(before) else None,
            'repeats': repeats,
            'elapsed_s': round(sum(lat) / 1000.0 + idle_s * repeats, 2),
        }
        sampled = mon.result()
        sampled['clock_mhz'] = round(sum(nums(after)) / len(nums(after))) if nums(after) else None
        out.update(sampled)
        return out

    # ---------- 一次完整跑分 ----------
    def run(self, label='', tier='', extra=None, progress=None):
        def step(msg, pct):
            if progress:
                progress(msg, pct)

        temp_before = None
        if self.thermal is not None:
            try:
                temp_before, _ = self.thermal.read()
            except Exception:                            # noqa: BLE001
                temp_before = None
        step('单线程负载…', 8)
        single = self.run_single()
        step('多线程负载…', 34)
        multi = self.run_multi()
        step('内存带宽…', 60)
        memory = self.run_memory()
        step('短任务延迟…', 78)
        burst = self.run_burst()
        step('完成', 100)
        record = {
            'ts': round(time.time(), 1),
            'tier': tier,
            'label': label,
            'single_mops': single['mops'],
            'multi_mops': multi['mops'],
            'mem_mb_s': memory['mb_s'],
            'burst_ms': burst['burst_ms'],
            'burst_worst_ms': burst['burst_worst_ms'],
            'clock_mhz': max(filter(None, [single.get('clock_mhz'), multi.get('clock_mhz')]),
                             default=None),
            'clock_peak_mhz': max(filter(None, [single.get('clock_peak_mhz'),
                                                multi.get('clock_peak_mhz')]), default=None),
            'temp_before_c': temp_before,
            'temp_after_c': max(filter(None, [single.get('temp_c'), multi.get('temp_c'),
                                              memory.get('temp_c')]), default=None),
            'workers': multi.get('workers'),
            'elapsed_s': round(single['elapsed_s'] + multi['elapsed_s'] + memory['elapsed_s']
                               + burst['elapsed_s'], 2),
        }
        if extra:
            record.update(extra)
        return record


# ---------- 历史与相对分数 ----------
def load_history():
    path = data_path(BENCH_NAME)
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {'baseline': None, 'runs': []}
    if not isinstance(raw, dict):
        return {'baseline': None, 'runs': []}
    return {'baseline': raw.get('baseline'),
            'runs': raw.get('runs') if isinstance(raw.get('runs'), list) else []}


def _write(data):
    path = data_path(BENCH_NAME)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
    return data


def _metrics(record):
    return {'single_mops': record.get('single_mops'),
            'multi_mops': record.get('multi_mops'),
            'mem_mb_s': record.get('mem_mb_s'),
            'burst_ms': record.get('burst_ms')}


def save_record(record, make_baseline=False):
    data = load_history()
    runs = data['runs']
    runs.append(record)
    del runs[:-HISTORY_LIMIT]
    if make_baseline or not data['baseline']:
        data['baseline'] = _metrics(record)
    return _write(data)


def set_baseline(record):
    """把某次成绩钉成 100 分基准（对比跑分用均衡档，用户最好理解）。"""
    data = load_history()
    data['baseline'] = _metrics(record)
    return _write(data)


# (记录字段, 输出名, 权重, 方向)：延迟类越低越好，按 ref/got 反算
SCORE_KEYS = (('single_mops', 'single', 0.30, 'higher'),
              ('multi_mops', 'multi', 0.30, 'higher'),
              ('burst_ms', 'burst', 0.30, 'lower'),
              ('mem_mb_s', 'mem', 0.10, 'higher'))


def score_of(record, baseline):
    """相对基准的指数（基准=100）。缺项返回 None。"""
    if not baseline:
        return None
    out, have = {}, []
    for key, name, weight, direction in SCORE_KEYS:
        ref, got = baseline.get(key), record.get(key)
        if not ref or not got:
            out[name] = None
            continue
        out[name] = round((ref / got if direction == 'lower' else got / ref) * 100.0, 1)
        have.append((out[name], weight))
    out['overall'] = (round(sum(v * w for v, w in have) / sum(w for _, w in have), 1)
                      if have else None)
    return out


def history_view():
    data = load_history()
    baseline = data['baseline']
    runs = [dict(r, score=score_of(r, baseline)) for r in data['runs'][-20:]]
    return {'baseline': baseline, 'runs': list(reversed(runs))}


TIER_ORDER = {'eco': 0, 'bal': 1, 'mid': 2, 'perf': 3}


def power_verdict():
    """用实测数据判定：Windows 电源档位在这台机器上到底控不控性能。

    本机 2026-09-29 实测：把「最大处理器状态」压到 30%、睿频设为禁用、EPP=100、
    最低频率 5%，单线程仍然跑 4283 MHz、全核 3316 MHz，和不限频时一模一样。
    也就是说频率归 BIOS/EC 管，powercfg 那一层是摆设——这正是「切了档但体会不出来」
    的根因。结论必须由跑分数据现场算出来，不能写死，换机器就可能不一样。

    只取「最近一次对比跑分」的记录：跨批次的成绩受机器冷热影响，混着比会得出假结论。
    """
    data = load_history()
    runs = data['runs']
    batches = {}
    for r in runs:
        batches.setdefault(r.get('run_id') or ('single-%s' % r.get('ts')), []).append(r)
    latest_key = None
    for r in reversed(runs):
        key = r.get('run_id') or ('single-%s' % r.get('ts'))
        if len(batches.get(key) or []) >= 2:
            latest_key = key
            break
    rows = batches.get(latest_key) if latest_key else None
    if not rows or len(rows) < 2:
        return None
    by_tier = {r.get('tier'): r for r in rows}
    clocks = [r['clock_mhz'] for r in rows if r.get('clock_mhz')]

    def gain(key):
        lo, hi = by_tier.get('eco'), by_tier.get('perf')
        if not lo or not hi or not lo.get(key) or not hi.get(key):
            return None
        return (hi[key] - lo[key]) * 100.0 / lo[key]

    gains = [g for g in (gain('single_mops'), gain('multi_mops')) if g is not None]
    spread = (max(clocks) - min(clocks)) * 100.0 / max(clocks) if clocks else None
    best_gain = max(gains) if gains else None
    effective = (best_gain is not None and best_gain >= 8.0) or \
                (best_gain is None and spread is not None and spread >= 8.0)
    freq_txt = ('频率 %d～%d MHz' % (min(clocks), max(clocks))) if clocks else '频率未采到'
    if effective:
        reason = ('实测性能档比省电档快 %s%%（%s），Windows 电源档位在本机有效。'
                  % (round(best_gain if best_gain is not None else spread, 1), freq_txt))
    else:
        reason = ('实测性能档只比省电档快 %s%%（%s），说明 BIOS/EC 接管了频率，'
                  'powercfg 那一层压不住——要真正换挡必须打通 EC 通道。'
                  % (round(best_gain if best_gain is not None else (spread or 0), 1), freq_txt))
    return {'effective': effective, 'perf_gain_pct': round(best_gain, 1) if best_gain is not None else None,
            'clock_spread_pct': round(spread, 1) if spread is not None else None,
            'clock_min_mhz': min(clocks) if clocks else None,
            'clock_max_mhz': max(clocks) if clocks else None,
            'tiers_measured': len(rows), 'reason': reason}


def best_of_each_tier():
    """每个档位取最好成绩，按 省电→均衡→流畅→性能 排列，便于横向对比。"""
    data = load_history()
    baseline = data['baseline']
    best = {}
    for r in data['runs']:
        tier = r.get('tier') or 'unknown'
        cur = best.get(tier)
        if cur is None or (r.get('single_mops') or 0) > (cur.get('single_mops') or 0):
            best[tier] = r
    out = [{'tier': tier, 'label': r.get('label') or tier, 'record': r,
            'score': score_of(r, baseline), 'ts': r.get('ts')}
           for tier, r in best.items()]
    out.sort(key=lambda x: TIER_ORDER.get(x['tier'], 9))
    return {'baseline': baseline, 'tiers': out}
