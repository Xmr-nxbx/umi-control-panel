@echo off
setlocal
rem 从本机安装的 Creator Center 里导出 EC 寄存器表到 data\ec_map.local.json。
rem 只读元数据，不加载执行 OEM 代码、不碰任何设备。这份表是 OEM 私有定义，
rem 按约定只留在本机 data\ 目录（已 gitignore），不进仓库、不再分发。
cd /d "%~dp0.."
if not exist "data" mkdir "data"
if exist "runtime\UmiPanel.exe" (
  "runtime\UmiPanel.exe" "tools\gen_ec_map.py"
) else (
  "runtime\python.exe" "tools\gen_ec_map.py"
)
echo.
echo 生成结果：data\ec_map.local.json
pause
