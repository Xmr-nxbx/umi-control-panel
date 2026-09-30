"""OEM GCUBridge MQTT 兜底通道。

本机事实：broker 是 OEM 组件 GCUBridge.exe 监听 127.0.0.1:13688，只接受固定身份；
它会周期性用 GetProcessesByName 校验「clientId 同名进程是否存在」，不存在就踢线
（约 26 秒一次），所以这里必须自带自动重连 + 发送队列补发。
2026-09-30 起该服务已恢复运行（`scripts\\启用造物者档控制.bat`），通道连通并用于只读；
连不上时 probe 失败就整通道降级，不报错刷屏。
**同一个 clientId 只允许一条连接**：后连上的会把先连上的顶掉，所以观察工具跑之前必须停面板
（2026-09-30 那次双实例并排跑，两边互踢，观察报告只留下前 60 秒）。
"""
import json
import os
import queue
import socket
import struct
import threading
import time

from app.act.channels.base import (Channel, CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ,
                                   CAP_PL_WRITE, CAP_RGB, CAP_RGB_WRITE, CAP_DGPU,
                                   CAP_WINKEY_WRITE, CAP_BATTERY_MODE_WRITE,
                                   MODE_LABELS, MODE_VERIFIED)

ACTIONS_PATH = os.path.join(os.path.dirname(__file__), 'gcu_actions.json')
STATUS_TOPICS = ('Tray/Status', 'Fan/Status', 'Setting/Status', 'Keyboard/Status',
                 'HidLightbar/Status', 'Display/Status', 'Monitor/Status',
                 'WhisperMode/Status', 'ProcessControl/Status', 'Customize/Status')
# OperatingMode 的取值只认 OEM 自己枚举里有的：OperatingMode{Office:0, Turbo:2}
# （tools/oem_constant_dump.ps1 导出）。以前这张表还写了 1→balance、3→turbo，
# 那是照抄别家的猜测，和 OEM 枚举直接矛盾；实测 Tray/Status 报的就是 1/2 这类
# 没定义的值，硬翻译成「均衡」等于在面板上编一个本机不存在的档（用户明确说过
# Creator Center 里没有办公/均衡/狂暴）。认不出来就写 unknown，并把原始值暴露出去。
MODE_FIELD = {'0': 'office', '2': 'turbo'}
MODE_ACTION = {'office': 'OPERATING_OFFICE_MODE', 'turbo': 'OPERATING_TURBO_MODE'}


def _varint(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _cstr(text):
    data = text.encode('utf-8')
    return struct.pack('>H', len(data)) + data


class MqttClient:
    """只实现本任务需要的 QoS0 子集：CONNECT / SUBSCRIBE / PUBLISH / PINGREQ。"""

    def __init__(self, host, port, client_id, username, password, keepalive=60):
        self.addr = (host, port)
        self.client_id = client_id
        self.username = username
        self.password = password
        self.keepalive = keepalive
        self.sock = None
        self.connected = False

    def _connect_packet(self):
        flags = 0x02
        if self.username:
            flags |= 0x80
        if self.password:
            flags |= 0x40
        body = _cstr(self.client_id)
        if self.username:
            body += _cstr(self.username)
        if self.password:
            body += _cstr(self.password)
        head = _cstr('MQTT') + bytes([4, flags]) + struct.pack('>H', self.keepalive)
        return b'\x10' + _varint(len(head + body)) + head + body

    def connect(self, timeout=2.0):
        s = socket.create_connection(self.addr, timeout=timeout)
        s.settimeout(timeout)
        s.sendall(self._connect_packet())
        hdr = s.recv(1)
        body = s.recv(4)
        if not hdr or hdr[0] != 0x20 or len(body) < 2 or body[1] != 0:
            rc = body[1] if len(body) > 1 else -1
            s.close()
            raise ConnectionError('CONNACK 拒绝 rc=%s' % rc)
        self.sock = s
        self.connected = True
        return True

    def subscribe(self, topics, packet_id=1):
        body = struct.pack('>H', packet_id)
        for t in topics:
            body += _cstr(t) + b'\x00'
        self.sock.sendall(b'\x82' + _varint(len(body)) + body)

    def publish(self, topic, payload_text):
        body = _cstr(topic) + payload_text.encode('utf-8')
        self.sock.sendall(b'\x30' + _varint(len(body)) + body)

    def ping(self):
        self.sock.sendall(b'\xc0\x00')

    def read_packet(self):
        hdr = self.sock.recv(1)
        if not hdr:
            raise ConnectionError('broker 关闭连接')
        code = hdr[0]
        mult, size = 0, 0
        while True:
            b = self.sock.recv(1)
            if not b:
                raise ConnectionError('剩余长度读取中断')
            n = b[0]
            size += (n & 0x7F) << mult
            if not n & 0x80:
                break
            mult += 7
        body = b''
        while len(body) < size:
            chunk = self.sock.recv(size - len(body))
            if not chunk:
                raise ConnectionError('报文体读取中断')
            body += chunk
        return code, body

    def close(self):
        try:
            if self.sock:
                self.sock.sendall(b'\xe0\x00')
        except OSError:
            pass
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None
        self.connected = False


class MqttChannel(Channel):
    name = 'mqtt'
    label = 'OEM GCUBridge（兜底）'
    RECONNECT_MIN = 5.0

    def __init__(self, cfg, log):
        super().__init__(cfg, log)
        m = cfg.get('hardware', 'mqtt', default={}) or {}
        self.host = m.get('host', '127.0.0.1')
        self.port = int(m.get('port', 13688))
        self.enabled = bool(m.get('enabled', True))
        self.client_id = m.get('client_id', '') or ''
        self.username = m.get('username', '') or ''
        self.password = m.get('password', '') or ''
        allow_write = bool(cfg.get('hardware', 'ec', 'allow_write', default=False))
        self.allow_write = allow_write
        self.actions = self._load_actions()
        self._payloads = {}
        self._lock = threading.Lock()
        self._outq = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._mode_cb = None
        self.last_mode = None
        self._last_probe_ts = 0.0
        self._kick_reason = None
        for cap in (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_PL_WRITE, CAP_RGB,
                    CAP_DGPU, CAP_WINKEY_WRITE, CAP_BATTERY_MODE_WRITE):
            self.caps[cap] = 'unsupported'
        self.detail = {'enabled': self.enabled, 'broker': '%s:%s' % (self.host, self.port)}

    def _load_actions(self):
        try:
            with open(ACTIONS_PATH, encoding='utf-8') as f:
                spec = json.load(f)
            return spec
        except (OSError, ValueError) as exc:
            self.log.error('命令白名单读取失败：%r' % (exc,))
            return {'actions': {}, 'topics': {}}

    def set_mode_callback(self, fn):
        self._mode_cb = fn

    def probe(self):
        self.detail['reason'] = '未启用' if not self.enabled else '尚未连接'
        return self.status()

    def start(self):
        if not self.enabled or self._thread:
            return
        if not (self.client_id and self.username):
            self.detail['reason'] = ('未配置 broker 身份：把 clientId/用户名/口令放进 '
                                     'data\\mqtt_identity.json（参考 mqtt_identity.example.json）')
            self.detail['state'] = 'blocked'
            self.caps[CAP_MODE_READ] = 'blocked'
            return
        self._thread = threading.Thread(target=self._run, name='gcu-mqtt', daemon=True)
        self._thread.start()

    def _run(self):
        backoff = self.RECONNECT_MIN
        while not self._stop.is_set():
            try:
                self._session()
                backoff = self.RECONNECT_MIN
            except Exception as exc:                       # noqa: BLE001
                self.alive = False
                self._kick_reason = repr(exc)
                self.caps[CAP_MODE_READ] = 'unsupported'
                # 掉线就把已验证的写能力收回：否则面板还亮着按钮，点了只会失败
                self.caps[CAP_WINKEY_WRITE] = 'unsupported'
                self.detail['reason'] = '未连接：%s' % self._kick_reason
                for _ in range(int(backoff * 10)):
                    if self._stop.is_set():
                        return
                    time.sleep(0.1)
                backoff = min(backoff * 2, 60.0)

    def _session(self):
        cli = MqttClient(self.host, self.port, self.client_id, self.username, self.password)
        cli.connect(timeout=2.5)
        cli.subscribe(STATUS_TOPICS)
        self.alive = True
        self.caps[CAP_MODE_READ] = 'verified'
        self.caps[CAP_PL_READ] = 'verified'
        # 精细功耗墙：护栏代码已经就位（set_power_limits 会拿 Fan/Status 的边界夹），
        # 但**还没做过一次在场验证**，所以照旧不点亮，能力表写 unknown 并说明缺什么。
        self.caps[CAP_PL_WRITE] = 'unknown'
        self.detail['pl_write_reason'] = (
            '护栏已就位（越界拒发、没边界不发），缺一次有人在场的一次验证：'
            '下发界内值 → 看 EC 0x0783 真的变了 → 还原。'
            '注意 0x0783-0x0785 还有 ACPI 侧写者，还原值可能不是原值，验证时要对照前后读数')
        # 写档位：2026-09-30 12:19-12:24 做完整可逆验证（notes/hardware-channels.md 6.11 十五）。
        # 关键发现是**方向反了**：EC 的 `0x0751`（面板风扇模式字节）是**服务的输出，不是输入**。
        # 只写那个字节 → PL1 死守 10 W，100 秒 52 个采样点一次没动；
        # 改发本动作 → 约 24 秒后 PL1 从 10 爬到 75，服务同时把 `0x0751` 写成 0x10。
        # 再发 OFFICE → PL1 掉回 10、`0x0751` 被服务改回 0xA0；再发 TURBO → 又回 75。
        # 往返两次都改到了真东西、都能还原，所以升 verified。
        self.caps[CAP_MODE_WRITE] = 'verified'
        self.detail['mode_write_reason'] = (
            '已在本机做过可逆验证（2026-09-30 12:21）：TURBO→OFFICE→TURBO 往返，'
            'EC 直读 PL1 依次 10→75→10→75 W。注意生效有约 24 秒延迟，'
            '且功耗墙只由这条路改变得了——裸写 EC 档位字节不动它。')
        # 按钮清单由后端给：前端不许自己写死档位（上一版写死了 office/balance/turbo，
        # 结果 balance 是个点了只会报「未知档位」的死按钮）。只放验过的。
        self.detail['mode_options'] = [{'id': m, 'label': MODE_LABELS[m]}
                                       for m in MODE_VERIFIED if m in MODE_ACTION]
        # Win 键锁定是唯一做完完整可逆验证的写操作（2026-09-30 01:43）：
        # 下发 WINKEY_UNLOCK 后 EC 的 ADDR_STAUTS_BYTE 由 1 变 0（面板日志 01:43:16 抓到，
        # 延迟约 6 秒），再下发 WINKEY_LOCK 又回到 1，EC 直读与 Setting/Status 两条通道
        # 读数一致。天然可逆、无温度风险，所以升成 verified，面板可以点亮这个开关。
        self.caps[CAP_WINKEY_WRITE] = 'verified'
        self.detail['winkey_write_reason'] = (
            '这个开关从下发到 EC 生效实测约 6 秒，点了之后状态不会马上翻，别连着点。')
        # 键盘背光：2026-09-30 15:31 做过完整可逆验证（tools/kb_power_test.py，用户在场看着灯）：
        # 初始 Off/亮度4/灯效3/速度1 → 发 {"function":"SetPower","powerstatus":1} →
        # **0.3 秒**回读 On → 保持 12 秒 → 关回 Off，回读一致，
        # 亮度/灯效/速度三个值全程没被顺带改掉（这点最重要：SetPower 只管开关，
        # 亮度是另一条 SetLightingLevel，见 notes/hardware-channels.md 6.11 十八）。
        # 比 Win 锁快得多，所以这条的提示不写延迟。
        self.caps[CAP_RGB_WRITE] = 'verified'
        self.detail['rgb_write_reason'] = (
            '背光开关已做过可逆验证（Off→On 约 0.3 秒生效，关回后亮度/灯效/速度不变）。'
            '亮度、灯效是另外两条命令，面板暂时不碰。')
        # 电池充电三档：动作名和 EC 落点都对上了（notes/hardware-channels.md 6.8），但没做过可逆验证，
        # 而且上游 Linux 驱动因为 2020 年前后的机型出过「开充电限制把电池搞坏」的事故，
        # 直接封死了强开路径（CVE-2026-64143）。本机正是那一代，所以保持 unknown。
        self.caps[CAP_BATTERY_MODE_WRITE] = 'unknown'
        self.detail['battery_write_reason'] = ('档位语义已确认，但充电门控在同代机型上有'
                                               '损坏电池的前例（CVE-2026-64143），'
                                               '用户点头之前不下发')
        self.detail['reason'] = '已连接 GCUBridge'
        cli.publish('Setting/Control', json.dumps({'Action': 'GETSTATUS'}))
        last_ping = time.time()
        cli.sock.settimeout(1.0)
        while not self._stop.is_set():
            try:
                code, body = cli.read_packet()
            except socket.timeout:
                if time.time() - last_ping > 25:
                    cli.ping()
                    last_ping = time.time()
                continue
            if code & 0xF0 == 0x30 and len(body) >= 2:
                tlen = struct.unpack('>H', body[:2])[0]
                topic = body[2:2 + tlen].decode('utf-8', 'replace')
                self._ingest(topic, body[2 + tlen:])
            while not self._outq.empty():
                topic, payload = self._outq.get_nowait()
                cli.publish(topic, payload)
        cli.close()

    def _ingest(self, topic, raw):
        try:
            data = json.loads(raw.decode('utf-8', 'replace') or '{}')
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            self._payloads[topic] = {'ts': time.time(), 'data': data}
        if topic == 'Tray/Status' and 'OperatingMode' in data:
            raw = str(data['OperatingMode'])
            mode = MODE_FIELD.get(raw)
            if self.detail.get('operating_mode_raw') != raw:
                self.detail['operating_mode_raw'] = raw
                self.detail['mode_reason'] = '' if mode else (
                    'OperatingMode=%s 不在 OEM 枚举里（枚举只有 0=Office、2=Turbo），'
                    '不翻译成任何档位' % raw)
                if not mode:
                    self.log.info('[MQTT] Tray/Status OperatingMode=%s：OEM 枚举未定义，'
                                  '按「未知」处理，不猜' % raw)
            if mode and mode != self.last_mode:
                first = self.last_mode is None
                self.last_mode = mode
                # 首包只当基线，不算事件（否则服务一启动就跟着旧档位跑）
                if not first and self._mode_cb:
                    self._mode_cb(mode, data)

    def payloads(self):
        with self._lock:
            return {k: dict(v) for k, v in self._payloads.items()}

    def send_action(self, action, extra=None, note=''):
        # 白名单只管「这个命令 OEM 认不认」，能不能发是另一道闸：
        # 6.3 第 3 条要求所有写入都挂在 config.hardware.ec.allow_write 上。
        # 之前这里没查，等于 /api/action 成了不设防的硬件写入口。
        if not self.allow_write:
            return False, ('写操作未在配置中允许（config.hardware.ec.allow_write=false），'
                           '拒绝下发 %s' % action)
        spec = self.actions['actions'].get(action)
        if not spec:
            return False, 'Action "%s" 不在白名单里，拒绝发送' % action
        topic = self.actions['topics'].get(spec['topic'], 'Fan/Control')
        # OEM 不是一套报文格式：Setting/Fan/Battery 用 {"Action":...}，
        # Keyboard/Ctrl 用的是 {"function":"SetPower", ...}（读 GCUService 的
        # OnMqttMessage switch 确认，见 notes/hardware-channels.md 6.11 十八）。
        # 字段名和固定参数都由白名单条目自己声明，代码里不写死任何一条命令的形状。
        obj = {spec.get('field', 'Action'): spec.get('value', action)}
        obj.update(spec.get('args') or {})
        if extra:
            obj.update(extra)
        # 第二道闸：这个动作对应的能力**验过没有**。上一版只查白名单，
        # 于是 lighting.rgb.write 还标着 blocked 就能从 HTTP 发出去——
        # "验证之前不点亮"必须是执行层的效果，不只是 UI 层的（6.3 第 7 条）。
        cap = spec.get('cap')
        if cap and self.caps.get(cap) != 'verified':
            return False, ('%s 对应的能力 %s 还没做过可逆验证（当前 %s），拒绝下发。'
                           '首次验证走 tools/ 下的脚本，不在 HTTP 上开口子'
                           % (action, cap, self.caps.get(cap) or '未上报'))
        if not self.alive:
            return False, 'GCUBridge 未连接'
        self._outq.put((topic, json.dumps(obj, ensure_ascii=False)))
        return True, note or action

    def set_mode(self, mode):
        if not self.allow_write:
            return False, '写操作未在配置中允许'
        action = MODE_ACTION.get(mode)
        if not action:
            return False, '未知档位 %s' % mode
        return self.send_action(action, note='切档 -> %s' % mode)

    # ---------- 精细功耗墙 ----------
    def pl_limits(self):
        """OEM 自己在 `Fan/Status` 里写的边界。拿不到就返回 None——没护栏就不发。

        注意 `Fan/Status` 不是周期广播：本机只在被 `GETSTATUS` 问到时才报，
        所以这一组数经常是空的（面板上那行显示「未报」不是 bug）。
        """
        fan = (self.payloads().get('Fan/Status') or {}).get('data') or {}

        def num(key):
            try:
                return int(str(fan[key]))
            except (KeyError, TypeError, ValueError):
                return None

        out = {'pl1_min': num('CPU_PL1Minimum'), 'pl1_max': num('CPU_PL1Maximum'),
               'pl4_max': num('CPU_PL4Maximum')}
        return out if any(v is not None for v in out.values()) else None

    def set_power_limits(self, pl1=None, pl2=None, pl4=None):
        """唯一那条受支持的改墙路径：`Fan/Control SET_OPERATING_MODE_DETAIL`。

        ⚠️ 服务端对这三个值**完全不做边界校验**：`Convert.ToInt32` 之后直接 `(byte)` 截断，
        发 300 会静默变成 44 W（notes/hardware-channels.md 6.11 十二）。
        所以护栏只能我们自己加，而且**越界是拒绝、不是悄悄夹到边上**——
        悄悄夹会让人以为设成了 300。PL1/PL2/PL4 是三个独立 `if`，一条消息能同时带三个。
        OEM 没单独报 PL2 的边界，这里借用 PL1 那一组，理由写在拒绝文案里。
        """
        want = {'PL1': pl1, 'PL2': pl2, 'PL4': pl4}
        want = dict((k, v) for k, v in want.items() if v is not None)
        if not want:
            return False, '没有要改的值'
        lim = self.pl_limits()
        if not lim:
            return False, ('拿不到 Fan/Status 里 OEM 自己报的上下限，拒绝下发：'
                           '没有护栏的功耗墙写入不做。Fan/Status 只在被问到时才报，'
                           '先发一条 GETSTATUS 再试')
        bounds = {'PL1': (lim.get('pl1_min'), lim.get('pl1_max')),
                  # OEM 没报 PL2 的独立边界，借用 PL1 那一组（它总不会比 PL1 上限更宽）
                  'PL2': (lim.get('pl1_min'), lim.get('pl1_max')),
                  'PL4': (lim.get('pl1_min'), lim.get('pl4_max'))}
        extra = {}
        for key, value in want.items():
            lo, hi = bounds[key]
            try:
                iv = int(value)
            except (TypeError, ValueError):
                return False, '%s=%r 不是整数' % (key, value)
            if iv < 0 or iv > 255:
                return False, ('%s=%d 超出一个字节，服务端会静默截断成 %d W，拒发'
                               % (key, iv, iv & 0xFF))
            if lo is None or hi is None:
                return False, '%s 的 OEM 边界没报（只报了一半），不敢发' % key
            if not lo <= iv <= hi:
                return False, '%s=%d 超出 OEM 自己给的区间 %d~%d，拒发' % (key, iv, lo, hi)
            extra[key] = str(iv)          # OEM 的报文里值是字符串，照它的样子发
        return self.send_action('SET_PL_DETAIL', extra=extra,
                                note='功耗墙 %s' % ' '.join('%s=%s' % kv for kv in sorted(extra.items())))

    def read(self):
        payloads = self.payloads()
        out = {'source': 'mqtt'}
        tray = (payloads.get('Tray/Status') or {}).get('data', {})
        fan = (payloads.get('Fan/Status') or {}).get('data', {})
        if tray:
            out['mode'] = MODE_FIELD.get(str(tray.get('OperatingMode')), 'unknown')
            out['fan_boost'] = str(tray.get('FanBoostEnable')) == '1'
        if fan:
            out['pl1'] = fan.get('CPU_PL1')
            out['pl2'] = fan.get('CPU_PL2')
            out['ec_mode_raw'] = fan.get('OperatingMode')
        kb = (payloads.get('Keyboard/Status') or {}).get('data', {})
        if kb:
            out['kb_backlight'] = {'on': str(kb.get('powerStatus', '')).lower() == 'on',
                                   'brightness': kb.get('brightNess')}
        oem = self._oem_switches(payloads)
        if oem:
            out['oem'] = oem
        out['topics'] = sorted(payloads.keys())
        return out

    @staticmethod
    def _oem_switches(payloads):
        """OEM 自己报上来的开关状态与允许范围（全是读数，一个字都不写）。

        这些字段的值就是 OEM 的原文（WINKEY_STATUS_LOCK 这种），照实转成布尔/词，
        并把原文留在 `_raw` 里，认不出来就是 None——面板不猜。
        2026-09-30 00:27 那一条 GETSTATUS 的完整回报见 notes/hardware-channels.md 6.6/6.7。
        """
        def data(topic):
            return (payloads.get(topic) or {}).get('data') or {}

        setting = data('Setting/Status')
        bar = data('HidLightbar/Status')
        kb = data('Keyboard/Status')
        fan = data('Fan/Status')
        if not setting and not bar and not fan:
            return None

        def flag(value, on_token):
            """比最后一段：'WINKEY_STATUS_LOCK'→True，'WINKEY_STATUS_UNLOCK'→False。

            不能用 endswith：'UNLOCK' 也以 'LOCK' 结尾，会把「没锁」判成「锁着」。
            空值/认不出来一律 None，面板照实写「未知」。
            """
            text = str(value or '')
            if not text or text == 'None' or '_' not in text:
                return None
            return text.rsplit('_', 1)[-1] == on_token

        def num(value):
            try:
                return int(str(value))
            except (TypeError, ValueError):
                return None

        out = {
            # Win 键锁定：OEM 原文 WINKEY_STATUS_LOCK / _UNLOCK，命令是
            # Setting/Control {"Action":"WINKEY_LOCK"|"WINKEY_UNLOCK"}。
            # 2026-09-30 01:16 观察3 已确认：用户连点三次，这里的 LOCK/UNLOCK/LOCK
            # 与 EC 的 ADDR_STAUTS_BYTE 0→1→0→1 逐条对齐。
            'win_key_locked': flag(setting.get('WinKey'), 'LOCK'),
            # 触摸板：本机触摸板上有个**实体拨动开关**，这是它的读数。OEM 字符串表里
            # 确实有 TOUCHPAD_TOGGLE_ON/OFF（cc-strings-all.txt、gcu-allfields.txt 都有），
            # 但没验证过软件写下去会不会被实体开关盖掉，所以面板只读不写。
            'touchpad_on': flag(setting.get('TouchpadToggle'), 'ON'),
            'lightbar_on': flag(setting.get('LightBar'), 'ON'),
            'kb_single_color_on': flag(setting.get('SingleColorKBBL'), 'ON'),
            'usb_charger_on': flag(setting.get('UsbCharger'), 'ON'),
            'osd_hidden': flag(setting.get('OSD'), 'ON'),
            'fn_locked': flag(setting.get('FnKey'), 'LOCK'),
            'numpad_locked': flag(setting.get('NumPad'), 'LOCK'),
            'mux_on': flag(setting.get('DiscreteGpuDirectConnectionSwitch_Status'), 'ON'),
            'mux_support': str(setting.get('DiscreteGpuDirectConnectionSwitch_Support') or '') == 'Support',
            # 2026-09-30 01:15 那条 Setting/Status 里这两个字段并存：
            #   DiscreteGpuDirectConnectionSwitch_Status = DGPU_DIRECT_CONNECT_TOGGLE_ON
            #   DGpu                                     = NV_CTRL_PANEL_AUTOSELECT
            # 看着像矛盾，更可能是**两层不同的东西**（一个是独显直连开关，一个是 NVIDIA
            # 控制面板的输出偏好）。Hackintosh 那边也印证 dGPU 的电源是挂在 ACPI 的
            # \_SB.PCI0.PEG0.PEGP._OFF/_ON 上，不是这个字段。没验证前两个都照实报。
            'dgpu_raw': setting.get('DGpu'),
            'display_mode': setting.get('DisplayMode'),
            'display_feature_on': flag(setting.get('DisplayFeatureStatus'), 'ON'),
            'fn_hotkey_on': flag(setting.get('FnWith1HotkeySwitch_Status'), 'ON'),
            'ac_recovery_on': flag(setting.get('AcRecoverySwitch_Status'), 'ON'),
            'ac_recovery_support': str(setting.get('AcRecoverySwitch_Support') or '') == 'Support',
            # Keyboard/Status.powerStatus 才是键盘背光的真开关（实测 'Off'），
            # Setting/Status.SingleColorKBBL 同时是 ON——两个字段说的不是一件事，
            # 所以面板上「背光开没开」只认 powerStatus，SingleColorKBBL 单独列。
            'kb_power_on': str(kb.get('powerStatus') or '').lower() == 'on' if kb else None,
            'kb_effect': kb.get('effect') if kb else None,
            'kb_speed': kb.get('speed') if kb else None,
            'kb_light': kb.get('light') if kb else None,
            'lightbar_brightness': bar.get('brightNess') if bar else None,
            'kb_brightness_ac': kb.get('ACBrightness') if kb else None,
            'kb_brightness_dc': kb.get('DCBrightness') if kb else None,
            'kb_controller': kb.get('solution') if kb else None,
            # Fan/Status 里 OEM 自己写的允许范围：以后任何写入都拿这组数当护栏。
            'limits': {k: num(fan.get(v)) for k, v in (
                ('pl1_min', 'CPU_PL1Minimum'), ('pl1_max', 'CPU_PL1Maximum'),
                ('pl4_max', 'CPU_PL4Maximum'), ('tgp_min', 'GPU_ConfigurableTGPMinimum'),
                ('tgp_max', 'GPU_ConfigurableTGPMaximum'),
                ('boost_min', 'GPU_DynamicBoostMinimum'), ('boost_max', 'GPU_DynamicBoostMaximum'),
                ('gpu_temp_min', 'GPU_TargetTemperatureMinimum'),
                ('gpu_temp_max', 'GPU_TargetTemperatureMaximum'))} if fan else None,
            # 电池那三档（平衡/健康/长效）已确认在 EC 的 ADDR_AP_OEM_BYTE4 高半字节，
            # 命令是 BatteryProtection/Control 的 BALANCEDMODE/HEALTHYMODE/PERFORMANCEDMODE
            # （解码见 channels.base）。Fan/Status 里这个 PowerMode 是**另一回事**，
            # 当时报 1，三档切换时它没跟着动过，语义未知，只当原始值暴露。
            'power_mode_raw': num(fan.get('PowerMode')) if fan else None,
            'profile_name': fan.get('ProfileName') if fan else None,
            'fan_table': fan.get('FAN_TableName') if fan else None,
            '_raw': {'WinKey': setting.get('WinKey'), 'TouchpadToggle': setting.get('TouchpadToggle'),
                     'LightBar': setting.get('LightBar'), 'UsbCharger': setting.get('UsbCharger')},
            'ts': max([v.get('ts', 0) for v in payloads.values()] or [0]),
        }
        return out

    def tick(self):
        now = time.time()
        if now - self._last_probe_ts > 45:
            self._last_probe_ts = now
            if self.alive:
                self._outq.put(('Setting/Control', json.dumps({'Action': 'GETSTATUS'})))

    def close(self):
        self._stop.set()
