r"""ACPI / EC 通道诊断（纯只读，绝不发送任何 IOCTL）。

用途：搞清楚本机到底能不能打开 \\.\ACPI 与 \\.\ACPIDriver、以什么权限打开、
失败的真实错误码是什么，以及有没有可用的 WMI EC 读取入口。
所有结论都会写进 tools\out\acpi-probe.txt，方便提权跑完后把文件交回分析。

安全边界：本脚本只做 CreateFile + 错误码解析 + 只读 WMI 查询，
不发任何 DeviceIoControl，不写 EC，不装/停任何驱动。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.FormatMessageW.restype = wt.DWORD

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_ALL = 3
OPEN_EXISTING = 3
INVALID = ctypes.c_void_p(-1).value

TARGETS = (
    ('\\\\.\\ACPI', GENERIC_READ, 'ACPI.sys 只读'),
    ('\\\\.\\ACPI', GENERIC_READ | GENERIC_WRITE, 'ACPI.sys 读写（求值 AML 需要）'),
    ('\\\\.\\ACPIDriver', GENERIC_READ, 'OEM UWACPIDriver 只读'),
    ('\\\\.\\ACPIDriver', GENERIC_READ | GENERIC_WRITE, 'OEM UWACPIDriver 读写'),
)


def last_error_text(err):
    if not err:
        return '(错误码为 0，通常表示调用其实成功了或取码时机不对)'
    buf = ctypes.create_unicode_buffer(512)
    k32.FormatMessageW(0x1000 | 0x200, None, err, 0, buf, 512, None)
    return buf.value.strip() or '未知错误'


def probe_handles():
    lines = []
    admin = False
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                     # noqa: BLE001
        pass
    lines.append('是否管理员权限运行：%s' % ('是' if admin else '否'))
    lines.append('')
    lines.append('--- CreateFile 探测（只开句柄，不发 IOCTL）---')
    for name, access, note in TARGETS:
        # 紧跟着取错误码，避免中间调用把它冲掉
        handle = k32.CreateFileW(name, access, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None)
        err = ctypes.get_last_error()
        value = ctypes.cast(handle, ctypes.c_void_p).value if handle else None
        if value is None and handle:
            value = int(handle)
        ok = value not in (None, 0, INVALID)
        flag = 'OK  ' if ok else 'FAIL'
        lines.append('%s %-16s %-22s handle=%s err=%s %s' % (
            flag, name, note, value, err, last_error_text(err)))
        if ok:
            k32.CloseHandle(handle)
        time.sleep(0.05)
    return lines, admin


def ps(command):
    """PowerShell 在部分环境里 stdout 会丢，统一落盘再读，保证拿得到结果。"""
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'umi-probe-tmp.txt')
    full = '%s | Out-File -Encoding utf8 "%s"' % (command, tmp)
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', full],
                       capture_output=True, timeout=90, creationflags=0x08000000)
        with open(tmp, encoding='utf-8-sig', errors='replace') as f:
            return f.read()
    except Exception as exc:                              # noqa: BLE001
        return '执行失败：%r' % (exc,)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def probe_wmi():
    lines = ['', '--- root\\WMI 里与 EC / ACPI / 热区相关的类（只读查询）---']
    out = ps("Get-CimClass -Namespace root/WMI | Where-Object { "
             "$_.CimClassName -match 'EC|ACPI|Thermal|Battery|Energy' } "
             "| Select-Object -ExpandProperty CimClassName")
    found = [x.strip() for x in out.splitlines() if x.strip()]
    lines.extend(found or ['（没取到，或该类命名空间不可读）'])
    lines.append('')
    lines.append('--- 可能带方法的 WMI 类（EC 读写候选）---')
    out2 = ps("Get-CimClass -Namespace root/WMI | Where-Object { "
              "$_.CimClassName -match 'EC|ACPI' } | ForEach-Object { "
              "$_.CimClassName + ' : ' + (($_.CimClassMethods | "
              "Select-Object -ExpandProperty Name) -join ',') }")
    lines.extend([x for x in out2.splitlines() if x.strip()] or ['（无）'])
    return lines


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, 'tools', 'out')
    os.makedirs(out_dir, exist_ok=True)
    report = ['ACPI/EC 通道诊断报告  %s' % time.strftime('%Y-%m-%d %H:%M:%S'), '']
    lines, admin = probe_handles()
    report.extend(lines)
    report.extend(probe_wmi())
    report.append('')
    report.append('--- 说明 ---')
    report.append(r'1) \\.\ACPI 读写都 FAIL 且非管理员 → 用管理员身份重跑本探测；')
    report.append(r'2) \\.\ACPI 管理员下 OK → EC 直连可走微软文档化的 AML 求值接口；')
    report.append(r'3) \\.\ACPI 全 FAIL 但 \\.\ACPIDriver OK → 只能走 OEM 驱动，'
                  r'其私有 IOCTL 需先从驱动镜像静态确认，禁止穷举；')
    report.append('4) 出现 root\\WMI 的 EC/ACPI 类与方法 → 优先评估 WMI 路线（免管理员、最安全）。')
    if not admin:
        report.append('')
        report.append(r'注意：本次不是管理员运行，\\.\ACPI 的失败结论还不能定论。')
    text = '\n'.join(report)
    path = os.path.join(out_dir, 'acpi-probe.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    print(text)
    print('\n报告已写入 %s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
