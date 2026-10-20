# -*- coding: utf-8 -*-
"""以管理员权限重新运行一个脚本（ctypes 调 ShellExecuteW，不经过 PowerShell）。

用法：
  runtime\\python.exe tools\\elevate.py scripts\\启用造物者档控制.bat
  runtime\\python.exe tools\\elevate.py --dry-run 某个脚本.bat

只负责发起 UAC 提权；被提权的脚本自己会再次检查 net session。
"""
import ctypes
import os
import subprocess
import sys

SW_SHOWNORMAL = 1


def build_target(path, extra):
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.bat', '.cmd'):
        comspec = os.environ.get('COMSPEC') or os.path.join(
            os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'cmd.exe')
        params = '/c ""%s""' % path
        if extra:
            params += ' ' + subprocess.list2cmdline(extra)
        return comspec, params, os.path.dirname(path)
    return path, (subprocess.list2cmdline(extra) if extra else None), os.path.dirname(path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = '--dry-run' in argv
    if dry_run:
        argv.remove('--dry-run')
    if not argv:
        print('用法：tools\\elevate.py [--dry-run] 脚本路径 [参数...]')
        return 2
    target = argv[0]
    if not os.path.exists(target):
        print('找不到要提权的文件：%s' % target)
        return 1
    file, params, cwd = build_target(target, argv[1:])
    if dry_run:
        print('将提权运行：%s %s' % (file, params or ''))
        print('工作目录：%s' % cwd)
        return 0
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_int]
    result = shell32.ShellExecuteW(None, 'runas', file, params, cwd, SW_SHOWNORMAL)
    code = int(result or 0)
    if code <= 32:
        print('提权失败（ShellExecuteW 返回 %d）。可能是 UAC 被取消或安全软件拦截。' % code)
        return 1
    print('已发起提权，请在 UAC 窗口点「是」。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
