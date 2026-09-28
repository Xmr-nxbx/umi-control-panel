"""EC 直连通道（主通道）：走微软文档化的 ACPI 求值接口。

为什么要自己实现：上一轮有人对第三方内核驱动 UWACPIDriver.sys 穷举 IOCTL，
直接把机器干蓝屏了。静态分析表明该驱动只是把用户态请求转成
IOCTL_ACPI_ASYNC_EVAL_METHOD 转交给 ACPI.sys，由 BIOS 里的 AML 方法真正读写 EC。
所以我们直接对 \\\\.\\ACPI 使用同一套文档化结构（ACPI_EVALUATE_INPUT / ACPI_OUTBUFFER），
不给任何第三方驱动发未确认的缓冲。

安全边界（不可协商）：
  1. 只做只读枚举（IOCTL_ACPI_ENUM_DEVICES）与只读求值；
  2. 写 EC 需要 config.hardware.ec.allow_write 打开，且方法在白名单内；
  3. 绝不穷举、绝不循环重试探测；失败就停手并把原因回报到面板。
"""
import ctypes
import ctypes.wintypes as wt

from app.act.channels.base import (Channel, CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ,
                                   CAP_FAN_RPM, CAP_TEMP_EC)

FILE_DEVICE_ACPI = 0x00000032
METHOD_BUFFERED = 0


def _ctl_code(dev, func, method, access):
    return (dev << 16) | (access << 14) | (func << 2) | method


IOCTL_ACPI_ASYNC_EVAL_METHOD = _ctl_code(FILE_DEVICE_ACPI, 0x001, METHOD_BUFFERED, 3)
IOCTL_ACPI_ENUM_DEVICES = _ctl_code(FILE_DEVICE_ACPI, 0x00F, METHOD_BUFFERED, 1)

SIG_INPUT = b'AeiC'        # ACPI_EVAL_INPUT_STRUCT_SIG
SIG_OUTPUT = b'AeoB'       # ACPI_EVAL_OUTPUT_BUFFER_SIG

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_SHARE_ALL = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

NAME_TYPE_METHOD = 2

# 静态分析（open-revo.exe 的 .rdata）得到的候选方法名；实测成功才算 verified
EC_METHOD_CANDIDATES = ('ECRR', 'ECRW', 'SMRW')

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


def _u32(n):
    return int(n & 0xFFFFFFFF).to_bytes(4, 'little')


def build_eval_input(name, in_bytes=b'', out_len=256, full_path=None):
    """按 ACPI_EVALUATE_INPUT 布局拼请求缓冲。"""
    head = bytearray(SIG_INPUT)
    if full_path:
        encoded = full_path.encode('utf-16-le') + b'\x00\x00'
        head += _u32(len(encoded)) + encoded
    else:
        head += name.encode('ascii')               # NamePath[1]：4 字符单名
    head += _u32(len(in_bytes or b''))
    head += _u32(out_len)
    return bytes(head) + (in_bytes or b'')


def parse_eval_output(raw):
    """解析 ACPI_OUTBUFFER：签名 / 版本 / 参数个数 / 各参数 (长度 + 数据)。"""
    if not raw or len(raw) < 12 or raw[:4] != SIG_OUTPUT:
        return None
    version = int.from_bytes(raw[4:8], 'little')
    count = int.from_bytes(raw[8:12], 'little')
    off, args = 12, []
    for _ in range(count):
        if off + 4 > len(raw):
            break
        size = int.from_bytes(raw[off:off + 4], 'little')
        off += 4
        if size and off + size <= len(raw):
            args.append(raw[off:off + size])
            off += size
    return {'version': version, 'args': args}


class AcpiHandle:
    def __init__(self):
        self.h = None
        self.error = None

    def open(self):
        if self.h:
            return True
        handle = k32.CreateFileW('\\\\.\\ACPI', GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None)
        if not handle or ctypes.cast(handle, ctypes.c_void_p).value == INVALID_HANDLE_VALUE:
            self.error = '打开 \\\\.\\ACPI 失败 err=%s（通常需要管理员）' % \
                         ctypes.windll.kernel32.GetLastError()
            return False
        self.h = handle
        return True

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None

    def ioctl(self, code, inbuf, out_len):
        if not self.open():
            return None, self.error
        in_ptr = ctypes.create_string_buffer(inbuf, len(inbuf)) if inbuf else None
        out = ctypes.create_string_buffer(max(out_len, 1))
        got = wt.DWORD(0)
        ok = k32.DeviceIoControl(self.h, code, in_ptr, len(inbuf or b''), out,
                                 len(out), ctypes.byref(got), None)
        if not ok:
            return None, 'DeviceIoControl(0x%08X) 失败 err=%s' % (
                code, ctypes.windll.kernel32.GetLastError())
        return out.raw[:got.value], None

    def enumerate_names(self, name_type=NAME_TYPE_METHOD, level=0xFFFFFFFF):
        """纯只读枚举 ACPI 命名空间。level 默认全深度。"""
        body = SIG_INPUT + _u32(name_type) + _u32(level)
        raw, err = self.ioctl(IOCTL_ACPI_ENUM_DEVICES, body, 256 * 1024)
        if raw is None:
            return [], err
        names, off = [], 4
        while off + 4 <= len(raw):
            size = int.from_bytes(raw[off:off + 4], 'little')
            off += 4
            if size == 0 or off + size > len(raw):
                break
            chunk = raw[off:off + size]
            off += size
            try:
                text = chunk.decode('utf-16-le').strip('\x00')
            except UnicodeDecodeError:
                continue
            if text:
                names.append(text)
        return names, None

    def evaluate(self, method, in_bytes=b'', out_len=128, full_path=None):
        buf = build_eval_input(method, in_bytes, out_len, full_path=full_path)
        raw, err = self.ioctl(IOCTL_ACPI_ASYNC_EVAL_METHOD, buf, out_len + 128)
        if raw is None:
            return None, err
        return parse_eval_output(raw), None


class EcChannel(Channel):
    name = 'ec'
    label = 'EC 直连（ACPI AML）'

    def __init__(self, cfg, log):
        super().__init__(cfg, log)
        self.enabled = bool(cfg.get('hardware', 'ec', 'enabled', default=True))
        self.allow_write = bool(cfg.get('hardware', 'ec', 'allow_write', default=False))
        self.acpi = AcpiHandle()
        self.methods = {}
        self.verified_read = False
        for cap in (CAP_MODE_READ, CAP_MODE_WRITE, CAP_PL_READ, CAP_FAN_RPM, CAP_TEMP_EC):
            self.caps[cap] = 'unsupported'
        self.detail = {'enabled': self.enabled, 'allow_write': self.allow_write}

    def probe(self):
        """只枚举命名空间，不读写 EC。"""
        if not self.enabled:
            self.detail['reason'] = '配置中已关闭 EC 通道'
            self.detail['state'] = 'unsupported'
            return self.status()
        if not self.acpi.open():
            self.detail['reason'] = self.acpi.error
            self.detail['state'] = 'blocked'
            self.detail['hint'] = '打开 \\\\.\\ACPI 设备需要管理员权限（用 scripts\\ACPI只读探测.bat 提权跑一次）'
            self.caps[CAP_MODE_READ] = 'blocked'
            return self.status()
        names, err = self.acpi.enumerate_names()
        if err:
            self.detail['reason'] = err
            self.detail['state'] = 'blocked'
            self.detail['hint'] = '枚举 ACPI 命名空间需要管理员权限'
            self.caps[CAP_MODE_READ] = 'blocked'
            return self.status()
        self.detail['method_count'] = len(names)
        for path in names:
            leaf = path.replace('\\', ' ').split()[-1] if path else ''
            if leaf in EC_METHOD_CANDIDATES:
                self.methods.setdefault(leaf, path)
        self.detail['methods'] = sorted(self.methods.values())[:12]
        if 'ECRR' in self.methods:
            self.alive = True
            self.caps[CAP_MODE_READ] = 'unknown'
            self.detail['state'] = 'unknown'
            self.detail['reason'] = '已定位 ECRR，待单次只读验证后才算可用'
        else:
            self.caps[CAP_MODE_READ] = 'unsupported'
            self.detail['state'] = 'unsupported'
            self.detail['reason'] = ('ACPI 命名空间里未找到 ECRR/ECRW（共 %d 个对象，'
                                     '本机型可能用了别的方法名）' % len(names))
        self.caps[CAP_MODE_WRITE] = 'unsupported' if not self.allow_write else 'unknown'
        return self.status()

    def read_ec(self, addr, length=1):
        """单次 EC 读；失败即返回原因，调用方不得循环重试。"""
        path = self.methods.get('ECRR')
        if not path:
            return None, 'ECRR 方法未定位，禁止盲试'
        payload = _u32(addr) + _u32(length)
        full = path if path.startswith('\\') else None
        parsed, err = self.acpi.evaluate('ECRR', payload, 64, full_path=full)
        if parsed is None:
            return None, err
        if not parsed['args']:
            return None, '回包无数据参数（AeoB 结构为空），格式需重新校准'
        self.verified_read = True
        self.caps[CAP_MODE_READ] = 'verified'
        return parsed['args'][0], None

    def set_mode(self, mode):
        return False, 'EC 写档未开放：需先完成只读验证，再在配置中显式允许写入'

    def read(self):
        return {'methods': sorted(self.methods.keys()),
                'verified_read': self.verified_read,
                'allow_write': self.allow_write}

    def close(self):
        self.acpi.close()
