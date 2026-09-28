"""守护主体：1 秒节拍把「遥测 → 决策 → 执行」串起来，并守住外部改动。"""
import threading
import time

from app.act.channels.base import CAP_LABELS, MODE_LABELS
from app.act.hardware import Hardware
from app.act.power import PowerExecutor, active_scheme
from app.config import SCHEME_LABELS, SCHEMES
from app.policy.scheduler import TIER_LABELS, Scheduler
from app.sense.gpu import GpuSense
from app.sense.system import SystemSense
from app.sense.thermal import Thermal

APPLY_COOLDOWN_S = 3.0


class Daemon:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.sense = SystemSense()
        self.thermal = Thermal()
        self.gpu = GpuSense()
        self.power = PowerExecutor(log)
        self.hw = Hardware(cfg, log)
        self.sched = Scheduler(cfg['scheduler'], cfg['apps'])
        self._apply_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_apply_ts = 0.0
        self._last_applied_tier = None
        self._external_hits = 0
        self.wanted_hw_mode = None
        self.started_at = time.time()
        self.snap = {'cpu_pct': None, 'gpu_pct': None, 'cpu_temp': None}
        self.capabilities = self.power.probe()

    # ---------- 生命周期 ----------
    def start(self):
        self.power.capture_baseline(['balanced', 'high_perf'])
        self.hw.start()
        self._thread = threading.Thread(target=self._run, name='daemon', daemon=True)
        self._thread.start()
        self.log.info('[启动] 调度=%s 意图=%s 活动方案=%s EPP支持=%s' % (
            '开' if self.cfg.get('scheduler', 'enabled') else '关',
            self.cfg.get('intent'), self.capabilities.get('active_scheme', '')[:8],
            self.capabilities.get('epp_supported')))

    def stop(self, restore=None):
        self._stop.set()
        self.hw.stop()
        if restore is None:
            restore = bool(self.cfg.get('power', 'restore_on_exit', default=True))
        if restore:
            self.power.restore(self.cfg['tiers'])
        self.thermal.close()
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
        g = self.gpu.read(min_interval=1.0) or {}
        snap['gpu_pct'] = g.get('util_pct')
        snap['gpu'] = g
        self.snap = snap

        intent = self.cfg.get('intent') or 'auto'
        if not self.cfg.get('scheduler', 'enabled', default=True) and intent == 'auto':
            intent = 'balance'
        decision = self.sched.decide(snap, now, intent)
        tier = decision['effective']

        self._watchdog(now)
        if self._last_applied_tier != tier or self._cooldown_passed(now):
            self._apply(tier, now)

        self._hardware_follow(tier, snap, now)
        self._state_store(snap, decision, tier, now)

    def _cooldown_passed(self, now):
        return (self._last_applied_tier is not None
                and now - self._last_apply_ts > 60.0)

    def _apply(self, tier, now):
        profile = dict(self.cfg['tiers'].get(tier) or self.cfg['tiers']['bal'])
        if self.sched.throttle:
            profile = dict(profile)
            profile['max'] = min(profile.get('max', 100), 85)
            profile['max_dc'] = min(profile.get('max_dc', 100), 85)
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

    # ---------- 对外状态 ----------
    def _state_store(self, snap, decision, tier, now):
        self._state = {
            'ts': now,
            'uptime_s': round(now - self.started_at),
            'intent': self.cfg.get('intent'),
            'tier': tier,
            'tier_label': TIER_LABELS.get(tier),
            'reason': decision['reason'],
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
                'gpu': snap.get('gpu'),
                'mem': snap.get('mem'),
                'power': snap.get('power'),
                'idle_s': round(snap.get('idle_s') or 0.0, 1),
                'foreground': snap.get('foreground'),
            },
            'hardware': self.hw.snapshot(),
            'capabilities': self.hw.capability_map(),
            'channels': [c.status() for c in self.hw.channels],
            'power_caps': self.capabilities,
            'external_hits': self._external_hits,
            'guard_hits': self.hw.guard_hits,
            'admin': self.is_admin(),
            'meta': {'cap_labels': CAP_LABELS, 'mode_labels': MODE_LABELS,
                     'tier_labels': TIER_LABELS},
        }

    def state(self):
        return getattr(self, '_state', {})

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

    def set_tier_now(self, tier):
        if tier not in self.cfg['tiers']:
            return False, '未知档位'
        self.sched.set_tier(tier, '面板锁定档位', time.time(), forced=True)
        self._apply(tier, time.time())
        return True, 'ok'

    def logs(self, n=100):
        return self.log.tail(n)
