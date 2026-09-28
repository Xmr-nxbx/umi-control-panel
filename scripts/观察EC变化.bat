@echo off
setlocal
rem 只读监听 EC 语义寄存器 90 秒：期间按一次实体「造物者模式」键，
rem 或在 Creator Center 里切一次模式，屏幕上出现变化的那个寄存器就是档位落点。
rem 不写任何东西，普通权限即可。结果同时存到 tools\out\ec-watch.txt。
cd /d "%~dp0.."
if not exist "tools\out" mkdir "tools\out"
if not exist "data\ec_map.local.json" (
  echo 还没有本机寄存器表，先运行 生成EC寄存器表.bat
  pause
  exit /b 1
)
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\ec_watch.py" 90
) else (
  "runtime\python.exe" "tools\ec_watch.py" 90
)
pause
