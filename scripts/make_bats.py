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
rem 负载是开源工具的真实工作量：zstd 压缩 + CoreMark + 短任务延迟。结束后自动还原电源设置。
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

BATS['下载跑分工具.bat'] = r"""@echo off
setlocal
rem 取开源跑分负载：zstd（facebook/zstd 官方 Release，BSD-3/GPL-2）放到 tools\bin。
rem 顺带生成固定种子的基准输入文件，保证每次跑的内容一模一样、档位之间才可比。
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
if not exist "tools\out" mkdir "tools\out"
"runtime\python.exe" "tools\fetch_bench_tools.py" > "tools\out\fetch-bench-tools.txt" 2>&1
type "tools\out\fetch-bench-tools.txt"
pause
"""

BATS['编译CoreMark.bat'] = r"""@echo off
setlocal
rem 从 EEMBC 官方仓库取 CoreMark 源码，用本机编译器编出 tools\bin\coremark.exe。
rem 不用网上的现成 exe：跑分工具来路不明，分数就没有意义。
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
if not exist "tools\out" mkdir "tools\out"
"runtime\python.exe" "tools\build_coremark.py" > "tools\out\build-coremark.txt" 2>&1
type "tools\out\build-coremark.txt"
if exist "tools\bin\coremark.exe" (
  echo.
  echo 编译成功：tools\bin\coremark.exe
) else (
  echo.
  echo 没编出来。多半是没有 C 编译器——装一个便携工具链就行，例如：
  echo   winget install --id BrechtSanders.WinLibs.POSIX.UCRT
  echo 或者把 gcc.exe 所在目录加进 PATH，再重跑这个脚本。
)
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


BATS['一键体检.bat'] = r"""@echo off
setlocal
rem 一次性检查：开机自启、便携运行时、面板服务、温度/频率/GPU 采集、EC 通道、寄存器表。
rem 只读，不改任何设置。结果同时存到 tools\out\health.txt。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --health
) else (
  "runtime\python.exe" "main.py" --health
)
pause
"""

BATS['观察EC变化.bat'] = r"""@echo off
setlocal
rem 只读监听 EC 语义寄存器 90 秒：期间按一次实体「造物者模式」键，
rem 或在 Creator Center 里切一次模式，屏幕上出现变化的那个寄存器就是档位落点。
rem 不写任何东西，普通权限即可。结果同时存到 tools\out\ec-watch.txt。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if not exist "data\ec_map.local.json" (
  echo 还没有本机寄存器表，先运行 生成EC寄存器表.bat
  pause
  exit /b 1
)
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\ec_watch.py" 90
) else (
  "runtime\python.exe" "tools\ec_watch.py" 90
)
pause
"""


BATS['观察EC全表.bat'] = r"""@echo off
setlocal
rem 破 Creator Center 各功能落在哪个 EC 寄存器上：全表只读观察 240 秒。
rem 必须先停面板——面板自己会写风扇字节，那些改动会混进报告里，分不清是谁干的。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务（观察期间不自动改任何硬件）…
"%EXE%" "main.py" --stop >nul 2>&1
timeout /t 3 /nobreak >nul
echo.
echo  接下来 240 秒，请打开 Creator Center，**一次只点一个功能，点完等 15 秒再点下一个**，
echo  并在纸上或手机里记下顺序，例如：
echo     1) 模式：办公 → 均衡 → 狂暴
echo     2) 电池：充电阈值切到 80%%
echo     3) 键盘背光：亮度调一格 / 换个灯效
echo     4) 灯条：开关一次
echo     5) Win 键锁定 / 触摸板开关：各切一次
echo     6) 独显直连：如果能点就点一次（会要求重启，可以先取消）
echo  报告里每条变化都带时间戳，按你的顺序就能一一对回去。
echo.
echo  现在开始观察（只读，一发都不写）…
"%EXE%" "tools\ec_watch.py" 240 all
echo.
echo 正在重新启动面板…
start "UmiPanel" /min "%EXE%" "main.py" --supervise --no-browser
echo 结果在 tools\out\ec-watch-all.txt —— 把这个文件和你记的点击顺序一起给我，
echo 我据此判断 Creator Center 各功能落在哪个寄存器（语义确认后才允许写）。
pause
"""


BATS['造物者三态实测.bat'] = r"""@echo off
setlocal
rem 实测「造物者模式」按键三态（自动/强冷/自定义）在满载下到底差多少。
rem 会写风扇模式字节（写完原样还原，带 97°C 保险），所以要先停面板：
rem 两边的自动跟随会互相抢方向盘，测出来的数不作数。全程约 6 分钟，风扇会很吵。
cd /d "%~dp0.."
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务…
"%EXE%" "main.py" --stop >nul 2>&1
timeout /t 3 /nobreak >nul
echo 开始三态实测（满载 + 高转，请别合上盖子）…
"%EXE%" "tools\ec_mode_bench.py" > "tools\out\ec-mode-bench.txt" 2>&1
type "tools\out\ec-mode-bench.txt"
echo.
echo 测完还原，正在重新启动面板…
start "UmiPanel" /min "%EXE%" "main.py" --supervise --no-browser
echo 结果已存到 tools\out\ec-mode-bench.txt
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
