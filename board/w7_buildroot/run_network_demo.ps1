# Local wired-link demo. No boot, PC NIC/firewall changes, or saved pixels.
# -PrepareOnly generates reviewed commands without opening serial or network.
param(
    [string]$BoardIp='169.254.73.29',
    [string]$PeerIp='169.254.73.28',
    [ValidatePattern('^COM[0-9]+$')][string]$Port='COM11',
    [ValidateRange(1024,65535)][int]$StreamPort=9000,
    [ValidateRange(1024,65535)][int]$ApiPort=8000,
    [ValidateRange(2,36000)][int]$Frames=300,
    [ValidateSet('raw','zlib-rgb-delta','jpeg')][string]$Codec='raw',
    [ValidateSet('default','rle','huffman')][string]$CompressionStrategy='default',
    [ValidateSet('serial','threaded')][string]$Pipeline='serial',
    [ValidateRange(1,2)][int]$DecodeWorkers=2,
    [ValidateSet('yield','spin')][string]$PlWait='yield',
    [ValidateSet('latest','sync')][string]$PreviewMode='latest',
    [ValidateRange(0,1000)][int]$AuditEvery=1,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')][string]$RunId='',
    [switch]$Set100Full,
    [switch]$PrepareOnly
)
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python=Join-Path $repo '.venv/Scripts/python.exe'
$utf8=New-Object Text.UTF8Encoding($false)
$apiBase='http://127.0.0.1:'+$ApiPort
$runDir=$null; $sessionLock=$null; $boardStarted=$false; $boardCollected=$false
$ownedRunDir=$false
$exitCode=1
$result=[ordered]@{pipeline_exit=$null; checker_exit=$null; board_probe_exit=$null; closed_loop_pass=$false; error=$null}
function Save-Text([string]$Path,[string]$Value){[IO.File]::WriteAllText($Path,$Value,$utf8)}
function Save-Result {if($runDir -and $ownedRunDir){Save-Text (Join-Path $runDir 'runner.summary.json') ($result|ConvertTo-Json -Depth 10)}}
function Native-Args([object[]]$Values){
    # Generated arguments are file paths/validated values; none ends in backslash.
    return (($Values|ForEach-Object{'"'+([string]$_).Replace('"','\"')+'"'}) -join ' ')
}
function Start-Hidden([string]$Exe,[object[]]$Arguments,[string]$Stem,[switch]$Wait){
    $options=@{FilePath=$Exe;ArgumentList=(Native-Args $Arguments);WorkingDirectory=$repo;WindowStyle='Hidden';PassThru=$true
        RedirectStandardOutput=($Stem+'.stdout.log');RedirectStandardError=($Stem+'.stderr.log')}
    if($Wait){$options.Wait=$true}
    return Start-Process @options
}
function Assert-Address([string]$Value){
    $address=$null
    if(-not [Net.IPAddress]::TryParse($Value,[ref]$address) -or
       $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
       $address.ToString() -ne $Value -or $address.GetAddressBytes()[0] -eq 0 -or
       $address.GetAddressBytes()[0] -eq 127 -or $address.GetAddressBytes()[0] -ge 224){
        throw "Expected a canonical unicast IPv4 address: $Value"
    }
}
function Get-Api {return Invoke-RestMethod -Uri ($apiBase+'/api/status') -TimeoutSec 2}
function Assert-Api($Status){
    if($Status.ok -ne $true -or $Status.source -ne 'ingest' -or
       $Status.mock_active -isnot [bool] -or $Status.mock_active -ne $false){
        throw 'Refusing API reuse: source=ingest and mock_active=false are required.'
    }
}
function Test-ApiPort {
    $tcp=New-Object Net.Sockets.TcpClient
    try{$task=$tcp.ConnectAsync('127.0.0.1',$ApiPort);if(-not $task.Wait(1500)){return $false};return $tcp.Connected}
    catch{return $false}finally{$tcp.Dispose()}
}
function Ensure-Api {
    if(Test-ApiPort){$status=Get-Api;Assert-Api $status;Write-Host "Reusing $apiBase";return $status}
    $proc=Start-Hidden $python @((Join-Path $repo 'backend/api.py'),'--no-mock','--host','127.0.0.1','--port',$ApiPort) (Join-Path $runDir 'api')
    $result.api_started_pid=$proc.Id
    $until=[DateTime]::UtcNow.AddSeconds(20)
    while([DateTime]::UtcNow -lt $until){
        if($proc.HasExited){throw 'API startup failed; see api.stderr.log.'}
        try{$status=Get-Api}catch{$status=$null}
        if($null -ne $status){Assert-Api $status;Write-Host "Started API PID=$($proc.Id); kept running.";return $status}
        Start-Sleep -Milliseconds 200
    }
    throw 'API startup timed out. No board command was sent.'
}
function Invoke-Serial([string]$Name,[int]$Seconds){
    $out=Join-Path $runDir ($Name+'.serial.log')
    $serialArgs=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'run_console.ps1'),
        '-CommandFile',(Join-Path $runDir ($Name+'.sh')),'-Port',$Port,'-TimeoutSeconds',$Seconds,'-Out',$out)
    $proc=Start-Hidden 'powershell.exe' $serialArgs ($out+'.helper') -Wait
    if($proc.ExitCode -ne 0){throw "Serial helper failed: $out.helper.stderr.log"}
    return [IO.File]::ReadAllText($out).Replace([string][char]13,'')
}
function Collect-Board {
    $text=Invoke-Serial 'collect' 90
    $match=[regex]::Match($text,'(?ms)^VIGI_BOARD_LOG_BEGIN\n(.*?)^VIGI_BOARD_LOG_END$')
    if(-not $match.Success){throw 'Complete board log markers not found.'}
    $body=$match.Groups[1].Value
    Save-Text (Join-Path $runDir 'board.log') $body
    $script:boardCollected=$true
    $probe=[regex]::Match($body,'(?m)^CAMERA_PL_EXIT=(\d+)$')
    if($probe.Success){$result.board_probe_exit=[int]$probe.Groups[1].Value}
}
Push-Location $repo
try{
    Assert-Address $BoardIp;Assert-Address $PeerIp
    if($BoardIp -eq $PeerIp -or (($BoardIp.Split('.')[0..1]-join '.') -ne ($PeerIp.Split('.')[0..1]-join '.'))){
        throw 'BoardIp and PeerIp must be distinct addresses on the same /16 wired subnet.'
    }
    if(-not (Test-Path -LiteralPath $python)){throw 'Project .venv Python is missing.'}
    if(-not $RunId){$RunId=(Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+[Guid]::NewGuid().ToString('N').Substring(0,6)}
    $runDir=Join-Path $repo ('metrics/logs/network_demo_'+$RunId)
    if(Test-Path -LiteralPath $runDir){throw "RunId already exists: $runDir"}
    $null=New-Item -ItemType Directory -Path $runDir
    $ownedRunDir=$true
    $result.run_id=$RunId;$result.board_ip=$BoardIp;$result.peer_ip=$PeerIp;$result.serial_port=$Port
    $result.api_url=$apiBase;$result.frames_requested=$Frames;$result.codec=$Codec;$result.compression_strategy=$CompressionStrategy;$result.prepared_only=[bool]$PrepareOnly
    $result.pipeline=$Pipeline;$result.audit_every=$AuditEvery;$result.decode_workers=if($Pipeline -eq 'serial'){1}else{$DecodeWorkers}
    $result.pl_wait=$PlWait
    $result.preview_mode=$PreviewMode
    $code="import json; from backend.config import load_config; c=load_config(); print(json.dumps(dict(fps=c['fps_nominal'],motion=c['motion_thresh_gray'])))"
    $configJson=& $python -c $code
    if($LASTEXITCODE -ne 0){throw 'Could not load config.'}
    $cfg=$configJson|ConvertFrom-Json
    if([double]$cfg.fps -ne [int]$cfg.fps -or [int]$cfg.fps -lt 1 -or [int]$cfg.fps -gt 120 -or
       [int]$cfg.motion -lt 0 -or [int]$cfg.motion -gt 255){throw 'Config rate/threshold is not representable by the board probe.'}
    $result.config_fps=[int]$cfg.fps;$result.config_motion_threshold=[int]$cfg.motion
    Save-Text (Join-Path $runDir 'config.json') ($cfg|ConvertTo-Json)
    $change=if($Set100Full){'/usr/bin/network_ctl eth0 --100-full || { echo VIGI_NETWORK_FAIL_ADVERTISEMENT; exit 12; }'}else{':'}
    $template=@'
sh -c '
if [ -n "$(pidof vigi_camera_pl 2>/dev/null)" ]; then echo VIGI_NETWORK_FAIL_ALREADY_RUNNING; exit 10; fi
if [ ! -c /dev/video0 ] || [ ! -x /usr/bin/vigi_camera_pl ]; then echo VIGI_NETWORK_FAIL_CAMERA_OR_BINARY; exit 11; fi
ifconfig eth0 __BOARD__ netmask 255.255.0.0 up || { echo VIGI_NETWORK_FAIL_IP; exit 12; }
__CHANGE__
i=0
while [ "$i" -lt 15 ]; do
 speed=$(cat /sys/class/net/eth0/speed 2>/dev/null)
 carrier=$(cat /sys/class/net/eth0/carrier 2>/dev/null)
 duplex=$(cat /sys/class/net/eth0/duplex 2>/dev/null)
 if [ "$speed" = 100 ] && [ "$carrier" = 1 ] && [ "$duplex" = full ]; then break; fi
 i=$((i+1)); sleep 1
done
if [ "$speed" != 100 ] || [ "$carrier" != 1 ] || [ "$duplex" != full ]; then echo VIGI_NETWORK_FAIL_LINK speed=$speed carrier=$carrier duplex=$duplex; exit 13; fi
ifconfig eth0
if ! ifconfig eth0 | grep -Fq "__BOARD__"; then echo VIGI_NETWORK_FAIL_IP_READBACK; exit 14; fi
echo VIGI_NETWORK_LINK speed=$speed carrier=$carrier duplex=$duplex peer=__PEER__
(
 env VIGI_STREAM_BIND=__BOARD__:__STREAM__ VIGI_STREAM_CODEC=__CODEC__ VIGI_STREAM_ZLIB_STRATEGY=__STRATEGY__ VIGI_JPEG_FAST=1 VIGI_VERIFY=__VERIFY__ VIGI_AUDIT_EVERY=__AUDIT__ VIGI_PIPELINE=__PIPELINE__ VIGI_DECODE_WORKERS=__WORKERS__ VIGI_PL_WAIT=__WAIT__ /usr/bin/vigi_camera_pl /dev/video0 1280 720 __FRAMES__ __FPS__ __MOTION__
 camera_rc=$?
 printf "CAMERA_PL_EXIT=%s\n" "$camera_rc"
 exit "$camera_rc"
) > /tmp/vigi_network.log 2>&1 < /dev/null &
child=$!
echo VIGI_BOARD_PID=$child
i=0
while [ "$i" -lt 20 ]; do
 if grep -q "^BOARD_STREAM_LISTEN " /tmp/vigi_network.log; then echo VIGI_NETWORK_READY; exit 0; fi
 if ! kill -0 "$child" 2>/dev/null; then cat /tmp/vigi_network.log; echo VIGI_NETWORK_FAIL_PROBE_EXITED; exit 15; fi
 i=$((i+1)); sleep 1
done
cat /tmp/vigi_network.log
echo VIGI_NETWORK_FAIL_LISTEN_TIMEOUT
exit 16
'
'@
    $launch=$template.Replace('__BOARD__',$BoardIp).Replace('__PEER__',$PeerIp).Replace('__STREAM__',[string]$StreamPort).Replace('__FRAMES__',[string]$Frames).Replace('__FPS__',[string][int]$cfg.fps).Replace('__MOTION__',[string][int]$cfg.motion).Replace('__CHANGE__',$change).Replace('__CODEC__',$Codec).Replace('__STRATEGY__',$CompressionStrategy).Replace('__VERIFY__','0').Replace('__AUDIT__',[string]$AuditEvery).Replace('__PIPELINE__',$Pipeline).Replace('__WORKERS__',[string]$result.decode_workers).Replace('__WAIT__',$PlWait)
    Save-Text (Join-Path $runDir 'launch.sh') ($launch+[char]10)
    $collect=@'
sh -c '
i=0
while [ "$i" -lt 75 ] && ! grep -q "^CAMERA_PL_EXIT=" /tmp/vigi_network.log; do i=$((i+1)); sleep 1; done
printf "\nVIGI_BOARD_LOG_BEGIN\n"
cat /tmp/vigi_network.log
printf "\nVIGI_BOARD_LOG_END\n"
'
'@
    Save-Text (Join-Path $runDir 'collect.sh') ($collect+[char]10)
    Save-Result
    if($PrepareOnly){
        Write-Host "Prepared for review: $runDir; no serial, API, or board connection performed."
        $exitCode=0
    }else{
        $sessionLock=[IO.File]::Open((Join-Path $repo 'metrics/logs/_network_demo.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        $localIps=[Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()|ForEach-Object{$_.GetIPProperties().UnicastAddresses}|ForEach-Object{$_.Address.ToString()}
        if($localIps -notcontains $PeerIp){throw "PeerIp $PeerIp is not assigned to this PC. No PC network settings were changed."}
        $status=Ensure-Api
        $offset=0L
        if([long]$status.published -gt 0){
            $offset=[long][Math]::Max([double]$status.frame.frame_id+1,[Math]::Ceiling([double]$status.frame.ts*[double]$cfg.fps)+1)
        }
        $result.frame_id_offset=$offset
        Write-Host "Starting board RAM session: $BoardIp, $Frames camera buffers; waiting for LISTEN."
        $reply=Invoke-Serial 'launch' 45
        $pidMatch=[regex]::Match($reply,'(?m)^VIGI_BOARD_PID=(\d+)$')
        if($pidMatch.Success){$result.board_pid=[int]$pidMatch.Groups[1].Value;$boardStarted=$true}
        if($reply -notmatch '(?m)^VIGI_NETWORK_READY$' -or $reply -match '(?m)^VIGI_NETWORK_FAIL_'){
            throw 'Board preflight/listen failed; no PC stream connection attempted. Inspect launch.serial.log.'
        }
        Write-Host 'LISTEN confirmed. Connecting the PC pipeline once; no reconnect or frame padding.'
        $pipelineArgs=@((Join-Path $repo 'backend/run_pipeline.py'),'--source',('board:'+$BoardIp+':'+$StreamPort),
            '--width','1280','--height','720','--rotate','0','--frame-id-offset',$offset,
            '--post',($apiBase+'/api/ingest'),'--preview-post',($apiBase+'/api/preview'),
            '--json',(Join-Path $runDir 'last.json'),'--jsonl',(Join-Path $runDir 'stream.jsonl'),
            '--csv',(Join-Path $runDir 'metrics.csv'),'--summary',(Join-Path $runDir 'pipeline.summary.json'))
        if($PreviewMode -eq 'latest'){$pipelineArgs+=@('--preview-async')}
        $pipelineProcess=Start-Hidden $python $pipelineArgs (Join-Path $runDir 'pipeline') -Wait
        $result.pipeline_exit=$pipelineProcess.ExitCode
        Collect-Board
        & $python (Join-Path $PSScriptRoot 'check_network_run.py') (Join-Path $runDir 'board.log') (Join-Path $runDir 'pipeline.summary.json') (Join-Path $runDir 'stream.jsonl') --out (Join-Path $runDir 'network.summary.json')
        $result.checker_exit=$LASTEXITCODE
        if(Test-Path -LiteralPath (Join-Path $runDir 'network.summary.json')){
            $checked=Get-Content -LiteralPath (Join-Path $runDir 'network.summary.json') -Raw|ConvertFrom-Json
            $result.native_camera_mode_verified=$checked.hardware.native_camera_mode_verified
            $result.realtime_target_met=$checked.hardware.realtime_target_met
            $result.selected_fps=$checked.hardware.selected_fps
            $result.hardware_compare_pass=$checked.hardware.hardware_compare_pass
            $result.audit_frames=$checked.hardware.audit_frames
            $result.pipeline_throughput_fps=$checked.pipeline_throughput_fps
            $result.processing_target_met=$checked.processing_target_met
            $result.face_visible_frames=$checked.face_visible_frames
        }
        Assert-Api (Get-Api)
        $result.closed_loop_pass=($result.pipeline_exit -eq 0 -and $result.checker_exit -eq 0)
        $exitCode=if($result.closed_loop_pass){0}else{1}
        Write-Host "Closed-loop pass=$($result.closed_loop_pass); PC exit=$($result.pipeline_exit); probe exit=$($result.board_probe_exit); checker exit=$($result.checker_exit)"
        Write-Host "Native30=$($result.native_camera_mode_verified); realtime30=$($result.realtime_target_met); selected fps=$($result.selected_fps)"
        Write-Host "Every-frame comparison=$($result.hardware_compare_pass); audited frames=$($result.audit_frames)"
        Write-Host "PC throughput=$($result.pipeline_throughput_fps); processing target met=$($result.processing_target_met)"
        Write-Host 'Probe exit 2 preserves the unmet native 30fps mode. This run does not certify fatigue/vital accuracy.'
        Write-Host ("Dashboard: "+$apiBase+"/ ; connect WebSocket ws://127.0.0.1:"+$ApiPort+"/ws in the page.")
    }
}catch{
    $result.error=$_.Exception.Message
    Write-Host ('[FAIL] '+$result.error)
    if($boardStarted -and -not $boardCollected){try{Collect-Board}catch{$result.board_collect_error=$_.Exception.Message}}
    $exitCode=1
}finally{
    Save-Result
    if($sessionLock){$sessionLock.Dispose()}
    if($runDir){Write-Host "Artifacts: $runDir"}
    Pop-Location
}
exit $exitCode
