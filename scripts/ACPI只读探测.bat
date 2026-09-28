@echo off
setlocal
cd /d "%~dp0.."
net session >nul 2>&1
if errorlevel 1 (
  echo 需要管理员权限（打开 \\.\ACPI 设备），正在提权重启本脚本...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
if not exist "tools\out" mkdir "tools\out"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --probe-acpi > "tools\out\acpi-namespace.txt" 2>&1
) else (
  "runtime\python.exe" "main.py" --probe-acpi > "tools\out\acpi-namespace.txt" 2>&1
)
echo 结果已写入 tools\out\acpi-namespace.txt
echo 请把该文件内容给我，用于确定 EC 读写方法的完整路径（本脚本只枚举，不读写 EC）
pause
