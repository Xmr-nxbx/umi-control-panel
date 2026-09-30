"""硬件通道总管：能力协商 + 档位下发 + 息屏掉档守护。

能力协商原则：EC 直连为主、OEM MQTT 为兜底，两者都不在线时软件层照常工作，
面板如实显示「硬件档位：不可控」而不是假装成功。
"""
import threading
import time

from app.act.channels.base import (CAP_FAN_MODE, CAP_MODE_READ, CAP_MODE_WRITE,
                                   CAP_PL_READ, CAP_FAN_RPM, MODE_LABELS)
from app.act.channels.ec_gpd import EcChannel
from app.act.channels.hid_ite8291 import HidLightChannel
from app.act.channels.mqtt_gcu import MqttChannel

# 自己下发的命令，它的回声要压住别当成外部事件报（notes/hardware-channels.md 6.11 十五、待办 #39）。
# 实测服务应用一条档位命令要 **24~26 秒**（12:21:34→12:21:57、12:59:12→12:59:38），
# 上一版这个窗口只有 8 秒，所以面板把自己发的每一次切档都认成了「有人按了造物者键」，
# 于是既给用户弹假消息，又给自己下一把 900 秒的按键优先锁。
ECHO_SUPPRESS_S = 45.0
# 有些命令我们只发给了 GCUBridge，**真正写 EC 的是服务**，所以轮询眼里那是一次"别人"的改动。
# 不提前打招呼，日志就会写成「不是本面板写的：实体按键或其它软件在改」——是句假话，
# 2026-09-30 16:05 实测：下发 Win 锁后 1~2 秒 ADDR_STAUTS_BYTE 就变了，却被归因成外部改动。
# 值域照实测写宽一点（这条实测 1~6 秒，取 15 秒），只影响归因、不影响任何写入。
ACTION_EC_ECHO = {
    'WINKEY_LOCK': ('ADDR_STAUTS_BYTE', 15.0, '面板下发锁定 Win 键'),
    'WINKEY_UNLOCK': ('ADDR_STAUTS_BYTE', 15.0, '面板下发解锁 Win 键'),
}
# 通道能给出的额外语义值，一并进快照供面板展示（EC 的寄存器语义 + GCUBridge 报的开关状态）
STATE_KEYS = ('mode', 'hw_mode', 'pl1', 'pl2', 'pl4', 'fan_rpm', 'fan2_rpm', 'fan_boost',
              'fan_mode',
              'fan_duty_l', 'fan_duty_r', 'fan_ctl_byte', 'fan_mode_flag', 'kb_backlight',
              'pl1_setting', 'pl2_setting', 'pl4_setting', 'vrm_max_limit',
              'battery_pct_ec', 'battery_temp_c', 'battery_cycles', 'charge_limit_up',
              'charge_limit_down', 'project_id', 'module_id', 'vrm_limit', 'silent_mode',
              'mode_index', 'ec_power_source', 'win_key_locked', 'battery_mode',
              'battery_mode_raw', 'tcc_offset_enabled', 'tcc_offset_c', 'oem')


class Hardware:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.lock = threading.Lock()
        self.ec = EcChannel(cfg, log)
        self.mqtt = MqttChannel(cfg, log)
        # 灯效通道只读（枚举 HID 设备），排在最后：它 read() 返回空表，
        # 不参与档位/快照，只往能力表里添 lighting.rgb 两条。
        self.hid = HidLightChannel(cfg, log)
        self.channels = [self.ec, self.mqtt, self.hid]
        self.state = {k: None for k in STATE_KEYS}
        self.state.update({'source': None, 'last_event': None, 'ec_raw': {}})
        self._last_write_ts = 0.0
        self._guard_log_ts = 0.0
        self.mqtt.set_mode_callback(self._on_mode_event)
        self._thread = None
        self._stop = threading.Event()
        self.guard_hits = 0

    # ---------- 生命周期 ----------
    def start(self):
        self.ec.probe()
        self.hid.probe()
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
                if not self.hid.alive:
                    # 灯设备可能在睡眠/唤醒后才出现，所以只在没找到时重试；
                    # 找到了就不再枚举（一轮要打开上百个句柄，没必要空转）。
                    try:
                        self.hid.probe()
                    except Exception as exc:               # noqa: BLE001
                        self.log.error('灯效通道复查异常：%r' % (exc,))
            for _ in range(20):
                if self._stop.is_set():
                    return
                time.sleep(0.1)

    def stop(self):
        self._stop.set()
        self.mqtt.close()
        self.ec.close()
        self.hid.close()

    # ---------- 能力 ----------
    def capability_map(self):
        merged = {}
        rank = {'verified': 4, 'unknown': 3, 'blocked': 2, 'missing': 2, 'unsupported': 1}
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
            if not ch.alive:
                continue          # 没连上的通道不许往快照里写字段（否则 source 会被冒名顶替）
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
            for key in STATE_KEYS:
                if merged.get(key) is not None:
                    self.state[key] = merged[key]
            self.state['source'] = merged.get('source', self.state.get('source'))
            try:
                raw = self.ec.raw_values()
            except Exception:                              # noqa: BLE001
                raw = {}
            if raw:
                self.state['ec_raw'] = raw

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    # ---------- 写 ----------
    def set_mode(self, mode, reason='', on_applied=None):
        if mode not in MODE_LABELS:
            return False, '未知档位：%s' % mode
        self._last_write_ts = time.time()
        results = []
        for ch in self.channels:
            if ch.caps.get(CAP_MODE_WRITE) == 'verified':
                ok, detail = ch.set_mode(mode)
                results.append((ch.name, ok, detail))
                if ok:
                    # 服务收到这条命令后，约 24~26 秒会**自己写一次** EC 的档位字节
                    # （那个字节是它的输出，见 notes/hardware-channels.md 6.11 十五）。提前跟 EC 通道打招乎，
                    # 别把这次回声归因成「有人按了造物者键」——否则既弹假消息，
                    # 又会下一个 900 秒按键优先锁把自动跟随冻住（待办 #39 的实测现场）。
                    # on_applied 是回声到达时的「真生效」提示，由调用方决定弹不弹。
                    self.ec.expect_fan_key_change(
                        why='面板下发 %s（%s）' % (mode, reason or '手动'),
                        on_applied=on_applied)
                    self.log.info('[硬件] 切档 %s（%s）：%s' % (MODE_LABELS[mode], reason or '-', detail))
                    with self.lock:
                        self.state['mode'] = mode
                    return True, '%s: %s' % (ch.name, detail)
        if not results:
            return False, '没有可用通道支持写档位（EC 未验证 / GCUBridge 未连接）'
        return False, '; '.join('%s:%s' % (n, d) for n, _, d in results)

    def set_fan_mode(self, flag, who='面板按钮'):
        """写 EC 风扇模式字节（取值来自 OEM 枚举，语义已确认）。

        who=None 表示这是自动跟随在写，不算人工意图。
        """
        for ch in self.channels:
            if hasattr(ch, 'set_fan_mode') and ch.caps.get(CAP_FAN_MODE) == 'verified':
                ok, detail = ch.set_fan_mode(flag, who=who)
                if ok:
                    self.log.info('[硬件] 风扇模式 → %s' % flag)
                    with self.lock:
                        self.state['fan_mode'] = flag
                return ok, detail
        for ch in self.channels:
            if hasattr(ch, 'set_fan_mode'):
                return False, ch.detail.get('fan_mode_reason') or '该通道不支持写风扇模式'
        return False, '没有通道支持写风扇模式'

    def fan_modes(self):
        for ch in self.channels:
            if hasattr(ch, 'fan_modes'):
                return ch.fan_modes()
        return {}

    def send_action(self, action, extra=None, note=''):
        for ch in self.channels:
            if hasattr(ch, 'send_action') and ch.alive:
                # 通道自己会在写成功后开追问窗口（mqtt_gcu.send_action），
                # 这里不重复催：Setting/Status 不问了不推，忘了开就要等 45 秒才翻。
                ok, detail = ch.send_action(action, extra, note)
                if ok:
                    reg = ACTION_EC_ECHO.get(action)
                    if reg:
                        self.ec.expect_external_change(reg[0], reg[1], reg[2])
                return ok, detail
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
