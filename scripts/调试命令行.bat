@echo off
setlocal DisableDelayedExpansion
rem 仅在项目根目录打开命令行，不自动运行任何诊断/硬件操作。
cd /d "%~dp0.." || exit /b 1
echo 调试命令行。日常启动请双击根目录 Umi Control Panel.vbs。
echo 此入口不自动执行诊断、服务或硬件操作。
"%SystemRoot%\System32\cmd.exe" /d /v:off /k
exit /b %ERRORLEVEL%
