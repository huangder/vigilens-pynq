# Local board shell helper. CommandFile must contain reviewed shell commands.
param([Parameter(Mandatory=$true)][string]$CommandFile,[string]$Port='COM11',[int]$TimeoutSeconds=30,[string]$Out='')
$ErrorActionPreference='Stop'
$sp=New-Object System.IO.Ports.SerialPort $Port,115200,'None',8,'One'
$sp.Encoding=[System.Text.Encoding]::UTF8
$sp.DtrEnable=$false; $sp.RtsEnable=$false
$transcript=New-Object Text.StringBuilder
if($Out){
    $Out=[IO.Path]::GetFullPath($Out)
    [IO.File]::WriteAllText($Out,'',(New-Object Text.UTF8Encoding($false)))
}
function Read-Reply([int]$milliseconds) {
    $until=[DateTime]::UtcNow.AddMilliseconds($milliseconds)
    $text=''
    while([DateTime]::UtcNow -lt $until) {
        $chunk=$sp.ReadExisting()
        if($chunk){$text+=$chunk}
        Start-Sleep -Milliseconds 50
    }
    [void]$transcript.Append($text)
    if($Out -and $text){[IO.File]::AppendAllText($Out,$text,(New-Object Text.UTF8Encoding($false)))}
    return $text
}
try {
    $commands=[IO.File]::ReadAllText((Resolve-Path -LiteralPath $CommandFile))
    $sp.Open(); $sp.Write("`r")
    $reply=Read-Reply 600
    if($reply -match 'login:') {$sp.Write("root`r"); $reply=Read-Reply 600}
    if($reply -match 'Password:') {$sp.Write("root`r"); $reply=Read-Reply 600}
    if($reply -notmatch '#') {throw 'Root shell prompt not received; no commands sent'}
    # Drain echoed output while sending; large bursts can overrun ttyPS0 when
    # the kernel prints PHY/USB messages. Retain early completion markers too.
    $result=''
    $wire=$commands.Replace("`r`n","`n").Replace("`n","`r") + "`rprintf '\nVIGI_COMMAND_DONE\n'`r"
    for($offset=0;$offset -lt $wire.Length;$offset+=64){
        $count=[Math]::Min(64,$wire.Length-$offset)
        $sp.Write($wire.Substring($offset,$count))
        $result+=Read-Reply 50
    }
    $until=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while([DateTime]::UtcNow -lt $until) {
        $result+=Read-Reply 100
        if($result -match '(?m)^VIGI_COMMAND_DONE\r?$'){break}
    }
    if($result -notmatch '(?m)^VIGI_COMMAND_DONE\r?$'){throw 'Board command timed out'}
} finally {
    if($sp.IsOpen){$sp.Close()}; $sp.Dispose()
    $text=$transcript.ToString()
    Write-Output $text
    if($Out){[IO.File]::WriteAllText([IO.Path]::GetFullPath($Out),$text,(New-Object Text.UTF8Encoding($false)))}
}
