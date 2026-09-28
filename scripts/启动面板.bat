@echo off
rem 启动 Umi Control Panel（守护模式：进程崩了会自动拉起，面板里点停止才真的退出）。
rem 用最小化窗口跑，不用 pythonw——它会被安全软件静默查杀。
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  start "UmiPanel" /min "runtime\UmiPanel.exe" "main.py" --supervise
) else (
  start "UmiPanel" /min "runtime\python.exe" "main.py" --supervise
)
echo 正在启动，面板地址 http://127.0.0.1:8747/
timeout /t 3 >nul
