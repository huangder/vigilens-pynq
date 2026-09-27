# omv_stream_bridge.ps1 -- receive the OpenMV stream over COM10 and hand it to the web app.
#
# WHY THIS EXISTS
#   OpenMV can act as a UVC camera only by flashing a separate UVC firmware, and the only
#   obtainable one (v4.6.20) bricked this camera once.  But the camera's MicroPython side can
#   already push frames over the USB virtual serial port (openmv_stream.py), and the frame
#   protocol is implemented once in board/openmv/vigilens_link.py.
#   pyserial is NOT installed and pip has no network here, so the receiver is implemented on
#   .NET's SerialPort instead.
#
# WHY TYPE_GRAY EXISTS (docs/18 route 2, the fix for docs/16 BUG-027)
#   On-camera JPEG (TYPE_JPEG) is produced by img.compress(), which needs one large CONTIGUOUS
#   heap block.  This board (OpenMV Cam H7 R2, sensor MT9M114 -- which physically cannot output
#   JPEG) only has ~302 KB of heap and a QVGA RGB565 frame buffer already takes 153600 B, so the
#   stream eventually dies with "OSError: Compression Failed!".
#   Route 2 removes that requirement entirely: the camera sends RAW GRAYSCALE (no encoder call
#   at all) and THIS script encodes the JPEG, where memory is not a problem.
#
# WIRE FORMAT (little-endian, from vigilens_link.py):
#   0  2  magic 0xA55A   (on the wire: 5A A5)
#   2  1  type           0x02 = JPEG, 0x03 = GRAY, 0x01 = STATS
#   3  1  flags
#   4  4  frame_id       uint32, starts at 1
#   8  4  payload_len
#   12 N  payload        JPEG bytes (0x02) | 4B w + 4B h + w*h gray bytes (0x03)
#   12+N 2 crc16  CRC-16/CCITT-FALSE over bytes [0, 12+N)
#
# ASCII-ONLY ON PURPOSE (PowerShell 5.1 decodes BOM-less .ps1 as GBK).
#
# USAGE
#   . .\metrics\scripts\omv_stream_bridge.ps1
#   Send-OMVStreamer                              # default: MODE="usb_gray" (route 2)
#   Send-OMVFramesToApi -TotalSeconds 300 -ApiBase http://127.0.0.1:8031 -BurstSeconds 0.25 -SaveMax 0
#   Send-OMVStreamer -Mode usb_jpeg -Quality 50   # the old on-camera-JPEG path, for comparison
#   Stop-OMVStreamer                              # Ctrl-C the camera

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

function Convert-OMVGrayToJpeg {
    <#
      GRAY payload -> JPEG bytes, so the web app gets a picture without the camera ever
      calling an encoder (docs/18 route 2, the fix for BUG-027).

      Payload layout (board/openmv/vigilens_link.py pack_gray):
        0  4  width   int32 LE
        4  4  height  int32 LE
        8  N  w*h gray bytes, row-major (no padding)

      Returns byte[] JPEG, or $null when the payload is not a plausible image (truncated or
      absurd size).  $null MUST be counted by the caller -- a conversion that always failed
      would otherwise be indistinguishable from "the camera sent nothing".

      Implementation note: an 8bpp indexed bitmap with a LINEAR GRAY PALETTE takes the camera's
      bytes as-is, so there is no per-pixel loop (a PowerShell loop over 19200 pixels would cap
      the frame rate far below the link's).  Rows still have to be copied one by one because
      GDI+ rows are padded to a 4-byte stride.
    #>
    param(
        [byte[]]$Payload = @(),
        [int]$Quality = 80
    )
    if (-not $Payload -or $Payload.Length -lt 9) { return $null }
    # [int] cast before -shl: PowerShell keeps the left operand's type, so [byte]0xFF -shl 24
    # would truncate back to a byte.  Same trap as the CRC code below.
    $w = [int]$Payload[0] -bor ([int]$Payload[1] -shl 8) -bor ([int]$Payload[2] -shl 16) -bor ([int]$Payload[3] -shl 24)
    $h = [int]$Payload[4] -bor ([int]$Payload[5] -shl 8) -bor ([int]$Payload[6] -shl 16) -bor ([int]$Payload[7] -shl 24)
    if ($w -le 0 -or $h -le 0 -or $w -gt 4096 -or $h -gt 4096) { return $null }
    if ($Payload.Length -lt (8 + $w * $h)) { return $null }

    if (-not $script:OMV_DRAWING_READY) {
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $script:OMV_DRAWING_READY = $true
    }
    $bmp = $null; $ms = $null
    try {
        $bmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format8bppIndexed)
        $pal = $bmp.Palette
        for ($i = 0; $i -lt 256; $i++) { $pal.Entries[$i] = [System.Drawing.Color]::FromArgb(255, $i, $i, $i) }
        $bmp.Palette = $pal
        $rect = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
        $data = $bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly,
                              [System.Drawing.Imaging.PixelFormat]::Format8bppIndexed)
        try {
            $stride = $data.Stride
            $buf = New-Object byte[] ($stride * $h)
            for ($y = 0; $y -lt $h; $y++) {
                [Array]::Copy($Payload, 8 + $y * $w, $buf, $y * $stride, $w)
            }
            [System.Runtime.InteropServices.Marshal]::Copy($buf, 0, $data.Scan0, $buf.Length)
        } finally {
            $bmp.UnlockBits($data)
        }
        $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
                 Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1
        if (-not $codec) { return $null }
        $ps = New-Object System.Drawing.Imaging.EncoderParameters(1)
        $ps.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
            [System.Drawing.Imaging.Encoder]::Quality, [long]$Quality)
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, $codec, $ps)
        return $ms.ToArray()
    } finally {
        if ($ms) { $ms.Dispose() }
        if ($bmp) { $bmp.Dispose() }
    }
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

    # 1b. the fast (C#) CRC must agree with the PowerShell reference on real-sized input.
    #     Why this is not optional: a wrong table would silently ACCEPT corrupt frames (or drop
    #     good ones) and the protocol's whole integrity story would be theatre.  Route 2 runs the
    #     fast path -- see Initialize-OMVCrc for the measured reason.
    $fastOk = Initialize-OMVCrc
    if (-not $fastOk) {
        # Not a failure: constrained language mode has no compiler.  Say so, because it also means
        # route 2 will fall behind on 19 KB frames (see the note in Initialize-OMVCrc).
        Write-Verbose "fast CRC unavailable -- using the PowerShell reference implementation"
    } else {
        $probe = New-Object byte[] 4200
        for ($i = 0; $i -lt $probe.Length; $i++) { $probe[$i] = ($i * 31 + 7) -band 0xFF }
        $fastVal = Get-Crc16Ccitt -Data $probe
        $savedReady = $script:OMV_CRC_READY
        $script:OMV_CRC_READY = $false              # force the reference loop
        $slowVal = Get-Crc16Ccitt -Data $probe
        $script:OMV_CRC_READY = $savedReady
        if ($fastVal -ne $slowVal) {
            $fails.Add(("fast CRC (0x{0:X4}) != PowerShell reference (0x{1:X4}) on a 4200-byte buffer" -f $fastVal, $slowVal))
        }
        # and the fast path must still honour the protocol's published check value
        $fastCheck = Get-Crc16Ccitt -Data ([System.Text.Encoding]::ASCII.GetBytes("123456789"))
        if ($fastCheck -ne 0x29B1) { $fails.Add(("fast CRC check value wrong: 0x{0:X4}" -f $fastCheck)) }
    }

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
# docs/18 route 2: a RAW GRAYSCALE frame.  Left half bright / right half dark, so a stride or
# row-offset bug in Convert-OMVGrayToJpeg shows up as a WRONG PIXEL, not as a crash.
gw, gh = 32, 32
gray = bytes(200 if x < gw // 2 else 40 for y in range(gh) for x in range(gw))
blob = (b'GARBAGE-BEFORE-FRAME\x00\xff\x5a\xa5\x00'      # fake magic on purpose
        + vl.encode(vl.TYPE_JPEG, 4242, payload)
        + vl.encode(vl.TYPE_STATS, 4243, b'xy')
        + vl.encode(vl.TYPE_GRAY, 4244, vl.pack_gray(gw, gh, gray)))
open(r'$tmp', 'wb').write(blob)
"@
        $gen | & $py - 2>&1 | Out-Null
        if (-not (Test-Path $tmp)) {
            $fails.Add("encoder failed: CPython wrote no $tmp (can vigilens_link.py be imported?)")
        } else {
            $res = Get-OMVFramesFromBytes -Data ([System.IO.File]::ReadAllBytes($tmp))
            $fr = $res.Frames
            if ($fr.Count -ne 3) {
                $fails.Add(("decoded {0} frames, expected 3 (resync or CRC accept is wrong)" -f $fr.Count))
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

                # --- frame 3: the route-2 GRAY frame (parser + PC-side encoder) ---------------
                if ($fr[2].Type -ne 0x03) { $fails.Add(("frame 3 type wrong: {0} != 3 (GRAY)" -f $fr[2].Type)) }
                if ($fr[2].FrameId -ne 4244) { $fails.Add(("frame 3 frame_id wrong: {0} != 4244" -f $fr[2].FrameId)) }
                if ($fr[2].Payload.Length -ne 1032) {
                    $fails.Add(("frame 3 payload length wrong: {0} != 1032 (8 + 32*32)" -f $fr[2].Payload.Length))
                } else {
                    $gw = [int]$fr[2].Payload[0] -bor ([int]$fr[2].Payload[1] -shl 8) -bor
                          ([int]$fr[2].Payload[2] -shl 16) -bor ([int]$fr[2].Payload[3] -shl 24)
                    $gh = [int]$fr[2].Payload[4] -bor ([int]$fr[2].Payload[5] -shl 8) -bor
                          ([int]$fr[2].Payload[6] -shl 16) -bor ([int]$fr[2].Payload[7] -shl 24)
                    if ($gw -ne 32 -or $gh -ne 32) {
                        $fails.Add(("frame 3 pack_gray header wrong: {0}x{1} != 32x32" -f $gw, $gh))
                    }
                    $badGray = 0
                    for ($i = 0; $i -lt 1024; $i++) {
                        $want = if (($i % 32) -lt 16) { 200 } else { 40 }
                        if ($fr[2].Payload[8 + $i] -ne $want) { $badGray++ }
                    }
                    if ($badGray -ne 0) { $fails.Add(("frame 3 gray pixels differ in {0} bytes" -f $badGray)) }

                    # The whole point of route 2: the PC encodes what the camera refused to.
                    try {
                        $jpg = Convert-OMVGrayToJpeg -Payload $fr[2].Payload -Quality 80
                    } catch {
                        $jpg = $null
                        $fails.Add("Convert-OMVGrayToJpeg threw: $($_.Exception.Message)")
                    }
                    if ($null -eq $jpg -or $jpg.Length -lt 100) {
                        $len = if ($null -eq $jpg) { "null" } else { $jpg.Length }
                        $fails.Add("GRAY -> JPEG produced no image (bytes=$len)")
                    } elseif ($jpg[0] -ne 0xFF -or $jpg[1] -ne 0xD8) {
                        $fails.Add(("GRAY -> JPEG is not a JPEG: {0:X2}{1:X2}" -f $jpg[0], $jpg[1]))
                    } else {
                        # Decode it back: a wrong palette, stride or channel order would survive the
                        # "starts with FFD8" check but not this one.
                        $mss = New-Object System.IO.MemoryStream
                        $mss.Write($jpg, 0, $jpg.Length); $mss.Position = 0
                        $img = $null; $dec = $null
                        try {
                            $img = [System.Drawing.Image]::FromStream($mss)
                            if ($img.Width -ne 32 -or $img.Height -ne 32) {
                                $fails.Add(("GRAY -> JPEG size wrong: {0}x{1} != 32x32" -f $img.Width, $img.Height))
                            } else {
                                $dec = New-Object System.Drawing.Bitmap($img)
                                # JPEG is lossy: compare BLOCK CENTRES with a wide tolerance.
                                $lp = $dec.GetPixel(8, 16); $rp = $dec.GetPixel(24, 16)
                                if ([Math]::Abs($lp.R - 200) -gt 24 -or [Math]::Abs($rp.R - 40) -gt 24) {
                                    $fails.Add(("GRAY -> JPEG pixels wrong: left R={0} (want ~200), right R={1} (want ~40)" -f $lp.R, $rp.R))
                                }
                            }
                        } catch {
                            $fails.Add("decoding the converted JPEG threw: $($_.Exception.Message)")
                        } finally {
                            if ($dec) { $dec.Dispose() }
                            if ($img) { $img.Dispose() }
                            $mss.Dispose()
                        }
                    }
                }
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

    # 3b. malformed GRAY payloads must come back as $null, never as an exception and never as a
    #     bogus image.  A truncated payload is what a dropped serial byte looks like, and route 2
    #     must degrade to "this frame is counted as a conversion failure", not to a crash.
    $badGrayCases = New-Object System.Collections.Generic.List[object]
    $badGrayCases.Add((New-Object byte[] 4))                       # shorter than the 8-byte header
    $trunc = New-Object byte[] 40                                  # claims 32x32, carries 32 pixels
    $trunc[0] = 32; $trunc[4] = 32
    $badGrayCases.Add($trunc)
    $negW = New-Object byte[] 16                                   # width = 0xFFFFFFFF -> negative
    for ($i = 0; $i -lt 4; $i++) { $negW[$i] = 0xFF }
    $badGrayCases.Add($negW)
    foreach ($bp in $badGrayCases) {
        try {
            $r1 = Convert-OMVGrayToJpeg -Payload $bp
            if ($null -ne $r1) { $fails.Add("malformed GRAY payload produced an image, expected `$null") }
        } catch {
            $fails.Add("Convert-OMVGrayToJpeg threw on a malformed payload: $($_.Exception.Message)")
        }
    }

    # 4. the mode/framesize overrides in Send-OMVStreamer are STRING REPLACEMENTS into
    #    openmv_stream.py.  Rename one of those constants and the replacement silently becomes
    #    a no-op: the camera then streams the in-file default instead of what we asked for
    #    (the transcript's CAM_SEES line would show it, but only if somebody reads it).
    #    Pin the anchors, and pin the route-2 pieces themselves.
    $streamPy = Join-Path $root "board\openmv\openmv_stream.py"
    if (-not (Test-Path $streamPy)) {
        $fails.Add("openmv_stream.py not found: $streamPy")
    } else {
        $srcStream = [System.IO.File]::ReadAllText($streamPy)
        foreach ($anchor in @('MODE = "probe"', 'STREAM_FRAMESIZE = "QVGA"',
                              'GRAY_FRAMESIZE = "QQVGA"', 'JPEG_QUALITY = 90')) {
            if ($srcStream -notmatch [regex]::Escape($anchor)) {
                $fails.Add(("openmv_stream.py lost the anchor Send-OMVStreamer replaces: {0}" -f $anchor))
            }
        }
        foreach ($needle in @('TYPE_GRAY', 'pack_gray', 'sensor.GRAYSCALE')) {
            if ($srcStream -notmatch [regex]::Escape($needle)) {
                $fails.Add(("openmv_stream.py has no {0} -- docs/18 route 2 is gone" -f $needle))
            }
        }
    }

    if ($fails.Count -eq 0) {
        return "RESULT: PASS  (CRC check value 0x29B1 + encode/decode byte compare + GRAY payload + GRAY->JPEG pixel check + stream anchors + empty-input guard)"
    }
    return ("RESULT: FAIL`n  - " + ($fails -join "`n  - "))
}

$script:OMV_PORT = 'COM10'
# Set once, the first time a GRAY payload has to be turned into a JPEG (see
# Convert-OMVGrayToJpeg).  Loading System.Drawing per frame would be wasted work.
$script:OMV_DRAWING_READY = $false
# Tri-state for the fast CRC: $null = not tried yet, $true = C# table-driven CRC is available,
# $false = use the PowerShell reference loop (see Initialize-OMVCrc / Get-Crc16Ccitt).
$script:OMV_CRC_READY = $null

function Initialize-OMVCrc {
    <#
      Compile a table-driven CRC-16/CCITT-FALSE in C# and use it for frame validation.

      WHY (measured 2026-09-27, this machine):
        the pure-PowerShell bit-by-bit CRC costs ~112 ms for one 19220-byte GRAY frame, and the
        whole parser ~93.5 ms/frame (41-frame blob, 3833 ms).  Route 2 (docs/18) delivers a frame
        every ~77 ms (measured 13.2 fps), so the PARSER, not the link, became the bottleneck:
        frames were handed over in clumps and /api/video_status showed a 0 -> 6 s age sawtooth
        (VISIBLE_PCT 47%, i.e. the web page still blanked half the time).
        A native CRC plus the same framing logic removes that bottleneck.

      SAFETY: this only replaces the CRC arithmetic.  Framing/resync stays in
      Get-OMVFramesFromBytes (the offline-verified code path), the protocol constants are
      unchanged, and Invoke-OMVBridgeSelfTest asserts the fast path AGREES with the PowerShell
      reference implementation -- a wrong table would otherwise silently accept corrupt frames.
      If Add-Type is unavailable (constrained language mode), we silently keep the slow one.
    #>
    if ($null -ne $script:OMV_CRC_READY) { return $script:OMV_CRC_READY }
    try {
        Add-Type -TypeDefinition @'
public static class VigiLensCrc
{
    private static readonly ushort[] Table = BuildTable();

    private static ushort[] BuildTable()
    {
        var table = new ushort[256];
        for (int i = 0; i < 256; i++)
        {
            ushort crc = (ushort)(i << 8);
            for (int bit = 0; bit < 8; bit++)
            {
                crc = (ushort)(((crc & 0x8000) != 0) ? ((crc << 1) ^ 0x1021) : (crc << 1));
            }
            table[i] = crc;
        }
        return table;
    }

    public static ushort Crc(byte[] data, int offset, int count)
    {
        ushort crc = 0xFFFF;
        int end = offset + count;
        for (int i = offset; i < end; i++)
        {
            crc = (ushort)((crc << 8) ^ Table[((crc >> 8) ^ data[i]) & 0xFF]);
        }
        return crc;
    }
}
'@ -ErrorAction Stop
        $script:OMV_CRC_READY = $true
    } catch {
        $script:OMV_CRC_READY = $false
    }
    return $script:OMV_CRC_READY
}

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
    #
    # The C# fast path (Initialize-OMVCrc) is what makes route 2 keep up; this PowerShell loop is
    # the reference implementation and the fallback.  Invoke-OMVBridgeSelfTest pins the two together.
    param([byte[]]$Data, [int]$Offset = 0, [int]$Count = -1)
    if ($Count -lt 0) { $Count = $Data.Length - $Offset }
    if ($null -eq $script:OMV_CRC_READY) { $null = Initialize-OMVCrc }
    if ($script:OMV_CRC_READY) { return [VigiLensCrc]::Crc($Data, $Offset, $Count) }
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
      exec openmv_stream.py with MODE=<Mode>.  openmv_stream.py is shipped as base64 chunks so
      that no file has to be written to the camera's tiny FAT.

      Default Mode is "usb_gray" = docs/18 route 2: the camera sends RAW GRAYSCALE and never
      calls img.compress(), which is what used to kill the stream (docs/16 BUG-027).
      "usb_jpeg" is kept for comparison -- it is the old, unreliable path.
    #>
    param(
        [string]$LinkPy   = 'board\openmv\vigilens_link.py',
        [string]$StreamPy = 'board\openmv\openmv_stream.py',
        [string]$Mode = 'usb_gray',
        # Empty = use the defaults hard-coded in openmv_stream.py
        # (STREAM_FRAMESIZE="QVGA" for jpeg/stats, GRAY_FRAMESIZE="QQVGA" for gray).
        [string]$Framesize = '',
        [int]$Quality = 80
    )
    $isGray = ($Mode -eq 'usb_gray' -or $Mode -eq 'uart_gray')
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
    $lines.Add('print("LINK_OK", _m.PROTOCOL_VERSION, _m.HDR_LEN, _m.TYPE_JPEG, _m.TYPE_GRAY)')
    # Fail on the camera BEFORE the stream starts, so the transcript says why.
    # TYPE_GRAY/pack_gray are route 2's whole point: a STALE /flash copy of the link module must
    # fail loudly here instead of raising AttributeError in the middle of the stream loop.
    $lines.Add('assert (_m.PROTOCOL_VERSION, _m.HDR_LEN, _m.TYPE_JPEG, _m.TYPE_GRAY) == (1, 12, 2, 3), "LINK_BAD"')
    $lines.Add('assert hasattr(_m, "pack_gray") and hasattr(_m, "pack_stats"), "LINK_OLD_NO_PACK_GRAY"')
    $lines.Add('_S=bytearray()')
    for ($i = 0; $i -lt $s64.Length; $i += 200) {
        $lines.Add("_S.extend(binascii.a2b_base64('" + $s64.Substring($i, [Math]::Min(200, $s64.Length - $i)) + "'))")
    }
    $lines.Add('print("LEN", len(_S))')
    $lines.Add('_src=_S.decode()')
    $lines.Add('_src=_src.replace(''MODE = "probe"'', ''MODE = "' + $Mode + '"'')')
    if ($Framesize) {
        # Keep the anchors in step with openmv_stream.py: they ARE the contract between the two files.
        if ($isGray) {
            $lines.Add('_src=_src.replace(''GRAY_FRAMESIZE = "QQVGA"'', ''GRAY_FRAMESIZE = "' + $Framesize + '"'')')
        } else {
            $lines.Add('_src=_src.replace(''STREAM_FRAMESIZE = "QVGA"'', ''STREAM_FRAMESIZE = "' + $Framesize + '"'')')
        }
    }
    $lines.Add('_src=_src.replace(''JPEG_QUALITY = 90'', ''JPEG_QUALITY = ' + $Quality + ''')')
    $lines.Add('print("CAM_SEES", [x for x in _src.split(chr(10)) if x.startswith("MODE = ") or x.startswith("STREAM_FRAMESIZE") or x.startswith("GRAY_FRAMESIZE") or x.startswith("JPEG_QUALITY")])')
    $lines.Add('del _S')
    $lines.Add('gc.collect()')
    $lines.Add('print("MEM", gc.mem_free())')
    # Compile FIRST, then drop the transferred source string and collect, BEFORE the loop.
    # img.compress() needs one large CONTIGUOUS buffer; keeping the ~16 KB source string
    # alive while the stream loop allocates a JPEG per frame is enough to make it raise
    # "OSError: Compression Failed!" after a while -- see docs/16 BUG-027.
    # (Harmless for route 2, which never compresses, and it keeps the jpeg fallback usable.)
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
    # The 4th number is TYPE_GRAY -- if the camera's /flash copy is stale this line is
    # "LINK_OK 1 12 2" (or the assert above already said LINK_OLD_NO_PACK_GRAY).
    if ($acc.ToString() -notmatch 'LINK_OK 1 12 2 3') {
        return ("LINK_FAIL: camera could not import a CURRENT vigilens_link from /flash " +
                "(expected LINK_OK 1 12 2 3 = PROTOCOL_VERSION, HDR_LEN, TYPE_JPEG, TYPE_GRAY; " +
                "route 2 needs TYPE_GRAY/pack_gray). Copy board\openmv\vigilens_link.py to the " +
                "camera's /flash with the OpenMV IDE and rerun.`n$($acc.ToString())")
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
      turn each frame into a JPEG, POST it to the web app, then repeat.

      Two payload types are accepted:
        0x02 JPEG -- forwarded as-is (the old on-camera-compression path)
        0x03 GRAY -- encoded HERE, on the PC (docs/18 route 2; see Convert-OMVGrayToJpeg)

      Why not the incremental List/RemoveRange parser (Receive-OMVFrames): it produced CRC
      failures on live data even though the identical bytes parsed cleanly offline, so this
      version uses only the offline-verified code path.

      NOTE on -BurstSeconds: a burst boundary can cut a frame in half, and the halves are
      dropped (the parser resynchronises on the next magic) -- so roughly ONE frame is lost per
      burst.  Smaller bursts make the picture fresher (the web page hides frames older than
      1.5 s) at the cost of a few frames per second.  0.25 s is the value docs/18 suggests.
    #>
    param(
        [int]$TotalSeconds = 12,
        [double]$BurstSeconds = 1.0,
        [string]$ApiBase = '',
        [string]$SaveDir = '',
        [int]$SaveMax = 3,
        [int]$GrayJpegQuality = 80
    )
    $p = $null
    try { $p = Get-OMVPort } catch { return "PORT_ERROR: $($_.Exception.Message)" }
    if ($SaveDir) { New-Item -ItemType Directory -Force -Path $SaveDir | Out-Null }
    $frames = 0; $jpegFrames = 0; $grayFrames = 0; $convFail = 0
    $crcBad = 0; $posted = 0; $saved = 0; $bursts = 0
    $sizes = New-Object System.Collections.Generic.List[int]        # JPEG bytes we produced
    $graySizes = New-Object System.Collections.Generic.List[int]    # GRAY bytes on the wire
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
                if ($f.Type -eq 0x02) {
                    $jpegFrames++
                    $jpg = $f.Payload
                } elseif ($f.Type -eq 0x03) {
                    # docs/18 route 2: the camera sent raw grayscale (it never called an
                    # encoder).  Encode it here, where memory is not the constraint.
                    $grayFrames++
                    $graySizes.Add([int]$f.Payload.Length)
                    $jpg = $null
                    try { $jpg = Convert-OMVGrayToJpeg -Payload $f.Payload -Quality $GrayJpegQuality }
                    catch { $jpg = $null }
                    if ($null -eq $jpg) {
                        # Counted, never silent: an always-failing conversion would otherwise look
                        # exactly like "the camera sent nothing".
                        $convFail++
                        continue
                    }
                } else {
                    continue
                }
                $frames++
                $sizes.Add([int]$jpg.Length); $ids.Add($f.FrameId)
                if ($SaveDir -and $saved -lt $SaveMax) {
                    $saved++
                    [System.IO.File]::WriteAllBytes((Join-Path $SaveDir ("frame_%04d.jpg" -f $saved)), $jpg)
                }
                if ($ApiBase) {
                    try {
                        $req = [System.Net.HttpWebRequest]::Create("$ApiBase/api/frame")
                        $req.Method = 'POST'; $req.ContentType = 'image/jpeg'
                        $req.ContentLength = $jpg.Length; $req.Timeout = 5000
                        $rs = $req.GetRequestStream(); $rs.Write($jpg, 0, $jpg.Length); $rs.Close()
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
    $out += "bursts=$bursts  frames=$frames  jpeg=$jpegFrames  gray=$grayFrames  conv_fail=$convFail  posted=$posted  saved=$saved  crc_bad=$crcBad  elapsed=$([math]::Round($all.Elapsed.TotalSeconds,1))s"
    if ($sizes.Count -gt 0) {
        $out += ("jpeg bytes: min={0} max={1} mean={2} B   frame_id: {3} -> {4}" -f `
            (($sizes | Measure-Object -Minimum).Minimum), (($sizes | Measure-Object -Maximum).Maximum), `
            [math]::Round((($sizes | Measure-Object -Average).Average), 1), $ids[0], $ids[$ids.Count - 1])
    }
    if ($graySizes.Count -gt 0) {
        $out += ("gray bytes on the wire: min={0} max={1} mean={2} B" -f `
            (($graySizes | Measure-Object -Minimum).Minimum), (($graySizes | Measure-Object -Maximum).Maximum), `
            [math]::Round((($graySizes | Measure-Object -Average).Average), 1))
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
