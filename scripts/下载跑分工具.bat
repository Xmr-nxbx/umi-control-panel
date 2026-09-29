@echo off
setlocal
rem 取开源跑分负载：zstd（facebook/zstd 官方 Release，BSD-3/GPL-2）放到 tools\bin。
rem 顺带生成固定种子的基准输入文件，保证每次跑的内容一模一样、档位之间才可比。
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
if not exist "tools\out" mkdir "tools\out"
"runtime\python.exe" "tools\fetch_bench_tools.py" > "tools\out\fetch-bench-tools.txt" 2>&1
type "tools\out\fetch-bench-tools.txt"
pause
