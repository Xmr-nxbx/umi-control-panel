@echo off
rem 登录自启（HKCU Run，不需要管理员）。启动后不自动弹浏览器。
cd /d "%~dp0.."
set "EXE=%CD%\runtime\UmiPanel.exe"
if not exist "%EXE%" set "EXE=%CD%\runtime\python.exe"
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v UmiControlPanel /t REG_SZ /d "\"%EXE%\" \"%CD%\main.py\" --no-browser" /f
if errorlevel 1 (
  echo 写入失败：可能被安全软件或注册表 ACL 拦截，请改用 PowerShell 手动添加
  exit /b 1
)
echo 已安装登录自启：%EXE%
echo 卸载请运行 卸载开机自启.bat
pause
