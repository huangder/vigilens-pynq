param(
    [string]$Work = '',
    [string]$Models = '',
    [string]$Images = '',
    [string]$Distribution = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Work) { $Work = Join-Path $repo ('.tmp\cold_boot_' + (Get-Date -Format 'yyyyMMdd_HHmmss')) }
if (-not $Models) { $Models = Join-Path $repo '.tmp\autonomy\hr_continuous_20261102\package' }
if (-not $Images) { $Images = Join-Path $repo '.tmp\net_loop' }
$Work = [IO.Path]::GetFullPath($Work)
$Models = [IO.Path]::GetFullPath($Models)
$Images = [IO.Path]::GetFullPath($Images)
if (Test-Path -LiteralPath $Work) { throw "Refusing to overwrite: $Work" }
if (-not $Work.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must remain inside this project.'
}
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment missing; read env.ps1.' }
function Linux-Path([string]$Path) {
    if ($Path -notmatch '^([A-Za-z]):[\\/](.*)$') { throw "Expected a Windows drive path: $Path" }
    return '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2].Replace('\', '/')
}
function Run-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}
Push-Location $repo
try {
    . .\env.ps1
    $linuxRepo = Linux-Path $repo
    $linuxWork = Linux-Path $Work
    $linuxImages = Linux-Path $Images
    Run-Native $python @('board/w7_buildroot/boot_delivery.py', 'prepare', '--images', $Images,
        '--bitstream', 'board/bitstream/system_wrapper_720p60.bit', '--out', (Join-Path $Work 'boot'))
    Run-Native 'wsl.exe' @('-d', $Distribution, '--exec', 'bash', "$linuxRepo/board/autonomy/build_native_vision.sh",
        'arm', "$linuxWork/application_build", 'ert_temporal')
    Run-Native $python @('board/cold_boot/delivery.py', 'prepare', '--work', $Work,
        '--build', (Join-Path $Work 'application_build'), '--models', $Models)
    Run-Native 'wsl.exe' @('-d', $Distribution, '--exec', 'bash', "$linuxRepo/board/w7_buildroot/build_boot_delivery.sh", "$linuxWork/boot")
    Run-Native 'wsl.exe' @('-d', $Distribution, '--exec', 'bash', "$linuxRepo/board/cold_boot/wrap_spl.sh", $linuxWork)
    & (Join-Path $PSScriptRoot 'Generate-BootImage.ps1') -Work $Work
    Run-Native 'wsl.exe' @('-d', $Distribution, '--exec', 'env', "SOURCE_IMAGES=$linuxImages", 'bash',
        "$linuxRepo/board/cold_boot/build_linux.sh", $linuxWork)
    Run-Native $python @('board/w7_buildroot/boot_delivery.py', 'seal', '--out', (Join-Path $Work 'boot'))
    Run-Native $python @('board/w7_buildroot/boot_delivery.py', 'verify', '--out', (Join-Path $Work 'boot'))
    $delivery = Join-Path $Work 'VigiLens_SD'
    Run-Native $python @('board/cold_boot/delivery.py', 'assemble', '--work', $Work, '--out', $delivery)
    & (Join-Path $delivery 'Verify-Copy.ps1')
    Write-Output "COLD_BOOT_FILES_READY $delivery"
    Write-Output 'Physical independent SD power-on remains unverified.'
} finally { Pop-Location }
