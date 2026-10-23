@echo off
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
