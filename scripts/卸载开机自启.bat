@echo off
setlocal DisableDelayedExpansion
rem 兼容入口：由桌面控制器只删除本软件自己的登录自启项。
cd /d "%~dp0.." || exit /b 1
set "EXE=%CD%\runtime\UmiPanel.exe"
if not exist "%EXE%" set "EXE=%CD%\runtime\python.exe"
if not exist "%EXE%" (
  echo 缺少运行时。请先手动运行 scripts\准备运行时.bat，再重试。
  pause
  exit /b 1
)
"%EXE%" "%CD%\desktop.py" --autostart off
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo 卸载自启失败，请查看上方错误信息。
pause
exit /b %RESULT%
