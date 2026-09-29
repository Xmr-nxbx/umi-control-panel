@echo off
setlocal
rem 四档逐一对比跑分（约 2 分钟）：面板在跑就交给面板执行，否则本机独立跑。
rem 负载是开源工具的真实工作量：zstd 压缩 + CoreMark + 短任务延迟。结束后自动还原电源设置。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --bench compare > "tools\out\bench-compare.txt" 2>&1
) else (
  "runtime\python.exe" "main.py" --bench compare > "tools\out\bench-compare.txt" 2>&1
)
type "tools\out\bench-compare.txt"
pause
