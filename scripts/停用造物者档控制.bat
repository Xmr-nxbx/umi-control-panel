@echo off
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
