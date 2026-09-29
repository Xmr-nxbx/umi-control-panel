@echo off
setlocal
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\ec_watch.py" 240 all
) else (
  "runtime\python.exe" "tools\ec_watch.py" 240 all
)
echo.
echo 结果在 tools\out\ec-watch-all.txt —— 把这个文件给我，我据此判断 Creator Center 各功能落在哪个寄存器
pause
