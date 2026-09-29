@echo off
setlocal
rem 实测「造物者模式」按键三态（自动/强冷/自定义）在满载下到底差多少。
rem 会写风扇模式字节（写完原样还原，带 97°C 保险），所以要先停面板：
rem 两边的自动跟随会互相抢方向盘，测出来的数不作数。全程约 6 分钟，风扇会很吵。
cd /d "%~dp0.."
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务…
set /a STOPTRIES=0
:stop_panel
"%EXE%" "main.py" --stop >nul 2>&1
timeout /t 2 /nobreak >nul
set /a STOPTRIES+=1
netstat -ano | findstr /c:":874" | findstr /c:"LISTENING" >nul
if errorlevel 1 goto stop_panel_done
if %STOPTRIES% lss 5 goto stop_panel
echo  警告：874x 端口上还有实例在听，可能另一个观察脚本正在跑。先关掉它再继续，
echo        否则两边抢同一个 broker 身份，MQTT 那份报告会半路断掉。
:stop_panel_done
echo 开始三态实测（满载 + 高转，请别合上盖子）…
"%EXE%" "tools\ec_mode_bench.py" > "tools\out\ec-mode-bench.txt" 2>&1
type "tools\out\ec-mode-bench.txt"
echo.
echo 测完还原，正在重新启动面板…
start "UmiPanel" /min "%EXE%" "main.py" --supervise --no-browser
echo 结果已存到 tools\out\ec-mode-bench.txt
pause
