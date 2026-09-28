#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""生成入口 bat（必须 GBK 编码 + CRLF 换行）。

中文 Windows 的 cmd 按代码页 936 解析批处理：UTF-8 的中文注释会被截断成乱码命令，
表现为「双击就闪退」。IDE 的写文件工具默认 UTF-8，所以 bat 一律由本脚本产出，
以后改 bat 也是改这个脚本再重新生成。

用法：runtime\python.exe scripts\make_bats.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'scripts')

BATS = {}

BATS['启动面板.bat'] = r"""@echo off
rem 启动 Umi Control Panel。用最小化窗口跑（不用 pythonw，它会被安全软件静默查杀）。
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  start "UmiPanel" /min "runtime\UmiPanel.exe" "main.py"
) else (
  start "UmiPanel" /min "runtime\python.exe" "main.py"
)
echo 正在启动，面板地址 http://127.0.0.1:8747/
timeout /t 3 >nul
"""

BATS['停止面板.bat'] = r"""@echo off
rem 优雅停止：还原被改过的电源设置后退出（不是直接杀进程）
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --stop
) else (
  "runtime\python.exe" "main.py" --stop
)
pause
"""

BATS['面板状态.bat'] = r"""@echo off
rem 自检：采一次遥测 + 调度决策，把结果打成 JSON（不改动电源设置）
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --one-shot
) else (
  "runtime\python.exe" "main.py" --one-shot
)
echo.
echo 完整日志见 data\umi-control-panel.log
pause
"""

BATS['安装开机自启.bat'] = r"""@echo off
rem 登录自启（HKCU Run，不需要管理员）。启动后不自动弹浏览器。
cd /d "%~dp0.."
set "EXE=%CD%\runtime\UmiPanel.exe"
if not exist "%EXE%" set "EXE=%CD%\runtime\python.exe"
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v UmiControlPanel /t REG_SZ /d "\"%EXE%\" \"%CD%\main.py\" --no-browser" /f
if errorlevel 1 (
  echo 写入失败：可能被安全软件或注册表 ACL 拦截，请改用 PowerShell 手动添加
  exit /b 1
)
echo 已安装登录自启：%EXE%
echo 卸载请运行 卸载开机自启.bat
pause
"""

BATS['卸载开机自启.bat'] = r"""@echo off
rem 只删本软件自己写入的 Run 值，不动其它软件的自启项
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v UmiControlPanel /f
if errorlevel 1 (
  echo 没有发现 UmiControlPanel 自启项（可能已经卸载）
) else (
  echo 已卸载登录自启
)
pause
"""

BATS['ACPI只读探测.bat'] = r"""@echo off
setlocal
cd /d "%~dp0.."
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限（打开 \\.\ACPI 设备），正在提权重启本脚本...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
if not exist "tools\out" mkdir "tools\out"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --probe-acpi > "tools\out\acpi-namespace.txt" 2>&1
) else (
  "runtime\python.exe" "main.py" --probe-acpi > "tools\out\acpi-namespace.txt" 2>&1
)
echo 结果已写入 tools\out\acpi-namespace.txt
echo 请把该文件内容给我，用于确定 EC 读写方法的完整路径（本脚本只枚举，不读写 EC）
pause
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, body in BATS.items():
        path = os.path.join(OUT, name)
        with open(path, 'w', encoding='gbk', newline='\r\n') as f:
            f.write(body)
        print('已生成 %s (%d 字节)' % (name, os.path.getsize(path)))
    print('共 %d 个，编码 GBK / 换行 CRLF' % len(BATS))


if __name__ == '__main__':
    main()
