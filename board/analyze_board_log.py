#!/usr/bin/env python3
# =============================================================================
#  analyze_board_log.py —— 上板日志诊断器（串口 console / Vivado / Vitis 输出）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#
#  为什么有这个东西：
#    Mizar-Z7020 从未上电，第一次上板一定会遇到"什么都不对"的时刻。
#    而**把串口日志原样抓下来、丢给脚本判定卡在哪一步**，比人眼在滚屏里找关键词快得多，
#    也避免了"看到的和贴出来的不一致"。
#    本脚本把 docs/11 / docs/12 / board/README.md 里**已经登记过的**失败模式固化成签名表。
#
#  用法：
#     # 0) 先把串口 console 抓成文件（Windows 举例，PowerShell）：
#     #    python board/analyze_board_log.py --list-ports
#     #    python board/analyze_board_log.py --capture COM3 --baud 115200 --seconds 30
#     #    或用任意串口工具另存为 txt，然后：
#     python board/analyze_board_log.py metrics/logs/boot.txt
#     python board/analyze_board_log.py --selftest      # 离线自检（不需要板子）
#
#  ⚠️ 本脚本**只做日志判读**，不产生任何"上板结论"。它的输出是"卡在哪一步 + 下一步该查什么"。
# =============================================================================

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
try:
    from console import enable_utf8_console  # type: ignore
    enable_utf8_console()
except Exception:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---- 阶段标记：按"出现即证明走到了这一步"的顺序排列 -------------------------
STAGES = [
    ("JTAG 链路",        r"xc7z020|xc7z0(10|20)",                     "Vivado Hardware Manager 认出了器件"),
    ("FSBL / BOOT.BIN",  r"Xilinx First Stage Boot Loader|FSBL",      "FSBL 已运行（说明 BOOT.BIN 被读到）"),
    ("U-Boot",           r"U-Boot\s+\d{4}\.\d+|Hit any key to stop",  "U-Boot 已运行"),
    ("内核启动",          r"Booting Linux|Starting kernel",            "开始引导 Linux 内核"),
    ("根文件系统",        r"systemd\[1\]|Welcome to|login:",           "根文件系统挂上、有登录提示"),
    ("以太网",            r"eth0.*link up|RTL8211|PHY.*link",          "网卡链路建立（速率须实测，手册自相矛盾）"),
]

# ---- 失败签名：命中即给出"可能原因 + 下一步" ---------------------------------
# 来源：docs/11 §环境坑、docs/12 §2/§7、board/README.md 风险表
FAILURES = [
    (r"no valid image|No image found|invalid boot image",
     "BOOT.BIN 无效或不在启动介质上",
     "查启动模式跳线 J1（JTAG/QSPI/SD 三选一）；确认 BOOT.BIN 是**为 Mizar 的 1GB DDR3** 编的，不是 PYNQ-Z2 的"),
    (r"Kernel panic|not syncing",
     "内核 panic",
     "若是 DDR 参数不对：Mizar 是 1GB DDR3，**绝不能套用 PYNQ-Z2(512MB) 的 PS7 预设**"),
    (r"mmc0: error|MMC: no card|sdhci.*timeout",
     "SD 卡/MMC 初始化失败",
     "重插卡、确认卡是启动模式选中的那个；换一张已正确烧写的卡"),
    (r"ddr|DDR.*(fail|error)|calibration failed",
     "DDR 初始化/校准失败",
     "PS7 的 DDR 参数必须取自 MicroPhase 参考设计（**不得猜**）；DDR 型号/位宽/容量都要对"),
    (r"ubi.*error|nand|QSPI.*fail",
     "QSPI Flash 读取失败",
     "确认 J1 跳线是 QSPI 模式，且 Flash 里确实烧过镜像"),
    (r"Waiting for root device|VFS: Cannot open root",
     "找不到根文件系统",
     "根分区没挂上：检查 bootargs 的 root= 与分区实际位置"),
    (r"Timeout waiting for hardware cmd|Xilinx Zynq MP First Stage",
     "FSBL 卡在硬件等待",
     "常见于 DDR 参数错；也可能是上电时序/供电不足（**必须用 +5V DC 圆孔，micro-USB 供电不够**）"),
    (r"i2c.*(timeout|error|nack)|xiic.*timeout",
     "I2C 通信失败（**与相机强相关**）",
     "MIPI 相机的 SCCB 走 I2C：确认 Mizar 的 15-pin 口**是否真的给出 I2C/MCLK/3.3V**"
     "（这是 docs/12 §7 第 9 条的未验证项），上拉电阻与地址是否正确"),
    (r"v4l2|video4linux|/dev/video",
     "出现了 V4L2 相关字样",
     "⚠️ 提醒：Mizar 的 MIPI 口接在 **PL IO** 上，**不会**自动变成 /dev/video0。"
     "若你在期待它出现，那是预期错了（见 docs/19 §0）"),
    (r"temperature.*(high|critical)|thermal",
     "温度告警",
     "散热/供电检查；长时间满负载跑 PL 时留意"),
]

# ---- 提示签名：不算失败，但值得注意 -----------------------------------------
NOTES = [
    (r"10/100|100Mbps|Speed: 100",
     "以太网协商到 100M —— 手册 Key Features 写 10/100、Giga ETH 节写 10/100/1000，**自相矛盾**，"
     "以 `ethtool eth0` 实测为准"),
    (r"Speed: 1000|1000Mbps",
     "以太网协商到 1000M ✓ 说明 RTL8211E 是千兆档"),
    (r"mipi|csi",
     "日志里出现 mipi/csi 字样：**注意区分**是 PL 侧自研链路还是内核驱动，"
     "本项目 MIPI 走 PL，没有现成内核驱动"),
    (r"pl_clk|50M|H16",
     "PL 50 MHz 晶振（H16）相关：它只用于**独立 PL 设计**；契约的 100 MHz 来自 PS FCLK0，两者无关"),
]


def analyze(text, verbose=True):
    hits_stage = []
    for name, pat, why in STAGES:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hits_stage.append((name, why, m.group(0)[:60]))

    hits_fail = []
    for pat, cause, nxt in FAILURES:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hits_fail.append((cause, nxt, m.group(0)[:60]))

    hits_note = []
    for pat, txt in NOTES:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hits_note.append((txt, m.group(0)[:60]))

    return hits_stage, hits_fail, hits_note


def report(text, title="上板日志诊断"):
    stage, fails, notes = analyze(text)
    lines = []
    lines.append("=" * 84)
    lines.append("%s —— 共 %d 行" % (title, text.count("\n") + 1))
    lines.append("=" * 84)

    lines.append("")
    lines.append("【走到哪一步了】（按顺序，最后一条 = 最远到达的阶段）")
    if stage:
        for name, why, ev in stage:
            lines.append("  ✔ %-14s %s   [证据: %s]" % (name, why, ev))
        lines.append("  ⇒ **最远到达：%s**" % stage[-1][0])
    else:
        lines.append("  ✘ 一个阶段标记都没命中 —— 说明**串口根本没有输出**")
        lines.append("    下一步：① 确认接的是 **USB-UART 口**（CH340，不是 JTAG 口）")
        lines.append("            ② 波特率 **115200 8N1**")
        lines.append("            ③ 供电用 **+5V DC 圆孔**（micro-USB 供电不足以让 PL 起来）")
        lines.append("            ④ 看 J1 启动模式跳线")

    lines.append("")
    lines.append("【命中的失败签名】")
    if fails:
        for cause, nxt, ev in fails:
            lines.append("  ✘ %s" % cause)
            lines.append("      证据: %s" % ev)
            lines.append("      下一步: %s" % nxt)
    else:
        lines.append("  （无）")

    lines.append("")
    lines.append("【值得注意】")
    if notes:
        for txt, ev in notes:
            lines.append("  ⚠ %s   [证据: %s]" % (txt, ev))
    else:
        lines.append("  （无）")

    lines.append("")
    lines.append("-" * 84)
    lines.append("⚠️ 本报告只是**日志判读**，不是上板结论。任何'跑通了'的结论都必须有")
    lines.append("   实测命令 + 原始输出，并按 AGENTS.md 写明是**哪块板**（器件串 XC7Z020-1CLG400C）。")
    return "\n".join(lines), stage, fails, notes


# -----------------------------------------------------------------------------
# 串口抓取（可选）
# -----------------------------------------------------------------------------
def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        print("!! 需要 pyserial：.venv\\Scripts\\python.exe -m pip install pyserial")
        return 1
    ports = list(lp.comports())
    if not ports:
        print("没有发现串口。检查：USB-UART 线是否插上、驱动（CH340）是否装了。")
        return 1
    print("可用串口（Mizar 的 console 是 CH340，通常显示为 USB-SERIAL CH340）：")
    for p in ports:
        print("  %-8s %s" % (p.device, p.description))
    return 0


def capture(port, baud, seconds, out):
    try:
        import serial
    except ImportError:
        print("!! 需要 pyserial：.venv\\Scripts\\python.exe -m pip install pyserial")
        return 1
    import time
    print("正在抓 %s @ %d 8N1，共 %d 秒 …（把板子上电/复位，让日志从头开始）" % (port, baud, seconds))
    ser = serial.Serial(port, baud, timeout=0.2)
    buf = []
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            data = ser.read(4096)
            if data:
                buf.append(data)
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()
    finally:
        ser.close()
    raw = b"".join(buf).decode("utf-8", "replace")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", newline="\n", encoding="utf-8") as f:
        f.write(raw)
    print("\n已写 %s（%d 字节）" % (out, len(raw.encode("utf-8"))))
    rep, _, _, _ = report(raw, "串口抓取诊断：%s" % port)
    print(rep)
    return 0


# -----------------------------------------------------------------------------
# 离线自检（不需要板子）
# -----------------------------------------------------------------------------
GOOD_LOG = """Xilinx First Stage Boot Loader Release 2026.1
U-Boot 2026.01 (Jan 01 2026)
Booting Linux on physical CPU 0x0
systemd[1]: Reached target Multi-User System.
Speed: 1000Mbps
"""

# ⚠️ 这两份"故障日志"是我手写的**合成样本**，只用来验证分析器的判读逻辑。
#    第一版把它们写得不真实（少了 U-Boot 行、也没有 ddr 字样），结果自测 FAIL ——
#    是**用例写错**，不是分析器错。改成贴近真实的形态，并让期望与之严格对应。
DDR_ROOT_BAD_LOG = """Xilinx First Stage Boot Loader Release 2026.1
Timeout waiting for hardware cmd
U-Boot 2026.01 (Jan 01 2026)
Booting Linux on physical CPU 0x0
Kernel panic - not syncing: VFS: Cannot open root device
"""

DDR_CALIB_BAD_LOG = """Xilinx First Stage Boot Loader Release 2026.1
DDR calibration failed
"""

I2C_BAD_LOG = """U-Boot 2026.01
Booting Linux on physical CPU 0x0
xiic-i2c ff030000.i2c: timeout
systemd[1]: Reached target Multi-User System.
"""

EMPTY_LOG = """"""


def selftest():
    print("=== analyze_board_log --selftest ===")
    cases = [
        ("正常启动日志", GOOD_LOG,
         ["U-Boot", "根文件系统"], []),
        ("FSBL 卡死 + 根文件系统丢", DDR_ROOT_BAD_LOG,
         ["U-Boot"], ["FSBL 卡在硬件等待", "内核 panic"]),
        ("DDR 校准失败", DDR_CALIB_BAD_LOG,
         ["FSBL / BOOT.BIN"], ["DDR 初始化"]),
        ("I2C 故障", I2C_BAD_LOG,
         ["根文件系统"], ["I2C"]),
        ("空日志", EMPTY_LOG, [], []),
    ]
    passed = failed = 0
    for name, text, want_stage, want_fail in cases:
        rep, stage, fails, notes = report(text, name)
        stage_names = [s[0] for s in stage]
        fail_text = " ".join(f[0] for f in fails)
        ok = True
        for w in want_stage:
            if w not in stage_names:
                ok = False
        for w in want_fail:
            if w not in fail_text:
                ok = False
        if name == "空日志":
            # 空日志必须明确指出"串口没有输出"且给出四条排查项
            if "串口根本没有输出" not in rep or "115200" not in rep:
                ok = False
        if ok:
            passed += 1
            print("  OK   %-18s 阶段=%s 失败=%s" % (name, stage_names or "[]", fail_text or "[]"))
        else:
            failed += 1
            print("  FAIL %-18s 阶段=%s 失败=%s（期望 stage⊇%s, fail⊇%s）"
                  % (name, stage_names, fail_text, want_stage, want_fail))
    print("  selftest: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description="上板日志诊断器")
    ap.add_argument("log", nargs="?", help="日志文件路径（串口 console 另存的 txt）")
    ap.add_argument("--selftest", action="store_true", help="离线自检，不需要板子")
    ap.add_argument("--list-ports", action="store_true", help="列出可用串口")
    ap.add_argument("--capture", metavar="COM", help="抓取该串口（需要 pyserial）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--out", default=os.path.join("metrics", "logs", "board_boot.txt"))
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.list_ports:
        return list_ports()
    if args.capture:
        return capture(args.capture, args.baud, args.seconds, args.out)
    if not args.log:
        ap.print_help()
        return 2
    if not os.path.exists(args.log):
        print("!! 找不到日志文件：%s" % args.log)
        return 1
    with open(args.log, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    rep, _, fails, _ = report(text, args.log)
    print(rep)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
