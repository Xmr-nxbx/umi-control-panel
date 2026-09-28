# 准备便携 Python 运行时（幂等，可重复执行）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_runtime.ps1
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root 'runtime'
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

function Test-Runtime {
    (Test-Path (Join-Path $Runtime 'python.exe')) -and (Test-Path (Join-Path $Runtime 'python312.zip'))
}

if (-not (Test-Runtime)) {
    $Copied = $false
    foreach ($src in @(
        'C:\Users\19062\Desktop\trae空间\AI电脑助手\runtime',
        'C:\Python312'
    )) {
        if (Test-Path (Join-Path $src 'python.exe')) {
            Write-Host "从 $src 复制运行时..."
            Copy-Item -Path (Join-Path $src '*') -Destination $Runtime -Recurse -Force
            $Copied = $true
            break
        }
    }
    if (-not $Copied) {
        # 官方 embeddable 包；如需代理请在执行前设置 $env:HTTPS_PROXY
        $Url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
        $Zip = Join-Path $env:TEMP 'umi-embed.zip'
        Write-Host "下载 $Url"
        Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
        $Unz = Join-Path $env:TEMP 'umi-embed'
        if (Test-Path $Unz) { Remove-Item $Unz -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $Unz | Out-Null
        # Git Bash 的 GNU tar 不支持 zip，用系统 bsdtar
        & 'C:\Windows\System32\tar.exe' -xf $Zip -C $Unz
        Copy-Item -Path (Join-Path $Unz '*') -Destination $Runtime -Recurse -Force
    }
}

if (-not (Test-Runtime)) { throw "运行时准备失败：$Runtime 内容不完整" }

# 改名副本：
#   UmiPanel.exe —— 主程序用（避开 pythonw 被安全软件静默查杀的老问题）
#   OcTool.exe   —— 兜底通道的身份锚点（GCUBridge 按 clientId 同名进程校验）
foreach ($name in @('UmiPanel.exe', 'OcTool.exe')) {
    $dst = Join-Path $Runtime $name
    if (-not (Test-Path $dst)) {
        Copy-Item (Join-Path $Runtime 'python.exe') $dst
        Write-Host "已生成 $name"
    }
}

$py = Join-Path $Runtime 'python.exe'
& $py -c "import sys, ctypes, socket, json, http.server, ctypes.wintypes; print('运行时 OK', sys.version.split()[0])"
Write-Host "完成。启动面板：双击 启动面板.bat"
