@echo off
rem 只删本软件自己写入的 Run 值，不动其它软件的自启项
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v UmiControlPanel /f
if errorlevel 1 (
  echo 没有发现 UmiControlPanel 自启项（可能已经卸载）
) else (
  echo 已卸载登录自启
)
pause
