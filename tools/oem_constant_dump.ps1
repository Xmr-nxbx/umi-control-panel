# 只读反射：导出 OEM 托管程序里的常量与枚举值（IOCTL 码、EC 寄存器定义）。
#
# 用途：GCUService.exe 里的 MyECIO.AcpiCtrl 用
#   DeviceIoControl(handle, ioctrl, IntPtr inBuffer, int inSize, ref int outBuffer, ...)
# 直接和 \\.\ACPIDriver 说话。要复刻这条路径，必须知道 ioctrl 的确切数值——
# 它们是常量字段，值就存在元数据里。枚举类型（Define.ECSpec 等）则是寄存器/命令定义。
#
# 只读元数据，不实例化、不调用任何方法。输出含厂商私有定义，
# 按 README 第 8 节约定：产物落在 tools\out（已 gitignore），不进仓库、不再分发。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\oem_constant_dump.ps1
param(
    [string]$Path = 'C:\Program Files\OEM\CreatorCenter\UniwillService\MyControlCenter\GCUService.exe',
    [string]$TypeFilter = 'Define|EC|Ec|Acpi|ACPI|IOCTL|Fan|FAN|Serv|Client|Power|Thermal|Temp|Spec|CMD|Mode|Smart|Apc|Gpu|RGB|Battery|Charge',
    [string]$FieldFilter = 'IOCTL|EC|FAN|TMP|TEMP|PL1|PL2|MODE|ADDR|REG|SMART|CMD|INDEX|OFFSET|READ|WRITE|TURBO|OFFICE|BALANCE|CHARGE|BATTERY|TGP|SM',
    [string]$OutJson = ''
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dir = Split-Path -Parent $Path
Set-Location $dir
Write-Output "# 目标：$Path（只读元数据）"

$onResolve = [System.ResolveEventHandler] {
    param($s, $e)
    $name = (New-Object System.Reflection.AssemblyName($e.Name)).Name
    foreach ($ext in @('.dll', '.exe')) {
        $cand = Join-Path $dir ($name + $ext)
        if (Test-Path $cand) { return [System.Reflection.Assembly]::LoadFrom($cand) }
    }
    return $null
}
[System.AppDomain]::CurrentDomain.add_ReflectionOnlyAssemblyResolve($onResolve)

$asm = $null
try { $asm = [System.Reflection.Assembly]::ReflectionOnlyLoadFrom($Path) } catch { }
if ($asm -eq $null) { $asm = [System.Reflection.Assembly]::LoadFrom($Path) }

$types = @()
try { $types = $asm.GetTypes() }
catch [System.Reflection.ReflectionTypeLoadException] {
    $types = @($_.Exception.Types | Where-Object { $_ -ne $null })
}

$flags = [System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'
$enumCount = 0; $constCount = 0
$collected = [ordered]@{}

foreach ($t in $types) {
    if ($t.Name -notmatch $TypeFilter) { continue }
    if ($t.IsEnum) {
        $names = @()
        try { $names = [System.Enum]::GetNames($t) } catch { continue }
        if ($names.Count -eq 0) { continue }
        Write-Output ""
        Write-Output ("[enum] {0}" -f $t.FullName)
        $vals = [ordered]@{}
        foreach ($n in $names) {
            $v = $null
            try { $v = $t.GetField($n).GetRawConstantValue() } catch { }
            if ($v -ne $null) {
                Write-Output ("    {0,-42} = {1}  (0x{1:X})" -f $n, [int64]$v)
                $vals[$n] = [int64]$v
            } else {
                Write-Output ("    {0,-42} = ?" -f $n)
            }
        }
        $collected[$t.FullName] = @{ kind = 'enum'; values = $vals }
        $enumCount++
        continue
    }
    $fields = @()
    try { $fields = $t.GetFields($flags) } catch { continue }
    $lines = @()
    $vals = [ordered]@{}
    foreach ($f in $fields) {
        if ($f.Name -notmatch $FieldFilter) { continue }
        if (-not $f.IsLiteral -and -not $f.IsStatic) { continue }
        $v = $null
        if ($f.IsLiteral) { try { $v = $f.GetRawConstantValue() } catch { } }
        if ($v -eq $null) { continue }
        $vals[$f.Name] = $v
        $val = $v
        if ($v -is [int] -or $v -is [uint32] -or $v -is [long] -or $v -is [uint64] -or $v -is [int16] -or $v -is [byte]) {
            $val = ("{0}  (0x{0:X})" -f [int64]$v)
        } elseif ($v -is [string]) {
            $val = '"' + $v + '"'
        }
        $lines += ("    {0,-42} = {1}" -f $f.Name, $val)
    }
    if ($lines.Count -gt 0) {
        Write-Output ""
        Write-Output ("[const] {0}" -f $t.FullName)
        $lines | ForEach-Object { Write-Output $_ }
        $collected[$t.FullName] = @{ kind = 'const'; values = $vals }
        $constCount += $lines.Count
    }
}

Write-Output ""
Write-Output "# 枚举类型 $enumCount 个，常量 $constCount 条"

if ($OutJson -ne '') {
    $payload = [ordered]@{
        source      = $Path
        generated   = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
        note        = 'OEM 私有定义，本机生成、本机使用，不入库、不再分发'
        types       = $collected
    }
    $json = $payload | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($OutJson, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "# 已写出 JSON：$OutJson（$($json.Length) 字符）"
}
