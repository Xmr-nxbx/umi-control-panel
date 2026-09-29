@echo off
setlocal
rem 听 GCUBridge 自己说话：Creator Center 每个开关最终都发一条 MQTT 命令，
rem 报文体里就写着 Action 名和参数 —— 比拿 EC 全表差分去猜寄存器可靠得多。
rem 两个观察器一起跑：MQTT 只订阅不发送，EC 只读不写。都必须先停面板，
rem 否则面板和观察器抢同一个 broker 身份，会被 GCUBridge 踢线。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务（观察期间不自动改任何硬件）…
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
