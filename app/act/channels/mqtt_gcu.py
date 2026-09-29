"""OEM GCUBridge MQTT 兜底通道。

本机事实：broker 是 OEM 组件 GCUBridge.exe 监听 127.0.0.1:13688，只接受固定身份；
它会周期性用 GetProcessesByName 校验「clientId 同名进程是否存在」，不存在就踢线
（约 26 秒一次），所以这里必须自带自动重连 + 发送队列补发。
当前该服务被 OpenRevo 的 takeover 停用（Start=4），probe 失败就整通道降级，不报错刷屏。
"""
import json
import os
import queue
import socket
import struct
import threading
import time

from app.act.channels.base import (Channel, CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ,
                                   CAP_RGB, CAP_DGPU)

ACTIONS_PATH = os.path.join(os.path.dirname(__file__), 'gcu_actions.json')
STATUS_TOPICS = ('Tray/Status', 'Fan/Status', 'Setting/Status', 'Keyboard/Status',
                 'HidLightbar/Status', 'Display/Status', 'Monitor/Status',
                 'WhisperMode/Status', 'ProcessControl/Status', 'Customize/Status')
# OperatingMode 的取值只认 OEM 自己枚举里有的：OperatingMode{Office:0, Turbo:2}
# （tools/oem_constant_dump.ps1 导出）。以前这张表还写了 1→balance、3→turbo，
# 那是照抄别家的猜测，和 OEM 枚举直接矛盾；实测 Tray/Status 报的就是 1/2 这类
# 没定义的值，硬翻译成「均衡」等于在面板上编一个本机不存在的档（机主明确说过
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
        for cap in (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_RGB, CAP_DGPU):
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
        # 写档位不做「连上就算可用」：OPERATING_*_MODE 这三个动作名确实来自 OEM 自己的
        # 动作表，但本机还没做过一次「写 → 观察哪个寄存器/跑分变了 → 还原」的可逆验证，
        # 按第 6.3 节的规矩，验证之前不许在面板上点亮这个开关。
        self.caps[CAP_MODE_WRITE] = 'unknown'
        self.detail['mode_write_reason'] = ('未做可逆验证：动作名来自 OEM 动作表，'
                                            '但本机还没实测过它到底改了什么，验证前不点亮')
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
        spec = self.actions['actions'].get(action)
        if not spec:
            return False, 'Action "%s" 不在白名单里，拒绝发送' % action
        topic = self.actions['topics'].get(spec['topic'], 'Fan/Control')
        obj = {'Action': action}
        if extra:
            obj.update(extra)
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
        out['topics'] = sorted(payloads.keys())
        return out

    def tick(self):
        now = time.time()
        if now - self._last_probe_ts > 45:
            self._last_probe_ts = now
            if self.alive:
                self._outq.put(('Setting/Control', json.dumps({'Action': 'GETSTATUS'})))

    def close(self):
        self._stop.set()
