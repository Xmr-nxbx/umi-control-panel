r"""EC 直连通道（主通道）：走 OEM 的 UWACPIDriver 设备 `\\.\ACPIDriver`。

本机实测（tools/acpi_probe.py 的输出）：
  * `\\.\ACPI` 不存在（CreateFile err=2 系统找不到指定的文件），
    所以微软文档化的 ACPI 求值接口在用户态没有入口 —— 上一版判断错了，这里纠正；
  * `\\.\ACPIDriver` 普通用户即可 GENERIC_READ|GENERIC_WRITE 打开（DACL 向所有人开着）；
  * 驱动代码段里硬编码了 `AeiC`/`AeoB` 签名与 `ECRR`/`ECRW`/`SMRW` 三个方法名
    （AeiC 4 处、AeoB 4 处、方法名各 1 处），说明 EC 方法名由驱动内部填，
    用户态只交请求负载；结合"IoControlCode=0 也返回 SUCCESS"的现象，
    它大概率不按 IOCTL 码分发。

安全边界（违反就是上次蓝屏的成因）：
  1. 绝不做穷举/循环探测；本文件的请求只允许"单次、完整缓冲、只读(ECRR)"；
  2. 写 EC（ECRW）默认关闭，需 config.hardware.ec.allow_write 且由用户逐项确认；
  3. 失败即停手，把原始字节回报出来供离线分析，不再发第二个请求。
"""
import ctypes
import ctypes.wintypes as wt

from app.act.channels.base import (Channel, CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ,
                                   CAP_FAN_RPM, CAP_TEMP_EC)

METHOD_BUFFERED = 0
FILE_DEVICE_UNKNOWN = 0x22


def _ctl_code(dev, func, method, access):
    return (dev << 16) | (access << 14) | (func << 2) | method


# 语义上最贴切的码；驱动若不分发码，这个值也无害（一次尝试，失败即停）
IOCTL_EVAL_ASYNC = _ctl_code(0x32, 0x001, METHOD_BUFFERED, 3)          # 0x32C004
IOCTL_GENERIC_BUFFERED = _ctl_code(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, 3)

SIG_IN = b'AeiC'        # ACPI_EVAL_INPUT_STRUCT_SIG
SIG_OUT = b'AeoB'       # ACPI_EVAL_OUTPUT_BUFFER_SIG
DEVICE = '\\\\.\\ACPIDriver'
ACPI_DEVICE = '\\\\.\\ACPI'

READ_METHOD = 'ECRR'
WRITE_METHODS = ('ECRW', 'SMRW')           # 只登记，不主动使用
IN_BUF_TOTAL = 0x28                        # 驱动侧观察到的入参总长（40）

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_ALL = 3
OPEN_EXISTING = 3
INVALID = ctypes.c_void_p(-1).value

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.DeviceIoControl.restype = wt.BOOL
k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                ctypes.c_void_p]
k32.CloseHandle.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.FormatMessageW.restype = wt.DWORD


def _u32(n):
    return int(n & 0xFFFFFFFF).to_bytes(4, 'little')


def error_text(err):
    if not err:
        return ''
    buf = ctypes.create_unicode_buffer(256)
    k32.FormatMessageW(0x1000 | 0x200, None, err, 0, buf, 256, None)
    return buf.value.strip()


class EcDevice:
    """`\\.`\ACPIDriver` 句柄 + 一次一发请求的封装（内置单次熔断）。"""

    def __init__(self):
        self.h = None
        self.device = None
        self.open_error = None
        self.attempts = 0            # 熔断用：本进程只允许极少量请求
        self.last_raw = b''
        self.last_ioctl = None

    def open(self):
        if self.h:
            return True
        for name in (DEVICE, ACPI_DEVICE):
            handle = k32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_ALL,
                                     None, OPEN_EXISTING, 0, None)
            err = ctypes.get_last_error()
            value = ctypes.cast(handle, ctypes.c_void_p).value if handle else None
            if value not in (None, 0, INVALID):
                self.h, self.device = handle, name
                self.open_error = None
                return True
            self.open_error = '%s 打开失败 err=%s %s' % (name, err, error_text(err))
        return False

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None
            self.device = None

    def request_read(self, addr, length=1, ioctl=IOCTL_EVAL_ASYNC, max_attempts=1):
        """单次只读 EC 请求。超出 max_attempts 直接拒绝，防止演变成穷举。"""
        if self.attempts >= max_attempts:
            return None, '已达到本次会话的请求上限（%d 次），按安全规则停手' % max_attempts
        if not self.open():
            return None, self.open_error
        self.attempts += 1
        self.last_ioctl = ioctl
        body = build_read_input(addr, length)
        in_buf = ctypes.create_string_buffer(body, len(body))
        out_len = 512
        out_buf = ctypes.create_string_buffer(out_len)
        got = wt.DWORD(0)
        ok = k32.DeviceIoControl(self.h, ioctl, in_buf, len(body), out_buf, out_len,
                                 ctypes.byref(got), None)
        err = ctypes.get_last_error()
        if not ok:
            self.last_raw = b''
            return None, 'DeviceIoControl(0x%08X) 失败 err=%s %s' % (ioctl, err, error_text(err))
        self.last_raw = out_buf.raw[:got.value]
        return parse_output(self.last_raw), None


def build_read_input(addr, length=1, data_len=IN_BUF_TOTAL):
    """按驱动侧观察到的布局拼只读请求：签名 + 方法名 + 入参长 + 出参长 + [状态, 数据长, 数据]。

    入参负载是 16 位状态 + 16 位长度 + 数据区，凑够驱动期望的总长（0x28）。
    """
    payload = bytearray()
    payload += (0).to_bytes(2, 'little')            # word 状态（输入侧填 0）
    payload += (length & 0xFFFF).to_bytes(2, 'little')
    payload += (addr & 0xFFFF).to_bytes(2, 'little')  # 首字给 EC 地址
    payload += bytes(max(0, data_len - 8))
    head = bytearray(SIG_IN) + READ_METHOD.encode('ascii')
    head += _u32(len(payload))                       # InBufferLength
    head += _u32(256)                                # OutBufferLength
    return bytes(head) + bytes(payload)


def parse_output(raw):
    """解析 ACPI_OUTBUFFER：'AeoB' + 版本 + 参数个数 + 各参数(长度+数据)。"""
    info = {'sig': raw[:4].hex(' ') if len(raw) >= 4 else '', 'size': len(raw),
            'ok': False, 'status': None, 'data': b'', 'args': []}
    if len(raw) < 12:
        return info
    info['version'] = int.from_bytes(raw[4:8], 'little')
    info['arg_count'] = int.from_bytes(raw[8:12], 'little')
    off = 12
    args = []
    for _ in range(max(info['arg_count'], 1)):
        if off + 4 > len(raw):
            break
        size = int.from_bytes(raw[off:off + 4], 'little')
        off += 4
        chunk = raw[off:off + size]
        off += size
        args.append(chunk)
    info['args'] = args
    if raw[:4] == SIG_OUT and args:
        head = args[0]
        if len(head) >= 4:
            info['status'] = int.from_bytes(head[:2], 'little')
            info['data'] = head[4:] if len(head) > 4 else b''
        else:
            info['data'] = head
        info['ok'] = True
    elif raw[:4] == SIG_OUT:
        info['ok'] = True
    return info


class EcChannel(Channel):
    name = 'ec'
    label = 'EC 直连（UWACPIDriver）'

    def __init__(self, cfg, log):
        super().__init__(cfg, log)
        self.enabled = bool(cfg.get('hardware', 'ec', 'enabled', default=True))
        self.allow_write = bool(cfg.get('hardware', 'ec', 'allow_write', default=False))
        self.dev = EcDevice()
        self.verified_read = False
        for cap in (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_FAN_RPM, CAP_TEMP_EC):
            self.caps[cap] = 'unsupported'
        self.detail = {'enabled': self.enabled, 'allow_write': self.allow_write,
                       'device': None, 'state': 'unknown'}

    def probe(self):
        """只开句柄 + 记录能力状态；不在这个函数里发 EC 请求。"""
        if not self.enabled:
            self.detail.update(reason='配置中已关闭 EC 通道', state='unsupported')
            return self.status()
        if not self.dev.open():
            self.detail.update(reason=self.dev.open_error, state='blocked')
            self.caps[CAP_MODE_READ] = 'blocked'
            return self.status()
        self.alive = True
        self.detail['device'] = self.dev.device
        self.detail['state'] = 'unknown'
        self.detail['reason'] = ('设备已打开（%s），等待单次只读验证后才能读档位/转速'
                                 % self.dev.device)
        self.caps[CAP_MODE_READ] = 'unknown'
        self.caps[CAP_MODE_WRITE] = 'unsupported' if not self.allow_write else 'unknown'
        return self.status()

    def read_ec(self, addr, length=1):
        """单次只读；调用方不得循环重试（EcDevice 内部也有熔断）。"""
        parsed, err = self.dev.request_read(addr, length)
        if parsed is None:
            return None, err
        if not parsed['ok']:
            return None, ('回包签名不是 AeoB（前 4 字节=%s，长度 %d），'
                          '请求布局需离线校准，不再重试' % (parsed['sig'], parsed['size']))
        self.verified_read = True
        self.caps[CAP_MODE_READ] = 'verified'
        self.detail['state'] = 'verified'
        self.detail['reason'] = '单次只读 ECRR 已成功，EC 读通道可用'
        return parsed['data'], None

    def set_mode(self, mode):
        return False, 'EC 写档未开放：需先完成只读验证，再由用户逐项允许写入'

    def read(self):
        return {'device': self.dev.device, 'opened': bool(self.dev.h),
                'verified_read': self.verified_read, 'allow_write': self.allow_write}

    def close(self):
        self.dev.close()
