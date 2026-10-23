#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""生成兼容/调试 bat（GBK + CRLF）及桌面入口 vbs（UTF-16 LE BOM + CRLF）。

日常启动只用根目录 Umi Control Panel.vbs；scripts 下保留旧路径供高级操作兼容，
不搬动、不自动执行诊断/硬件工具。调试命令行只打开根目录的 cmd。
中文 Windows 的 cmd 按代码页 936 解析批处理：UTF-8 的中文注释会被截断成乱码命令，
表现为「双击就闪退」。IDE 的写文件工具默认 UTF-8，所以入口一律由本脚本产出，
以后改入口也是改这个脚本再重新生成。

用法：runtime\python.exe scripts\make_bats.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'scripts')

BATS = {}

# 必须确认命名实例已退出；无法核实就终止，不能继续实验。
STOP_PANEL = r""""%EXE%" "tools\stop_panel.py"
if errorlevel 1 (
  echo 无法确认面板已停止，已取消后续操作。请查看上方错误后重试。
  pause
  exit /b 1
)
"""

BATS['准备运行时.bat'] = r"""@echo off
setlocal
rem 准备便携 Python 3.12。纯 cmd，引导只用系统自带 curl/tar，不调用 PowerShell。
rem 优先复用本机已有的 Python；一个都没有时才下载官方 embeddable 包。
cd /d "%~dp0.." || exit /b 1
if exist "runtime\python.exe" goto run_runtime
if not defined UMI_PYTHON_SRC goto try_cpython
if exist "%UMI_PYTHON_SRC%\python.exe" goto copy_src
:try_cpython
if exist "C:\Python312\python.exe" goto copy_cpython
where py >nul 2>&1
if errorlevel 1 goto try_python
echo 用 py 启动器准备运行时...
py -3 "tools\setup_runtime.py"
if errorlevel 1 goto bootstrap
goto done
:try_python
where python >nul 2>&1
if errorlevel 1 goto bootstrap
echo 用 PATH 里的 Python 准备运行时...
python "tools\setup_runtime.py"
if errorlevel 1 goto bootstrap
goto done
:run_runtime
"runtime\python.exe" "tools\setup_runtime.py"
if errorlevel 1 goto try_cpython
goto done
:copy_src
echo 从 %UMI_PYTHON_SRC% 复制运行时...
"%UMI_PYTHON_SRC%\python.exe" "tools\setup_runtime.py" --source "%UMI_PYTHON_SRC%"
goto done
:copy_cpython
echo 从 C:\Python312 复制运行时...
"C:\Python312\python.exe" "tools\setup_runtime.py" --source "C:\Python312"
goto done
:bootstrap
if not exist "runtime" mkdir "runtime"
set "ZIP=%TEMP%\umi-python-embed.zip"
echo 没有找到可用的 Python，正在用 curl 下载官方 embeddable 包...
curl.exe -L --fail --retry 3 --connect-timeout 15 -o "%ZIP%" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
if errorlevel 1 (
  echo 下载失败。请手动下载并解压到 runtime\ 后重跑本脚本：
  echo   https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
  pause
  exit /b 1
)
"%SystemRoot%\System32\tar.exe" -xf "%ZIP%" -C "runtime"
if errorlevel 1 (
  echo 解压失败：系统缺少 tar.exe。
  pause
  exit /b 1
)
del "%ZIP%" >nul 2>&1
"runtime\python.exe" "tools\setup_runtime.py"
:done
pause
"""

BATS['重启并自检.bat'] = r"""@echo off
setlocal
rem 优雅停止 → 守护模式拉起 → 等端口 → 核对三通道与写能力。
rem 面板一停 OEM 服务会把功耗墙压低，所以停完必须立刻拉起；这个脚本把这一步自动化。
cd /d "%~dp0.." || exit /b 1
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\restart_panel.py"
) else (
  "runtime\python.exe" "tools\restart_panel.py"
)
pause
"""

BATS['抓CreatorCenter配置.bat'] = r"""@echo off
setlocal
rem 让 Creator Center 当一次示范：你在它里面点，面板把原始 MQTT 报文抄到 logs\。
rem 不停面板、不新建 broker 连接、不发任何命令。
cd /d "%~dp0.." || exit /b 1
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\creator_capture.py"
) else (
  "runtime\python.exe" "tools\creator_capture.py"
)
pause
"""

BATS['调试命令行.bat'] = r"""@echo off
setlocal DisableDelayedExpansion
rem 仅在项目根目录打开命令行，不自动运行任何诊断/硬件操作。
cd /d "%~dp0.." || exit /b 1
echo 调试命令行。日常启动请双击根目录 Umi Control Panel.vbs。
echo 此入口不自动执行诊断、服务或硬件操作。
"%SystemRoot%\System32\cmd.exe" /d /v:off /k
exit /b %ERRORLEVEL%
"""

BATS['停止面板.bat'] = r"""@echo off
rem 优雅停止：还原被改过的电源设置后退出（不是直接杀进程）
cd /d "%~dp0.." || exit /b 1
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
@@STOP@@
echo 面板已停止。
pause
"""

BATS['面板状态.bat'] = r"""@echo off
rem 自检：采一次遥测 + 调度决策，把结果打成 JSON（不改动电源设置）
cd /d "%~dp0.." || exit /b 1
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
setlocal DisableDelayedExpansion
rem 兼容入口：由桌面控制器安装隐藏登录自启，不直接注册控制台进程。
cd /d "%~dp0.." || exit /b 1
set "EXE=%CD%\runtime\UmiPanel.exe"
if not exist "%EXE%" set "EXE=%CD%\runtime\python.exe"
if not exist "%EXE%" (
  echo 缺少运行时。请先手动运行 scripts\准备运行时.bat，再重试。
  pause
  exit /b 1
)
"%EXE%" "%CD%\desktop.py" --autostart on
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo 安装自启失败，请查看上方错误信息。
pause
exit /b %RESULT%
"""

BATS['卸载开机自启.bat'] = r"""@echo off
setlocal DisableDelayedExpansion
rem 兼容入口：由桌面控制器只删除本软件自己的登录自启项。
cd /d "%~dp0.." || exit /b 1
set "EXE=%CD%\runtime\UmiPanel.exe"
if not exist "%EXE%" set "EXE=%CD%\runtime\python.exe"
if not exist "%EXE%" (
  echo 缺少运行时。请先手动运行 scripts\准备运行时.bat，再重试。
  pause
  exit /b 1
)
"%EXE%" "%CD%\desktop.py" --autostart off
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo 卸载自启失败，请查看上方错误信息。
pause
exit /b %RESULT%
"""

BATS['生成EC寄存器表.bat'] = r"""@echo off
setlocal
rem 从本机安装的 Creator Center 里导出 EC 寄存器表到 data\ec_map.local.json。
rem 只读元数据，不加载执行 OEM 代码、不碰任何设备。这份表是 OEM 私有定义，
rem 按约定只留在本机 data\ 目录（已 gitignore），不进仓库、不再分发。
cd /d "%~dp0.." || exit /b 1
if not exist "data" mkdir "data"
rem 仅此手动工具转发显式路径参数；GUI 仍只调用固定脚本。
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\gen_ec_map.py" %*
) else (
  "runtime\python.exe" "tools\gen_ec_map.py" %*
)
echo.
echo 生成结果：data\ec_map.local.json
pause
"""

BATS['EC只读自检.bat'] = r"""@echo off
setlocal
rem EC 只读自检：普通权限即可（\\.\ACPIDriver 对普通用户开放读写）。
rem 只发确认过的 ECREAD，绝不穷举、不写任何寄存器。
cd /d "%~dp0.." || exit /b 1
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
cd /d "%~dp0.." || exit /b 1
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
cd /d "%~dp0.." || exit /b 1
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
cd /d "%~dp0.." || exit /b 1
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
cd /d "%~dp0.." || exit /b 1
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限，正在提权重启本脚本...
  if exist "runtime\UmiPanel.exe" (
    "runtime\UmiPanel.exe" "tools\elevate.py" "%~f0"
  ) else if exist "runtime\python.exe" (
    "runtime\python.exe" "tools\elevate.py" "%~f0"
  ) else (
    echo 缺便携运行时，请先双击 scripts\准备运行时.bat
    pause
  )
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
cd /d "%~dp0.." || exit /b 1
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限，正在提权重启本脚本...
  if exist "runtime\UmiPanel.exe" (
    "runtime\UmiPanel.exe" "tools\elevate.py" "%~f0"
  ) else if exist "runtime\python.exe" (
    "runtime\python.exe" "tools\elevate.py" "%~f0"
  ) else (
    echo 缺便携运行时，请先双击 scripts\准备运行时.bat
    pause
  )
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
cd /d "%~dp0.." || exit /b 1
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
cd /d "%~dp0.." || exit /b 1
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
cd /d "%~dp0.." || exit /b 1
if not exist "tools\out" mkdir "tools\out"
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务（观察期间不自动改任何硬件）…
@@STOP@@
echo.
echo  接下来 240 秒，请**一次只动一个功能，动完等 15 秒再动下一个**，
echo  并在纸上或手机里记下顺序，例如：
echo     1) 实体键：按到全亮 → 再按到不亮（本机 Creator Center 没有办公/均衡/狂暴档）
echo     2) Creator Center 里的造物者开关：开一次、关一次
echo     3) 电池：充电阈值切到 80%%
echo     4) 键盘背光：亮度调一格 / 换个灯效
echo     5) 灯条：开关一次
echo     6) Win 键锁定 / 触摸板开关：各切一次
echo     7) 独显直连：如果能点就点一次（会要求重启，可以先取消）
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


BATS['观察OEM通道.bat'] = r"""@echo off
setlocal
rem 听 GCUBridge 自己说话：Creator Center 每个开关最终都发一条 MQTT 命令，
rem 报文体里就写着 Action 名和参数 —— 比拿 EC 全表差分去猜寄存器可靠得多。
rem 两个观察器一起跑：MQTT 只订阅不发送，EC 只读不写。都必须先停面板，
rem 否则面板和观察器抢同一个 broker 身份，会被 GCUBridge 踢线。
cd /d "%~dp0.." || exit /b 1
if not exist "tools\out" mkdir "tools\out"
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务（观察期间不自动改任何硬件）…
@@STOP@@
echo.
echo  接下来 240 秒，请打开 Creator Center，**一次只点一个功能，点完等 10 秒再点下一个**，
echo  并把顺序记下来。观察器只会发一条 GETSTATUS 问当前状态，不改任何设置。
echo  顺序按「还没弄清楚的」排（已经确认的排在后面，顺便复核）：
echo     1) Win 键锁定：关一次，等 10 秒，再开一次
echo     2) 电池那三档（平衡 / 健康 / 长效）：一档一档切，每档等 15 秒，最后切回原来那档
echo     3) 键盘背光：亮度调一格，再换一个灯效或速度
echo     4) 灯条（顶部那条）：关一次、开一次
echo     5) 触摸板：拨一下触摸板上那个实体开关，再拨回来
echo     6) 屏幕提示 OSD、风扇加速 FanBoost：各切一次
echo     7) 独显直连：能点就点一次（要求重启可以先取消）
echo     8) 实体「造物者模式」键：按到全亮，等 10 秒，再按到不亮
echo  两份报告都带时间戳，按你记的顺序就能一一对回去。
echo  注意：别再双击另一个「观察」脚本，两个观察器会抢同一个 broker 身份。
echo.
start "OEM-MQTT" /min "%EXE%" "tools\mqtt_watch.py" 240 --ask
echo  另一个窗口在听 MQTT；本窗口同时做 EC 全表只读观察（2 秒一轮，一发都不写）…
echo.
"%EXE%" "tools\ec_watch.py" 240 all
echo.
echo 等 MQTT 观察器收尾…
timeout /t 8 /nobreak >nul
echo 正在重新启动面板…
start "UmiPanel" /min "%EXE%" "main.py" --supervise --no-browser
echo 结果在 tools\out\mqtt-watch.txt 和 tools\out\ec-watch-all.txt ——
echo 把这两个文件和你记的点击顺序一起给我，我据此确认每个功能的真实命令/寄存器。
pause
"""


BATS['造物者三态实测.bat'] = r"""@echo off
setlocal
rem 实测「造物者模式」按键三态（自动/强冷/自定义）在满载下到底差多少。
rem 会写风扇模式字节（写完原样还原，带 97°C 保险），所以要先停面板：
rem 两边的自动跟随会互相抢方向盘，测出来的数不作数。全程约 6 分钟，风扇会很吵。
cd /d "%~dp0.." || exit /b 1
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务…
@@STOP@@
echo 开始三态实测（满载 + 高转，请别合上盖子）…
"%EXE%" "tools\ec_mode_bench.py" > "tools\out\ec-mode-bench.txt" 2>&1
type "tools\out\ec-mode-bench.txt"
echo.
echo 测完还原，正在重新启动面板…
start "UmiPanel" /min "%EXE%" "main.py" --supervise --no-browser
echo 结果已存到 tools\out\ec-mode-bench.txt
pause
"""


# WSH 按 BOM 识别中文；不依赖系统 ANSI 代码页。
VBS = {
    'Umi Control Panel.vbs': r'''Option Explicit
' 由 scripts/make_bats.py 生成；日常入口不使用 pythonw 或控制台包装。
Dim background, shell, fso, root, exe, entry, entryPath, command
Dim setupRelative, setupPath, cmdExe, answer, result
background = False
' 拒绝未知或多余参数，不转发用户输入。
If WScript.Arguments.Count > 1 Then WScript.Quit 2
If WScript.Arguments.Count = 1 Then
    If LCase(WScript.Arguments(0)) <> "/background" Then WScript.Quit 2
    background = True
End If

On Error Resume Next
Set shell = CreateObject("WScript.Shell")
CheckError "无法使用 Windows 脚本服务，请检查系统是否允许运行 VBS 脚本。"
Set fso = CreateObject("Scripting.FileSystemObject")
CheckError "无法访问文件，请检查文件夹权限。"
root = fso.GetParentFolderName(WScript.ScriptFullName)
CheckError "无法找到面板文件夹，请将软件完整解压后再双击此入口。"
shell.CurrentDirectory = root
CheckError "无法进入面板文件夹，请检查路径和访问权限。"
exe = FindRuntime()
If Len(exe) = 0 Then
    ' 登录自启不弹初始化提示，也不下载。
    If background Then WScript.Quit 1
    setupRelative = "scripts\准备运行时.bat"
    setupPath = fso.BuildPath(root, setupRelative)
    If Not fso.FileExists(setupPath) Then Fail "缺少初始化脚本，请完整解压软件后重试：" & vbCrLf & setupPath
    CheckError "无法找到初始化脚本，请检查文件夹权限。"
    answer = MsgBox("首次使用需要准备 Python 运行环境。" & vbCrLf & vbCrLf & _
        "是否现在打开可见的命令窗口，把运行时安装到项目 runtime 文件夹？" & vbCrLf & setupPath & _
        vbCrLf & vbCrLf & "将优先使用本机 Python；没有可用版本时可能从 python.org 下载，不请求管理员权限。" & _
        vbCrLf & "请等待窗口提示完成，按任意键关闭后会自动打开面板。" & _
        vbCrLf & "选择“否”则不作任何更改，稍后可再次双击此入口。", _
        vbYesNo + vbQuestion + vbDefaultButton2, "Umi Control Panel - 首次使用")
    CheckError "无法显示初始化提示，请手动运行 scripts\准备运行时.bat。"
    If answer <> vbYes Then WScript.Quit 1
    cmdExe = fso.BuildPath(fso.GetSpecialFolder(0), "System32\cmd.exe")
    CheckError "无法找到 Windows 命令窗口，请检查系统文件。"
    ' CMD 只接收固定相对文件名，不拼入根目录或用户参数。
    result = shell.Run(Quote(cmdExe) & " /d /v:off /c " & Quote(Quote(setupRelative)), 1, True)
    CheckError "无法打开初始化窗口，请手动运行 scripts\准备运行时.bat 并查看提示。"
    exe = FindRuntime()
    If result <> 0 Or Len(exe) = 0 Then
        Fail "初始化未完成，暂时无法打开面板。退出码：" & result & vbCrLf & _
            "请手动双击以下脚本，查看窗口中的错误提示后重试：" & vbCrLf & setupPath
    End If
End If

If background Then
    entry = "main.py"
Else
    entry = "desktop.py"
End If
entryPath = fso.BuildPath(root, entry)
CheckError "无法找到启动脚本，请检查软件是否完整解压。"
If Not fso.FileExists(entryPath) Then Fail "缺少启动脚本，请完整解压软件后重试：" & vbCrLf & entryPath
CheckError "无法检查启动脚本，请检查文件夹权限。"
' Run 会展开路径里的 %变量%；CurrentDirectory 已定位到项目，启动只使用固定相对路径。
command = Quote(exe) & " " & Quote(entry)
If background Then command = command & " --supervise --no-browser"
' 隐藏控制台；进程创建后的界面错误由 desktop.py 处理。
result = shell.Run(command, 0, False)
CheckError "无法启动面板，请检查运行环境和文件夹权限，必要时重新运行 scripts\准备运行时.bat。"
WScript.Quit 0

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Function FindRuntime()
    On Error Resume Next
    Dim candidate, exists
    candidate = fso.BuildPath(root, "runtime\UmiPanel.exe")
    exists = fso.FileExists(candidate)
    CheckError "无法检查运行环境 runtime\UmiPanel.exe，请检查文件夹权限。"
    FindRuntime = ""
    If exists Then
        FindRuntime = "runtime\UmiPanel.exe"
        Exit Function
    End If
    candidate = fso.BuildPath(root, "runtime\python.exe")
    exists = fso.FileExists(candidate)
    CheckError "无法检查运行环境 runtime\python.exe，请检查文件夹权限。"
    If exists Then FindRuntime = "runtime\python.exe"
End Function

Sub CheckError(context)
    Dim detail
    If Err.Number <> 0 Then
        detail = Err.Description & " (0x" & Hex(Err.Number) & ")"
        Err.Clear
        Fail context & vbCrLf & detail
    End If
End Sub

Sub Fail(message)
    On Error Resume Next
    If Not background Then MsgBox message, vbOKOnly + vbCritical, "Umi Control Panel - 启动失败"
    WScript.Quit 1
End Sub
''',
}


def render_bat(body):
    body = body.replace('@@STOP@@', STOP_PANEL.rstrip('\n'))
    if '@@' in body:
        raise ValueError('bat 里还有没替换的占位符')
    return body.replace('\r\n', '\n').replace('\n', '\r\n').encode('gbk')


def render_vbs(body):
    return b'\xff\xfe' + body.replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-16-le')


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, body in BATS.items():
        path = os.path.join(OUT, name)
        with open(path, 'wb') as f:
            f.write(render_bat(body))
        print('已生成兼容/调试脚本 %s (%d 字节)' % (name, os.path.getsize(path)))
    for name, body in VBS.items():
        with open(os.path.join(ROOT, name), 'wb') as f:
            f.write(render_vbs(body))
        print('已生成桌面脚本 %s (UTF-16 LE BOM / CRLF；未执行)' % name)
    print('共 %d 个 bat，编码 GBK / 换行 CRLF；日常请用根目录 Umi Control Panel.vbs' % len(BATS))


if __name__ == '__main__':
    main()
