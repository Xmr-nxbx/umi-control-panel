"""跑分：负载用**开源 zstd 的真实压缩**，指标由本机现测，不写死结论。

为什么不用自己写的数学循环（上一版就是）：
  * 纯 Python 紧循环只压 CPU 整数单元，噪声大、和真实使用没关系，
    实测四档差距本来就只有 5~9%，噪声一盖就出现「省电档分数比性能档高」；
  * zstd 的 -b 基准模式是官方自带的真实负载（CPU + 内存带宽 + 多线程调度），
    输出直接是 MB/s，机主看得懂、也没法被我们的解析代码编出来。
工具来源：facebook/zstd v1.5.7 官方 GitHub Release（BSD-3-Clause / GPL-2.0 双许可），
便携单文件放在 tools/bin/，不进仓库、不装系统、不写注册表。
输入文件用固定随机种子生成，每次跑的内容完全一样，档位之间才可比。
"""
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.request
import zipfile

from app.paths import ROOT_DIR, data_path
from app.sense.clock import ClockSense

BENCH_NAME = 'bench.json'
HISTORY_LIMIT = 60
NO_WINDOW = 0x08000000

BIN_DIR = os.path.join(ROOT_DIR, 'tools', 'bin')
ZSTD_EXE = os.path.join(BIN_DIR, 'zstd.exe')
INPUT_FILE = os.path.join(BIN_DIR, 'bench_input.bin')
ZSTD_VERSION = '1.5.7'
ZSTD_URL = ('https://github.com/facebook/zstd/releases/download/v%s/'
            'zstd-v%s-win64.zip' % (ZSTD_VERSION, ZSTD_VERSION))
ZSTD_MEMBER = 'zstd-v%s-win64/zstd.exe' % ZSTD_VERSION
INPUT_MB = 44
SPEED_RE = re.compile(r'\),\s+([\d.]+) MB/s')

# CoreMark（EEMBC 官方，Apache-2.0）：量的是「纯计算」——链表/矩阵/状态机/CRC，
# 不吃内存带宽，正好补上 zstd 覆盖不到的那一半。没有官方 Windows 二进制，
# 只能用 tools\build_coremark.py 从官方源码现编，编不出来就跳过这一项（不影响其它分数）。
COREMARK_EXE = os.path.join(BIN_DIR, 'coremark.exe')
COREMARK_BUILD = os.path.join(ROOT_DIR, 'tools', 'build_coremark.py')
COREMARK_SCORE_RE = re.compile(r'CoreMark 1\.0\s*:\s*([\d.]+)')
COREMARK_TIME_RE = re.compile(r'total_time \(secs\):\s*([\d.]+)')
# 跑不满 10 秒 CoreMark 就拒给分数（只打印 Iterations/Sec），标定靠这一行就够。
COREMARK_IPS_RE = re.compile(r'Iterations/Sec\s*:\s*([\d.]+)')
COREMARK_CAL_ITERS = 20000
# 官方规则：单次跑不满 10 秒不算有效成绩、不吐分数。所以目标时长必须压在 10 秒以上，
# 留 2 秒余量给「这一档比标定时快」的情况。
COREMARK_TARGET_S = 12.0


def zstd_missing():
    """没下载就跑不了外部跑分，但面板要能明确说出缺什么、怎么补。"""
    if os.path.exists(ZSTD_EXE):
        return None
    return '缺少开源跑分工具 zstd（tools\\bin\\zstd.exe）：运行 scripts\\下载跑分工具.bat'


def ensure_zstd(log=None, timeout=60.0):
    """从 GitHub 官方 Release 取 zstd.exe；已存在就不重复下载。"""
    if os.path.exists(ZSTD_EXE):
        return True, '已就绪'
    try:
        os.makedirs(BIN_DIR, exist_ok=True)
        zip_path = os.path.join(BIN_DIR, 'zstd.zip')
        if log:
            log.info('[跑分] 下载 zstd v%s（官方 GitHub Release，约 1.7MB）' % ZSTD_VERSION)
        with urllib.request.urlopen(ZSTD_URL, timeout=timeout) as resp, \
                open(zip_path, 'wb') as f:
            f.write(resp.read())
        with zipfile.ZipFile(zip_path) as z:
            with z.open(ZSTD_MEMBER) as src, open(ZSTD_EXE, 'wb') as dst:
                dst.write(src.read())
        try:
            os.remove(zip_path)          # 杀软正在扫时会拒绝，删不掉不影响跑分
        except OSError:
            pass
        return True, 'zstd v%s 已下载' % ZSTD_VERSION
    except Exception as exc:                        # noqa: BLE001
        return False, '下载失败：%r' % exc


def ensure_input(log=None):
    """固定种子的 44MB 输入：一半高重复文本、一半低熵随机。

    重复文本模拟日志/代码这类真实可压数据，随机尾巴防止压缩器靠跳过常量取巧；
    种子写死，所以任何一次跑分的输入字节完全一致。
    """
    if os.path.exists(INPUT_FILE) and os.path.getsize(INPUT_FILE) > 1024 * 1024:
        return os.path.getsize(INPUT_FILE)
    rnd = random.Random(20260929)
    words = [b'power', b'thermal', b'scheduler', b'umi', b'panel', b'0x10',
             b'fan', b'boost', b'ADDR_MAFAN_CONTROL_BYTE']
    buf = bytearray()
    while len(buf) < INPUT_MB * 1024 * 1024:
        buf += b' '.join(rnd.choice(words) for _ in range(2048)) + b'\n'
    buf += bytes(rnd.getrandbits(8) for _ in range(4 * 1024 * 1024))
    tmp = INPUT_FILE + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(bytes(buf))
    os.replace(tmp, INPUT_FILE)
    if log:
        log.info('[跑分] 生成基准输入 %.1fMB（固定种子，可复现）' % (len(buf) / 1048576.0))
    return len(buf)


def _best_speed(text):
    """zstd -b 每次迭代都重打一行，取压缩速度里最好的那个。

    只匹配「x倍数), 速度 MB/s」前半段，所以不会把解压速度混进来。
    """
    vals = [float(x) for x in SPEED_RE.findall(text or '')]
    return (max(vals) if vals else None), vals


def coremark_missing():
    """CoreMark 是加分项不是必需项：缺了只跳过，不许把整轮跑分带崩。"""
    if os.path.exists(COREMARK_EXE):
        return None
    return '没有 CoreMark（tools\\bin\\coremark.exe）：运行 scripts\\编译CoreMark.bat'


def build_coremark(log=None, timeout=900.0):
    """调 tools/build_coremark.py 现场编译（需要本机有 gcc/clang）。"""
    if os.path.exists(COREMARK_EXE):
        return True, '已就绪'
    if not os.path.exists(COREMARK_BUILD):
        return False, '缺少编译脚本 tools\\build_coremark.py'
    if log:
        log.info('[跑分] 开始编译 CoreMark（EEMBC 官方源码，约 1 分钟）')
    try:
        proc = subprocess.run(
            [os.path.join(ROOT_DIR, 'runtime', 'python.exe'), COREMARK_BUILD],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            creationflags=NO_WINDOW, timeout=timeout,
            env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        text = (proc.stdout or '') + (proc.stderr or '')
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, '编译脚本没跑起来：%r' % exc
    if proc.returncode == 0 and os.path.exists(COREMARK_EXE):
        tail = [ln for ln in text.splitlines() if ln.strip()][-1:]
        return True, tail[0] if tail else 'CoreMark 编译完成'
    return False, (text.strip().splitlines() or ['编译失败'])[-1][:300]


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


def _spin(n):
    x = 123456789
    mask = 0xFFFFFFFF
    for _ in range(n):
        x = (x * 1103515245 + 12345) & mask
        x ^= x >> 13
    return x


class Bench:
    def __init__(self, log=None, thermal=None, clock=None):
        self.log = log
        self.thermal = thermal
        self.clock = clock or ClockSense()
        self._ips = None
        self._cm_ips = None
        self._zstd_ips = None

    # ---------- 外部负载：开源 zstd ----------
    def zstd(self, level, threads, iterations=3):
        """跑一次 `zstd -b<level> -T<threads> -i<迭代>`，返回最高的压缩速度。

        -T0 = 用满所有线程（吃 CPU + 内存带宽，最能反映散热/功耗限制）；
        -T1 = 单线程（最能反映频率上限，也就是「点东西卡不卡」那部分）。
        zstd 自己每轮迭代重打一行速度，取最好的那个，和它官方报告的口径一致。
        """
        missing = zstd_missing()
        if missing:
            raise RuntimeError(missing)
        args = [ZSTD_EXE, '-b%d' % level, '-T%s' % threads,
                '-i%d' % iterations, INPUT_FILE]
        mon = _Monitor(self.clock, self.thermal)
        mon.start()
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', creationflags=NO_WINDOW, timeout=300)
            text = (proc.stdout or '') + (proc.stderr or '')
            err = 'zstd 退出码 %s' % proc.returncode if proc.returncode else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            text, err = '', 'zstd 调用失败：%r' % exc
        dt = time.perf_counter() - t0
        mon.halt()
        mon.join(timeout=1.0)
        best, vals = _best_speed(text)
        out = {'mb_s': best, 'runs': len(vals), 'elapsed_s': round(dt, 2), 'error': err}
        out.update(mon.result())
        return out

    def load(self, workers=None, target_s=2.5, level=3):
        """给 EC 实验用的「全核负载 + 一个可比的吞吐数」，大约跑 target_s 秒。

        用低压缩档 -b3：一遍 44MB 只要 0.1 秒左右，所以能按 target_s 折算迭代次数，
        误差小，也不会像高档位那样一跑十几秒把机器烤热、干扰后面的测量。
        """
        workers = workers or (os.cpu_count() or 4)
        if self._zstd_ips is None:                 # 每秒能压多少 MB，标定一次就够
            res = self.zstd(level, str(workers), iterations=3)
            if not res.get('mb_s'):
                raise RuntimeError(res.get('error') or 'zstd 没吐出速度，负载不可用')
            self._zstd_ips = res['mb_s']
        size_mb = os.path.getsize(INPUT_FILE) / 1048576.0
        iters = max(1, int(self._zstd_ips * target_s / size_mb))
        res = self.zstd(level, str(workers), iterations=iters)
        res['workers'] = workers
        return res

    # ---------- 外部负载：EEMBC CoreMark ----------
    def _coremark_once(self, iterations):
        args = [COREMARK_EXE, '0x0', '0x0', '0x66', str(iterations), '0']
        mon = _Monitor(self.clock, self.thermal)
        mon.start()
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', creationflags=NO_WINDOW, timeout=300)
            text = (proc.stdout or '') + (proc.stderr or '')
            err = 'coremark 退出码 %s' % proc.returncode if proc.returncode else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            text, err = '', 'coremark 调用失败：%r' % exc
        dt = time.perf_counter() - t0
        mon.halt()
        mon.join(timeout=1.0)
        score = COREMARK_SCORE_RE.search(text)
        secs = COREMARK_TIME_RE.search(text)
        out = {'score': float(score.group(1)) if score else None,
               'iterations': iterations,
               'reported_s': float(secs.group(1)) if secs else None,
               'elapsed_s': round(dt, 2),
               'text': text,
               'error': err or (None if score else 'coremark 没吐出分数')}
        out.update(mon.result())
        return out

    def run_coremark(self):
        """迭代次数按本机速度现算，目标跑 COREMARK_TARGET_S 秒。

        CoreMark 的分数就是「每秒迭代次数」，所以给多少次不影响分数，只影响测得准不准。
        标定那次只跑 0.7 秒、官方不给分数，但会给 Iterations/Sec，拿它折算就够；
        正式跑必须满 10 秒才吐分数，所以目标时长定在 12 秒。万一还是没分数
        （比如这一档比标定时快太多），迭代数加半再补跑一次。
        """
        if self._cm_ips is None:
            cal = self._coremark_once(COREMARK_CAL_ITERS)
            ips = COREMARK_IPS_RE.search(cal.get('text') or '')
            if ips:
                self._cm_ips = float(ips.group(1))
            else:
                secs = cal.get('reported_s') or cal.get('elapsed_s') or 0.0
                if secs <= 0:
                    return cal
                self._cm_ips = COREMARK_CAL_ITERS / secs
        iters = max(2000, int(self._cm_ips * COREMARK_TARGET_S))
        res = self._coremark_once(iters)
        if not res.get('score'):
            res = self._coremark_once(iters * 3 // 2)
        return res

    def calibrate(self):
        """短任务用的循环次数标定一次就够（只为量延迟，不当分数）。"""
        if self._ips:
            return self._ips
        best = 0.0
        for _ in range(2):
            t0 = time.perf_counter()
            _spin(200000)
            dt = time.perf_counter() - t0
            best = max(best, 200000 / max(dt, 1e-6))
        self._ips = best
        return best

    def run_burst(self, repeats=3, idle_s=1.2, work_s=0.25):
        """短任务延迟：空转降频后突然来一下活，看多久能干完。

        这正是「息屏回来点什么都卡一下」的量纲——zstd 的满载吞吐看不出这个差别，
        最低处理器状态和 EPP 的作用全体现在这里。
        """
        ips = self.calibrate()
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
            'repeats': repeats,
            'elapsed_s': round(sum(lat) / 1000.0 + idle_s * repeats, 2),
        }
        sampled = mon.result()
        sampled['clock_mhz'] = round(sum(nums(after)) / len(nums(after))) if nums(after) else None
        out.update(sampled)
        return out

    # ---------- 一次完整跑分 ----------
    STEPS = ((9, '0', '全核压缩（zstd -b9 -T0）', 'all_mb_s'),
             (12, '1', '单核压缩（zstd -b12 -T1）', 'core_mb_s'))

    def run(self, label='', tier='', extra=None, progress=None):
        """跑一整轮。进度百分比按「这一步大概占多少时间」给，保证条子匀速往前走。

        上一版的进度是按步骤号算的，一步卡住条子就完全不动，机主只能干等；
        而且最后停在 98%，看着就像没跑完。
        """
        def step(msg, pct):
            if progress:
                progress(msg, pct)

        temp_before = None
        if self.thermal is not None:
            try:
                temp_before, _ = self.thermal.read()
            except Exception:                            # noqa: BLE001
                temp_before = None
        if not os.path.exists(ZSTD_EXE):
            step('下载开源跑分工具 zstd…', 2)
            ok, detail = ensure_zstd(self.log)
            if not ok:
                raise RuntimeError('外部跑分工具不可用：%s' % detail)
        step('准备基准数据…', 4)
        ensure_input(self.log)

        out, clocks, temps = {}, [], []
        total_s = 0.0
        for i, (level, threads, msg, key) in enumerate(self.STEPS):
            step(msg + '…', 8 + 24 * i)
            res = self.zstd(level, threads)
            if res.get('error') or not res.get('mb_s'):
                raise RuntimeError(res.get('error') or 'zstd 没吐出速度，结果不可信')
            out[key] = res['mb_s']
            total_s += res['elapsed_s']
            clocks.append(res.get('clock_mhz'))
            temps.append(res.get('temp_c'))

        # CoreMark 是加分项：编不出来/没装就跳过这一项，分数按剩下的项重新归一化，
        # 绝不因为少一项就把整轮跑分判失败。
        coremark, note = None, coremark_missing()
        if note is None:
            step('核心计算（CoreMark）…', 8 + 24 * len(self.STEPS))
            res = self.run_coremark()
            if res.get('score'):
                coremark = res['score']
                total_s += res['elapsed_s']
                clocks.append(res.get('clock_mhz'))
                temps.append(res.get('temp_c'))
                note = None
            else:
                note = res.get('error') or 'CoreMark 没跑出分数'
        if self.log and note:
            self.log.info('[跑分] 跳过核心计算项：%s' % note)

        step('短任务延迟…', 82)
        burst = self.run_burst()
        total_s += burst['elapsed_s']
        step('完成', 96)
        tools = ['zstd v%s（开源，BSD-3/GPL-2）' % ZSTD_VERSION]
        if coremark is not None:
            tools.append('CoreMark（EEMBC，Apache-2.0）')
        record = {
            'ts': round(time.time(), 1),
            'tier': tier,
            'label': label,
            'tool': ' + '.join(tools),
            'all_mb_s': out['all_mb_s'],
            'core_mb_s': out['core_mb_s'],
            'coremark': coremark,
            'coremark_note': note,
            'burst_ms': burst['burst_ms'],
            'burst_worst_ms': burst['burst_worst_ms'],
            'clock_mhz': max(filter(None, clocks + [burst.get('clock_mhz')]), default=None),
            'clock_peak_mhz': max(filter(None, clocks), default=None),
            'temp_before_c': temp_before,
            'temp_after_c': max(filter(None, temps + [burst.get('temp_c')]), default=None),
            'elapsed_s': round(total_s, 2),
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
    runs = raw.get('runs') if isinstance(raw.get('runs'), list) else []
    # 换成开源负载之前（2026-09-29）的成绩是自己写的 Python 数学循环量的，
    # 和 zstd/CoreMark 没有可比性；混进同一张表只会得出假结论，所以整批丢掉。
    # 基准同理：字段对不上就当作没有基准，下一次对比跑分会重新钉一个。
    runs = [r for r in runs if isinstance(r, dict) and r.get('all_mb_s')]
    baseline = raw.get('baseline')
    if not (isinstance(baseline, dict) and baseline.get('all_mb_s')):
        baseline = None
    return {'baseline': baseline, 'runs': runs}


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
    return {'all_mb_s': record.get('all_mb_s'),
            'core_mb_s': record.get('core_mb_s'),
            'coremark': record.get('coremark'),
            'burst_ms': record.get('burst_ms')}


# (记录字段, 输出名, 权重, 方向)：延迟类越低越好，按 ref/got 反算。
# 权重加起来是 1，但 score_of 只按「这次真测到的项」重新归一化——
# 机器上没装 CoreMark 时，剩下三项照样能给出总分，不会凭空掉分。
SCORE_KEYS = (('all_mb_s', 'all', 0.30, 'higher'),
              ('core_mb_s', 'core', 0.20, 'higher'),
              ('coremark', 'cpu', 0.25, 'higher'),
              ('burst_ms', 'burst', 0.25, 'lower'))


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

    gains = [g for g in (gain('all_mb_s'), gain('core_mb_s'), gain('coremark'))
             if g is not None]
    spread = (max(clocks) - min(clocks)) * 100.0 / max(clocks) if clocks else None
    best_gain = max(gains) if gains else None
    effective = (best_gain is not None and best_gain >= 8.0) or \
                (best_gain is None and spread is not None and spread >= 8.0)
    freq_txt = ('频率 %d～%d MHz' % (min(clocks), max(clocks))) if clocks else '频率未采到'
    if effective:
        reason = ('实测性能档比省电档快 %s%%（%s），Windows 电源档位在本机有效。'
                  % (round(best_gain if best_gain is not None else spread, 1), freq_txt))
    else:
        # 只说「压不住」等于把机主晾在半路：他真正要知道的是那什么才压得住。
        # 本机实测（README 6.2 / 6.4）：EC 风扇字节就是硬件模式总开关，按一次键
        # PL1_SETTING_VALUE 75W↔10W、风扇 PWM 表、TGP 整组跟着换，满载差 23~26%。
        reason = ('实测性能档只比省电档快 %s%%（%s），说明 BIOS/EC 接管了频率，'
                  'powercfg 那一层压不住。这台机器的性能墙在 EC 侧：风扇字节就是'
                  '硬件模式总开关，它一档 PL1 就在 75W/10W 之间换，满载实测差 23~26%%'
                  '（README 6.2、6.4），所以面板在性能档会把它推到强冷；'
                  '反过来直接写 PL1_SETTING_VALUE 实测不生效（写完自清零），'
                  '所以不裸写功耗墙。'
                  % (round(best_gain if best_gain is not None else (spread or 0), 1), freq_txt))
    return {'effective': effective, 'perf_gain_pct': round(best_gain, 1) if best_gain is not None else None,
            'clock_spread_pct': round(spread, 1) if spread is not None else None,
            'clock_min_mhz': min(clocks) if clocks else None,
            'clock_max_mhz': max(clocks) if clocks else None,
            'tiers_measured': len(rows), 'reason': reason}


def _rank(record):
    """「哪次跑得更好」的排序键：优先看全核吞吐，缺项退到单核，再缺就 0。"""
    return record.get('all_mb_s') or record.get('core_mb_s') or 0


def best_of_each_tier():
    """每个档位取最好成绩，按 省电→均衡→流畅→性能 排列，便于横向对比。"""
    data = load_history()
    baseline = data['baseline']
    best = {}
    for r in data['runs']:
        tier = r.get('tier') or 'unknown'
        cur = best.get(tier)
        if cur is None or _rank(r) > _rank(cur):
            best[tier] = r
    out = [{'tier': tier, 'label': r.get('label') or tier, 'record': r,
            'score': score_of(r, baseline), 'ts': r.get('ts')}
           for tier, r in best.items()]
    out.sort(key=lambda x: TIER_ORDER.get(x['tier'], 9))
    return {'baseline': baseline, 'tiers': out}
