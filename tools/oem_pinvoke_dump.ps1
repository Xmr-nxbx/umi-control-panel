# 只读反射：把 OEM 托管程序里的 P/Invoke 包装方法签名列出来。
#
# 为什么这么做：ACPIDriverDll.dll 是原生 x64 DLL，导出 ReadEC/WriteEC 等 19 个函数，
# 但机器上没有头文件。调用它的 GCUService.exe 是 .NET 程序集，其元数据里就有确切的
# 返回类型与参数类型。这里只加载程序集读签名，不实例化任何类型、不调用任何方法。
#
# 逐个方法加 try/catch：GCUService 依赖的第三方程序集不一定都能解析，
# 某些方法的参数类型拿不到就跳过，不能让整个导出中断（第一版就是这么死的）。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\oem_pinvoke_dump.ps1 [-Path 程序集]
param(
    [string]$Path = 'C:\Program Files\OEM\CreatorCenter\UniwillService\MyControlCenter\GCUService.exe',
    [string]$TypeFilter = 'ACPI|EC|Fan|Hardware|Win32|Native|Driver|Thermal|Temp|Power|Sm'
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dir = Split-Path -Parent $Path
Set-Location $dir
Write-Output "目标：$Path"
Write-Output "工作目录：$dir（让 LoadFrom 能解析同目录的依赖程序集）"

# 依赖解析：只在同目录找，找不到就返回 null，绝不去别处加载
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
try { $asm = [System.Reflection.Assembly]::ReflectionOnlyLoadFrom($Path) }
catch { Write-Output "ReflectionOnly 加载失败，改用 LoadFrom：$($_.Exception.Message)" }
if ($asm -eq $null) { $asm = [System.Reflection.Assembly]::LoadFrom($Path) }

$types = @()
try { $types = $asm.GetTypes() }
catch [System.Reflection.ReflectionTypeLoadException] {
    $types = @($_.Exception.Types | Where-Object { $_ -ne $null })
    Write-Output "部分类型无法加载，已取到 $($types.Count) 个"
}
catch { Write-Output "GetTypes 失败：$($_.Exception.Message)" }

$flags = [System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'
$pinvokes = 0; $matched = 0

function Format-Method($t, $m) {
    $params = @()
    foreach ($p in $m.GetParameters()) {
        $mod = ''
        if ($p.IsOut) { $mod = 'out ' } elseif ($p.ParameterType.IsByRef) { $mod = 'ref ' }
        $pt = '?'
        try { $pt = $p.ParameterType.Name.TrimEnd('&') } catch { $pt = '?' }
        $params += "$mod$pt $($p.Name)"
    }
    $rt = '?'
    try { $rt = $m.ReturnType.Name } catch { }
    $dll = ''
    try {
        foreach ($a in [System.Reflection.CustomAttributeData]::GetCustomAttributes($m)) {
            if ($a.Constructor.DeclaringType.Name -eq 'DllImportAttribute') {
                $dll = ' [DllImport ' + $a.ConstructorArguments[0].Value + ']'
            }
        }
    } catch { }
    return ("{0,-30} {1,-8} {2}({3}){4}" -f $t.Name, $rt, $m.Name, ($params -join ', '), $dll)
}

Write-Output ""
Write-Output "=== 全部 DllImport（原生互操作入口）==="
foreach ($t in $types) {
    $methods = @()
    try { $methods = $t.GetMethods($flags) } catch { continue }
    foreach ($m in $methods) {
        $isPin = $false
        try {
            foreach ($a in [System.Reflection.CustomAttributeData]::GetCustomAttributes($m)) {
                if ($a.Constructor.DeclaringType.Name -eq 'DllImportAttribute') { $isPin = $true }
            }
        } catch { }
        if (-not $isPin) { continue }
        try { Write-Output (Format-Method $t $m); $pinvokes++ } catch { }
    }
}
Write-Output ""
Write-Output "=== 类型名匹配 /$TypeFilter/ 的方法（找 EC 包装层）==="
foreach ($t in $types) {
    if ($t.Name -notmatch $TypeFilter) { continue }
    $methods = @()
    try { $methods = $t.GetMethods($flags) } catch { continue }
    Write-Output ("--- {0}（{1} 个方法）" -f $t.FullName, $methods.Count)
    foreach ($m in $methods) {
        try { Write-Output ("    " + (Format-Method $t $m)); $matched++ }
        catch { Write-Output ("    " + $m.Name + "(参数解析失败)") }
    }
}
Write-Output ""
Write-Output "DllImport 共 $pinvokes 条；匹配类型的方法共 $matched 条"
