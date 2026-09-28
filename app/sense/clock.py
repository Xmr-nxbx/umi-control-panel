r"""CPU 实际频率：PDH 读 `% Processor Performance` × 标称频率，用户态、零驱动。

为什么要这个数：档位切换只改 Windows 电源属性，用户「体会不出来」是因为看不到
真实频率。本机实测：空载约 1800～4100 MHz，加载 4232 MHz（标称 2304 MHz）。
第一次采集必然返回 0xC0000BC6（无数据），所以按「无效就返回上次值」处理，
不当成错误——和热区温度那套一样的自愈思路。
"""
import ctypes
from ctypes import wintypes as wt

pdh = ctypes.windll.pdh

PDH_SUCCESS = 0x00000000
PDH_FMT_DOUBLE = 0x00000200
PERF_PATH = r'\Processor Information(_Total)\% Processor Performance'
PROC_ROOT = r'HARDWARE\DESCRIPTION\System\CentralProcessor\0'

pdh.PdhOpenQueryW.restype = wt.DWORD
pdh.PdhOpenQueryW.argtypes = [wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
pdh.PdhAddEnglishCounterW.restype = wt.DWORD
pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p)]
pdh.PdhCollectQueryData.restype = wt.DWORD
pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
pdh.PdhGetFormattedCounterValue.restype = wt.DWORD
pdh.PdhGetFormattedCounterValue.argtypes = [ctypes.c_void_p, wt.DWORD,
                                            ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
pdh.PdhCloseQuery.restype = wt.DWORD
pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]


class _VALUE(ctypes.Structure):
    _fields_ = [('cstatus', wt.DWORD), ('pad', wt.DWORD),
                ('doubleValue', ctypes.c_double), ('rest', ctypes.c_byte * 8)]


def base_frequency_mhz():
    """标称频率（MHz）+ CPU 名，来自注册表只读。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PROC_ROOT) as key:
            mhz = int(winreg.QueryValueEx(key, '~MHz')[0])
            try:
                name = str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).strip()
            except OSError:
                name = ''
        return mhz, name
    except (OSError, ValueError, ImportError):
        return None, ''


def _plausible(mhz):
    return mhz is not None and 100.0 <= mhz <= 8000.0


class ClockSense:
    def __init__(self):
        self.base_mhz, self.cpu_name = base_frequency_mhz()
        self.query = ctypes.c_void_p()
        self.handle = None
        self.available = False
        self.error = None
        self.last_mhz = None
        self._misses = 0
        self._open()

    def _open(self):
        if pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != PDH_SUCCESS:
            self.error = 'PdhOpenQueryW 失败'
            return
        h = ctypes.c_void_p()
        if pdh.PdhAddEnglishCounterW(self.query, PERF_PATH, None, ctypes.byref(h)) != PDH_SUCCESS:
            self.error = '本机没有 Processor Information 计数器'
            return
        self.handle, self.available = h, True

    def read(self):
        """返回实际频率 MHz；拿不到就返回上次值（可能是 None）。"""
        if not self.available or not self.base_mhz:
            return self.last_mhz
        try:
            if pdh.PdhCollectQueryData(self.query) != PDH_SUCCESS:
                return self.last_mhz
            val, ctime = _VALUE(), wt.DWORD(0)
            rc = pdh.PdhGetFormattedCounterValue(self.handle, PDH_FMT_DOUBLE,
                                                 ctypes.byref(ctime), ctypes.byref(val))
            if rc != PDH_SUCCESS or val.cstatus != PDH_SUCCESS:
                self._misses += 1
                return self.last_mhz
            mhz = self.base_mhz * val.doubleValue / 100.0
            if not _plausible(mhz):
                self._misses += 1
                return self.last_mhz
            self._misses = 0
            self.last_mhz = round(mhz)
            return self.last_mhz
        except Exception as exc:                     # noqa: BLE001 - 采集层必须自愈
            self.error = repr(exc)
            return self.last_mhz

    def close(self):
        if self.query:
            try:
                pdh.PdhCloseQuery(self.query)
            except Exception:                        # noqa: BLE001
                pass
            self.query = None
