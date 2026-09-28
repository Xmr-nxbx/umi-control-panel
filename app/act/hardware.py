"""硬件通道总管：能力协商 + 档位下发 + 息屏掉档守护。

能力协商原则：EC 直连为主、OEM MQTT 为兜底，两者都不在线时软件层照常工作，
面板如实显示「硬件档位：不可控」而不是假装成功。
"""
import threading
import time

from app.act.channels.base import (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ,
                                   CAP_FAN_RPM, MODE_LABELS)
from app.act.channels.ec_acpi import EcChannel
from app.act.channels.mqtt_gcu import MqttChannel

ECHO_SUPPRESS_S = 8.0


class Hardware:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.lock = threading.Lock()
        self.ec = EcChannel(cfg, log)
        self.mqtt = MqttChannel(cfg, log)
        self.channels = [self.ec, self.mqtt]
        self.state = {'mode': None, 'pl1': None, 'pl2': None, 'fan_rpm': None,
                      'fan_boost': None, 'kb_backlight': None, 'source': None,
                      'last_event': None}
        self._last_write_ts = 0.0
        self._guard_log_ts = 0.0
        self.mqtt.set_mode_callback(self._on_mode_event)
        self._thread = None
        self._stop = threading.Event()
        self.guard_hits = 0

    # ---------- 生命周期 ----------
    def start(self):
        self.ec.probe()
        self.mqtt.start()
        self._thread = threading.Thread(target=self._run, name='hardware', daemon=True)
        self._thread.start()

    def _run(self):
        next_probe = time.time() + 20
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as exc:                       # noqa: BLE001
                self.log.error('硬件轮询异常：%r' % (exc,))
            if time.time() >= next_probe:
                next_probe = time.time() + 60
                try:
                    self.ec.probe()
                except Exception as exc:                   # noqa: BLE001
                    self.log.error('EC 通道复查异常：%r' % (exc,))
            for _ in range(20):
                if self._stop.is_set():
                    return
                time.sleep(0.1)

    def stop(self):
        self._stop.set()
        self.mqtt.close()
        self.ec.close()

    # ---------- 能力 ----------
    def capability_map(self):
        merged = {}
        rank = {'verified': 4, 'unknown': 3, 'blocked': 2, 'unsupported': 1}
        for ch in self.channels:
            for cap, state in ch.caps.items():
                prev = merged.get(cap)
                if prev is None or rank.get(state, 0) > rank.get(prev.get('state'), 0):
                    merged[cap] = {'state': state, 'channel': ch.name}
        return merged

    def can_write_mode(self):
        return any(ch.caps.get(CAP_MODE_WRITE) == 'verified' for ch in self.channels)

    # ---------- 读 ----------
    def _poll(self):
        merged = {}
        for ch in self.channels:
            ch.tick()
            try:
                data = ch.read() or {}
            except Exception as exc:                       # noqa: BLE001
                continue
            for k, v in data.items():
                if v not in (None, '', {}, []):
                    merged[k] = v
            if data.get('mode'):
                merged['source'] = ch.name
        with self.lock:
            for key in ('mode', 'pl1', 'pl2', 'fan_rpm', 'fan_boost', 'kb_backlight'):
                if key in merged:
                    self.state[key] = merged[key]
            self.state['source'] = merged.get('source', self.state.get('source'))

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    # ---------- 写 ----------
    def set_mode(self, mode, reason=''):
        if mode not in MODE_LABELS:
            return False, '未知档位：%s' % mode
        self._last_write_ts = time.time()
        results = []
        for ch in self.channels:
            if ch.caps.get(CAP_MODE_WRITE) == 'verified':
                ok, detail = ch.set_mode(mode)
                results.append((ch.name, ok, detail))
                if ok:
                    self.log.info('[硬件] 切档 %s（%s）：%s' % (MODE_LABELS[mode], reason or '-', detail))
                    with self.lock:
                        self.state['mode'] = mode
                    return True, '%s: %s' % (ch.name, detail)
        if not results:
            return False, '没有可用通道支持写档位（EC 未验证 / GCUBridge 未连接）'
        return False, '; '.join('%s:%s' % (n, d) for n, _, d in results)

    def send_action(self, action, extra=None, note=''):
        for ch in self.channels:
            if hasattr(ch, 'send_action') and ch.alive:
                return ch.send_action(action, extra, note)
        return False, 'GCUBridge 未连接，命令不下发'

    # ---------- 息屏/锁屏掉档守护 ----------
    def _on_mode_event(self, mode, payload):
        with self.lock:
            prev = self.state.get('mode')
            self.state['mode'] = mode
            self.state['last_event'] = {'mode': mode, 'ts': time.time(), 'payload': payload}
        if time.time() - self._last_write_ts < ECHO_SUPPRESS_S:
            return                                  # 自己发的命令的回声，忽略
        self.log.info('[硬件] 档位变化 %s -> %s（外部触发）' % (prev, mode))

    def guard_sleep_downshift(self, hw_mode, idle_s, want_mode):
        """息屏/锁屏期间硬件档被系统自动降掉 → 重新拉回。

        判据：空闲超过阈值（人不在）且档位低于期望档 → 认定不是人工按键。
        """
        if not want_mode or not hw_mode:
            return False
        order = {'office': 0, 'balance': 1, 'turbo': 2}
        limit = float(self.cfg.get('scheduler', 'sleep_guard_idle_s', default=60.0))
        if idle_s < limit:
            return False
        if order.get(hw_mode, 0) >= order.get(want_mode, 0):
            return False
        now = time.time()
        if now - self._guard_log_ts < 20:
            return False
        self._guard_log_ts = now
        ok, detail = self.set_mode(want_mode, reason='息屏自动降档拦截')
        self.guard_hits += 1
        self.log.info('[拦截] 空闲 %ss 期间档位 %s<%s，重发指令 %s' % (
            round(idle_s), hw_mode, want_mode, detail))
        return ok
