#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_eeprom.py —— 判读 EEPROM dump 探针的 ILA CSV（纯 PC 侧，只读）

背景
    cam_detect 探针（docs/34）已确认：CAM_CLK 被 PL 驱动到 25 MHz，但相机（7bit 0x10）
    在任何地址都不应答，总线上只有 0x50 / 0x58 两个「EEPROM 类」地址应答。板上只有
    一颗 24C32(U18)，它的 A1/A2 接地 ⇒ 只能占 0x50。**0x58 是谁？**

    `pl_eeprom_dump.v` 把这两台器件的前 16 字节读出来，本脚本负责把 CSV 解码成人能看的
    hexdump，并给出三条互斥结论：

      · b_ok=0                        → 0x58 根本没应答（本轮没读到它）
      · b_ok=1 且 B 与 A 内容完全相同 → 同一颗芯片被两个地址命中（0x58 不是独立器件）
      · b_ok=1 且内容不同             → 独立器件 ⇒ **很可能是相机模块自带的 EEPROM**
                                        ⇒ 模块已通电、排线也通，故障点转向传感器本体/XCLR

用法
    python board/check_eeprom.py _cam_build/eeprom_dump/eeprom_dump_ila.csv --raw
    python board/check_eeprom.py <csv> --json out.json

退出码
    0 = 读到终态（done=1）且 A（0x50）有应答；1 = 其它（CSV 读不动 / 没跑完 / A 都没应答）
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# dbg 总线布局 —— 与 board/cam_detect/pl_eeprom_dump.v 的 assign dbg0..dbg3 逐位一致
# RTL 是 MSB-first 拼接：
#   dbg0[31:0] = { 16'hCAFE, last_qi[7:0], done, a_ok, b_ok,
#                  mclk_seen, fail_seen, 2'b00, devi }
#   dbg1[63:0] = mem_a[0..7]   （byte0 在 [7:0]）
#   dbg2[63:0] = mem_a[8..15]
#   dbg3[63:0] = mem_b[0..7]
# 下面的表是 LSB-first（从 shift=0 起），字段顺序与上面正好相反。
# ---------------------------------------------------------------------------
DBG_LAYOUT = {
    "dbg0": [("devi", 1), ("_rsvd0", 2), ("fail_seen", 1), ("mclk_seen", 1),
             ("b_ok", 1), ("a_ok", 1), ("done", 1), ("last_qi", 8), ("magic", 16)],
    "dbg1": [("a_b0", 8), ("a_b1", 8), ("a_b2", 8), ("a_b3", 8),
             ("a_b4", 8), ("a_b5", 8), ("a_b6", 8), ("a_b7", 8)],
    "dbg2": [("a_b8", 8), ("a_b9", 8), ("a_b10", 8), ("a_b11", 8),
             ("a_b12", 8), ("a_b13", 8), ("a_b14", 8), ("a_b15", 8)],
    "dbg3": [("b_b0", 8), ("b_b1", 8), ("b_b2", 8), ("b_b3", 8),
             ("b_b4", 8), ("b_b5", 8), ("b_b6", 8), ("b_b7", 8)],
}

MAGIC = 0xCAFE


def console_utf8():
    """Windows GBK 控制台下不因中文/⚠️ 崩掉（同 board/check_camera.py 的做法）。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        rec = getattr(stream, "reconfigure", None)
        if rec is None:
            continue
        try:
            rec(encoding="utf-8", errors="replace")
        except Exception:      # noqa: BLE001
            pass


class CsvError(Exception):
    """CSV 读不动（格式/字段不认）。"""


def _norm(name):
    """把 ILA 导出的列名归一化：`dbg0[31:0]` / `probe0[31:0]` → `dbg0` / `probe0`。"""
    n = name.strip().strip('"').lower()
    for ch in ("[", "]"):
        n = n.replace(ch, " ")
    n = n.split()[0] if n.split() else n
    # Vivado 按位流里的网名导出，本设计里网名是 dbg0..dbg3
    if n.startswith("dbg") and n[3:].isdigit():
        return "dbg" + n[3:]
    if n.startswith("probe") and n[5:].isdigit():
        return "dbg" + n[5:]
    return n.replace(" ", "_")


def load_rows(path):
    """读 CSV，返回 (列名→索引, 数据行列表)。跳过 `Radix - ...` 那类说明行。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\r\n") for ln in f if ln.strip()]
    except OSError as exc:
        raise CsvError("打不开文件：%s（%s）" % (path, exc))

    if len(lines) < 3:
        raise CsvError("文件太短（%d 行），不像 ILA 导出的 CSV" % len(lines))

    header = [_norm(x) for x in lines[0].split(",")]
    col = {}
    for key in DBG_LAYOUT:
        if key in header:
            col[key] = header.index(key)
    if not col:
        raise CsvError("表头里找不到 dbg0..dbg3；实际列名：%s" % ",".join(header))

    rows = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < len(header):
            continue
        vals = {}
        ok = True
        for key, idx in col.items():
            try:
                vals[key] = int(parts[idx].strip(), 16)
            except ValueError:
                ok = False
                break
        if ok:
            rows.append(vals)
    if not rows:
        raise CsvError("没有任何可解析的数据行（值不是十六进制？）")
    return col, rows


def decode(vals):
    """按 LSB-first 把 val 拆成 {字段名: 值}。"""
    out = {}
    for key, layout in DBG_LAYOUT.items():
        if key not in vals:
            continue
        v = vals[key]
        shift = 0
        for name, width in layout:
            mask = (1 << width) - 1
            out[name] = (v >> shift) & mask
            shift += width
    return out


def hexdump(data):
    """16 字节一行 + ASCII 列（非可打印字符用 '.'）。"""
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexpart = " ".join("%02X" % b for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("  %04X  %-47s  |%s|" % (off, hexpart, asc))
    return "\n".join(lines)


def _write_csv(path, rows, header=("Sample in Buffer", "Sample in Window", "TRIGGER",
                                   "dbg0[31:0]", "dbg1[63:0]", "dbg2[63:0]", "dbg3[63:0]")):
    """造一个形状与 Vivado ILA 导出完全一致的 CSV（供 --selftest 用）。"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(header) + "\n")
        f.write(",".join(["Radix - UNSIGNED", "UNSIGNED", "UNSIGNED", "HEX", "HEX", "HEX", "HEX"]) + "\n")
        for i, r in enumerate(rows):
            f.write("%d,%d,%d,%s\n" % (i, i, 0, ",".join(r)))


def _mk_dbg0(last_qi, done, a_ok, b_ok, mclk_seen, fail_seen, devi):
    """按 RTL 的 MSB-first 拼接算 dbg0（见 pl_eeprom_dump.v 的 assign dbg0）。"""
    return "%08x" % ((MAGIC << 16) | (last_qi << 8) | (done << 7) | (a_ok << 6) |
                     (b_ok << 5) | (mclk_seen << 4) | (fail_seen << 3) | (devi & 1))


def selftest():
    """离线自检：不碰板卡，用构造 CSV 钉住「解码顺序」与「三条结论分支」。"""
    checks = []

    def check(name, ok, extra=""):
        checks.append((name, ok, extra))

    # 临时文件放在仓库的 `_cam_build/`（已在 .gitignore 的 `/_*` 里）：
    # 受限沙箱通常不允许写系统临时目录，也不允许在新建的随机目录里创建文件。
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_cam_build")
    if not os.path.isdir(base):
        base = os.getcwd()
    tmpdir = base
    good = os.path.join(tmpdir, "_selftest_eeprom_good.csv")
    blank = os.path.join(tmpdir, "blank.csv")
    short = os.path.join(tmpdir, "short.csv")

    # T1：A=01..10、B=11..18（RTL 里 byte0 在 [7:0]）
    dbg1 = "%016x" % sum((0x01 + i) << (8 * i) for i in range(8))
    dbg2 = "%016x" % sum((0x09 + i) << (8 * i) for i in range(8))
    dbg3 = "%016x" % sum((0x11 + i) << (8 * i) for i in range(8))
    _write_csv(good, [(_mk_dbg0(22, 1, 1, 1, 1, 0, 0), dbg1, dbg2, dbg3)])
    _, rows = load_rows(good)
    d = decode(rows[-1])
    check("T1 魔数/标志位解码正确", d["magic"] == MAGIC and d["done"] == 1 and
          d["last_qi"] == 22 and d["a_ok"] == 1 and d["b_ok"] == 1)
    check("T1 A 的 byte0..15（LSB-first）", [d["a_b%d" % i] for i in range(16)] == list(range(1, 17)),
          "a=%s" % [hex(d["a_b%d" % i]) for i in range(16)])
    check("T1 B 的 byte0..7", [d["b_b%d" % i] for i in range(8)] == list(range(0x11, 0x19)))

    # T2：两台全 0xFF ⇒ 必须落到「无区分力」而不是「同一颗芯片」
    ff = "ff" * 8
    _write_csv(blank, [(_mk_dbg0(22, 1, 1, 1, 1, 0, 1), ff, ff, ff)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_blank = main([blank])
    out = buf.getvalue()
    check("T2 全 0xFF ⇒ 无区分力（不是'同一颗芯片'）",
          "两台器件都读回全 0xFF" in out and "结论：B(0x58) 与 A(0x50) 前 8 字节" not in out)
    check("T2 退出码 0", rc_blank == 0)

    # T3：坏 CSV（只有两行）⇒ 必须报错退出 1，不许崩
    with open(short, "w", encoding="utf-8") as f:
        f.write("a,b\n1,2\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_short = main([short])
    check("T3 坏 CSV 报错退出 1（不崩）", rc_short == 1 and "CSV 读不动" in buf.getvalue())

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, extra in checks:
        print("  [%s] %s%s" % ("ok" if ok else "!!", name, ("  " + extra) if (extra and not ok) else ""))
    print("RESULT: %s (%d/%d)" % ("PASS" if passed == len(checks) else "FAIL", passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    console_utf8()
    ap = argparse.ArgumentParser(description="判读 EEPROM dump 探针的 ILA CSV")
    ap.add_argument("csv", nargs="?", help="ILA 导出的 CSV 路径（--selftest 时可省略）")
    ap.add_argument("--raw", action="store_true", help="额外打印解码后的原始字段")
    ap.add_argument("--json", help="把结论另存为 JSON")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不需要板卡/CSV）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.csv:
        ap.error("缺 CSV 路径（或加 --selftest）")

    try:
        _, rows = load_rows(args.csv)
    except CsvError as exc:
        print("CSV 读不动：%s" % exc)
        print("RESULT: FAIL（CSV 读不动）")
        return 1

    # 取最后一行的终态；若最后一行不是 done，则回退到最后一个 done 行
    last = rows[-1]
    d_last = decode(last)
    if last.get("dbg0") is not None and not d_last.get("done", 0):
        for r in reversed(rows):
            d = decode(r)
            if d.get("done", 0):
                d_last = d
                break

    d = d_last
    a_bytes = [d.get("a_b%d" % i, 0) for i in range(8)] + [d.get("a_b%d" % i, 0) for i in range(8, 16)]
    b_bytes = [d.get("b_b%d" % i, 0) for i in range(8)]

    print("=" * 74)
    print("EEPROM dump 判读 —— %s" % args.csv)
    print("采样数 = %d（取终态行）" % len(rows))
    print("=" * 74)
    print("魔数 magic      = 0x%04X %s" % (d.get("magic", 0),
                                          "（正确，是本探针的数据）" if d.get("magic", 0) == MAGIC
                                          else "（⚠️ 不是 0xCAFE：位流不配套？）"))
    print("终态 done       = %d" % d.get("done", 0))
    print("最后一步 last_qi= %d" % d.get("last_qi", 0))
    print("A(0x50) 应答    = %d" % d.get("a_ok", 0))
    print("B(0x58) 应答    = %d" % d.get("b_ok", 0))
    print("出现过 NACK     = %d" % d.get("fail_seen", 0))
    print("CAM_CLK 有翻转  = %d" % d.get("mclk_seen", 0))
    if args.raw:
        print("原始字段        = %s" % json.dumps(d, ensure_ascii=False, sort_keys=True))

    print("-" * 74)
    print("A(7bit 0x50 = 8bit 0xA0) 前 16 字节：")
    print(hexdump(a_bytes))
    print("B(7bit 0x58 = 8bit 0xB0) 前 8 字节（只带了 8 字节）：")
    print(hexdump(b_bytes))

    print("-" * 74)
    a_zero = all(x == 0xFF for x in a_bytes)
    a_same = all(a_bytes[i] == b_bytes[i] for i in range(8))
    b_blank = all(x == 0xFF for x in b_bytes)

    if not d.get("done", 0):
        print("结论：没跑到终态（done=0）⇒ 这轮数据不可用，重跑 read_dump.tcl")
        rc = 1
    elif not d.get("a_ok", 0):
        print("结论：连 0x50(A) 都没应答 ⇒ 总线/上拉/供电先查，dump 没有意义")
        rc = 1
    elif not d.get("b_ok", 0):
        print("结论：0x58(B) 本轮无应答 ⇒ 0x58 那次 ACK 不是稳定的第二颗器件")
        print("      （上一轮 cam_detect 见到过 0xB0，可能是模块未通电/接触不良）")
        rc = 0
    elif a_zero and b_blank:
        print("结论：两台器件都读回全 0xFF ⇒ **本测试无区分力**")
        print("      空白 EEPROM 读出来本来就是全 1，所以「内容相同」既可能是同一颗芯片，")
        print("      也可能只是两颗都空 ⇒ 改用下面任一条判定归属：")
        print("      · 拔掉相机 FFC 后重跑本探针：0x58 若消失 ⇒ 它在模块上；若仍在 ⇒ 在板上")
        print("      · 万用表量 J11 pin15 对 GND：3.3 V = 模块已通电（最直接）")
        rc = 0
    elif a_same:
        print("结论：B(0x58) 与 A(0x50) 前 8 字节**完全相同**且非空白 ⇒ 同一颗芯片被两个地址命中")
        print("      ⇒ 0x58 不是模块自带 EEPROM，故障点与原判断相同（传感器本体/XCLR）")
        rc = 0
    elif b_blank:
        print("结论：B(0x58) 应答但内容全 0xFF ⇒ 那是一颗空白 EEPROM（大概率在模块上）")
        print("      ⇒ 模块已通电、排线通；故障点转向传感器本体 / XCLR")
        rc = 0
    else:
        print("结论：B(0x58) 应答且内容与 A **不同** ⇒ 独立器件")
        print("      ⇒ 极可能为**相机模块自带的 EEPROM** ⇒ 模块已通电、排线可用")
        print("      ⇒ 故障点转向传感器本体 / XCLR(pin 22) / 模块 XCLR 使能")
        rc = 0

    if a_zero:
        print("附注：A(0x50) 内容全 0xFF —— 板上 24C32 是空白片（未烧写），属正常")

    print("RESULT: %s" % ("PASS" if rc == 0 else "FAIL"))
    if args.json:
        payload = {
            "csv": args.csv,
            "samples": len(rows),
            "decoded": d,
            "a_bytes_hex": "".join("%02X" % x for x in a_bytes),
            "b_bytes_hex": "".join("%02X" % x for x in b_bytes),
            "a_all_ff": a_zero,
            "b_all_ff": b_blank,
            "a_eq_b_prefix8": a_same,
            "rc": rc,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("JSON = %s" % args.json)
    return rc


if __name__ == "__main__":
    sys.exit(main())
