# Upload a reviewed local executable to the board's RAM filesystem over UART.
param(
    [Parameter(Mandatory=$true)][string]$File,
    [Parameter(Mandatory=$true)][string]$RemoteFile,
    [string]$Port='COM11'
)
$ErrorActionPreference='Stop'
if ($RemoteFile -notmatch '^/tmp/[A-Za-z0-9_.-]+$') { throw 'Destination must be a plain filename under /tmp/' }
$bytes=[IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $File))
$expected=(Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
$memory=New-Object IO.MemoryStream
$gzip=New-Object IO.Compression.GZipStream($memory,[IO.Compression.CompressionMode]::Compress,$true)
$gzip.Write($bytes,0,$bytes.Length); $gzip.Dispose()
$payload=[Convert]::ToBase64String($memory.ToArray()); $memory.Dispose()
$sp=New-Object IO.Ports.SerialPort $Port,115200,'None',8,'One'
$sp.DtrEnable=$false; $sp.RtsEnable=$false
function Read-Until([string]$pattern,[int]$seconds=10) {
    $until=[DateTime]::UtcNow.AddSeconds($seconds); $reply=''
    while([DateTime]::UtcNow -lt $until) {
        $reply+=$sp.ReadExisting()
        if($reply -match $pattern) { return $reply }
        Start-Sleep -Milliseconds 10
    }
    throw "Serial response missing: $pattern; tail: $($reply.Substring([Math]::Max(0,$reply.Length-500)))"
}
function Run-Checked([string]$command,[int]$number) {
    $marker="VIGI_UPLOAD_ACK_$number"
    $sp.Write("$command && printf '\n$marker\n'`r")
    $null=Read-Until "(?m)^$marker\r?`$"
}
try {
    $sp.Open(); $sp.Write("`r")
    $reply=Read-Until '(#|login:|Password:)'
    if($reply -match 'login:') {$sp.Write("root`r"); $reply=Read-Until '(#|Password:)'}
    if($reply -match 'Password:') {$sp.Write("root`r"); $reply=Read-Until '#'}
    if($reply -notmatch '#') {throw 'Root shell unavailable'}
    Run-Checked ": > $RemoteFile.b64" 0
    $number=1
    # Acknowledge every sub-kilobyte command, avoiding the tty input buffer limit.
    for($offset=0;$offset -lt $payload.Length;$offset+=512) {
        $part=$payload.Substring($offset,[Math]::Min(512,$payload.Length-$offset))
        Run-Checked "printf '%s' '$part' >> $RemoteFile.b64" $number
        $number++
    }
    Run-Checked "base64 -d $RemoteFile.b64 > $RemoteFile.gz" $number; $number++
    Run-Checked "gzip -dc $RemoteFile.gz > $RemoteFile" $number; $number++
    Run-Checked "chmod 700 $RemoteFile" $number
    $sp.Write("sha256sum $RemoteFile`r")
    $null=Read-Until "(?m)^$expected  $([regex]::Escape($RemoteFile))\r?`$"
    Write-Output "UPLOAD_PASS bytes=$($bytes.Length) sha256=$expected destination=$RemoteFile"
} finally {
    if($sp.IsOpen){$sp.Close()}; $sp.Dispose()
}
