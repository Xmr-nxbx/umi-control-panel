# 只读扫描：这台同方模具上有没有可用的 OEM WMI 接口（tongfang-mifs-wmi 那条线索）。
# 不开任何设备句柄、不发任何 IOCTL、不改任何东西。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

function Head($t) { Write-Output ''; Write-Output ('=== ' + $t + ' ===') }

Head 'ACPI\PNP0C14 / WMI 映射设备'
Get-PnpDevice | Where-Object { $_.InstanceId -like 'ACPI*' } | ForEach-Object {
    $hid = ($_.HardwareID -join ',')
    if ($hid -match 'PNP0C14' -or $hid -match 'WMI' -or $_.FriendlyName -match 'WMI') {
        Write-Output ('  ' + $_.Status + '  ' + $_.InstanceId + '  ' + $_.FriendlyName + '  [' + $hid + ']')
    }
}

Head '非微软 ACPI 设备（按 HardwareID 过滤掉标准 PNP 码）'
$std = 'PNP0A08|PNP0C0C|PNP0C0D|PNP0C0E|PNP0C0F|PNP0C14|PNP0C0A|PNP0C01|PNP0C02|PNP0C04|PNP0C09|PNP0100|PNP0103|PNP0200|PNP0800|PNP0B00|PNP0303|PNP0F13|PNP0501|PNP0400|PNP0501|PNP0C06|PNP0C07|PNP0C08|PNP0000|PNP0C0B|PNP0D80|PNP0E00'
Get-PnpDevice | Where-Object { $_.InstanceId -like 'ACPI*' } | ForEach-Object {
    $hid = ($_.HardwareID -join ',')
    if ($hid -and $hid -notmatch $std) {
        Write-Output ('  ' + $_.Status + '  ' + $_.InstanceId + '  ' + $_.FriendlyName + '  [' + $hid + ']')
    }
} | Select-Object -First 60

Head 'root/wmi 里名字像 GUID 的类（ACPI WMI 数据块）'
$guids = @(Get-WmiObject -Namespace root/wmi -List | Where-Object { $_.Name -match '^[0-9A-Fa-f]{8}-([0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$' })
Write-Output ('  GUID 类数量: ' + $guids.Count)
foreach ($c in $guids) {
    $n = @(Get-WmiObject -Namespace root/wmi -Class $c.Name).Count
    if ($n -gt 0) { Write-Output ('  PRESENT  ' + $c.Name + '  instances=' + $n) }
}

Head 'root/wmi 里名字带 OEM 味道的类'
$pat = 'Tongfang|Uniwill|UW|GCU|Mechrevo|Clevo|Hotkey|HotKey|Oem|OEM|MIFS|Fan|Mode|Keyboard|KBL|RGB|Light|LightBar|EC|Batt|Charge|Turbo|Silent|Gaming|Creator'
Get-CimClass -Namespace root/wmi | Where-Object { $_.CimClassName -match $pat } | ForEach-Object {
    $n = 0
    try { $n = @(Get-CimInstance -Namespace root/wmi -ClassName $_.CimClassName -ErrorAction Stop).Count } catch {}
    if ($n -gt 0) { Write-Output ('  HAS-INSTANCE(' + $n + ')  ' + $_.CimClassName) }
    else { Write-Output ('  class-only             ' + $_.CimClassName) }
}

Head '本机服务里 OEM 相关（只看状态，不动它）'
Get-Service | Where-Object { $_.Name -match 'GCU|Uniwill|UW|Mechrevo|Tongfang|Creator|OEM' } | ForEach-Object {
    Write-Output ('  ' + $_.Status + '  ' + $_.StartType + '  ' + $_.Name + '  ' + $_.DisplayName)
}
