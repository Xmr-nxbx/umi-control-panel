@echo off
setlocal DisableDelayedExpansion
rem 兼容入口：由桌面控制器安装隐藏登录自启，不直接注册控制台进程。
cd /d "%~dp0.." || exit /b 1
set "EXE=%CD%\runtime\UmiPanel.exe"
if not exist "%EXE%" set "EXE=%CD%\runtime\python.exe"
if not exist "%EXE%" (
  echo 缺少运行时。请先手动运行 scripts\准备运行时.bat，再重试。
  pause
  exit /b 1
)
"%EXE%" "%CD%\desktop.py" --autostart on
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo 安装自启失败，请查看上方错误信息。
pause
exit /b %RESULT%
