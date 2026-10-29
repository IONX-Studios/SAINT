# Boot a SAINTOS ISO in a disposable VirtualBox VM and wait for the image's own boot report.
#
#   powershell -ExecutionPolicy Bypass -File packaging\linux\vm\boot_test.ps1 -Iso dist\SAINTOS-x86_64.iso
#       [-Name SAINTOS-test] [-Firmware efi|bios] [-TimeoutMin 25] [-FromDisk] [-Keep]
#
# The VM: EFI (or legacy BIOS with -Firmware bios), 6 GB RAM, 4 CPUs, a fresh empty 40 GB virtual disk, serial port -> serial.log, SSH on
# 127.0.0.1:2224. It never touches a physical disk. The image's saintos-boot-report.service writes:
#     SAINTOS-BOOT-OK <version> <live|installed>
#     SAINTOS-DESKTOP-OK user=... type=wayland
#     SAINTOS-SAINT-OK | SAINTOS-SAINT-FAIL <why>
# PASS = BOOT-OK + DESKTOP-OK + SAINT-OK before the timeout. Screenshots are saved next to the log.
# -FromDisk boots the VM's installed disk (after an install test) instead of the ISO.
param(
    [string]$Iso = "",
    [string]$Name = "SAINTOS-test",
    [ValidateSet("efi", "bios")][string]$Firmware = "efi",
    [int]$TimeoutMin = 25,
    [switch]$FromDisk,
    [switch]$Keep
)
$ErrorActionPreference = "Stop"
$vb = "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe"
$dir = "$env:USERPROFILE\VirtualBox VMs\$Name"
$out = Join-Path (Resolve-Path "$PSScriptRoot\..\..\..").Path "dist\boot-test-$Name"
New-Item -ItemType Directory -Force $out | Out-Null
$serial = Join-Path $out "serial.log"

$exists = (& $vb list vms) -match "`"$Name`""
if (-not $FromDisk) {
    if (-not (Test-Path $Iso)) { throw "ISO not found: $Iso" }
    if ($exists) { throw "$Name already exists; remove it first (VBoxManage unregistervm $Name --delete) or pass -Name" }
    & $vb createvm --name $Name --ostype Fedora_64 --register | Out-Null
    & $vb modifyvm $Name --memory 6144 --cpus 4 --firmware $Firmware --graphicscontroller vmsvga --vram 128 `
        --nic1 nat --natpf1 "ssh,tcp,127.0.0.1,2224,,22" --audio-enabled off --rtc-use-utc on --mouse usbtablet `
        --uart1 0x3F8 4 --uartmode1 file $serial
    & $vb createmedium disk --filename "$dir\$Name.vdi" --size 40960 --format VDI --variant Standard | Out-Null
    & $vb storagectl $Name --name SATA --add sata --controller IntelAhci --portcount 2 --bootable on
    & $vb storageattach $Name --storagectl SATA --port 0 --device 0 --type hdd --medium "$dir\$Name.vdi"
    & $vb storageattach $Name --storagectl SATA --port 1 --device 0 --type dvddrive --medium (Resolve-Path $Iso).Path
} else {
    if (-not $exists) { throw "$Name doesn't exist; run the ISO boot and install test first" }
    & $vb storageattach $Name --storagectl SATA --port 1 --device 0 --type dvddrive --medium emptydrive
    & $vb modifyvm $Name --uartmode1 file $serial
}
if (Test-Path $serial) { Remove-Item $serial }

$started = Get-Date
& $vb startvm $Name --type headless | Out-Null
$deadline = $started.AddMinutes($TimeoutMin)
$seen = @{}
$result = "TIMEOUT"
$n = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 10
    $log = if (Test-Path $serial) { Get-Content $serial -Raw -ErrorAction SilentlyContinue } else { "" }
    foreach ($m in "SAINTOS-BOOT-OK", "SAINTOS-DESKTOP-OK", "SAINTOS-SAINT-OK", "SAINTOS-SAINT-FAIL", "SAINTOS-DESKTOP-TIMEOUT") {
        if ($log -match $m -and -not $seen[$m]) {
            $seen[$m] = [int]((Get-Date) - $started).TotalSeconds
            Write-Output ("{0,5}s  {1}" -f $seen[$m], ($log -split "`n" | Where-Object { $_ -match $m } | Select-Object -First 1).Trim())
        }
    }
    if ((++$n % 6) -eq 0) { & $vb controlvm $Name screenshotpng (Join-Path $out ("screen-{0:D4}s.png" -f [int]((Get-Date) - $started).TotalSeconds)) 2>$null }
    if ($seen["SAINTOS-SAINT-FAIL"] -or $seen["SAINTOS-DESKTOP-TIMEOUT"]) { $result = "FAIL"; break }
    if ($seen["SAINTOS-BOOT-OK"] -and $seen["SAINTOS-DESKTOP-OK"] -and $seen["SAINTOS-SAINT-OK"]) { $result = "PASS"; break }
}
& $vb controlvm $Name screenshotpng (Join-Path $out "final.png") 2>$null
$summary = [ordered]@{ vm = $Name; firmware = $Firmware; mode = $(if ($FromDisk) { "installed" } else { "live" }); result = $result;
                       seconds = [int]((Get-Date) - $started).TotalSeconds; markers = $seen; serial_log = $serial }
$summary | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $out "result-$($summary.mode).json")
Write-Output "BOOT TEST $result ($($summary.mode), $($summary.seconds)s) - evidence in $out"
if (-not $Keep) { & $vb controlvm $Name poweroff 2>$null | Out-Null }
if ($result -ne "PASS") { exit 1 }
