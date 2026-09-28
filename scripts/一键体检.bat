@echo off
setlocal
rem 一次性检查：开机自启、便携运行时、面板服务、温度/频率/GPU 采集、EC 通道、寄存器表。
rem 只读，不改任何设置。结果同时存到 tools\out\health.txt。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --health
) else (
  "runtime\python.exe" "main.py" --health
)
pause
