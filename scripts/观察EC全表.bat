@echo off
setlocal
rem 破 Creator Center 各功能落在哪个 EC 寄存器上：全表只读观察 240 秒。
rem 必须先停面板——面板自己会写风扇字节，那些改动会混进报告里，分不清是谁干的。
cd /d "%~dp0.." || exit /b 1
if not exist "tools\out" mkdir "tools\out"
set "EXE=runtime\python.exe"
if exist "runtime\UmiPanel.exe" set "EXE=runtime\UmiPanel.exe"
echo 正在停止面板服务（观察期间不自动改任何硬件）…
"%EXE%" "tools\stop_panel.py"
if errorlevel 1 (
  echo 无法确认面板已停止，已取消后续操作。请查看上方错误后重试。
  pause
  exit /b 1
)
echo.
echo  接下来 240 秒，请**一次只动一个功能，动完等 15 秒再动下一个**，
echo  并在纸上或手机里记下顺序，例如：
echo     1) 实体键：按到全亮 → 再按到不亮（本机 Creator Center 没有办公/均衡/狂暴档）
echo     2) Creator Center 里的造物者开关：开一次、关一次
echo     3) 电池：充电阈值切到 80%%
echo     4) 键盘背光：亮度调一格 / 换个灯效
echo     5) 灯条：开关一次
echo     6) Win 键锁定 / 触摸板开关：各切一次
echo     7) 独显直连：如果能点就点一次（会要求重启，可以先取消）
echo  报告里每条变化都带时间戳，按你的顺序就能一一对回去。
echo.
echo  现在开始观察（只读，一发都不写）…
"%EXE%" "tools\ec_watch.py" 240 all
echo.
echo 正在重新启动面板…
start "UmiPanel" /min "%EXE%" "main.py" --supervise --no-browser
echo 结果在 tools\out\ec-watch-all.txt —— 把这个文件和你记的点击顺序一起给我，
echo 我据此判断 Creator Center 各功能落在哪个寄存器（语义确认后才允许写）。
pause
