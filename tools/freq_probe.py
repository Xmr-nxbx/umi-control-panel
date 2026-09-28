# -*- coding: utf-8 -*-
"""只读探测：能不能拿到「实际生效频率」。

用 PDH 试三个候选计数器，跑一小段负载看数值变化。不写任何东西。
"""
import ctypes
import os
import sys
import time

pdh = ctypes.windll.pdh
PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_LONG = 0x00000100

CANDIDATES = [
    r'\Processor Information(_Total)\% Processor Performance',
    r'\Processor Information(_Total)\% of maximum frequency',
    r'\Processor(_Total)\% Processor Time',
]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [('CStatus', ctypes.c_ulong), ('_u', ctypes.c_double * 4)]

    @property
    def doubleValue(self):
        return ctypes.cast(ctypes.byref(self._u), ctypes.POINTER(ctypes.c_double)).contents.value

    @property
    def longValue(self):
        return ctypes.cast(ctypes.byref(self._u), ctypes.POINTER(ctypes.c_long)).contents.value


def add(query, path):
    counter = ctypes.c_void_p()
    rc = pdh.PdhAddEnglishCounterW(query, path, None, ctypes.byref(counter))
    return (counter, rc) if rc == 0 else (None, rc)


def main():
    query = ctypes.c_void_p()
    rc = pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
    print('PdhOpenQueryW rc=0x%08X' % (rc & 0xFFFFFFFF))
    if rc:
        return 1
    handles = []
    for path in CANDIDATES:
        h, rc = add(query, path)
        print('  %-70s rc=0x%08X %s' % (path, rc & 0xFFFFFFFF, 'OK' if h else 'FAIL'))
        if h:
            handles.append((path, h))
    if not handles:
        print('没有可用计数器')
        return 1

    # 基频（MHz）：从注册表读，只读
    base_mhz = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as k:
            base_mhz = int(winreg.QueryValueEx(k, '~MHz')[0])
            name = winreg.QueryValueEx(k, 'ProcessorNameString')[0]
        print('CPU=%s 标称=%d MHz' % (name, base_mhz))
    except OSError as exc:
        print('读标称频率失败：%r' % (exc,))

    print('\n--- 空载 3 秒 ---')
    for _ in range(3):
        pdh.PdhCollectQueryData(query)
        time.sleep(1.0)
        line = []
        for path, h in handles:
            v = PDH_FMT_COUNTERVALUE()
            rc = pdh.PdhGetFormattedCounterValue(h, PDH_FMT_DOUBLE, None, ctypes.byref(v))
            line.append('%s=%.1f' % (path.split(')')[-1].strip(), v.doubleValue)
                        if rc == 0 else '%s=err0x%08X' % (path.split(')')[-1].strip(),
                                                          rc & 0xFFFFFFFF))
        print('  ' + ' | '.join(line))

    print('\n--- 加载 4 秒（纯计算）---')
    stop = time.time() + 4.0
    work_done = [0]

    def burn():
        x = 1.0001
        n = 0
        while time.time() < stop:
            for _ in range(200000):
                x = (x * 1.0000001) % 1.5
            n += 1
        work_done[0] = n

    import threading
    ts = [threading.Thread(target=burn) for _ in range(os.cpu_count() or 4)]
    for t in ts:
        t.start()
    while time.time() < stop:
        pdh.PdhCollectQueryData(query)
        time.sleep(1.0)
        line = []
        for path, h in handles:
            v = PDH_FMT_COUNTERVALUE()
            rc = pdh.PdhGetFormattedCounterValue(h, PDH_FMT_DOUBLE, None, ctypes.byref(v))
            if rc == 0:
                label = path.split(')')[-1].strip()
                extra = ''
                if base_mhz and 'Processor Performance' in path:
                    extra = ' → %.0f MHz' % (base_mhz * v.doubleValue / 100.0)
                line.append('%s=%.1f%s' % (label, v.doubleValue, extra))
            else:
                line.append('%s=err0x%08X' % (path.split(')')[-1].strip(), rc & 0xFFFFFFFF))
        print('  ' + ' | '.join(line))
    for t in ts:
        t.join()
    pdh.PdhCloseQuery(query)
    return 0


if __name__ == '__main__':
    sys.exit(main())
