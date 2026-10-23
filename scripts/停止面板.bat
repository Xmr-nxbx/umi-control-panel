@echo off
rem 优雅停止：还原被改过的电源设置后退出（不是直接杀进程）
cd /d "%~dp0.." || exit /b 1
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
"%EXE%" "tools\stop_panel.py"
if errorlevel 1 (
  echo 无法确认面板已停止，已取消后续操作。请查看上方错误后重试。
  pause
  exit /b 1
)
echo 面板已停止。
pause
