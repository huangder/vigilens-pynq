param(
    [string]$Port = "COM11",
    [int]$Seconds = 180,
    [string]$Out = "D:/w7/cap.txt",
    [string]$UntilMarker = ""
)
$ErrorActionPreference = 'Stop'
$sp = New-Object System.IO.Ports.SerialPort $Port,115200,'None',8,'One'
$sp.DtrEnable = $false
$sp.RtsEnable = $false
$encoding = New-Object System.Text.UTF8Encoding($false)
$writer = $null
$failed = $false
try {
    $writer = New-Object System.IO.StreamWriter($Out,$false,$encoding)
    $writer.AutoFlush = $true
    $sp.Open()
    $writer.WriteLine("[[open ok $Port @ 115200]]")
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    $tail = ""
    while ([DateTime]::UtcNow -lt $deadline) {
        $chunk = $sp.ReadExisting()
        if ($chunk) {
            $writer.Write($chunk)
            $tail += $chunk
            if ($UntilMarker -and $tail.Contains($UntilMarker)) { break }
            if ($tail.Length -gt 4096) { $tail = $tail.Substring($tail.Length - 4096) }
        }
        Start-Sleep -Milliseconds 40
    }
} catch {
    $failed = $true
    if ($writer) { $writer.WriteLine("OPEN_OR_READ_FAIL: " + $_.Exception.Message) }
    Write-Error $_ -ErrorAction Continue
} finally {
    if ($sp.IsOpen) { $sp.Close() }
    $sp.Dispose()
    if ($writer) { $writer.WriteLine("`n[[capture end]]"); $writer.Dispose() }
}
if ($failed) { exit 1 }
