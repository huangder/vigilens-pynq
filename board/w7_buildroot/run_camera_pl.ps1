# Run the board experiment, then check actual data and completion markers.
param(
    [int]$Frames=300,
    [string]$Port='COM11',
    [string]$Out='',
    [switch]$FastJpeg,
    [switch]$RequireRealtime
)
$ErrorActionPreference='Stop'
if($Frames -lt 2 -or $Frames -gt 300){throw 'Frames must be between 2 and 300'}
$repo=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python=Join-Path $repo '.venv/Scripts/python.exe'
Push-Location $repo
try {
    # Product thresholds/rate come from the same config as the backend.
    $configCode="import json; from backend.config import load_config; c=load_config(); print(json.dumps(dict(fps=c['fps_nominal'],motion=c['motion_thresh_gray'])))"
    $json=& $python -c $configCode
    if($LASTEXITCODE -ne 0){throw 'Could not load config'}
    $cfg=$json | ConvertFrom-Json
    if(-not $Out){$Out=Join-Path $repo ('metrics/logs/_camera_pl_'+(Get-Date -Format yyyyMMdd_HHmmss)+'.log')}
    $Out=[IO.Path]::GetFullPath($Out)
    $null=New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out)
    $work=Join-Path $repo '.tmp/direct_loop'
    $null=New-Item -ItemType Directory -Force -Path $work
    $commandFile=Join-Path $work 'run_camera_pl.sh'
    $prefix=if($FastJpeg){'VIGI_JPEG_FAST=1 '}else{''}
    $command=$prefix+'/usr/bin/vigi_camera_pl /dev/video0 1280 720 '+$Frames+' '+$cfg.fps+' '+$cfg.motion+'; printf ''CAMERA_PL_EXIT=%s\n'' "$?"'
    [IO.File]::WriteAllText($commandFile,$command+"`n",(New-Object Text.UTF8Encoding($false)))
    & (Join-Path $PSScriptRoot 'run_console.ps1') -CommandFile $commandFile -Port $Port -TimeoutSeconds 90 -Out $Out
    $checker=Join-Path $PSScriptRoot 'check_camera_pl_log.py'
    $flags=@()
    if($RequireRealtime){$flags+='--require-realtime'}
    & $python $checker $Out --out ($Out+'.summary.json') @flags
    exit $LASTEXITCODE
} finally {Pop-Location}
