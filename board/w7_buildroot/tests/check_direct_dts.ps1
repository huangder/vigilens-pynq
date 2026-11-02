# Strict artifact checks: fail on command errors, absent output, or wrong profile.
param([string]$Out='')
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$repo=Split-Path -Parent (Split-Path -Parent $root)
if(-not $Out){$Out=Join-Path $repo '.tmp/direct_loop/system.dtb'}
$Out=[IO.Path]::GetFullPath($Out)
& (Join-Path $root 'build_dtb.ps1') -UserDts (Join-Path $root 'direct_loop.dtsi') -Out $Out
if($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Out)){throw 'DTB build failed'}
$back=$Out+'.checked.dts'
$previous=$ErrorActionPreference
$ErrorActionPreference='Continue'
$diagnostics=& 'D:\Xilinx\2026.1\Vitis\bin\dtc.exe' -I dtb -O dts -o $back $Out 2>&1
$code=$LASTEXITCODE
$ErrorActionPreference=$previous
if($code -ne 0 -or -not (Test-Path -LiteralPath $back)){throw "dtc failed: $diagnostics"}
$text=[IO.File]::ReadAllText($back)
if($text -notmatch 'vigilens-dma@12000000 \{\s*reg = <0x12000000 0x1000000>;\s*no-map;'){
    throw 'Missing exact 16MiB no-map carveout'
}
if($text -notmatch 'vigilens,pl-profile = "720p-3of8"'){throw 'Missing profile'}
$node=[regex]::Match($text,'dma@41e00000 \{(?s).*?status = "([a-z]+)"')
if($node.Groups[1].Value -ne 'disabled'){throw 'DMA kernel ownership must be disabled'}
'DIRECT_LOOP_DTB_PASS carveout=0x12000000+16MiB no-map=1 kernel_dma_owner=disabled'
