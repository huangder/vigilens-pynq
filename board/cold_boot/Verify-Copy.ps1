param([string]$Root = (Join-Path $PSScriptRoot 'copy_to_sd'))
$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$resolved = (Resolve-Path -LiteralPath $Root).Path.TrimEnd([char[]]'\/')
$count = 0
foreach ($entry in $manifest.files.PSObject.Properties) {
    $relative = $entry.Name.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $target = [IO.Path]::GetFullPath((Join-Path $resolved $relative))
    if (-not $target.StartsWith($resolved + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Invalid manifest path: $relative"
    }
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Missing: $target" }
    if ((Get-Item -LiteralPath $target).Length -ne $entry.Value.bytes) { throw "Size mismatch: $target" }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value.sha256) {
        throw "SHA256 mismatch: $target"
    }
    $count++
}
Write-Output "COPY_CHECK_PASS files=$count release=$($manifest.release_id)"
Write-Output 'This checks files only. Physical SD power-on is a separate acceptance step.'
