# Create the SAINTOS development VM in VirtualBox (Windows host). Reproducible; touches no real disk.
#
#   powershell -ExecutionPolicy Bypass -File packaging\linux\vm\create_vm.ps1 -Iso <path to Fedora KDE ISO>
#
# Makes: 8 GB RAM, 6 CPUs, EFI, an 80 GB dynamically growing virtual disk (a file under
# "VirtualBox VMs"), the ISO in the optical drive, NAT with SSH on 127.0.0.1:2222, HD audio in/out,
# and this checkout shared into the VM as "saint" (/media/sf_saint).
param(
    [Parameter(Mandatory = $true)][string]$Iso,
    [string]$Name = "SAINTOS-dev",
    [int]$MemoryMB = 8192,
    [int]$Cpus = 6,
    [int]$DiskMB = 81920
)
$ErrorActionPreference = "Stop"
$vb = "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe"
if (-not (Test-Path $vb)) { throw "VirtualBox isn't installed (expected $vb)." }
if (-not (Test-Path $Iso)) { throw "ISO not found: $Iso" }
if ((& $vb list vms) -match "`"$Name`"") { throw "A VM named $Name already exists; pick another -Name." }
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path

& $vb createvm --name $Name --ostype Fedora_64 --register | Out-Null
$cfg = ((& $vb showvminfo $Name --machinereadable | Select-String '^CfgFile=').ToString() -split '=', 2)[1].Trim('"')
$dir = Split-Path ($cfg -replace '\\\\', '\')
& $vb modifyvm $Name --memory $MemoryMB --cpus $Cpus --firmware efi --graphicscontroller vmsvga --vram 128 `
    --nic1 nat --natpf1 "ssh,tcp,127.0.0.1,2222,,22" --audio-driver default --audio-controller hda `
    --audio-enabled on --audio-in on --audio-out on --clipboard-mode bidirectional --rtc-use-utc on --mouse usbtablet
& $vb createmedium disk --filename "$dir\$Name.vdi" --size $DiskMB --format VDI --variant Standard | Out-Null
& $vb storagectl $Name --name SATA --add sata --controller IntelAhci --portcount 2 --bootable on
& $vb storageattach $Name --storagectl SATA --port 0 --device 0 --type hdd --medium "$dir\$Name.vdi"
& $vb storageattach $Name --storagectl SATA --port 1 --device 0 --type dvddrive --medium $Iso
& $vb sharedfolder add $Name --name saint --hostpath $repo --automount
Write-Output "Created $Name in $dir. Start it with: VBoxManage startvm $Name"
