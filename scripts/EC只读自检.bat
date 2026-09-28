@echo off
setlocal
rem EC 只读自检：普通权限即可（\\.\ACPIDriver 对普通用户开放读写）。
rem 只发确认过的 ECREAD，绝不穷举、不写任何寄存器。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if not exist "data\ec_map.local.json" (
  echo 还没有本机寄存器表，先运行 生成EC寄存器表.bat
  pause
  exit /b 1
)
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "main.py" --ec-test > "tools\out\ec-selftest.txt" 2>&1
) else (
  "runtime\python.exe" "main.py" --ec-test > "tools\out\ec-selftest.txt" 2>&1
)
type "tools\out\ec-selftest.txt"
echo.
echo 结果同时写入 tools\out\ec-selftest.txt
pause
