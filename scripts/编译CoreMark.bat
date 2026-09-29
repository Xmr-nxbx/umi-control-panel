@echo off
setlocal
rem 从 EEMBC 官方仓库取 CoreMark 源码，用本机编译器编出 tools\bin\coremark.exe。
rem 不用网上的现成 exe：跑分工具来路不明，分数就没有意义。
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
if not exist "tools\out" mkdir "tools\out"
"runtime\python.exe" "tools\build_coremark.py" > "tools\out\build-coremark.txt" 2>&1
type "tools\out\build-coremark.txt"
if exist "tools\bin\coremark.exe" (
  echo.
  echo 编译成功：tools\bin\coremark.exe
) else (
  echo.
  echo 没编出来。多半是没有 C 编译器——装一个便携工具链就行，例如：
  echo   winget install --id BrechtSanders.WinLibs.POSIX.UCRT
  echo 或者把 gcc.exe 所在目录加进 PATH，再重跑这个脚本。
)
pause
