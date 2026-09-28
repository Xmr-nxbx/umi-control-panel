r"""CPU 温度：PDH 读 ACPI 热区，用户态、零驱动。

本机实测出来的三条规律（换机器时靠候选路径 + 自校验自愈）：
  1. `_Total` 实例能添加成功却永远返回 0xC0000BC6（无数据）；
  2. 实例枚举 PdhEnumObjectItemsW 在中文系统上要的是本地化对象名，
     传英文名拿不到列表，所以不依赖它；
  3. `(*)` 数组在 x64 上每项 40 字节：+0 是名字指针、+8 是 CStatus、+16 是 double，
     缓冲区尾部还挂着 UTF-16 实例名。名字指针必然落在缓冲区内部，
     用这一点反推并校验步长，比猜结构体大小可靠得多。

读数单位 0.1K；给的是热区最高温而不是逐核心温度，用于温控决策和面板展示足够。
"""
import ctypes
from ctypes import wintypes as wt

pdh = ctypes.windll.pdh

PDH_SUCCESS = 0x00000000
PDH_MORE_DATA = 0x800007D3
PDH_HINT_CODES = (0x800007D2, PDH_MORE_DATA)
PDH_FMT_DOUBLE = 0x00000200
CSTATUS_VALID = 0x00000000
PDH_NO_DATA = 0x800007D5

OBJECT = 'Thermal Zone Information'
ARRAY_PATHS = tuple(r'\%s(*)\%s' % (OBJECT, c) for c in
                    ('High Precision Temperature', 'Temperature'))
SINGLE_PATHS = tuple(r'\%s(_Total)\%s' % (OBJECT, c) for c in
                     ('High Precision Temperature', 'Temperature'))

_NAME_OFF, _CSTATUS_OFF, _UNION_OFF = 0, 8, 16
STRIDE_CANDIDATES = (40, 44, 48, 32, 24, 56, 64)

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
pdh.PdhGetFormattedCounterArrayW.restype = wt.DWORD
pdh.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, wt.DWORD,
                                             ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
                                             ctypes.c_void_p]
pdh.PdhCloseQuery.restype = wt.DWORD
pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]


class _VALUE(ctypes.Structure):
    _fields_ = [('cstatus', wt.DWORD), ('pad', wt.DWORD),
                ('doubleValue', ctypes.c_double), ('rest', ctypes.c_byte * 8)]


def _to_celsius(raw):
    """0.1K 为主；部分实现给 K 或 °C。"""
    if raw is None or raw <= 0:
        return None
    if raw > 1000:                      # 3242 => 324.2K => 51.0°C
        return raw / 10.0 - 273.15
    if raw > 200:
        return raw - 273.15
    return raw


def _plausible(c):
    return c is not None and -40.0 <= c <= 160.0


def _name_in_buffer(raw, base_addr, ptr):
    """名字指针必须落在缓冲区内部；用它校验步长，并还原出热区名。"""
    if not (base_addr <= ptr < base_addr + len(raw)):
        return None
    off = ptr - base_addr
    chunk = raw[off:]
    text = bytearray()
    i = 0
    while i + 1 < len(chunk):
        unit = chunk[i] | (chunk[i + 1] << 8)
        if unit == 0:
            break
        text += chunk[i:i + 2]
        i += 2
    try:
        name = text.decode('utf-16-le')
    except UnicodeDecodeError:
        return None
    return name if name.strip() else None


class Thermal:
    def __init__(self):
        self.query = ctypes.c_void_p()
        self.handle = None
        self.mode = None            # 'array' | 'single'
        self.source = None
        self.stride = None
        self.voff = _UNION_OFF
        self.available = False
        self.last_temp = None
        self.zones = 0
        self.zone_names = []
        self.error = None
        self._misses = 0
        self._paths = [(p, 'array') for p in ARRAY_PATHS] + [(p, 'single') for p in SINGLE_PATHS]
        self._next = 0
        self._open()

    def _open(self):
        if pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != PDH_SUCCESS:
            self.error = 'PdhOpenQueryW 失败'
            return
        self._select_next()

    def _select_next(self):
        """添加候选计数器；一个在本机加不上就顺延，全失败才降级为无温度。"""
        while self._next < len(self._paths):
            path, mode = self._paths[self._next]
            h = ctypes.c_void_p()
            self._next += 1
            if pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(h)) == PDH_SUCCESS:
                self.handle, self.mode, self.source = h, mode, path
                self.stride, self._misses = None, 0
                self.available = True
                return True
        self.available = False
        self.error = '本机没有可用的热区温度计数器'
        return False

    def _collect(self):
        return pdh.PdhCollectQueryData(self.query) in (PDH_SUCCESS, PDH_NO_DATA)

    # ---------- 单值路径 ----------
    def _read_single(self):
        val, ctime = _VALUE(), wt.DWORD(0)
        rc = pdh.PdhGetFormattedCounterValue(self.handle, PDH_FMT_DOUBLE,
                                             ctypes.byref(ctime), ctypes.byref(val))
        if rc != PDH_SUCCESS or val.cstatus != CSTATUS_VALID:
            return None, []
        c = _to_celsius(val.doubleValue)
        return (c if _plausible(c) else None), [self.source] if _plausible(c) else []

    # ---------- 数组路径 ----------
    def _fetch_array(self):
        size, count = wt.DWORD(0), wt.DWORD(0)
        rc = pdh.PdhGetFormattedCounterArrayW(self.handle, PDH_FMT_DOUBLE,
                                              ctypes.byref(size), ctypes.byref(count), None)
        if rc not in PDH_HINT_CODES + (PDH_SUCCESS,) or not size.value or not count.value:
            return None, 0
        buf = ctypes.create_unicode_buffer(size.value // 2 + 4)
        asize = wt.DWORD((size.value // 2 + 4) * 2)
        if pdh.PdhGetFormattedCounterArrayW(self.handle, PDH_FMT_DOUBLE,
                                            ctypes.byref(asize), ctypes.byref(count),
                                            ctypes.byref(buf)) != PDH_SUCCESS:
            return None, 0
        return buf, count.value

    def _read_array(self):
        buf, n = self._fetch_array()
        if buf is None or n <= 0:
            return None, []
        raw = ctypes.string_at(ctypes.byref(buf), len(buf) * 2)
        base_addr = ctypes.addressof(buf)
        if self.stride is None:
            self._calibrate(raw, base_addr, n)
            if not self.stride:
                return None, []
        best, names = None, []
        for k in range(n):
            base = self.stride * k
            if base + self.voff + 8 > len(raw):
                break
            if int.from_bytes(raw[base + _CSTATUS_OFF:base + _CSTATUS_OFF + 4],
                              'little') != CSTATUS_VALID:
                continue
            ptr = int.from_bytes(raw[base + _NAME_OFF:base + _NAME_OFF + 8], 'little')
            zone = _name_in_buffer(raw, base_addr, ptr)
            c = _to_celsius(ctypes.c_double.from_buffer_copy(
                raw[base + self.voff:base + self.voff + 8]).value)
            if not _plausible(c):
                continue
            if best is None or c > best:
                best = c
            names.append(zone or ('zone%d' % k))
        return best, names

    def _calibrate(self, raw, base_addr, n):
        """步长候选逐个试：要求每项 CStatus 有效、double 是合理温度、且名字指针在缓冲区内。"""
        for stride in sorted(set([len(raw) // max(n, 1)] + list(STRIDE_CANDIDATES)), reverse=True):
            if stride < _UNION_OFF + 8:
                continue
            for voff in (_UNION_OFF, 24, 32):
                if stride <= voff:
                    continue
                ok = True
                for k in range(n):
                    base = stride * k
                    if base + voff + 8 > len(raw):
                        ok = False
                        break
                    if int.from_bytes(raw[base + _CSTATUS_OFF:base + _CSTATUS_OFF + 4],
                                      'little') != CSTATUS_VALID:
                        ok = False
                        break
                    ptr = int.from_bytes(raw[base + _NAME_OFF:base + _NAME_OFF + 8], 'little')
                    if not _name_in_buffer(raw, base_addr, ptr):
                        ok = False
                        break
                    if not _plausible(_to_celsius(ctypes.c_double.from_buffer_copy(
                            raw[base + voff:base + voff + 8]).value)):
                        ok = False
                        break
                if ok:
                    self.stride, self.voff = stride, voff
                    return

    # ---------- 对外 ----------
    def read(self):
        """返回 (最高温度°C, 有数据的热区数)。"""
        if not self.available:
            return None, 0
        try:
            if not self._collect():
                return self.last_temp, self.zones
            best, names = self._read_array() if self.mode == 'array' else self._read_single()
            if best is None:
                self._misses += 1
                if self._misses >= 3 and self._next < len(self._paths):
                    self._select_next()       # 本机这个计数器不给数据，换下一条路径
                return self.last_temp, self.zones
            self._misses = 0
            self.zone_names = names
            self.zones = len(names)
            self.last_temp = round(best, 1)
            return self.last_temp, self.zones
        except Exception as exc:                  # noqa: BLE001
            self.error = repr(exc)
            return self.last_temp, self.zones

    def close(self):
        if self.query:
            try:
                pdh.PdhCloseQuery(self.query)
            except Exception:                     # noqa: BLE001
                pass
            self.query = None
