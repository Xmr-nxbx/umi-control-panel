@echo off
rem 自检：采一次遥测 + 调度决策，把结果打成 JSON（不改动电源设置）
cd /d "%~dp0.."
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --one-shot
) else (
  "runtime\python.exe" "main.py" --one-shot
)
echo.
echo 完整日志见 data\umi-control-panel.log
pause
