# -*- coding: utf-8 -*-
"""单次、只读、自校验的 EC 读取验证（IOCTL_GPD_ACPI_ECREAD）。

安全约束（notes/hardware-channels.md 第 6 节）逐条对照：
  * 不做任何穷举：IOCTL 码 0x9C40A488 是从 GCUService.exe 的常量表里取出来的，
    并且在 UWACPIDriver.sys 的分发链里确认存在（tools/ioctl_layout_probe.py）；
  * 缓冲布局不是猜的：驱动 ECREAD 处理函数把调用者输入缓冲的前 4 字节
    memcpy 进 ACPI_METHOD_ARGUMENT（Type=0/DataLength=4），输出写 4 字节；
  * 只发**一次**，只发**读**，失败即停手回报，绝不重试、绝不换布局再试。

自校验：默认读 ecBt1RSOC（电量百分比），和 GetSystemPowerStatus 的电量对照。
两个独立来源一致 → 布局与地址表同时被证实；不一致 → 如实报告，不当成功。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sense.system import power_status  # noqa: E402

DEVICE = r'\\.\ACPIDriver'
IOCTL_GPD_ACPI_ECREAD = 0x9C40A488
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# 默认验证目标：电量百分比（可独立校验）
DEFAULT_ADDR = 1195        # ecBt1RSOC
DEFAULT_NAME = 'ecBt1RSOC（电池电量百分比）'

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'ec-gpd-read.txt')


def fmt_err(code):
    buf = wt.LPWSTR()
    n = ctypes.windll.kernel32.FormatMessageW(
        0x1200, None, code & 0xFFFFFFFF, 0, ctypes.byref(buf), 0, None)
    text = buf.value.strip() if n else ''
    return '%s（Win32 错误 %d / 0x%08X）' % (text or '无描述', code, code & 0xFFFFFFFF)


def main(argv):
    addr = int(argv[0], 0) if argv else DEFAULT_ADDR
    name = argv[1] if len(argv) > 1 else DEFAULT_NAME
    lines = []

    def out(text=''):
        print(text)
        lines.append(text)

    out('EC 单次只读验证（IOCTL_GPD_ACPI_ECREAD）  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    out('目标地址=%d (0x%X)  含义=%s' % (addr, addr, name))
    out('安全约束：本进程最多发送 1 次请求，只读，不重试')

    ps = power_status()
    out('独立参照（GetSystemPowerStatus）：交流供电=%s 电量=%s%% 充电=%s' % (
        ps['on_ac'], ps['battery_pct'], ps['charging']))

    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.DeviceIoControl.restype = wt.BOOL
    k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                    ctypes.c_void_p, wt.DWORD,
                                    ctypes.POINTER(wt.DWORD), ctypes.c_void_p]

    handle = k32.CreateFileW(DEVICE, GENERIC_READ | GENERIC_WRITE,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE or handle is None:
        out('[失败] 打不开 %s：%s' % (DEVICE, fmt_err(k32.GetLastError())))
        return _finish(lines, 1)
    out('设备已打开：%s（句柄 0x%X）' % (DEVICE, handle))

    in_buf = ctypes.c_uint32(addr & 0xFFFFFFFF)
    out_buf = ctypes.c_uint32(0)
    returned = wt.DWORD(0)
    ok = k32.DeviceIoControl(handle, IOCTL_GPD_ACPI_ECREAD,
                             ctypes.byref(in_buf), 4,
                             ctypes.byref(out_buf), 4,
                             ctypes.byref(returned), None)
    err = k32.GetLastError()
    k32.CloseHandle(handle)

    out('')
    out('请求：IOCTL=0x%08X  输入=4 字节地址 %d  输出缓冲=4 字节' % (IOCTL_GPD_ACPI_ECREAD, addr))
    out('结果：DeviceIoControl=%s  bytesReturned=%d  GetLastError=%d' % (
        'TRUE' if ok else 'FALSE', returned.value, err))
    out('输出原始 dword=0x%08X（%d）  按字节=%s' % (
        out_buf.value, out_buf.value,
        ' '.join('%02X' % b for b in out_buf.value.to_bytes(4, 'little'))))
    low_byte = out_buf.value & 0xFF
    low_word = out_buf.value & 0xFFFF
    out('低字节=%d (0x%02X)   低字=%d (0x%04X)' % (low_byte, low_byte, low_word, low_word))

    if not ok:
        out('')
        out('[失败] %s' % fmt_err(err))
        out('判读：请求被拒绝。按安全约束停手，不换布局重试。')
        return _finish(lines, 1)

    out('')
    if addr == DEFAULT_ADDR and ps['battery_pct'] >= 0:
        diff = abs(low_byte - ps['battery_pct'])
        if diff <= 2:
            out('[成功] 读到 %d%%，与系统电量 %d%% 一致（差 %d）→ 缓冲布局与地址表同时被证实。'
                % (low_byte, ps['battery_pct'], diff))
            out('结论：EC 只读通道打通，可以继续做风扇转速/档位/PL 的只读采集。')
            return _finish(lines, 0)
        out('[存疑] 请求成功，但读到 %d，与系统电量 %d%% 不符（差 %d）。'
            % (low_byte, ps['battery_pct'], diff))
        out('可能：该地址在本机型不是电量，或返回值的字节序/宽度不同。')
        out('按安全约束：不再试别的地址或布局，先把这个结果报给用户决定下一步。')
        return _finish(lines, 2)
    out('[成功] 请求被驱动接受并返回数据（%d）。该地址没有独立参照，不做「已证实」的断言。'
        % low_byte)
    return _finish(lines, 0)


def _finish(lines, code):
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
