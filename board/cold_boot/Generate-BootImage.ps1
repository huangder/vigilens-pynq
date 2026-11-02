param(
    [Parameter(Mandatory=$true)][string]$Work,
    [string]$Bootgen = 'D:\Xilinx\2026.1\Vitis\bin\bootgen.bat'
)
$ErrorActionPreference = 'Stop'
$Work = (Resolve-Path -LiteralPath $Work).Path
$boot = Join-Path $Work 'boot'
$source = Join-Path $boot 'uboot\spl\u-boot-spl.bin'
if (-not (Test-Path -LiteralPath $Bootgen)) { throw 'AMD bootgen is required to seal the SD boot image.' }
if (-not (Test-Path -LiteralPath $source)) { throw 'Build the private SPL first.' }
if (-not (Test-Path -LiteralPath (Join-Path $boot 'spl_payload.elf'))) { throw 'Run wrap_spl.sh to include the SPL control DTB.' }
$bif = "the_ROM_image:`n{`n [bootloader] spl_payload.elf`n}`n"
[IO.File]::WriteAllText((Join-Path $boot 'boot_spl.bif'), $bif, [Text.Encoding]::ASCII)
Push-Location $boot
try {
    & $Bootgen -image boot_spl.bif -arch zynq -o fat/BOOT.BIN -w on
    if ($LASTEXITCODE -ne 0) { throw "bootgen failed: $LASTEXITCODE" }
    $record = [ordered]@{
        tool = $Bootgen
        payload_sha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        image_sha256 = (Get-FileHash -LiteralPath 'fat/BOOT.BIN' -Algorithm SHA256).Hash.ToLowerInvariant()
        physical_boot_verified = $false
    }
    [IO.File]::WriteAllText((Join-Path $boot 'bootgen_record.json'), ($record | ConvertTo-Json) + "`n", [Text.UTF8Encoding]::new($false))
} finally { Pop-Location }
