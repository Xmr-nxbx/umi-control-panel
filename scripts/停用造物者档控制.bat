@echo off
setlocal
cd /d "%~dp0.."
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
