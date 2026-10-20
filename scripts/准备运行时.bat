@echo off
setlocal
rem 准备便携 Python 3.12。纯 cmd，引导只用系统自带 curl/tar，不调用 PowerShell。
rem 优先复用本机已有的 Python；一个都没有时才下载官方 embeddable 包。
cd /d "%~dp0.."
if exist "runtime\python.exe" goto run_runtime
if not defined UMI_PYTHON_SRC goto try_cpython
if exist "%UMI_PYTHON_SRC%\python.exe" goto copy_src
:try_cpython
if exist "C:\Python312\python.exe" goto copy_cpython
where py >nul 2>&1
if errorlevel 1 goto try_python
echo 用 py 启动器准备运行时...
py -3 "tools\setup_runtime.py"
if errorlevel 1 goto bootstrap
goto done
:try_python
where python >nul 2>&1
if errorlevel 1 goto bootstrap
echo 用 PATH 里的 Python 准备运行时...
python "tools\setup_runtime.py"
if errorlevel 1 goto bootstrap
goto done
:run_runtime
"runtime\python.exe" "tools\setup_runtime.py"
if errorlevel 1 goto try_cpython
goto done
:copy_src
echo 从 %UMI_PYTHON_SRC% 复制运行时...
"%UMI_PYTHON_SRC%\python.exe" "tools\setup_runtime.py" --source "%UMI_PYTHON_SRC%"
goto done
:copy_cpython
echo 从 C:\Python312 复制运行时...
"C:\Python312\python.exe" "tools\setup_runtime.py" --source "C:\Python312"
goto done
:bootstrap
if not exist "runtime" mkdir "runtime"
set "ZIP=%TEMP%\umi-python-embed.zip"
echo 没有找到可用的 Python，正在用 curl 下载官方 embeddable 包...
curl.exe -L --fail --retry 3 --connect-timeout 15 -o "%ZIP%" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
if errorlevel 1 (
  echo 下载失败。请手动下载并解压到 runtime\ 后重跑本脚本：
  echo   https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
  pause
  exit /b 1
)
"%SystemRoot%\System32\tar.exe" -xf "%ZIP%" -C "runtime"
if errorlevel 1 (
  echo 解压失败：系统缺少 tar.exe。
  pause
  exit /b 1
)
del "%ZIP%" >nul 2>&1
"runtime\python.exe" "tools\setup_runtime.py"
:done
pause
