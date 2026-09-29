# 只读探测：本机有没有注册同方/机械革命那一族 WMI 接口（GUID 来自上游 Linux 驱动源码）。
# 这一步只列类名、方法名、实例名，**不调用任何方法**——先看门在不在，再谈推门。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

$guids = @(
    'B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B',   # MICommonInterface（上游文档写法）
    'b60bfb48-3e5b-49e4-a0e9-8cffe1b3434b',   # 小写变体
    '{B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B}', # 带花括号变体
    '46C93E13-EE9B-4262-8488-563BCA757FEF',   # HID_EVENT20
    'FA78E245-2C0F-4CA1-91CF-15F34E474850'    # HID_EVENT21
)

foreach ($g in $guids) {
    Write-Output ('--- ' + $g)
    $c = Get-CimClass -Namespace root/wmi -ClassName $g
    if (-not $c) { Write-Output '    class: NOT FOUND'; continue }
    Write-Output ('    class: FOUND  super=' + $c.CimSuperClassName)
    foreach ($m in $c.CimClassMethods) {
        $ps = ($m.Parameters | ForEach-Object { $_.CimType.ToString() + ' ' + $_.Name }) -join ', '
        Write-Output ('    method: ' + $m.Name + '(' + $ps + ') -> ' + $m.ReturnType)
    }
    foreach ($p in $c.CimClassProperties) {
        Write-Output ('    prop: ' + $p.CimType.ToString() + ' ' + $p.Name)
    }
    $inst = @(Get-CimInstance -Namespace root/wmi -ClassName $g)
    Write-Output ('    instances: ' + $inst.Count)
    foreach ($i in $inst) { Write-Output ('      ' + $i.InstanceName) }
}
