# -*- coding: utf-8 -*-
"""编译并调用 dotnet_probe.cs（只读反射 / WMI 探测，不依赖 PowerShell）。

第一次调用会用 Windows 自带的 .NET Framework 编译器把 dotnet_probe.cs
编到 tools\\out\\dotnet_probe.exe（已 gitignore），之后直接复用。

用法：
  runtime\\python.exe tools\\dotnet_probe.py constants -Path GCUService.exe -OutJson x.json
  runtime\\python.exe tools\\dotnet_probe.py pinvoke
  runtime\\python.exe tools\\dotnet_probe.py il -TypeFilter "MyECIO\\.AcpiCtrl"
  runtime\\python.exe tools\\dotnet_probe.py wmi -Mode scan
"""
import locale
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
SOURCE = os.path.join(TOOLS, 'dotnet_probe.cs')
OUT_DIR = os.path.join(TOOLS, 'out')
EXE = os.path.join(OUT_DIR, 'dotnet_probe.exe')
CSC_CANDIDATES = (
    os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Microsoft.NET',
                 'Framework64', 'v4.0.30319', 'csc.exe'),
    os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Microsoft.NET',
                 'Framework', 'v4.0.30319', 'csc.exe'),
)


def find_csc():
    for path in CSC_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError('找不到 .NET Framework 4 的 csc.exe；Windows 10/11 默认自带。')


def ensure_helper(force=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    if not force and os.path.exists(EXE):
        if os.path.getmtime(EXE) >= os.path.getmtime(SOURCE):
            return EXE
    csc = find_csc()
    cmd = [csc, '/nologo', '/optimize+', '/target:exe', '/out:' + EXE,
           '/r:System.Core.dll', '/r:System.Management.dll',
           '/r:System.Web.Extensions.dll', SOURCE]
    res = subprocess.run(cmd, cwd=TOOLS, capture_output=True,
                         creationflags=0x08000000, timeout=180)
    if res.returncode != 0 or not os.path.exists(EXE):
        encoding = locale.getpreferredencoding(False) or 'utf-8'
        text = (res.stdout or b'').decode(encoding, 'replace') + \
               (res.stderr or b'').decode(encoding, 'replace')
        raise RuntimeError('编译 dotnet_probe.cs 失败：\n%s' % text[-2000:])
    return EXE


def run(args, timeout=300):
    exe = ensure_helper()
    res = subprocess.run([exe] + list(args), capture_output=True,
                         creationflags=0x08000000, timeout=timeout)
    text = (res.stdout or b'').decode('utf-8', 'replace') + \
           (res.stderr or b'').decode('utf-8', 'replace')
    if res.returncode != 0:
        raise RuntimeError(text.strip() or ('dotnet_probe 退出码 %d' % res.returncode))
    return text


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if argv else 2
    try:
        sys.stdout.write(run(argv))
    except Exception as exc:                              # noqa: BLE001
        print('[失败] %s' % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
