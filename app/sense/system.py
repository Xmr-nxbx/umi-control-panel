"""系统侧遥测：全部走用户态 Win32 API，不加载任何内核驱动。"""
import ctypes
import ctypes.wintypes as wt
import os
import threading
import time

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
power_base = ctypes.windll.kernel32


class _FILETIME(ctypes.Structure):
    _fields_ = [('lo', wt.DWORD), ('hi', wt.DWORD)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.UINT), ('dwTime', wt.UINT)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', wt.DWORD), ('dwMemoryLoad', wt.DWORD),
        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ('ACLineStatus', wt.BYTE), ('BatteryFlag', wt.BYTE),
        ('BatteryLifePercent', wt.BYTE), ('SystemStatusFlag', wt.BYTE),
        ('BatteryLifeTime', wt.DWORD), ('BatteryFullLifeTime', wt.DWORD),
    ]


def _ft(v):
    return (v.hi << 32) | v.lo


class CpuSampler:
    """GetSystemTimes 差分：user+system 占用率。"""

    def __init__(self):
        self._prev = None
        self._lock = threading.Lock()
        self.pct = 0.0

    def sample(self):
        idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return self.pct
        cur = (_ft(idle), _ft(kern), _ft(user))
        with self._lock:
            prev, self._prev = self._prev, cur
            if prev is None:
                return self.pct
            d_idle = cur[0] - prev[0]
            d_total = max(cur[1] - prev[1], 1)          # kernel 已含 idle
            busy = d_total - d_idle
            self.pct = max(0.0, min(100.0, busy * 100.0 / d_total))
            if os.name == 'nt' and cur[2] < prev[2]:   # 时间回绕，丢弃一拍
                return self.pct
        return self.pct


def idle_seconds():
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = k32.GetTickCount()
    return max(0.0, (tick - info.dwTime) / 1000.0)


def power_status():
    st = _SYSTEM_POWER_STATUS()
    if not power_base.GetSystemPowerStatus(ctypes.byref(st)):
        return {'on_ac': True, 'battery_pct': -1, 'charging': False}
    b = st.BatteryLifePercent
    return {
        'on_ac': st.ACLineStatus == 1,
        'battery_pct': -1 if b == 255 else int(b),
        'charging': bool(st.BatteryFlag & 0x08),
    }


def memory_status():
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if not k32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return {'total_gb': 0.0, 'used_gb': 0.0, 'pct': 0.0}
    total = m.ullTotalPhys / 1073741824.0
    avail = m.ullAvailPhys / 1073741824.0
    return {'total_gb': round(total, 1), 'used_gb': round(total - avail, 1),
            'pct': round(m.dwMemoryLoad, 1)}


k32.QueryFullProcessImageNameW.restype = wt.BOOL
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def foreground_app():
    """返回 (进程名小写, pid)；失败返回 ('', 0)。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return '', 0
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return '', 0
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return '', pid.value
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return '', pid.value
        return os.path.basename(buf.value).lower(), pid.value
    finally:
        k32.CloseHandle(h)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def uptime_seconds():
    t = k32.GetTickCount()
    return t / 1000.0


class SystemSense:
    """把上面的采集点汇总成一次快照；每项独立容错，单项失败不影响整体。"""

    def __init__(self):
        self._cpu = CpuSampler()
        self.last_error = {}

    def tick(self):
        snap = {'ts': time.time()}
        for key, fn in (('cpu_pct', self._cpu.sample),
                        ('idle_s', idle_seconds),
                        ('mem', memory_status),
                        ('power', power_status)):
            try:
                v = fn()
            except Exception as exc:                      # noqa: BLE001 - 采集层必须自愈
                self.last_error[key] = repr(exc)
                v = None
            if key == 'cpu_pct':
                snap['cpu_pct'] = round(v, 1) if v is not None else None
            else:
                snap[key] = v
        try:
            name, pid = foreground_app()
            snap['foreground'], snap['foreground_pid'] = name, pid
        except Exception as exc:                          # noqa: BLE001
            self.last_error['foreground'] = repr(exc)
            snap['foreground'], snap['foreground_pid'] = '', 0
        return snap
