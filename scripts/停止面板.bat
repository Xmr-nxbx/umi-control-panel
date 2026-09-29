@echo off
rem 优雅停止：还原被改过的电源设置后退出（不是直接杀进程）
cd /d "%~dp0.."
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
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
echo 面板已停止。
pause
