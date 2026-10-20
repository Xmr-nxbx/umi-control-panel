@echo off
setlocal
rem 优雅停止 → 守护模式拉起 → 等端口 → 核对三通道与写能力。
rem 面板一停 OEM 服务会把功耗墙压低，所以停完必须立刻拉起；这个脚本把这一步自动化。
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\restart_panel.py"
) else (
  "runtime\python.exe" "tools\restart_panel.py"
)
pause
