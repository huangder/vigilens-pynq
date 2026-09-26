# _omv_stream_recv.ps1 -- receive the OpenMV JPEG stream over COM10 and hand it to the web app.
#
# WHY THIS EXISTS
#   OpenMV can act as a UVC camera only by flashing a separate UVC firmware, and the only
#   obtainable one (v4.6.20) bricked this camera once.  But the camera's MicroPython side can
#   already push JPEG frames over the USB virtual serial port (openmv_stream.py MODE="usb_jpeg"),
#   and the frame protocol is implemented once in board/openmv/vigilens_link.py.
#   pyserial is NOT installed and pip has no network here, so the receiver is implemented on
#   .NET's SerialPort instead.
#
# WIRE FORMAT (little-endian, from vigilens_link.py):
#   0  2  magic 0xA55A   (on the wire: 5A A5)
#   2  1  type           0x02 = JPEG
#   3  1  flags
#   4  4  frame_id       uint32, starts at 1
#   8  4  payload_len
#   12 N  payload
#   12+N 2 crc16  CRC-16/CCITT-FALSE over bytes [0, 12+N)
#
# ASCII-ONLY ON PURPOSE (PowerShell 5.1 decodes BOM-less .ps1 as GBK).
#
# USAGE
#   . .\metrics\logs\_omv_stream_recv.ps1
#   Send-OMVStreamer                          # load vigilens_link + openmv_stream, start streaming
#   Receive-OMVFrames -Seconds 10 -ApiBase http://127.0.0.1:8011 -SaveDir metrics\logs\_omv_frames
#   Stop-OMVStreamer                          # Ctrl-C the camera

param([switch]$SelfTest)

# ---------------------------------------------------------------------------
# Offline self-test (no hardware, no serial port).  Run:
#   pwsh -NoProfile -File metrics\scripts\omv_stream_bridge.ps1 -SelfTest
# ---------------------------------------------------------------------------

function Get-OMVFramesFromBytes {
    <#
      Pure function: pull every valid frame out of a byte blob
      (magic 5A A5 + 12-byte header + payload + CRC-16).

      The live receiver and the self-test share THIS implementation, so a
      regression here is caught offline instead of showing up on hardware as
      "0 frames + hundreds of CRC errors".

      Returns @{ Frames = @(<frame>...); CrcBad = <int> }, where each frame is
      @{ Type; FrameId; Payload } with Payload a byte[].
    #>
    param(
        # NOT Mandatory: a burst can legitimately come back empty (nothing arrived in
        # that second), and a mandatory [byte[]] REJECTS an empty array with a
        # parameter-binding exception -- which killed the live receiver on its first
        # quiet burst.  Empty input must be "0 frames", not an error.
        [byte[]]$Data = @(),
        [int]$MaxFrames = 0
    )
    $out = New-Object System.Collections.Generic.List[object]
    if (-not $Data -or $Data.Length -lt 14) { return [pscustomobject]@{ Frames = $out; CrcBad = 0 } }
    $pos = 0; $crcBad = 0
    while ($pos -lt $Data.Length - 14) {
        if (-not ($Data[$pos] -eq 0x5A -and $Data[$pos + 1] -eq 0xA5)) { $pos++; continue }
        $type = $Data[$pos + 2]
        $fid = [long]$Data[$pos + 4] -bor ([long]$Data[$pos + 5] -shl 8) -bor ([long]$Data[$pos + 6] -shl 16) -bor ([long]$Data[$pos + 7] -shl 24)
        $plen = [long]$Data[$pos + 8] -bor ([long]$Data[$pos + 9] -shl 8) -bor ([long]$Data[$pos + 10] -shl 16) -bor ([long]$Data[$pos + 11] -shl 24)
        if ($plen -le 0 -or $plen -gt 1048576 -or ($pos + 12 + $plen + 2) -gt $Data.Length) { $pos++; continue }
        $calc = Get-Crc16Ccitt -Data $Data -Offset $pos -Count (12 + $plen)
        # [int] cast BEFORE -shl: PowerShell keeps the left operand's type, so
        # [byte]0xB3 -shl 8 truncates back to a byte (0x00).  Bit us once.
        $want = [int]$Data[$pos + 12 + $plen] -bor ([int]$Data[$pos + 13 + $plen] -shl 8)
        if ($calc -ne $want) { $crcBad++; $pos++; continue }
        $payload = New-Object byte[] $plen
        [Array]::Copy($Data, $pos + 12, $payload, 0, $plen)
        $out.Add([pscustomobject]@{ Type = $type; FrameId = $fid; Payload = $payload })
        $pos += 12 + $plen + 2
        if ($MaxFrames -gt 0 -and $out.Count -ge $MaxFrames) { break }
    }
    return [pscustomobject]@{ Frames = $out; CrcBad = $crcBad }
}

function Invoke-OMVBridgeSelfTest {
    <#
      Two offline checks:

        1. CRC-16/CCITT-FALSE check value must be 0x29B1 (fixed by the protocol doc).
        2. Build frames with CPython's board/openmv/vigilens_link.py -- on purpose
           preceded by junk bytes, to prove resynchronisation -- then decode them
           with this script's parser and compare the payload byte by byte.

      Why (2) is not optional: this script once truncated CRCs because of
      [byte] -shl 8, and on real hardware that looked like "0 frames + hundreds
      of CRC errors".  A self-test that only checked (1) could not see it.
    #>
    $fails = New-Object System.Collections.Generic.List[string]

    # 1. CRC check value
    $ck = Get-Crc16Ccitt -Data ([System.Text.Encoding]::ASCII.GetBytes("123456789"))
    if ($ck -ne 0x29B1) { $fails.Add(("CRC check value wrong: 0x{0:X4} != 0x29B1" -f $ck)) }

    # 2. encode with CPython -> decode here -> compare
    $root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $py = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("omv_selftest_{0}.bin" -f ([guid]::NewGuid().ToString("N")))
    try {
        $gen = @"
import sys
sys.path.insert(0, r'board/openmv')
import vigilens_link as vl
payload = bytes(range(256)) * 4        # 1024 B: every byte value appears
blob = (b'GARBAGE-BEFORE-FRAME\x00\xff\x5a\xa5\x00'      # fake magic on purpose
        + vl.encode(vl.TYPE_JPEG, 4242, payload)
        + vl.encode(vl.TYPE_STATS, 4243, b'xy'))
open(r'$tmp', 'wb').write(blob)
"@
        $gen | & $py - 2>&1 | Out-Null
        if (-not (Test-Path $tmp)) {
            $fails.Add("encoder failed: CPython wrote no $tmp (can vigilens_link.py be imported?)")
        } else {
            $res = Get-OMVFramesFromBytes -Data ([System.IO.File]::ReadAllBytes($tmp))
            $fr = $res.Frames
            if ($fr.Count -ne 2) {
                $fails.Add(("decoded {0} frames, expected 2 (resync or CRC accept is wrong)" -f $fr.Count))
            } else {
                if ($fr[0].Type -ne 0x02) { $fails.Add(("frame 1 type wrong: {0} != 2" -f $fr[0].Type)) }
                if ($fr[0].FrameId -ne 4242) { $fails.Add(("frame 1 frame_id wrong: {0} != 4242" -f $fr[0].FrameId)) }
                if ($fr[0].Payload.Length -ne 1024) {
                    $fails.Add(("frame 1 payload length wrong: {0} != 1024" -f $fr[0].Payload.Length))
                } else {
                    $badBytes = 0
                    for ($i = 0; $i -lt 1024; $i++) { if ($fr[0].Payload[$i] -ne ($i -band 0xFF)) { $badBytes++ } }
                    if ($badBytes -ne 0) { $fails.Add(("frame 1 payload differs in {0} bytes (byte-compare failed)" -f $badBytes)) }
                }
                if ($fr[1].Type -ne 0x01) { $fails.Add(("frame 2 type wrong: {0} != 1" -f $fr[1].Type)) }
                if ($fr[1].FrameId -ne 4243) { $fails.Add(("frame 2 frame_id wrong: {0} != 4243" -f $fr[1].FrameId)) }
            }
        }
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }

    # 3. empty / garbage input must be "0 frames", not an exception.
    #    Regression guard: a burst with nothing in it is NORMAL on a live port, and a
    #    mandatory [byte[]] parameter used to throw on the empty array -- which took the
    #    whole receiver down on its first quiet burst.
    #    (Built through a List: an empty array inside @(...) would be flattened away and
    #    the most important case -- the empty one -- would silently not run.)
    $badCases = New-Object System.Collections.Generic.List[object]
    $badCases.Add((New-Object byte[] 0))
    $badCases.Add([byte[]](1, 2, 3))
    $badCases.Add([byte[]](0x5A, 0xA5, 0x02))
    foreach ($bad in $badCases) {
        try {
            $r0 = Get-OMVFramesFromBytes -Data $bad
            if ($r0.Frames.Count -ne 0) { $fails.Add(("bad input produced {0} frames, expected 0" -f $r0.Frames.Count)) }
        } catch {
            $fails.Add("Get-OMVFramesFromBytes threw on empty/short input: $($_.Exception.Message)")
        }
    }

    if ($fails.Count -eq 0) {
        return "RESULT: PASS  (CRC check value 0x29B1 + encode/decode byte compare + empty-input guard)"
    }
    return ("RESULT: FAIL`n  - " + ($fails -join "`n  - "))
}

$script:OMV_PORT = 'COM10'

function Get-OMVPort {
    param([string]$Port = $script:OMV_PORT)
    $p = New-Object System.IO.Ports.SerialPort($Port, 115200, 'None', 8, 'One')
    $p.Encoding = [System.Text.Encoding]::GetEncoding(28591)   # latin-1: byte-preserving
    $p.ReadTimeout = 300
    $p.WriteTimeout = 5000
    $p.DtrEnable = $true
    $p.RtsEnable = $true
    $p.ReadBufferSize = 1MB
    $p.Open()
    return $p
}

function Get-Crc16Ccitt {
    # CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflect, no xorout.
    # Check: crc16_ccitt(b"123456789") == 0x29B1
    param([byte[]]$Data, [int]$Offset = 0, [int]$Count = -1)
    if ($Count -lt 0) { $Count = $Data.Length - $Offset }
    $crc = 0xFFFF
    for ($i = $Offset; $i -lt $Offset + $Count; $i++) {
        $crc = $crc -bxor ([int]$Data[$i] -shl 8)
        for ($b = 0; $b -lt 8; $b++) {
            if ($crc -band 0x8000) { $crc = (($crc -shl 1) -bxor 0x1021) -band 0xFFFF }
            else { $crc = ($crc -shl 1) -band 0xFFFF }
        }
    }
    return $crc
}

function Send-OMVStreamer {
    <#
      Load vigilens_link.py into sys.modules (it must be importable on the camera) and then
      exec openmv_stream.py with MODE="usb_jpeg".  Both sources are shipped as base64 chunks so
      that no file has to be written to the camera's tiny FAT.
    #>
    param(
        [string]$LinkPy   = 'board\openmv\vigilens_link.py',
        [string]$StreamPy = 'board\openmv\openmv_stream.py',
        [string]$Mode = 'usb_jpeg',
        [string]$Framesize = 'QVGA',
        [int]$Quality = 80
    )
    # The link module USED to be shipped as base64 and injected into sys.modules through a
    # fake class instance.  That injection does not work on MicroPython:
    #   exec(source, _m.__dict__)  ->  dies at the module's first `import` with a bare
    #   "TypeError:" -- and because the result was never checked, the half-built _M was
    #   registered as sys.modules["vigilens_link"] anyway.  Every later `import` then got
    #   the corpse, and openmv_stream died on `link.TYPE_JPEG`.
    # Symptom on the wire: camera alive, script "started", ZERO bytes.  See docs/16 BUG-026.
    # The camera's /flash already carries vigilens_link.py, so just import it properly.
    $sb = [System.IO.File]::ReadAllBytes((Resolve-Path $StreamPy))
    $s64 = [Convert]::ToBase64String($sb)

    $lines = New-Object System.Collections.Generic.List[string]
    # Each import is its own line so a failure names itself.
    $lines.Add('import sys')
    $lines.Add('import binascii')
    $lines.Add('import gc')
    $lines.Add('import pyb')
    $lines.Add('print("PYB_USB_VCP", hasattr(pyb, "USB_VCP"), hasattr(pyb, "LED"))')
    $lines.Add('if "/flash" not in sys.path: sys.path.append("/flash")')
    $lines.Add('import vigilens_link as _m')
    $lines.Add('print("LINK_OK", _m.PROTOCOL_VERSION, _m.HDR_LEN, _m.TYPE_JPEG)')
    # Fail on the camera BEFORE the stream starts, so the transcript says why.
    $lines.Add('assert (_m.PROTOCOL_VERSION, _m.HDR_LEN, _m.TYPE_JPEG) == (1, 12, 2), "LINK_BAD"')
    $lines.Add('_S=bytearray()')
    for ($i = 0; $i -lt $s64.Length; $i += 200) {
        $lines.Add("_S.extend(binascii.a2b_base64('" + $s64.Substring($i, [Math]::Min(200, $s64.Length - $i)) + "'))")
    }
    $lines.Add('print("LEN", len(_S))')
    $lines.Add('_src=_S.decode()')
    $lines.Add('_src=_src.replace(''MODE = "probe"'', ''MODE = "' + $Mode + '"'')')
    $lines.Add('_src=_src.replace(''STREAM_FRAMESIZE = "QVGA"'', ''STREAM_FRAMESIZE = "' + $Framesize + '"'')')
    $lines.Add('_src=_src.replace(''JPEG_QUALITY = 90'', ''JPEG_QUALITY = ' + $Quality + ''')')
    $lines.Add('print("CAM_SEES", [x for x in _src.split(chr(10)) if x.startswith("MODE = ") or x.startswith("STREAM_FRAMESIZE") or x.startswith("JPEG_QUALITY")])')
    $lines.Add('del _S')
    $lines.Add('gc.collect()')
    $lines.Add('print("MEM", gc.mem_free())')
    # Compile FIRST, then drop the transferred source string and collect, BEFORE the loop.
    # img.compress() needs one large CONTIGUOUS buffer; keeping the ~16 KB source string
    # alive while the stream loop allocates a JPEG per frame is enough to make it raise
    # "OSError: Compression Failed!" after a while -- see docs/16 BUG-027.
    $lines.Add('_code=compile(_src, "<omv_stream>", "exec")')
    $lines.Add('del _src')
    $lines.Add('gc.collect()')
    $lines.Add('print("MEM2", gc.mem_free())')
    $lines.Add('exec(_code)')

    $p = $null
    try { $p = Get-OMVPort } catch { return "PORT_ERROR: $($_.Exception.Message)" }
    $acc = New-Object System.Text.StringBuilder
    try {
        Start-Sleep -Milliseconds 250
        $p.DiscardInBuffer()
        $p.Write([char]3); Start-Sleep -Milliseconds 200
        # Soft reset (Ctrl-D) FIRST: a previous failed run can leave a POISONED
        # sys.modules["vigilens_link"], and then even a perfectly good `import` hands back
        # the corpse.  Ctrl-D clears sys.modules; /flash/main.py is renamed to main_off.py
        # so the reset cannot start anything on its own.
        $p.Write([char]4); Start-Sleep -Milliseconds 1500
        $p.DiscardInBuffer()
        $p.Write([char]3); Start-Sleep -Milliseconds 200
        $p.Write([char]2); Start-Sleep -Milliseconds 200
        $p.DiscardInBuffer()
        # handshake: confirm the friendly REPL is alive BEFORE sending the payload,
        # otherwise the first lines get swallowed and every later line fails.
        $hs = $false
        for ($try = 1; $try -le 3 -and -not $hs; $try++) {
            $p.Write("print('HS_READY')`r`n")
            Start-Sleep -Milliseconds 700
            $h = $p.ReadExisting()
            [void]$acc.Append($h)
            if ($h -match 'HS_READY') { $hs = $true }
        }
        if (-not $hs) {
            $p.Close(); $p.Dispose()
            return "NO_REPL: camera did not answer print('HS_READY')`n$($acc.ToString())"
        }
        Start-Sleep -Milliseconds 200
        for ($k = 0; $k -lt $lines.Count; $k++) {
            $p.Write($lines[$k] + "`r`n")
            $isLast = ($k -eq $lines.Count - 1)
            $limit = if ($isLast) { 4000 } else { 6000 }
            $deadline = (Get-Date).AddMilliseconds($limit)
            $buf = ''
            while ((Get-Date) -lt $deadline) {
                try { $s = $p.ReadExisting(); if ($s) { $buf += $s } } catch { }
                # if the REPL fell into continuation mode ("..."), send a blank line to close it
                if ($buf -match '\.\.\.\s*$' -and -not $isLast) {
                    $p.Write("`r`n"); Start-Sleep -Milliseconds 150
                    [void]$acc.Append($buf); $buf = ''
                    continue
                }
                # the last line starts an infinite loop -> no prompt will come back
                if (-not $isLast -and $buf -match '>>>\s*$') { break }
                if ($isLast -and $buf -match 'stream|JPEG|fps') { break }
                Start-Sleep -Milliseconds 15
            }
            [void]$acc.Append($buf)
        }
    } finally {
        $p.Close(); $p.Dispose()
    }
    # Never let a broken link module look like a running stream: that failure mode cost a
    # whole afternoon ("camera alive, script started, zero bytes").  See docs/16 BUG-026.
    if ($acc.ToString() -notmatch 'LINK_OK 1 12 2') {
        return "LINK_FAIL: camera could not import vigilens_link from /flash (expected LINK_OK 1 12 2)`n$($acc.ToString())"
    }
    return $acc.ToString()
}

function Receive-OMVFrames {
    <#
      !!! DO NOT USE THIS ON LIVE DATA -- KEPT ONLY AS A RECORD !!!

      This is the incremental List[byte] + RemoveRange parser.  It reports CRC failures on
      live serial data even though the very same bytes parse cleanly offline, and it was
      replaced by Send-OMVFramesToApi (one burst -> Get-OMVFramesFromBytes -> POST/loop),
      which is the path that has actually delivered frames from the camera.

      Parse the frame stream, print a summary, optionally POST each JPEG to the web app's
      bypass endpoint (POST /api/frame) and/or save frames to disk for inspection.
      Stops after -Seconds or -MaxFrames, then sends Ctrl-C to the camera.
    #>
    param(
        [int]$Seconds = 10,
        [int]$MaxFrames = 0,
        [string]$ApiBase = '',
        [string]$SaveDir = '',
        [int]$SaveMax = 3
    )
    $p = $null
    try { $p = Get-OMVPort } catch { return "PORT_ERROR: $($_.Exception.Message)" }
    $buf = New-Object System.Collections.Generic.List[byte]
    $frames = 0; $crcBad = 0; $resync = 0; $saved = 0; $posted = 0
    $sizes = New-Object System.Collections.Generic.List[int]
    $ids = New-Object System.Collections.Generic.List[long]
    $t0 = Get-Date
    if ($SaveDir) { New-Item -ItemType Directory -Force -Path $SaveDir | Out-Null }
    $http = $null
    if ($ApiBase) { $http = $true }   # posting uses HttpWebRequest (always available in PS 5.1)
    try {
        $p.DiscardInBuffer()
        $chunk = New-Object byte[] 65536
        while ($true) {
            if (((Get-Date) - $t0).TotalSeconds -ge $Seconds) { break }
            if ($MaxFrames -gt 0 -and $frames -ge $MaxFrames) { break }
            $n = 0
            try { $n = $p.Read($chunk, 0, $chunk.Length) } catch { $n = 0 }
            if ($n -gt 0) {
                for ($i = 0; $i -lt $n; $i++) { $buf.Add($chunk[$i]) }
            } else { Start-Sleep -Milliseconds 5; continue }

            # keep the buffer from growing without bound
            if ($buf.Count -gt (1 -shl 21)) { $buf.RemoveRange(0, $buf.Count - (1 -shl 19)); $resync++ }

            while ($true) {
                if ($buf.Count -lt 14) { break }
                # resync on magic 5A A5 (magic is 0xA55A stored little-endian)
                if (-not ($buf[0] -eq 0x5A -and $buf[1] -eq 0xA5)) {
                    $idx = -1
                    for ($j = 1; $j -lt $buf.Count - 1; $j++) {
                        if ($buf[$j] -eq 0x5A -and $buf[$j + 1] -eq 0xA5) { $idx = $j; break }
                    }
                    if ($idx -lt 0) { $buf.RemoveRange(0, $buf.Count - 1); $resync++; break }
                    $buf.RemoveRange(0, $idx); $resync++
                    if ($buf.Count -lt 14) { break }
                }
                $type = $buf[2]
                # frame_id / payload_len are uint32: widen to [long] BEFORE any cast.
                # (PowerShell 5.1's [int] is Int32, so [int](uint32 value) throws.)
                $fid = [long]$buf[4] -bor ([long]$buf[5] -shl 8) -bor ([long]$buf[6] -shl 16) -bor ([long]$buf[7] -shl 24)
                $plen = [long]$buf[8] -bor ([long]$buf[9] -shl 8) -bor ([long]$buf[10] -shl 16) -bor ([long]$buf[11] -shl 24)
                $total = 12 + $plen + 2
                if ($plen -lt 0 -or $plen -gt 1048576) { $buf.RemoveRange(0, 2); $resync++; continue }
                if ($buf.Count -lt $total) { break }   # need more bytes
                $arr = $buf.ToArray()
                $calc = Get-Crc16Ccitt -Data $arr -Offset 0 -Count (12 + $plen)
                # [int] cast BEFORE -shl: PowerShell keeps the left operand's type, so
                # [byte]0xB3 -shl 8 truncates back to a byte (0x00).  Bit us once (docs/16).
                $want = [int]$arr[12 + $plen] -bor ([int]$arr[13 + $plen] -shl 8)
                if ($calc -ne $want) {
                    $crcBad++
                    $buf.RemoveRange(0, 2); $resync++      # bad frame -> resync
                    continue
                }
                if ($type -eq 0x02) {
                    $frames++
                    $sizes.Add($plen)
                    $ids.Add($fid)
                    if ($SaveDir -and $saved -lt $SaveMax) {
                        $jpg = New-Object byte[] $plen
                        [Array]::Copy($arr, 12, $jpg, 0, $plen)
                        $saved++
                        [System.IO.File]::WriteAllBytes((Join-Path $SaveDir ("frame_%04d.jpg" -f $saved)), $jpg)
                    }
                    if ($http) {
                        $jpg = New-Object byte[] $plen
                        [Array]::Copy($arr, 12, $jpg, 0, $plen)
                        try {
                            $req = [System.Net.HttpWebRequest]::Create("$ApiBase/api/frame")
                            $req.Method = 'POST'
                            $req.ContentType = 'image/jpeg'
                            $req.ContentLength = $jpg.Length
                            $req.Timeout = 5000
                            $rs = $req.GetRequestStream()
                            $rs.Write($jpg, 0, $jpg.Length)
                            $rs.Close()
                            $resp = $req.GetResponse()
                            $resp.Close()
                            $posted++
                        } catch { }
                    }
                }
                $buf.RemoveRange(0, $total)
            }
        }
    } finally {
        $p.Write([char]3)                    # Ctrl-C: stop the camera-side loop
        Start-Sleep -Milliseconds 300
        $p.Close(); $p.Dispose()
    }
    $secs = ((Get-Date) - $t0).TotalSeconds
    $out = @()
    $out += "frames=$frames  seconds=$([math]::Round($secs,2))  fps=$([math]::Round($frames/$secs,2))"
    $out += "crc_bad=$crcBad  resync=$resync  saved=$saved  posted=$posted"
    if ($sizes.Count -gt 0) {
        $out += "payload bytes: min=$((($sizes | Measure-Object -Minimum).Minimum)) max=$((($sizes | Measure-Object -Maximum).Maximum)) mean=$([math]::Round((($sizes | Measure-Object -Average).Average),1))"
        $out += "frame_id: first=$($ids[0]) last=$($ids[$ids.Count-1])"
    }
    return ($out -join "`n")
}

function Stop-OMVStreamer {
    param([string]$Port = $script:OMV_PORT)
    try {
        $p = Get-OMVPort -Port $Port
        $p.Write([char]3); Start-Sleep -Milliseconds 300
        $r = $p.ReadExisting()
        $p.Close(); $p.Dispose()
        return "stopped`n$r"
    } catch { return "PORT_ERROR: $($_.Exception.Message)" }
}

function Send-OMVFramesToApi {
    <#
      Robust receiver: keep the port open, grab one burst, parse it with plain array
      indexing (the same logic that was verified offline against a captured stream),
      POST every JPEG to the web app, then repeat.

      Why not the incremental List/RemoveRange parser: it produced CRC failures on live
      data even though the identical bytes parsed cleanly offline, so this version uses
      only the offline-verified code path.
    #>
    param(
        [int]$TotalSeconds = 12,
        [double]$BurstSeconds = 1.0,
        [string]$ApiBase = '',
        [string]$SaveDir = '',
        [int]$SaveMax = 3
    )
    $p = $null
    try { $p = Get-OMVPort } catch { return "PORT_ERROR: $($_.Exception.Message)" }
    if ($SaveDir) { New-Item -ItemType Directory -Force -Path $SaveDir | Out-Null }
    $frames = 0; $crcBad = 0; $posted = 0; $saved = 0; $bursts = 0
    $sizes = New-Object System.Collections.Generic.List[int]
    $ids = New-Object System.Collections.Generic.List[long]
    $chunk = New-Object byte[] 65536
    $all = [Diagnostics.Stopwatch]::StartNew()
    try {
        $p.DiscardInBuffer()
        while ($all.Elapsed.TotalSeconds -lt $TotalSeconds) {
            $bursts++
            $ms = New-Object System.IO.MemoryStream
            $bs = [Diagnostics.Stopwatch]::StartNew()
            while ($bs.Elapsed.TotalSeconds -lt $BurstSeconds) {
                $n = 0
                try { $n = $p.Read($chunk, 0, $chunk.Length) } catch { $n = 0 }
                if ($n -gt 0) { $ms.Write($chunk, 0, $n) }
            }
            $data = $ms.ToArray(); $ms.Dispose()
            # Exactly the parser the self-test exercises (see Get-OMVFramesFromBytes).
            $parsed = Get-OMVFramesFromBytes -Data $data
            $crcBad += $parsed.CrcBad
            foreach ($f in $parsed.Frames) {
                if ($f.Type -ne 0x02) { continue }
                $frames++
                $sizes.Add([int]$f.Payload.Length); $ids.Add($f.FrameId)
                if ($SaveDir -and $saved -lt $SaveMax) {
                    $saved++
                    [System.IO.File]::WriteAllBytes((Join-Path $SaveDir ("frame_%04d.jpg" -f $saved)), $f.Payload)
                }
                if ($ApiBase) {
                    try {
                        $req = [System.Net.HttpWebRequest]::Create("$ApiBase/api/frame")
                        $req.Method = 'POST'; $req.ContentType = 'image/jpeg'
                        $req.ContentLength = $f.Payload.Length; $req.Timeout = 5000
                        $rs = $req.GetRequestStream(); $rs.Write($f.Payload, 0, $f.Payload.Length); $rs.Close()
                        $resp = $req.GetResponse(); $resp.Close(); $posted++
                    } catch { }
                }
            }
        }
    } finally {
        $p.Write([char]3); Start-Sleep -Milliseconds 250
        $p.Close(); $p.Dispose()
    }
    $out = @()
    $out += "bursts=$bursts  frames=$frames  posted=$posted  saved=$saved  crc_bad=$crcBad  elapsed=$([math]::Round($all.Elapsed.TotalSeconds,1))s"
    if ($sizes.Count -gt 0) {
        $out += ("payload: min={0} max={1} mean={2} B   frame_id: {3} -> {4}" -f `
            (($sizes | Measure-Object -Minimum).Minimum), (($sizes | Measure-Object -Maximum).Maximum), `
            [math]::Round((($sizes | Measure-Object -Average).Average), 1), $ids[0], $ids[$ids.Count - 1])
    }
    return ($out -join "`n")
}

# Entry point for the offline self-test.  Must stay at the very END of the file:
# it calls functions defined above it.  Skipped when the file is dot-sourced
# (the normal live usage), so `exit` can never kill an interactive session.
if ($SelfTest -and $MyInvocation.InvocationName -ne '.') {
    $r = Invoke-OMVBridgeSelfTest
    Write-Output $r
    if ($r -notmatch "RESULT: PASS") { exit 1 }
    exit 0
}
