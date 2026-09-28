# 只读 IL 追踪：把 OEM 托管方法的 IL 字节扫一遍，按顺序还原
# 「用了哪些常量 + 调了哪些函数 + 传了什么字符串」。
#
# 为什么需要它：ACPIDriverDll/AcpiCtrl 用 DeviceIoControl 直连 \\.\ACPIDriver，
# 光有签名不够——输入缓冲到底传地址还是传结构体、长度是几字节，必须看实现。
# 机器上没有反编译器，所以只做「有序标记还原」：这不是完整反汇编，
# 但足以确认 inBufferSize / outBufferSize / 编组方式。全程只读元数据，不执行。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\oem_il_dump.ps1 -MethodFilter 'ReadACPI'
param(
    [string]$Path = 'C:\Program Files\OEM\CreatorCenter\UniwillService\MyControlCenter\GCUService.exe',
    [string]$TypeFilter = 'MyECIO\.AcpiCtrl|MyECIO\.MyEcCtrl',
    [string]$MethodFilter = '.'
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dir = Split-Path -Parent $Path
Set-Location $dir

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

function Resolve-Token($mod, $token, $kind) {
    try {
        switch ($kind) {
            'method' { $m = $mod.ResolveMethod($token); return ($m.DeclaringType.Name + '::' + $m.Name) }
            'field'  { $f = $mod.ResolveField($token); return ($f.DeclaringType.Name + '::' + $f.Name) }
            'string' { return ('"' + $mod.ResolveString($token) + '"') }
            'member' { $x = $mod.ResolveMember($token); return ($x.DeclaringType.Name + '::' + $x.Name) }
        }
    } catch { return ('token:0x{0:X8}' -f $token) }
    return ('token:0x{0:X8}' -f $token)
}

# 单字节 opcode → (类别, 操作数长度)
$marks = @{
    0x20 = @('int32', 4); 0x1F = @('int8', 1); 0x72 = @('string', 4)
    0x28 = @('method', 4); 0x6F = @('method', 4); 0x73 = @('method', 4); 0x29 = @('method', 4)
    0x7E = @('field', 4); 0x80 = @('field', 4); 0x7B = @('field', 4); 0x7D = @('field', 4)
    0xD0 = @('member', 4)
}
$smallConst = @{
    0x15 = '-1'; 0x16 = '0'; 0x17 = '1'; 0x18 = '2'; 0x19 = '3'
    0x1A = '4'; 0x1B = '5'; 0x1C = '6'; 0x1D = '7'; 0x1E = '8'
}

$flags = [System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'
foreach ($t in $types) {
    if ($t.FullName -notmatch $TypeFilter) { continue }
    $methods = @()
    try { $methods = $t.GetMethods($flags) } catch { continue }
    foreach ($m in $methods) {
        if ($m.Name -notmatch $MethodFilter) { continue }
        $body = $null
        try { $body = $m.GetMethodBody() } catch { }
        if ($body -eq $null) { continue }
        $il = $null
        try { $il = $body.GetILAsByteArray() } catch { }
        if ($il -eq $null -or $il.Length -eq 0) { continue }

        $ps = @()
        try { foreach ($p in $m.GetParameters()) { $ps += ($p.ParameterType.Name + ' ' + $p.Name) } } catch { $ps += '?' }
        Write-Output ''
        Write-Output ('==== {0}.{1}({2}) -> {3}   IL {4} 字节' -f $t.Name, $m.Name, ($ps -join ', '), $m.ReturnType.Name, $il.Length)
        Write-Output '  原始 IL（每行 16 字节）：'
        for ($o = 0; $o -lt $il.Length; $o += 16) {
            $end = [Math]::Min($o + 15, $il.Length - 1)
            $hex = ($il[$o..$end] | ForEach-Object { '{0:X2}' -f $_ }) -join ' '
            Write-Output ('    {0:X4}  {1}' -f $o, $hex)
        }

        # 不跳操作数地扫：宁可多报也不漏报 call/field/string 标记
        $seq = @()
        for ($i = 0; $i -lt $il.Length; $i++) {
            $b = $il[$i]
            if ($smallConst.ContainsKey([int]$b)) {
                $seq += ('偏移 {0,4}: ldc.i4.{1}' -f $i, $smallConst[[int]$b])
                continue
            }
            if (-not $marks.ContainsKey([int]$b)) { continue }
            $kind = $marks[[int]$b][0]; $len = $marks[[int]$b][1]
            if ($i + $len -ge $il.Length) { continue }
            if ($kind -eq 'int8') {
                $seq += ('偏移 {0,4}: ldc.i4.s {1}' -f $i, [int]$il[$i + 1])
                continue
            }
            $val = [BitConverter]::ToUInt32($il, $i + 1)
            if ($kind -eq 'int32') {
                $seq += ('偏移 {0,4}: ldc.i4 {1}  (0x{1:X8})' -f $i, $val)
            } else {
                $mod = $m.Module
                $resolved = Resolve-Token $mod ([int]$val) $kind
                $label = switch ($kind) { 'method' { 'call' } 'field' { 'field' } 'string' { 'ldstr' } default { 'ldtoken' } }
                $seq += ('偏移 {0,4}: {1,-7} {2}' -f $i, $label, $resolved)
            }
        }
        Write-Output '  有序标记：'
        $seq | ForEach-Object { Write-Output ('    ' + $_) }
    }
}
