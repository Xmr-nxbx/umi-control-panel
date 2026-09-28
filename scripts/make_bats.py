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
rem 启动 Umi Control Panel（守护模式：进程崩了会自动拉起，面板里点停止才真的退出）。
rem 用最小化窗口跑，不用 pythonw——它会被安全软件静默查杀。
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  start "UmiPanel" /min "runtime\UmiPanel.exe" "main.py" --supervise
) else (
  start "UmiPanel" /min "runtime\python.exe" "main.py" --supervise
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
rem 登录自启（HKCU Run，不需要管理员），并启用守护模式自动重启。启动后不自动弹浏览器。
cd /d "%~dp0.."
set "EXE=%CD%\runtime\UmiPanel.exe"
if not exist "%EXE%" set "EXE=%CD%\runtime\python.exe"
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v UmiControlPanel /t REG_SZ /d "\"%EXE%\" \"%CD%\main.py\" --supervise --no-browser" /f
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

BATS['生成EC寄存器表.bat'] = r"""@echo off
setlocal
rem 从本机安装的 Creator Center 里导出 EC 寄存器表到 data\ec_map.local.json。
rem 只读元数据，不加载执行 OEM 代码、不碰任何设备。这份表是 OEM 私有定义，
rem 按约定只留在本机 data\ 目录（已 gitignore），不进仓库、不再分发。
cd /d "%~dp0.."
if not exist "data" mkdir "data"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\gen_ec_map.py"
) else (
  "runtime\python.exe" "tools\gen_ec_map.py"
)
echo.
echo 生成结果：data\ec_map.local.json
pause
"""

BATS['EC只读自检.bat'] = r"""@echo off
setlocal
rem EC 只读自检：普通权限即可（\\.\ACPIDriver 对普通用户开放读写）。
rem 只发确认过的 ECREAD，绝不穷举、不写任何寄存器。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if not exist "data\ec_map.local.json" (
  echo 还没有本机寄存器表，先运行 生成EC寄存器表.bat
  pause
  exit /b 1
)
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --ec-test > "tools\out\ec-selftest.txt" 2>&1
) else (
  "runtime\python.exe" "main.py" --ec-test > "tools\out\ec-selftest.txt" 2>&1
)
type "tools\out\ec-selftest.txt"
echo.
echo 结果同时写入 tools\out\ec-selftest.txt
pause
"""

BATS['跑分对比.bat'] = r"""@echo off
setlocal
rem 四档逐一对比跑分（约 2 分钟）：面板在跑就交给面板执行，否则本机独立跑。
rem 纯内置负载，不下载任何第三方跑分软件。结束后自动还原电源设置。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --bench compare > "tools\out\bench-compare.txt" 2>&1
) else (
  "runtime\python.exe" "main.py" --bench compare > "tools\out\bench-compare.txt" 2>&1
)
type "tools\out\bench-compare.txt"
pause
"""


BATS['启用造物者档控制.bat'] = r"""@echo off
setlocal
rem 恢复 OEM 的 GCUBridge 服务（它是硬件档位 MQTT 命令链路的宿主）。
rem 之前 OpenRevo 的 takeover 把它停用了（Start=4），所以实体按键链路的后台是断的。
rem 本脚本只做两件事：设为自动启动 + 启动服务；不装任何东西，不动 Creator Center 的界面。
cd /d "%~dp0.."
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限，正在提权重启本脚本...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
sc config GCUBridge start= auto
if errorlevel 1 echo 设置自启失败（服务可能不存在）
net start GCUBridge
if errorlevel 1 echo 启动服务失败
echo.
echo 若面板仍显示兜底通道不可用，请确认 data\mqtt_identity.json 存在（抄 mqtt_identity.example.json）
sc query GCUBridge | find /i "RUNNING" >nul && echo GCUBridge 已在运行，刷新面板即可
pause
"""

BATS['停用造物者档控制.bat'] = r"""@echo off
setlocal
cd /d "%~dp0.."
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限，正在提权重启本脚本...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
net stop GCUBridge
sc config GCUBridge start= disabled
echo 已停用（回到 OpenRevo takeover 之后的状态；EC 直连不受影响）
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
