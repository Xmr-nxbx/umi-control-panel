"""守护主体：1 秒节拍把「遥测 → 决策 → 执行」串起来，并守住外部改动。"""
import threading
import time

from app.act.channels.base import CAP_LABELS, MODE_LABELS
from app.act.hardware import Hardware
from app.act.power import PowerExecutor, active_scheme
from app.bench import Bench, best_of_each_tier, power_verdict, save_record, set_baseline
from app.config import SCHEME_LABELS, SCHEMES
from app.history import History
from app.policy.scheduler import TIER_LABELS, Scheduler
from app.sense.clock import ClockSense
from app.sense.gpu import GpuSense
from app.sense.system import SystemSense
from app.sense.thermal import Thermal

APPLY_COOLDOWN_S = 3.0
REASSERT_S = 120.0
BENCH_SETTLE_S = 5.0
BENCH_COOLDOWN_S = 8.0
COMPARE_TIERS = ('eco', 'bal', 'mid', 'perf')


class Daemon:
    def __init__(self, cfg, log, record_history=True):
        """record_history=False 给一次性自检用：
        --one-shot / --health 也会构造一个 Daemon 走两拍，但那是另一个进程，
        不该去写常驻实例正在维护的 data/history.json。"""
        self.cfg = cfg
        self.log = log
        self.sense = SystemSense()
        self.thermal = Thermal()
        self.clock = ClockSense()
        self.gpu = GpuSense()
        self.power = PowerExecutor(log)
        self.hw = Hardware(cfg, log)
        self.sched = Scheduler(cfg['scheduler'], cfg['apps'])
        self.history = History(log) if record_history else None
        self._apply_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_apply_ts = 0.0
        self._last_applied_tier = None
        self._external_hits = 0
        self.wanted_hw_mode = None
        self._last_fan_ts = 0.0
        self._fan_lock_log_ts = 0.0
        self._logged_tier = None
        self.started_at = time.time()
        self.snap = {'cpu_pct': None, 'gpu_pct': None, 'cpu_temp': None}
        self.capabilities = self.power.probe()
        self.capabilities['cpu_name'] = self.clock.cpu_name
        self.capabilities['cpu_base_mhz'] = self.clock.base_mhz
        self.bench = {'running': False, 'mode': None, 'tier': None, 'label': None,
                      'step': '', 'pct': 0, 'results': [], 'error': None,
                      'started_at': None, 'finished_at': None}

    # ---------- 生命周期 ----------
    def start(self):
        self.power.capture_baseline(['balanced', 'high_perf'])
        if self.history:
            self.log.info('[历史] 接上了上次运行的 %d 个采样点' % self.history.load())
        self.hw.start()
        self._thread = threading.Thread(target=self._run, name='daemon', daemon=True)
        self._thread.start()
        self.log.info('[启动] 调度=%s 意图=%s 活动方案=%s EPP支持=%s 频率采集=%s' % (
            '开' if self.cfg.get('scheduler', 'enabled') else '关',
            self.cfg.get('intent'), self.capabilities.get('active_scheme', '')[:8],
            self.capabilities.get('epp_supported'),
            '可用' if self.clock.available else '不可用'))

    def stop(self, restore=None):
        self._stop.set()
        self.hw.stop()
        if self.history:
            self.history.close()
        if restore is None:
            restore = bool(self.cfg.get('power', 'restore_on_exit', default=True))
        if restore:
            self.power.restore(self.cfg['tiers'])
        self.thermal.close()
        self.clock.close()
        self.log.info('[停止] 已退出')

    def _run(self):
        while not self._stop.is_set():
            began = time.time()
            try:
                self.tick()
            except Exception as exc:                       # noqa: BLE001
                self.log.error('节拍异常：%r' % (exc,))
            wait = max(0.05, 1.0 - (time.time() - began))
            if self._stop.wait(wait):
                return

    # ---------- 单拍 ----------
    def tick(self):
        now = time.time()
        snap = self.sense.tick()
        temp, zones = self.thermal.read()
        snap['cpu_temp'] = temp
        snap['thermal_zones'] = zones
        snap['cpu_mhz'] = self.clock.read()
        # nvidia-smi 是子进程，每秒起一个本身就是负载来源——测量工具不该给被测机器加负载
        g = self.gpu.read(min_interval=5.0) or {}
        snap['gpu_pct'] = g.get('util_pct')
        snap['gpu'] = g
        self.snap = snap

        if self.bench.get('running'):
            self._bench_tick(snap, now)
            return

        intent = self.cfg.get('intent') or 'auto'
        if not self.cfg.get('scheduler', 'enabled', default=True) and intent == 'auto':
            intent = 'balance'
        decision = self.sched.decide(snap, now, intent)
        tier = decision['effective']

        self._watchdog(now)
        tier = decision['effective']
        if self._last_applied_tier != tier or self._needs_reassert(now):
            self._apply(tier, now)
        self._log_tier_change(decision, tier, snap, now)

        self._hardware_follow(tier, snap, now)
        self._fan_follow(tier, now)
        self._state_store(snap, decision, tier, now)

    def _log_tier_change(self, decision, tier, snap, now):
        """档位一旦真的变了就记一行带传感器的原因。

        之前只有 [执行] 行、看不到「为什么切」，用户报「一直在切档」时无从判断。
        """
        if tier == getattr(self, '_logged_tier', None):
            return
        prev = getattr(self, '_logged_tier', None)
        self._logged_tier = tier
        if prev is None:
            return
        self.log.info('[档位] %s → %s：%s（CPU=%s%% GPU=%s%% 温度=%s°C 空闲=%ss%s）' % (
            TIER_LABELS.get(prev, prev), TIER_LABELS.get(tier, tier), decision['reason'],
            round(snap.get('cpu_pct') or 0), round(snap.get('gpu_pct') or 0),
            snap.get('cpu_temp'), round(snap.get('idle_s') or 0),
            '，温度保护中' if decision['throttle'] else ''))

    def _bench_tick(self, snap, now):
        """跑分期间冻结调度与看门狗，否则测量结果就是调度器自己的噪声。"""
        tier = self.bench.get('tier') or self.sched.tier
        self._state_store(snap, {'reason': '跑分中（锁定 %s 档）' % TIER_LABELS.get(tier, tier),
                                 'throttle': self.sched.throttle,
                                 'dwell_left': 0.0, 'resume_left': 0.0}, tier, now)

    def _needs_reassert(self, now):
        """每隔一阵复核一次电源设置：别的软件改了就补写。

        power.apply 内部会跳过已经一致的项，值没变时既不写注册表也不产生日志，
        所以这不是「反复折腾电源」，只是自愈。
        """
        return (self._last_applied_tier is not None
                and now - self._last_apply_ts > REASSERT_S)

    def _apply(self, tier, now):
        profile = dict(self.cfg['tiers'].get(tier) or self.cfg['tiers']['bal'])
        if self.sched.throttle:
            # 温度保护：把频率上限压到 85%（约 1.96GHz），先降温再说性能
            for key in ('max_ac', 'max_dc', 'max'):
                if profile.get(key) is not None:
                    profile[key] = min(int(profile[key]), 85)
        with self._apply_lock:
            result = self.power.apply(tier, profile)
            self._last_apply_ts = time.time()
            self._last_applied_tier = tier
        return result

    def _watchdog(self, now):
        """外部（别的软件/系统）把方案改了 → 抢回来，但要避开自己刚写完的窗口。"""
        if not self.cfg.get('power', 'watchdog_external_change', default=True):
            return
        if now - self._last_apply_ts < APPLY_COOLDOWN_S:
            return
        expected = SCHEMES.get((self.cfg['tiers'].get(self._last_applied_tier) or {}).get('scheme'))
        got = active_scheme()
        if expected and got and got != expected:
            with self._apply_lock:
                self._external_hits += 1
                self.log.warn('[看门狗] 方案被外部改为 %s，重贴 %s 档' % (
                    got[:8], self._last_applied_tier))
                self.power.apply(self._last_applied_tier,
                                 self.cfg['tiers'][self._last_applied_tier])
                self._last_apply_ts = time.time()

    def _hardware_follow(self, tier, snap, now):
        """硬件档位跟随：进出 perf/eco 边界时同步 EC，并拦住息屏自动掉档。"""
        hw = self.hw
        caps = hw.capability_map()
        can_write = caps.get('mode.write', {}).get('state') == 'verified'
        sync = bool(self.cfg.get('hardware', 'sync_ec_mode')) and can_write
        mapping = self.cfg.get('hardware', 'ec_mode_map', default={}) or {}
        if sync:
            want = mapping.get(tier)
            if want and want != self.wanted_hw_mode:
                ok, detail = hw.set_mode(want, reason='调度档位=%s' % tier)
                if ok:
                    self.wanted_hw_mode = want
                elif now - self._last_apply_ts < 1:
                    self.log.warn('[硬件] 同步失败：%s' % detail)
        state = hw.snapshot()
        if state.get('mode') and self.wanted_hw_mode:
            hw.guard_sleep_downshift(state['mode'], snap.get('idle_s') or 0.0,
                                     self.wanted_hw_mode)

    def _fan_follow(self, tier, now):
        """风扇模式跟随档位：省电/均衡/流畅用自动，性能档加强散热。

        只写语义已确认的那个字节，取值只允许 OEM 枚举里的名字；
        带冷却窗口，避免档位抖动时反复改硬件。
        """
        if not self.cfg.get('hardware', 'ec', 'fan_follow_tier', default=False):
            return
        if self.hw.capability_map().get('fan.mode', {}).get('state') != 'verified':
            return
        mapping = self.cfg.get('hardware', 'ec', 'fan_map', default={}) or {}
        want = mapping.get(tier)
        if not want or want not in self.hw.fan_modes():
            return
        snap = self.hw.snapshot()
        if snap.get('fan_ctl_byte') is None:
            return                    # 还没读到当前值就别动硬件，盲写会覆盖用户的选择
        # 用户自己按出来的自定义曲线（0x80 位）永远不自动覆盖；
        # 刚按过键的优先窗口内也只读不写。上一版没有这两条，实测到按键/重启后
        # 面板会在 0 秒内把用户的自定义曲线写回自动档。
        left = self.hw.ec.fan_lock_left(now)
        owned = self.hw.ec.fan_user_owned() or bool(
            (snap.get('fan_mode_flag') or '').startswith('User_Fan'))
        if left or owned:
            if now - self._fan_lock_log_ts >= 60:
                self._fan_lock_log_ts = now
                who = (self.hw.ec.status().get('detail') or {}).get('fan_lock_by')
                self.log.info('[风扇] %s，面板不自动跟随%s' % (
                    '%s优先' % who if who else '用户自定义曲线优先',
                    '（窗口还剩 %.0f 秒）' % left if left else ''))
            return
        cooldown = float(self.cfg.get('hardware', 'ec', 'fan_cooldown_s', default=20.0))
        if now - self._last_fan_ts < cooldown:
            return
        if snap.get('fan_mode_flag') == want:
            return
        self._last_fan_ts = now
        ok, detail = self.hw.set_fan_mode(want, who=None)
        if ok:
            self.log.info('[风扇] 跟随%s档 → %s（%s）' % (
                TIER_LABELS.get(tier, tier), want, detail))
        else:
            self.log.warn('[风扇] 跟随失败：%s' % detail)

    # ---------- 对外状态 ----------
    def _state_store(self, snap, decision, tier, now):
        self._state = {
            'ts': now,
            'uptime_s': round(now - self.started_at),
            'intent': self.cfg.get('intent'),
            'tier': tier,
            'tier_label': TIER_LABELS.get(tier),
            'reason': decision['reason'],
            'pending': decision.get('pending'),
            'throttle': decision['throttle'],
            'dwell_left': round(decision['dwell_left'], 1),
            'resume_left': round(decision['resume_left'], 1),
            'scheme': SCHEME_LABELS.get(
                (self.cfg['tiers'].get(tier) or {}).get('scheme'), '?'),
            'applied': self.power.applied,
            'sensor': {
                'cpu_pct': snap.get('cpu_pct'),
                'gpu_pct': snap.get('gpu_pct'),
                'cpu_temp': snap.get('cpu_temp'),
                'cpu_mhz': snap.get('cpu_mhz'),
                'gpu': snap.get('gpu'),
                'mem': snap.get('mem'),
                'power': snap.get('power'),
                'idle_s': round(snap.get('idle_s') or 0.0, 1),
                'foreground': snap.get('foreground'),
            },
            'hardware': self.hw.snapshot(),
            'fan_modes': self.hw.fan_modes(),
            'capabilities': self.hw.capability_map(),
            'channels': [c.status() for c in self.hw.channels],
            'power_caps': self.capabilities,
            'external_hits': self._external_hits,
            'guard_hits': self.hw.guard_hits,
            'admin': self.is_admin(),
            'bench': {k: self.bench.get(k) for k in
                      ('running', 'mode', 'tier', 'label', 'step', 'pct', 'error')},
            'meta': {'cap_labels': CAP_LABELS, 'mode_labels': MODE_LABELS,
                     'tier_labels': TIER_LABELS},
        }
        if self.history:
            self.history.record(self._state, tier, decision.get('throttle'), now)

    def state(self):
        return getattr(self, '_state', {})

    def history_view(self, minutes=None):
        return self.history.view(minutes) if self.history else {'samples': [], 'marks': []}

    def is_admin(self):
        from app.sense.system import is_admin
        return is_admin()

    # ---------- 面板动作 ----------
    def set_intent(self, intent):
        if intent not in ('auto', 'office', 'balance', 'turbo'):
            return False, '未知意图'
        self.cfg.set('intent', intent)
        self.cfg.save()
        now = time.time()
        if intent == 'auto':
            self.sched.reason = '切回自适应'
            self.wanted_hw_mode = None
        else:
            self.sched.set_tier({'office': 'eco', 'balance': 'bal', 'turbo': 'perf'}[intent],
                                '手动指定（意图=%s）' % intent, now, forced=True)
            self._apply(self.sched.tier, now)
        self.log.info('[面板] 意图设为 %s' % intent)
        return True, 'ok'

    def set_mode_now(self, mode):
        """直接下发硬件档位（不改编排意图）。"""
        ok, detail = self.hw.set_mode(mode, reason='面板手动')
        if ok:
            self.wanted_hw_mode = mode
        return ok, detail

    def set_fan_mode(self, flag):
        """直接写 EC 风扇模式（自动/强冷/加速）。"""
        return self.hw.set_fan_mode(flag)

    def fan_modes(self):
        return self.hw.fan_modes()

    def set_tier_now(self, tier):
        if tier not in self.cfg['tiers']:
            return False, '未知档位'
        self.sched.set_tier(tier, '面板锁定档位', time.time(), forced=True)
        self._apply(tier, time.time())
        return True, 'ok'

    def logs(self, n=100):
        return self.log.tail(n)

    # ---------- 跑分 ----------
    def start_bench(self, mode='current'):
        """mode: current=只测当前档位；compare=逐档测一遍并给出对比表。"""
        if self.bench.get('running'):
            return False, '跑分正在进行中'
        if mode == 'compare':
            tiers = [t for t in COMPARE_TIERS if t in self.cfg['tiers']]
        else:
            mode = 'current'
            tiers = [self.sched.tier]
        self.bench = {
            'running': True, 'mode': mode, 'tiers': tiers, 'tier': tiers[0],
            'label': TIER_LABELS.get(tiers[0], tiers[0]), 'step': '准备中…', 'pct': 0,
            'results': [], 'error': None, 'started_at': time.time(), 'finished_at': None,
            'restore_tier': self.sched.tier, 'restore_intent': self.cfg.get('intent'),
        }
        threading.Thread(target=self._bench_run, name='bench', daemon=True).start()
        self.log.info('[跑分] 开始（%s，档位 %s）' % (mode, '→'.join(tiers)))
        return True, 'ok'

    def _bench_progress(self, step, pct):
        self.bench['step'] = step
        self.bench['pct'] = pct

    def _bench_run(self):
        thermal, clock = Thermal(), ClockSense()
        bench = Bench(log=self.log, thermal=thermal, clock=clock)
        results = []
        run_id = int(self.bench.get('started_at') or time.time())
        try:
            n = len(self.bench['tiers'])
            for i, tier in enumerate(self.bench['tiers']):
                label = TIER_LABELS.get(tier, tier)
                profile = dict(self.cfg['tiers'].get(tier) or {})
                # 进度只按「档位序号」算：一整套里每档占 100/n，最后再留 1% 给还原。
                # 之前是几段拼出来的，跑完最多到 98%，看着就像卡住了。
                pct = lambda frac: min(99, int((i + frac) * 100.0 / n))
                self.bench.update({'tier': tier, 'label': label})
                self._bench_progress('应用中…', pct(0.02))
                self.sched.set_tier(tier, '跑分锁定（%s）' % label, time.time(), forced=True)
                self._apply(tier, time.time())
                self._bench_progress('稳定中…', pct(0.10))
                time.sleep(BENCH_SETTLE_S)
                record = bench.run(
                    label=label, tier=tier,
                    extra={'run_id': run_id,
                           'profile': {k: profile.get(k) for k in
                                       ('scheme', 'min_ac', 'max_ac', 'boost', 'cool', 'epp')},
                           'throttled': self.sched.throttle},
                    progress=lambda msg, p: self._bench_progress(
                        msg, pct(0.15 + 0.80 * p / 100.0)))
                save_record(record)
                results.append(record)
                self.bench['results'] = list(results)
                if i + 1 < n:
                    self._bench_progress('测完，降温 %ds…' % BENCH_COOLDOWN_S, pct(0.98))
                    time.sleep(BENCH_COOLDOWN_S)
            if self.bench['mode'] == 'compare':
                anchor = next((r for r in results if r.get('tier') == 'bal'), results[0])
                set_baseline(anchor)
                self.log.info('[跑分] 基准设为 %s 档：单线程 %s Mops/s，多线程 %s Mops/s' % (
                    anchor.get('label'), anchor.get('single_mops'), anchor.get('multi_mops')))
        except Exception as exc:                            # noqa: BLE001
            self.bench['error'] = repr(exc)
            self.log.error('[跑分] 失败：%r' % (exc,))
        finally:
            thermal.close()
            clock.close()
            self._bench_restore()
            self.bench.update({'running': False, 'step': '完成', 'pct': 100,
                               'finished_at': time.time()})
            self.log.info('[跑分] 结束，已回到 %s 档' % TIER_LABELS.get(
                self.bench.get('restore_tier'), self.bench.get('restore_tier')))

    def _bench_restore(self):
        tier = self.bench.get('restore_tier') or 'bal'
        self.sched.set_tier(tier, '跑分结束恢复', time.time(), forced=True)
        self._apply(tier, time.time())

    def bench_view(self):
        view = best_of_each_tier()
        view['job'] = self.bench
        view['verdict'] = power_verdict()
        return view
