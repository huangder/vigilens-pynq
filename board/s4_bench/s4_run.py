"""S4 裸机自检驱动：xsdb 编程 PL + ps7_init + 下载裸机程序 + 跑一帧 + 取回结果块。

从 `.tmp/s4/` 搬进仓库（2026-10-23），因为"怎么在裸板上取证"这件事本身要可复现：
板子没有 Linux/PYNQ，三个 board/*.py 都 import pynq，所以 S4 的读数是**唯一的**硬件证据来源。

用法（仓库根）：
  python board\\s4_bench\\s4_run.py init   # 只做 PL 编程 + ps7_init + 自检打印（不跑程序）
  python board\\s4_bench\\s4_run.py recm   # 640×480 档全流程：一帧 + 推荐接法 + 灰度逐字节比对
  python board\\s4_bench\\s4_run.py long   # 连续 300 帧不死锁（门限 6）
  python board\\s4_bench\\s4_run.py gen    # 只生成 tcl，不执行

⚠️ 跑之前：① 板子必须真的上电且没进 DAP sticky error（`rst -dap` 清不掉时只能断电重上电，
   见 docs/36 §5）；② `board/bitstream/*.bit` 第一次使用前跑一次 `board/build_bd.tcl` 生成。
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
S4 = Path(__file__).resolve().parent
# 运行产物（tcl / res.bin / gray_out.bin）默认落在脚本目录；受限沙箱里可用环境变量改到临时目录。
OUT = Path(os.environ.get("S4_OUT_DIR", str(S4)))
OUT.mkdir(parents=True, exist_ok=True)
BIT = ROOT / "board" / "bitstream" / "system_wrapper.bit"
PROG = S4 / "s4_bench.bin"
FRAME_SRC = ROOT / "fpga" / "sim" / "data_motion" / "rgb_frames.bin"
# 黄金灰度（灌到 0x0B000000 由 ARM 逐字节比对）。档位一变它也必须跟着变 ——
# 所以抽成模块常量，`s4_720.py` 只改常量就能换档，不用复制一份 s4_run.py。
GOLD_SRC = ROOT / "fpga" / "sim" / "data_motion" / "gray.bin"
# ps7_init.tcl 是 PS 的初始化序列（PLL/DDR/MIO），由 build_bd.tcl 生成的 HDF 里带一份；
# 它与 bitstream 同源，所以优先用 board/bitstream/ 下那份（不要再手工维护第二份）。
PS7INIT = ROOT / "board" / "bitstream" / "ps7_init.tcl"
XSDB = r"D:\Xilinx\2026.1\Vitis\bin\xsdb.bat"

W, H = 640, 480
GW, GH = 384, 288
FRAME_BYTES = W * H * 3
GRAY_BYTES = GW * GH
CHUNK = 8192
# phase=10（视频批量）的约定，必须与 s4_bench.c 顶部一致
VIDEO_FRAME_ADDR = 0x12000000
VIDEO_N_ADDR = 0x0A001008
VIDEO_PHASE_ADDR = 0x0A001000

# 最近一次 xsdb 的完整输出（main 用它判断是不是"PS 挂死"这类特定失败）
LAST_OUT = ""


# 注：mask_poll 在 ps7_init.tcl 里上限 1e8 次、每次 mrd 约 1 ms —— 一挂就是几小时。
# 这里 source 之后立刻替换成 20 万次上限的版本，挂住时能看到 POLLTIMEOUT 而不是干等。
PREAMBLE = r'''proc W {addr words} { if {[catch {mwr -force $addr $words} e]} { puts "MERR@$addr: $e" } }
connect
puts "TARGETS0=[targets]"
# DAP 的 AHB-AP sticky error（0x30000021 / 0xF0000021）会让所有 DDR/SLCR 访问失败。
# 唯一在线的恢复手段是 rst -dap；这里重试若干次，并且**必须**先读到 SLCR 才继续，
# 否则后面的 ps7_init 会一路 "Memory read error" 却照旧往下跑（2026-10-19 踩过）。
set dapok 0
for {set i 0} {$i < 8} {incr i} {
  # 必须先选中 DAP 这个调试目标再 rst -dap；用 jtag targets -set 选 TAP 会报
  # "Invalid target. Use targets command to select a target"（2026-10-19 实测）
  if {[catch {targets -set -filter {name =~ "*DAP*"}} er]} { puts "SELDAP_ERR i=$i $er" }
  if {[catch {rst -dap} er]} { puts "RSTDAP_ERR i=$i $er" }
  after 1500
  catch {targets -set -filter {name =~ "*APU*"}}
  if {![catch {mrd -force 0xF8007080} v]} {
    puts "SLCR_OK i=$i v=$v"
    set dapok 1
    break
  }
  puts "SLCR_RETRY i=$i err=$v t=[targets]"
  catch {connect}
  after 1000
}
if {$dapok == 0} { puts "ABORT_DAP"; exit 1 }

# ---- 预检：PS 是否还活着 ------------------------------------------------------
# PS 挂死时 SLCR/PLL 读回全 0（rst -system 还会报 "Timeout waiting for PLL lock"），
# 这种情况下继续跑 ps7_init 只会让 5 个 mask_poll 依次空转到上界（实测一次浪费 15+ 分钟），
# 而**唯一恢复手段是断电重上电**。所以这里提前判定、几秒内失败退出（退出码 2）。
proc rdv {addr} { set s [lindex [mrd -force $addr] end] ; scan $s %x v ; return $v }
catch {targets -set -filter {name =~ "*APU*"}}
if {[catch {
    set silv [rdv 0xF8007080]
    set pllv [rdv 0xF800010C]
    puts "PRE_SIL=$silv PRE_PLL=$pllv"
    if {$silv == 0 && $pllv == 0} { puts "ABORT_PS_WEDGED_NEED_POWER_CYCLE"; exit 2 }
} epre]} {
    puts "PRE_ERR=$epre"
    puts "ABORT_PS_WEDGED_NEED_POWER_CYCLE"; exit 2
}

catch {jtag targets -set -filter {name =~ "*xc7z020*"}}
puts "FPGA_BEGIN"
catch {fpga -file __BIT__} ef
puts "FPGA_ERR=$ef"
after 2000
catch {targets -set -filter {name =~ "*Cortex-A9*#0"}}
catch {stop}
puts "STATE=[state]"
catch {source __PS7INIT__} es
puts "SRC_ERR=$es"
proc mask_poll { addr mask } {
  # 上限从 20 万降到 2 万：健康状态下 ps7_init 每个 poll 的 i 都是 0~1，
  # 2 万次（≈20 s）足够判定"真的等不到"，而 5 个 poll 最坏也只耗 ~100 s。
  for {set i 0} {$i < 20000} {incr i} {
    set v "0x[string range [mrd -force $addr] end-8 end]"
    if {[expr {$v & $mask}] != 0} { puts "POLLOK $addr $mask i=$i" ; return }
  }
  puts "POLLTIMEOUT $addr $mask"
}
puts "PS7_BEGIN"
catch {ps7_init} e8
puts "PS7_ERR=$e8"
catch {ps7_post_config}
puts "PS7_DONE"
# ps7_init 期间 DAP 偶发 sticky error（0xF0000021）：一次就读失败就会让后面全部取证作废。
# 所以这里的读改成"带 DAP 恢复的重试"，重试耗尽才判定失败（rst -dap 只复位调试口，不动 PS/PL）。
proc safeRd {addr} {
  for {set i 0} {$i < 6} {incr i} {
    if {![catch {mrd -force $addr} v]} { return $v }
    puts "SAFEREAD_RETRY i=$i addr=$addr err=$v"
    catch {targets -set -filter {name =~ "*DAP*"}}
    catch {rst -dap}
    after 900
    catch {targets -set -filter {name =~ "*Cortex-A9*#0"}}
    catch {stop}
  }
  return ""
}
set e9 [safeRd 0xF8007080]
if {$e9 eq ""} { puts "ABORT_SIL err=DAP unrecoverable"; exit 1 }
puts "SIL=$e9"
set ea [safeRd 0x08000000]
if {$ea eq ""} { puts "ABORT_DDR err=DAP unrecoverable"; exit 1 }
puts "DDRREAD=$ea"
'''


def load_words(path, offset=0, nbytes=None):
    data = Path(path).read_bytes()
    data = data[offset:] if nbytes is None else data[offset:offset + nbytes]
    if len(data) % 4:
        data += b"\x00" * (4 - len(data) % 4)
    return list(struct.unpack("<%dI" % (len(data) // 4), data))


def emit_mwr(addr, words):
    lines = []
    a = addr
    for i in range(0, len(words), CHUNK):
        chunk = words[i:i + CHUNK]
        body = " ".join("0x%08X" % v for v in chunk)
        lines.append("W 0x%08X {%s}" % (a, body))
        a += len(chunk) * 4
    return lines


def build(stage, clip=None):
    if stage == "video":
        return build_video(clip)
    head = (PREAMBLE
            .replace("__BIT__", BIT.as_posix())
            .replace("__PS7INIT__", PS7INIT.as_posix()))
    lines = [head, 'puts "PS7_STAGE_END"']
    if stage != "init":
        prog = load_words(PROG)
        frame = load_words(FRAME_SRC, 0, FRAME_BYTES)
        gold = load_words(GOLD_SRC, 0, GRAY_BYTES)
        print("prog words", len(prog), "frame words", len(frame), "gold words", len(gold))
        lines += emit_mwr(0x00100000, prog)
        lines += emit_mwr(0x08000000, frame)
        lines += emit_mwr(0x0B000000, gold)
        lines += emit_mwr(0x0A000000, [0, 0, 0, 0])
        lines.append('puts "LOADED"')
        # 阶段/顺序开关放在 0x0A001000 之后：0x0A000000+16 起是固件的 ASCII 日志区（≤3000 B），
        # 早先把开关放在 0x0A000100 被日志正文覆盖，读回来是 "l_st" 这种乱码（2026-10-19 实测）。
        lines += ["mwr -force 0x0A001000 %d" % {"probe": 1, "mini": 3, "dec": 4, "roionly": 5,
                                                "sinkfree": 6, "recm": 7, "fullold": 2,
                                                "long": 8, "stream": 9}.get(stage, 2),
                  "mwr -force 0x0A001004 %d" % (1 if stage == "fullold" else 0),
                  "catch {rwr pc 0x00100000}",
                  'puts "PC=[rrd pc]"',
                  "catch {con}",
                  "after %d" % (60000 if stage in ("long", "stream") else 30000),
                  "catch {stop}",
                  'puts "STOPPED pc=[rrd pc] state=[state]"',
                  "catch {mrd -force 0x0A000000 4} eh",
                  'puts "RESHDR=$eh"',
                  "catch {mrd -bin -file %s -force 0x0A000000 800}" % (OUT / "res.bin").as_posix(),
                  'puts "DUMPED"']
    lines.append("exit")
    path = OUT / ("run_%s.tcl" % stage)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("tcl", stage, path.stat().st_size, "bytes")
    return path


def build_video(clip):
    """把 make_clip.py 产出的 frames.bin 用 `mwr -bin -file` 整块写 DDR，跑 phase 10。

    与 `recm` 的区别：不灌 sim 的黄金帧，也不用 emit_mwr 逐字生成几十 MB 的 Tcl；
    校验靠板端回传的逐帧 sum/FNV-1a，与主机 `clip_digests()` 对哈希。
    """
    sys.path.insert(0, str(ROOT / "board" / "video_link"))
    from video_clip import clip_digests, get_tier, load_manifest, verify_clip

    if not clip:
        raise SystemExit("video 阶段必须给 --clip <目录>")
    clip = Path(clip).resolve()
    v = verify_clip(clip)
    if not v["ok"]:
        raise SystemExit("clip 校验失败：" + "; ".join(v["reasons"]))
    m = load_manifest(clip)
    t = get_tier(str(m["tier"]))
    bit = ROOT / t.bitstream
    prog = ROOT / t.firmware
    if not bit.exists():
        raise SystemExit(f"缺 bitstream：{bit}")
    if not prog.exists():
        raise SystemExit(
            f"缺固件：{prog}\n先跑 board/s4_bench/build.ps1（或 build.sh）重新编译 phase 10 固件"
        )
    if prog.stat().st_mtime < (S4 / "s4_bench.c").stat().st_mtime:
        print(
            "⚠️ 固件比 s4_bench.c 旧：它可能还没有 phase 10。"
            "先跑 board/s4_bench/build.ps1 重新编译。"
        )
    n = int(m["frames"])
    if n <= 0 or n > 60:
        raise SystemExit(f"帧数 {n} 不在 1~60；改 manifest 或重新打包")
    head = (PREAMBLE
            .replace("__BIT__", bit.as_posix())
            .replace("__PS7INIT__", PS7INIT.as_posix()))
    lines = [head, 'puts "PS7_STAGE_END"']
    lines += emit_mwr(0x00100000, load_words(prog))
    # 一条命令写整块：xsdb 的 `mwr -bin -file <file> <addr>` 省略 num 时写整个文件。
    # ⚠️ 该用法尚未上板实测（2026-10-26 只确认了 xsdb help 里有这条语法）。
    lines.append('mwr -force -bin -file {%s} 0x%08X' % ((clip / "frames.bin").as_posix(), VIDEO_FRAME_ADDR))
    lines += emit_mwr(0x0A000000, [0, 0, 0, 0])
    lines.append('puts "LOADED"')
    lines += ["mwr -force 0x%08X 10" % VIDEO_PHASE_ADDR,
              "mwr -force 0x0A001004 0",
              "mwr -force 0x%08X %d" % (VIDEO_N_ADDR, n),
              "catch {rwr pc 0x00100000}",
              'puts "PC=[rrd pc]"',
              "catch {con}",
              # 每帧 ≈10 ms，再加逐帧软复位/日志；给足 2 s/帧的余量
              "after %d" % max(30000, n * 2000),
              "catch {stop}",
              'puts "STOPPED pc=[rrd pc] state=[state]"',
              "catch {mrd -force 0x0A000000 4} eh",
              'puts "RESHDR=$eh"',
              "catch {mrd -bin -file %s -force 0x0A000000 800}" % (OUT / "res.bin").as_posix(),
              'puts "DUMPED"']
    lines.append("exit")
    path = OUT / "run_video.tcl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("clip", clip, "frames", n, "tier", t.name)
    print("tcl video", path.stat().st_size, "bytes（frames.bin 未展开成文本）")
    return path


def run_xsdb(tcl_path, timeout=480):
    global LAST_OUT
    cp = subprocess.run(["cmd", "/c", XSDB, "-quiet", str(tcl_path)],
                        capture_output=True, text=True, timeout=timeout)
    out = (cp.stdout or "") + (cp.stderr or "")
    LAST_OUT = out
    print("xsdb exit", cp.returncode)
    for line in out.splitlines():
        s = line.strip()
        if any(k in s for k in ("TARGETS=", "FPGA_", "STATE=", "SRC_ERR", "POLL", "PS7_",
                                "PRE_", "ABORT_", "SIL=", "DDRREAD", "LOADED", "PC=", "STOPPED",
                                "RESHDR", "DUMPED", "MERR", "rror", "ailed", "Invalid")):
            print("  |", s[:220])
    return cp.returncode


def _check_video_log(log, clip):
    """把板端 phase 10 的逐帧 sum/FNV 与主机 clip_digests() 对齐。

    这一层刻意**不读回 1.3 MB 灰度**：JTAG 回读慢，而逐帧哈希已经足以证明
    "板端拿到的像素与主机送出的 clip 是同一份"。灰度正确性另有 recm 阶段对黄金参考。
    """
    sys.path.insert(0, str(ROOT / "board" / "video_link"))
    from video_clip import clip_digests

    pat = re.compile(
        r"^vid f=(\d+) sum=(\d+) hash=0x([0-9A-Fa-f]+) fid=(\d+)"
        r"(?: mot_diff=(\d+) mot_pix=(\d+) mot_ratio=(\d+) mot_cnt=(\d+)"
        r" roi_cnt=(\d+) rgb_sum=(\d+))?$"
    )
    got = {}
    for line in log.splitlines():
        m = pat.match(line.strip())
        if m:
            row = {
                "sum": int(m.group(2)),
                "hash": int(m.group(3), 16),
                "fid": int(m.group(4)),
            }
            if m.group(5) is not None:
                row.update({
                    "mot_diff": int(m.group(5)),
                    "mot_pix": int(m.group(6)),
                    "mot_ratio": int(m.group(7)),
                    "mot_cnt": int(m.group(8)),
                    "roi_cnt": int(m.group(9)),
                    "rgb_sum": int(m.group(10)),
                })
            got[int(m.group(1))] = row
    try:
        digests = clip_digests(clip)
    except Exception as e:
        print("!! 无法计算主机侧 digests：%s" % e)
        return 1
    if not got:
        print("!! 日志里没有 `vid f=...` 行；固件可能不是 phase 10 版本")
        return 1
    bad = 0
    print("---- 逐帧对拍（板端 vs 主机）----")
    for d in digests:
        f = int(d["frame"])
        g = got.get(f)
        if g is None:
            print("  f=%-2d 板端缺这一帧" % f)
            bad += 1
            continue
        ok = g["sum"] == d["sum"] and g["hash"] == d["hash"]
        print(
            "  f=%-2d %s  sum=%d/%d hash=0x%08X/0x%08X fid=%d"
            % (f, "OK" if ok else "MISMATCH", g["sum"], d["sum"], g["hash"], d["hash"], g["fid"])
        )
        bad += 0 if ok else 1
    m = re.search(r"^vid_fails=(\d+)$", log, re.M)
    fw_fails = int(m.group(1)) if m else -1
    print("板端 vid_fails=%s；主机对拍不匹配 %d 帧 / 共 %d 帧" % (fw_fails, bad, len(digests)))
    if got and all("mot_ratio" in g for g in got.values()):
        rows = [{
            "fid": f,
            "frame": f,
            "valid": f > 1,  # motion_quality 第 1 帧 prev_buf 上电未定义
            "motion_ratio_q16": g["mot_ratio"],
            "motion_pixels": g["mot_pix"],
            "diff_total": g["mot_diff"],
            "count": g["mot_cnt"],
            "roi_count": g["roi_cnt"],
            "rgb_sum": g["rgb_sum"],
        } for f, g in sorted(got.items())]
        p = OUT / "pl_motion.json"
        p.write_text(json.dumps({
            "schema": "vigilens.pl_motion/1",
            "source": "board_phase10",
            "is_board_run": True,
            "frames": len(rows),
            "per_frame": rows,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("PL motion : %s（可传给 run_pipeline.py --pl-motion-json）" % p)
    if fw_fails != 0:
        print("!! 板端自报失败（看上面的 vid_fail_frame / vid_fail_kind）")
        return 1
    return 0 if bad == 0 else 1


def report(stage="full", clip=None):
    res = OUT / "res.bin"
    if not res.exists():
        print("NO res.bin")
        return 1
    raw = res.read_bytes()
    magic, loglen, status = struct.unpack("<III", raw[:12])
    print("magic=0x%08X loglen=%d status=%d" % (magic, loglen, status))
    if magic != 0x600D600D:
        print("!! magic 不是 0x600D600D（程序未跑完或结果块被覆盖）")
    if not (0 < loglen <= 3000):
        # 程序没跑到 finish() 时 loglen 还没写回，但日志正文已经在 DDR 里（结果块先清零过）。
        # 这时按"第一个 0 字节"截断，仍然把已写出的日志打出来 —— 不然这段取证就白跑了。
        end = raw.find(b"\x00", 16)
        loglen = (end - 16) if end > 16 else 0
        if loglen <= 0:
            print("!! loglen 越界且正文为空；前 64 B：%s" % raw[:64].hex(" "))
            return 1
        print("!! loglen 无效，按 NUL 截断得到 %d B（程序可能还在跑）" % loglen)
    log = raw[16:16 + loglen].decode("ascii", "replace")
    log = "".join(c if 32 <= ord(c) < 127 or c == "\n" else "?" for c in log)
    print("---- log ----")
    print(log)
    print("---- end log ----")

    if stage == "video":
        return _check_video_log(log, clip)

    gout = OUT / "gray_out.bin"
    if stage == "probe":
        print("(probe 阶段不做灰度比对)")
        return 0
    if gout.exists():
        hw = gout.read_bytes()[:GRAY_BYTES]
        exp = (ROOT / "fpga" / "sim" / "data_motion" / "gray.bin").read_bytes()[:GRAY_BYTES]
        bad = [i for i in range(min(len(hw), GRAY_BYTES)) if hw[i] != exp[i]]
        print("gray hw vs golden: %d bytes, mismatch %d" % (len(hw), len(bad)))
        if bad:
            i = bad[0]
            print("  first mismatch idx=%d y=%d x=%d hw=%d golden=%d" % (i, i // GW, i % GW, hw[i], exp[i]))
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("stage", nargs="?", default="full")
    ap.add_argument("--clip")
    ap.add_argument("--gen", action="store_true", help="只生成 tcl，不连板")
    args, _unknown = ap.parse_known_args()
    stage = args.stage
    if stage == "video" and not args.clip:
        print("用法：python board\\s4_bench\\s4_run.py video --clip metrics/logs/video_clips/720p_0")
        return 2
    # 先删旧产物：否则一次失败的运行会把上一轮的 res.bin/gray_out.bin 当成"本次结果"报出来
    for name in ("res.bin", "gray_out.bin"):
        p = OUT / name
        if p.exists():
            p.unlink()
    tcl = build(stage, clip=args.clip)
    if stage == "gen" or args.gen:
        return 0
    timeout = 480
    if stage == "video" and args.clip:
        # 按 0.25 MB/s 的保守值 + 每帧 2 s，给足传输余量
        try:
            nbytes = (Path(args.clip) / "frames.bin").stat().st_size
            timeout = max(480, int(nbytes / 0.25e6) + 120)
        except OSError:
            pass
    rc = run_xsdb(tcl, timeout=timeout)
    if stage == "init":
        return rc
    if "ABORT_PS_WEDGED_NEED_POWER_CYCLE" in LAST_OUT:
        print("【PS 挂死】SLCR/PLL 读回全 0（或预检根本读不到）⇒ 只能断电重上电，再重跑本命令。")
        return 2
    if not (OUT / "res.bin").exists():
        print("没有 res.bin ⇒ 这次没跑出结果（看上面 | 开头的行）")
        return rc or 3
    return report(stage, clip=args.clip)


if __name__ == "__main__":
    sys.exit(main())
