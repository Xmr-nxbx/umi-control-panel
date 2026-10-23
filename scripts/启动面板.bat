@echo off
setlocal DisableDelayedExpansion
rem 旧路径兼容入口：bat 自身仍可能闪过 CMD；日常请双击根目录 Umi Control Panel.vbs。
rem 不转发参数；桌面入口由 WScript 隐藏启动，不使用 pythonw。
cd /d "%~dp0.." || exit /b 1
"%SystemRoot%\System32\wscript.exe" "%CD%\Umi Control Panel.vbs"
exit /b %ERRORLEVEL%
