# USB-focused JTAG boot. Run after a physical power cycle when possible.
param(
    [int]$CaptureSeconds = 180,
    [int]$WaitSeconds = 40,
    [string]$Out = "",
    [string]$Tcl = "",
    [string]$Port = "COM11",
    [string]$ImageDir = "",
    [string]$Bitstream = "",
    [string]$Xsdb = "D:/Xilinx/2026.1/Vitis/bin/xsdb.bat",
    [switch]$RequireColdState
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $ImageDir) { $ImageDir = Join-Path $repo '.tmp/w7_usb_images' }
if (-not $Out) { $Out = Join-Path $repo ('metrics/logs/_usb_boot_' + (Get-Date -Format yyyyMMdd_HHmmss) + '.log') }
if (-not $Tcl) { $Tcl = Join-Path $PSScriptRoot 'jtag_boot.tcl' }
$Out = [IO.Path]::GetFullPath($Out)
$ImageDir = [IO.Path]::GetFullPath($ImageDir)
foreach ($path in @($Tcl,$Xsdb,(Join-Path $PSScriptRoot 'serial_cap2.ps1'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing: $path" }
}
foreach ($image in @('zImage','uInitrd','system.dtb','boot_jtag.scr','u-boot-dtb.bin')) {
    $path = Join-Path $ImageDir $image
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing image: $path" }
    Get-FileHash -LiteralPath $path -Algorithm SHA256 | ForEach-Object { "$image SHA256=$($_.Hash)" }
}
$null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out)
# Remove stale readiness text before starting the reader.
[IO.File]::WriteAllText($Out, '', (New-Object Text.UTF8Encoding($false)))
$serialArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',
    ('"' + (Join-Path $PSScriptRoot 'serial_cap2.ps1') + '"'),
    '-Port',$Port,'-Seconds',$CaptureSeconds,'-Out',('"' + $Out + '"'),
    '-UntilMarker','"VIGI SELF-REPORT (end)"')
$reader = Start-Process powershell.exe -WindowStyle Hidden -ArgumentList $serialArgs -PassThru
try {
    $opened = $false
    for ($n=0; $n -lt 50; $n++) {
        if ((Test-Path -LiteralPath $Out) -and (Select-String -LiteralPath $Out -Pattern '^\[\[open ok ' -Quiet)) { $opened=$true; break }
        if ($reader.HasExited) { break }
        Start-Sleep -Milliseconds 100
    }
    if (-not $opened) { throw "Serial reader failed to open $Port; see $Out" }
    $env:VIGI_IMAGE_DIR = $ImageDir.Replace('\','/')
    $env:VIGI_REQUIRE_COLD = if ($RequireColdState) { '1' } else { '0' }
    $env:VIGI_BITSTREAM = ''
    if ($Bitstream) {
        $resolvedBit = (Resolve-Path -LiteralPath $Bitstream).Path
        $env:VIGI_BITSTREAM = $resolvedBit.Replace('\','/')
        Get-FileHash -LiteralPath $resolvedBit -Algorithm SHA256
    }
    "SERIAL_READY $Port log=$Out"
    # XSDB can return exit 0 after a Tcl error; require an explicit end marker too.
    $ErrorActionPreference = 'Continue'
    $bootOutput = & $Xsdb $Tcl 2>&1 | Tee-Object -FilePath ($Out + '.xsdb.log')
    $bootCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    $bootOutput | ForEach-Object { Write-Output $_ }
    if ($bootCode -ne 0 -or ($bootOutput -join "`n") -notmatch 'JTAG_BOOT_STARTED') {
        throw "XSDB failed or did not reach start marker; see $Out.xsdb.log"
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    while (-not $reader.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (-not $reader.HasExited) { Stop-Process -Id $reader.Id }
    Get-Content -LiteralPath $Out -Tail 100
    $log = Get-Content -LiteralPath $Out -Raw
    if ($log -notmatch 'VIGI SELF-REPORT \(end\)') { throw "Linux self-report missing; see $Out" }
    if ($log -notmatch 'CAPTURE_PASS .*640x480' -or $log -notmatch 'CAPTURE_PASS .*1280x720') {
        $have640 = $log -match 'CAPTURE_(PASS .*640x480|FRAMES_OK .*negotiated=640x480)'
        $have720 = $log -match 'CAPTURE_(PASS .*1280x720|FRAMES_OK .*negotiated=1280x720)'
        if ($have640 -and $have720) { "USB_LINK_FRAMES_OK_REQUESTED_30FPS_UNMET log=$Out" }
        "USB_CAPTURE_NOT_YET_PASS log=$Out"
        exit 2
    }
    "USB_CAPTURE_PASS log=$Out"
} finally {
    if (-not $reader.HasExited) { Stop-Process -Id $reader.Id -ErrorAction SilentlyContinue }
}
