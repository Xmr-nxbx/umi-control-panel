@echo off
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
