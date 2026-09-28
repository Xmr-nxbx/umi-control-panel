@echo off
rem 优雅停止：还原被改过的电源设置后退出（不是直接杀进程）
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --stop
) else (
  "runtime\python.exe" "main.py" --stop
)
pause
