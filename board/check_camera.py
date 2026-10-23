# -*- coding: utf-8 -*-
"""
check_camera.py —— 把 ILA 抓到的调试总线 CSV 判读成「FFC 处的 MIPI 摄像头连上了没」

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

它在整条链路的什么位置
    `board/cam_detect/pl_cam_detect.v`（PL 探针，上电即自动扫描 SCCB 总线 + 读相机型号寄存器）
      → `board/cam_detect/build_dbg.tcl`（带 ILA 的位流）
      → `board/cam_detect/read_ila.tcl`（编程 + trigger_now + 导出 CSV）
      → **本脚本**（PC 侧判读 → 「连上 / 没连上」）
      → 用 ILA 而不用 UART 的原因见 `board/imx219_probe/README.md`：拿不到 PL 的裸 GPIO UART。

用法
    python board/check_camera.py <ila.csv>              # 判定（退出码 0=连上，1=没连上）
    python board/check_camera.py <ila.csv> --json out.json
    python board/check_camera.py <ila.csv> --raw        # 连每行解码结果一起打印
    python board/check_camera.py --selftest             # 离线自检（不需要板卡，用的就是本机）

判据（全部来自真实硬件读数，不做任何推测性补齐）
    ① hit_n ≥ 1                        总线上有器件应答
    ② cam_ack = 1                      7bit 0x10 / 8bit 0x20 这台相机应答了
    ③ cam16_rec/cam8_rec >> 16 == 0x0219   IMX219 的 MODEL_ID（寄存器 0x00/0x01）
    ④ mclk_seen / mclk_period          CAM_CLK(pin12) 上量到的频率
      f_cam = MEAS_N × CLK_HZ ÷ mclk_period（默认 4096 × 50 MHz）

⚠️ 读这份结论时必须一起看的三件事
   1. **`cam_ack=1` 只说明"有个器件在 0x10 应答了"**，不代表图像通路能用；
      真正的"能用"要到 M3 的 DMA/像素链上去证（本项目现在只有本探针）。
   2. **`hit_a[]` 里出现 0xA0/0xB0 是板载 24C32 EEPROM**（与 SCCB 共总线、板上已有 4.7K 上拉），
      **不是相机**；只有 0x20（= 7bit 0x10）才是相机。共总线这件事见 `docs/31`。
   3. **CAM_CLK 频率**：板上 U19 经 R156 给 pin12 送的是 50 MHz，而 IMX219 的 INCK 规格是
      6~27 MHz —— 若"不应答 + 量到 50 MHz"，头号嫌疑就是这条（详见 `board/cam_detect/README.md`）。

退出码：0 = 判定为「连上」（cam_ack=1 且型号读回正确）；1 = 其它一切（含 CSV 读不动）。
"""

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# 1. dbg 总线布局（**与 board/cam_detect/pl_cam_detect.v 的 assign dbg0..dbg3 逐位一致**）
#    改 RTL 那三行就必须改这里，否则判读会静默错位。
# ---------------------------------------------------------------------------
#   RTL 里的拼接是【MSB-first】（Verilog {a,b,c} 左边是最高位）：
#   dbg0[31:0] = { 7'b0, cam_ack, scan_a[7:0], step[3:0], ms[3:0],
#                  mclk_seen, mclk_self, i2c_act, pwr_done, hit_n[3:0] }
#   dbg1[63:0] = { rec0[31:0], hit_a[0][7:0], mclk_period[15:0], mclk_npulse[7:0] }
#   dbg2[63:0] = { rec1[31:0], hit_a[1][7:0], hit_a[2][7:0], hit_a[3][7:0], hit_a[4][7:0] }
#   dbg3[63:0] = { cam16_rec[31:16], cam8_rec[31:0], hit_a[5][7:0], hit_a[6][7:0] }
#   ⚠️ 下面 DBG_LAYOUT 是【LSB-first】（解码/打包都从 shift=0 起），所以字段顺序
#      必须与上面 MSB-first 拼接【正好相反】：列表第一个 = 最低位。T10 用手算的
#      RTL 拼接值钉住这一点，防止有人按 MSB-first 顺序照抄导致静默反序。
DBG_LAYOUT = {
    "dbg0": [("hit_n", 4), ("pwr_done", 1), ("i2c_act", 1), ("mclk_self", 1),
             ("mclk_seen", 1), ("ms", 4), ("step", 4), ("scan_a", 8),
             ("cam_ack", 1), ("_rsvd0", 7)],
    "dbg1": [("mclk_npulse", 8), ("mclk_period", 16), ("hit_a0", 8), ("rec0", 32)],
    "dbg2": [("hit_a4", 8), ("hit_a3", 8), ("hit_a2", 8), ("hit_a1", 8), ("rec1", 32)],
    "dbg3": [("hit_a6", 8), ("hit_a5", 8), ("cam8_rec", 32), ("cam16_hi", 16)],
}

STEP_NAMES = {0: "IDLE", 1: "PWRUP", 2: "SCAN", 3: "REC0", 4: "REC1",
              5: "CAM16", 6: "CAM8", 7: "DONE"}

# 已知的 8bit 写地址 → 说明（判读时把"谁应答了"讲清楚）
KNOWN_ADDRS = {
    0x20: "相机（IMX219，7bit 0x10）",
    0x21: "相机（读地址 0x21）",
    0xA0: "板载 EEPROM 24C32 页0（不是相机）",
    0xB0: "板载 EEPROM 24C32 页1（不是相机）",
    0x30: "相机（IMX219 备选 7bit 0x18？按数据手册核对）",
    0x60: "EEPROM 24C32（备选地址）",
}

DEFAULT_MODEL_ID = 0x0219      # IMX219 MODEL_ID 高 16 位（寄存器 0x00/0x01 = 0x02,0x19）


def console_utf8():
    """让本脚本在 Windows GBK 控制台上不因中文/⚠️ 崩掉（与 board/dma_test.py 同一做法）。

    ⚠️ 2026-10-02 实测踩过：脚本打印完 `RESULT: PASS` 之后才因 UnicodeEncodeError 崩，
    退出码变成 1 —— 人眼看着 PASS、机器记着 FAIL。`errors="replace"` 只把编不出的字符
    显示成 '?'，绝不让程序崩。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        rec = getattr(stream, "reconfigure", None)
        if rec is None:
            continue
        try:
            rec(encoding="utf-8", errors="replace")
        except Exception:      # noqa: BLE001 —— 某些包装流不允许 reconfigure
            pass


# ---------------------------------------------------------------------------
# 2. CSV 解析
# ---------------------------------------------------------------------------
class CsvError(Exception):
    """CSV 读不动（格式/字段不认）—— 由调用方转成一句人话，不抛栈。"""


def parse_header(text):
    """把表头行拆成 [(base_name, msb, lsb, width), ...]。

    兼容两种真实形态：
      · probe 形态：`probe0[31:0],probe1[63:0],...`（create_ip 出来的 ILA 默认如此）
      · 逐信号形态：Vivado 把 `{a,b}` 这种拼接探针拆成逐信号列（归档的
        `board/imx219_probe/ila_capture.csv` 第一行就是 `dbg_hit_n[3:0],dbg_sq[2:0],...`）
    另外兼容 `hit_a[0][7:0]` 这种"数组元素 + 位域"的双重下标（取最后一段当位域）。
    """
    out = []
    for raw in text.split(","):
        name = raw.strip()
        if not name:
            continue
        base, msb, lsb = name, None, None
        # 末尾若形如 [m:l] 则当位域；否则形如 [i] 的当数组下标（并入 base）
        if name.endswith("]") and ":" in name[name.rfind("["):]:
            head, _, bitrange = name.rpartition("[")
            msb_s, _, lsb_s = bitrange[:-1].partition(":")
            try:
                msb, lsb = int(msb_s), int(lsb_s)
            except ValueError:
                msb, lsb = None, None
            base = head
        out.append((base, msb, lsb))
    return out


def _norm(name):
    """名字归一化：`hit_a[0]` / `hit_a_0` / `hit_a0` 都变成 `hit_a0`。"""
    n = name.strip().lower().replace(" ", "")
    for ch in "[]":
        n = n.replace(ch, "")
    n = n.replace("hit_a_", "hit_a")
    # Vivado ILA 导出时探针按位流里的网名命名：本设计探针叫 dbg0..dbg3，
    # 于是表头是 `dbg0[31:0]` 而非 create_ip 默认的 `probe0[31:0]`。两者同义，
    # 统一成 probeN 供后面拼接解码（这是 2026-10-19 真机读回才发现的实际形态）。
    if n.startswith("dbg") and n[3:].isdigit():
        n = "probe" + n[3:]
    return n


def decode_rows(header_cells, rows):
    """把每行 CSV 解成字段字典。返回 (list[dict], 说明字符串列表)。

    优先按 probe0..probe3 拼接（宽度取自表头位域）；否则按已知字段名逐列取。
    """
    names = [_norm(c[0]) for c in header_cells]
    widths = {n: (c[1] - c[2] + 1) if (c[1] is not None and c[2] is not None) else None
              for n, c in zip(names, header_cells)}
    notes = []

    probe_idx = {}
    for i, n in enumerate(names):
        if n.startswith("probe") and n[5:].isdigit():
            probe_idx[int(n[5:])] = i
    use_probe = all(k in probe_idx for k in (0, 1, 2, 3))

    if use_probe:
        notes.append("按 probe0..probe3 拼接解码（dbg0..dbg3）")
        need = {"dbg0": 32, "dbg1": 64, "dbg2": 64, "dbg3": 64}
        for k, want in need.items():
            got = widths.get("probe%d" % int(k[-1]))
            if got is not None and got != want:
                raise CsvError("探针 %s 宽度 %d，期望 %d —— 位流与解码表不配套" %
                               (k, got, want))
    else:
        notes.append("按逐信号列名解码（未找到 probe0..probe3）")

    decoded = []
    for lineno, cells in rows:
        if use_probe:
            vals = {}
            for i, name in enumerate(names):
                if i >= len(cells):
                    raise CsvError("第 %d 行列数不足（%d < %d）" % (lineno, len(cells), len(names)))
                if cells[i].strip() == "":
                    continue
                try:
                    vals[name] = int(cells[i].strip(), 16)
                except ValueError:
                    vals[name] = 0
            f = {}
            for dbg, fields in DBG_LAYOUT.items():
                bus = vals.get("probe%d" % int(dbg[-1]), 0)
                shift = 0
                for fname, w in fields:
                    f[fname] = (bus >> shift) & ((1 << w) - 1)
                    shift += w
                if shift != (32 if dbg == "dbg0" else 64):
                    raise CsvError("内部布局表 %s 宽度 %d ≠ 总线宽度" % (dbg, shift))
        else:
            f = {}
            for i, name in enumerate(names):
                if i >= len(cells):
                    break
                raw = cells[i].strip()
                if raw == "":
                    continue
                try:
                    val = int(raw, 16)
                except ValueError:
                    continue
                if name in [x[0] for x in DBG_LAYOUT["dbg0"]] + \
                           [x[0] for x in DBG_LAYOUT["dbg1"]] + \
                           [x[0] for x in DBG_LAYOUT["dbg2"]] + \
                           [x[0] for x in DBG_LAYOUT["dbg3"]]:
                    f[name] = val
            missing = [k for k in ("step", "hit_n", "cam_ack", "rec0", "rec1",
                                   "mclk_seen", "mclk_period", "cam8_rec")
                       if k not in f]
            if missing:
                raise CsvError("逐信号表头里认不出这些字段：%s；实际列名：%s" %
                               (",".join(missing), ",".join(names[:16])))
        f["cam16_rec"] = (f.get("cam16_hi", 0) << 16)
        f["hit_a"] = [f.get("hit_a%d" % i, 0) for i in range(7)]
        f["_line"] = lineno
        decoded.append(f)
    return decoded, notes


def load_csv_text(text, tag="<内存>"):
    """从 CSV 文本解出 (header_cells, rows)。第 2 行若以 Radix 开头则当 radix 行丢掉。

    ⚠️ 自检**刻意走这条纯内存通路**（不写任何临时文件）：本机受限沙箱连
    `%TEMP%` 都不给写（实测 `PermissionError: [Errno 13]`），一写文件自检就废。
    """
    lines = [ln.rstrip("\r\n") for ln in text.splitlines() if ln.strip() != ""]
    if len(lines) < 3:
        raise CsvError("%s 行数太少（%d）：不像 ILA 导出的 CSV" % (tag, len(lines)))
    header = parse_header(lines[0])
    body = lines[1:]
    if body and body[0].lstrip().lower().startswith("radix"):
        body = body[1:]
    rows = []
    for i, ln in enumerate(body, start=3):
        cells = ln.split(",")
        if len(cells) < 3:
            continue
        rows.append((i, cells))
    if not rows:
        raise CsvError("%s 没有数据行" % tag)
    return header, rows


def load_csv(path):
    """读 ILA CSV 文件 → (header_cells, rows)。"""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        raise CsvError("读不到文件：%s（%s）" % (path, exc))
    return load_csv_text(text, tag=path)


def pick_best(decoded):
    """从所有采样里挑"最接近跑完"的那一档 —— ILA 抓到的是**一段窗口**，
    前几拍可能还停在扫描中间（step<7），所以要挑终态而不是第 0 行。"""
    def key(f):
        return (1 if f.get("step") == 7 else 0, f.get("hit_n", 0), f.get("cam_ack", 0),
                f.get("mclk_seen", 0))
    return max(decoded, key=key)


# ---------------------------------------------------------------------------
# 3. 判定
# ---------------------------------------------------------------------------
def cam_clock_hz(f, meas_n, clk_hz):
    """由 mclk_period（两次翻转之间的 clk 数）反推 CAM_CLK 频率；量不到返回 None。"""
    if not f.get("mclk_seen"):
        return None
    period = f.get("mclk_period", 0)
    if period in (0, 0xFFFF):
        return None
    return meas_n * clk_hz / period


def judge(f, expect_model=DEFAULT_MODEL_ID, meas_n=4096, clk_hz=50_000_000):
    """把一行解码结果判成结论。返回 dict（全部字段都来自真实读数）。"""
    hit_n = f.get("hit_n", 0)
    cam_ack = bool(f.get("cam_ack", 0))
    model16 = (f.get("cam16_rec", 0) >> 16) & 0xFFFF
    model8 = (f.get("cam8_rec", 0) >> 16) & 0xFFFF
    model_ok = (model16 == expect_model) or (model8 == expect_model)
    clk = cam_clock_hz(f, meas_n, clk_hz)

    if hit_n == 0:
        verdict = "BUS_NO_DEVICE"
    elif cam_ack and model_ok:
        verdict = "CONNECTED_OK"
    elif cam_ack:
        verdict = "CONNECTED_NO_MODEL"
    elif clk is None:
        verdict = "CAMERA_NO_CLOCK"
    else:
        verdict = "CAMERA_NOT_ACK"

    return {
        "verdict": verdict,
        "connected": verdict == "CONNECTED_OK",
        "hit_n": hit_n,
        # ★ 只取真正命中的前 hit_n 个：hit_a[5]/hit_a[6] 在"相机没应答"时
        #   被 STEP_RISE 借去放总线负载测量值，若把整张表都当地址列出来
        #   会凭空冒出两个"未知器件 0x06"（2026-10-20 实测踩到）。
        "hit_addrs": [a for a in f.get("hit_a", [])[:hit_n] if a],
        "cam_ack": cam_ack,
        "model_id_16bit_path": model16,
        "model_id_8bit_path": model8,
        "model_expected": expect_model,
        "model_ok": model_ok,
        "mclk_seen": bool(f.get("mclk_seen", 0)),
        "mclk_period": f.get("mclk_period", 0),
        "cam_clk_hz": clk,
        "step": f.get("step"),
        "step_name": STEP_NAMES.get(f.get("step"), "?"),
        "rec0": f.get("rec0", 0),
        "rec1": f.get("rec1", 0),
        "cam16_rec": f.get("cam16_rec", 0),
        "cam8_rec": f.get("cam8_rec", 0),
        "scan_a": f.get("scan_a", 0),
        "ms": f.get("ms", 0),
        # ★ 2026-10-20 新增：总线负载测量（STEP_RISE 把释放后"还被采成低"的拍数
        #   放在 hit_a[5]/hit_a[6]）。没有相机应答时它才有意义：拍数越大 = 总线电容越大，
        #   用来判断"模组到底有没有摸到 SCL/SDA 两根线上"（拔/插模组各测一次对比）。
        "bus_rise_scl": f.get("hit_a5", 0),
        "bus_rise_sda": f.get("hit_a6", 0),
        "csv_line": f.get("_line"),
    }


VERDICT_TEXT = {
    "CONNECTED_OK":       "连上 —— 相机地址应答，且型号寄存器读回正确",
    "CONNECTED_NO_MODEL": "可疑 —— 相机地址应答了，但型号寄存器读不回",
    "CAMERA_NOT_ACK":     "没连上 —— 总线有器件（见 hit_addrs）但相机地址不应答",
    "CAMERA_NO_CLOCK":    "没连上 —— 相机不应答，且 CAM_CLK 上没量到时钟",
    "BUS_NO_DEVICE":      "没连上 —— 整条总线上没有任何器件应答",
}

HINTS = {
    "BUS_NO_DEVICE": [
        "先查供电与地：FFC 插反/没插到位是最常见的",
        "SCCB 与板载 24C32 共总线，板上已有 4.7K 上拉 —— 连 EEPROM 都不应答，说明是总线/供电层的问题",
        "确认 pin13/pin14 的引脚约束与实物一致（A20/E17）",
    ],
    "CAMERA_NOT_ACK": [
        "相机侧供电（排线 pin1/2 的 3.3V 与 GND）与 MCLK 是首要怀疑对象",
        "IMX219 没有 INCK 就不接 SCCB —— 用 --json 看 cam_clk_hz 是否量到、频率是多少",
        "查看相机侧 FFC 是否插反（pin1 对 pin1）",
    ],
    "CAMERA_NO_CLOCK": [
        "pin12(CAM_CLK) 上没量到时钟 ⇒ 相机拿不到 INCK，按数据手册就不会应答 SCCB",
        "板上 U19 经 R156 送的是 50 MHz，而 IMX219 的 INCK 是 6~27 MHz —— 要核实实物",
        "可改用 build_dbg.tcl 里的 DRIVE_MCLK_SEL=1（FPGA 驱动 25 MHz）做对照实验",
    ],
    "CONNECTED_NO_MODEL": [
        "地址应答了但寄存器读不回：可能是上电序列/时钟不对，或读时序口径不符",
        "存档本次 CSV 与 check_camera.py --raw 的输出，作为下一轮排查的原始证据",
    ],
    "CONNECTED_OK": [
        "下一步（M3）才轮到像素通路：DMA/像素链，不在本探针范围里",
    ],
}


def explain(res):
    """把结论渲染成人看的几行。"""
    out = []
    out.append("结论：%s（%s）" % (res["verdict"], VERDICT_TEXT[res["verdict"]]))
    out.append("  采样行：CSV 第 %s 行；step=%s(%s) ms=%d scan_a=0x%02X"
               % (res["csv_line"], res["step"], res["step_name"], res["ms"], res["scan_a"]))
    addrs = "、".join("0x%02X %s" % (a, KNOWN_ADDRS.get(a, "（未知器件）"))
                     for a in res["hit_addrs"]) or "（无）"
    out.append("  应答过的地址（8bit 写地址）：%s；hit_n=%d" % (addrs, res["hit_n"]))
    out.append("  相机地址应答（cam_ack）：%s" % ("是" if res["cam_ack"] else "否"))
    out.append("  型号寄存器：16bit 口径=0x%04X，8bit 口径=0x%04X，期望 0x%04X ⇒ %s"
               % (res["model_id_16bit_path"], res["model_id_8bit_path"],
                  res["model_expected"], "对得上" if res["model_ok"] else "对不上"))
    if res["cam_clk_hz"] is None:
        out.append("  CAM_CLK：没量到（mclk_period=%d）" % res["mclk_period"])
    else:
        out.append("  CAM_CLK：%.2f MHz（mclk_period=%d）"
                   % (res["cam_clk_hz"] / 1e6, res["mclk_period"]))
    out.append("  读回：rec0=0x%08X rec1=0x%08X cam16=0x%08X cam8=0x%08X"
               % (res["rec0"], res["rec1"], res["cam16_rec"], res["cam8_rec"]))
    # 只有当相机没应答时，STEP_RISE 才会把测量值写进 hit_a[5]/hit_a[6]
    if not res["cam_ack"]:
        out.append("  总线负载（释放后仍被采成低的拍数，1 拍=20 ns @50 MHz）："
                   "SCL=%d SDA=%d" % (res["bus_rise_scl"], res["bus_rise_sda"]))
        out.append("    ↑ 插/拔模组各测一次：若两次数值几乎一样，说明模组没有电气连到 SCL/SDA")
    for h in HINTS.get(res["verdict"], []):
        out.append("  · " + h)
    return "\n".join(out)


def run_file(path, expect_model, meas_n, clk_hz, emit_json=None, raw=False):
    header, rows = load_csv(path)
    decoded, notes = decode_rows(header, rows)
    best = pick_best(decoded)
    res = judge(best, expect_model, meas_n, clk_hz)
    res["csv"] = path
    res["samples"] = len(decoded)
    res["decode_notes"] = notes

    print("== check_camera.py ==")
    print("  文件：%s（%d 个采样）" % (path, len(decoded)))
    for n in notes:
        print("  解码：" + n)
    if raw:
        for f in decoded[:8]:
            print("   line %s: step=%s hit_n=%d cam_ack=%d scan_a=0x%02X  period=%d"
                  % (f["_line"], STEP_NAMES.get(f["step"], "?"), f["hit_n"],
                     f["cam_ack"], f["scan_a"], f["mclk_period"]))
        if len(decoded) > 8:
            print("   …（共 %d 行）" % len(decoded))
    print(explain(res))
    print("RESULT: %s" % ("PASS（相机连上）" if res["connected"] else "FAIL（相机没连上）"))
    if emit_json:
        with open(emit_json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        print("JSON: %s" % emit_json)
    return 0 if res["connected"] else 1


# ---------------------------------------------------------------------------
# 4. 离线自检（不需要板卡）：构造 CSV 文本 → 走同一套解码/判定
# ---------------------------------------------------------------------------
def pack_probe_cols(f):
    """按 DBG_LAYOUT 把字段字典打成 probe0..probe3 的十六进制串（自检用）。"""
    buses = {}
    for dbg, fields in DBG_LAYOUT.items():
        v, shift = 0, 0
        for fname, w in fields:
            v |= (f.get(fname, 0) & ((1 << w) - 1)) << shift
            shift += w
        buses[dbg] = v
    return buses


def make_csv(fields, style="probe", rows_prefix=0, prefix_fields=None):
    """生成一段 ILA CSV 文本。

    style='probe' 或 'named'；`rows_prefix` 是**过程态**的行数（用 prefix_fields，
    缺省与终态相同）—— 用来模拟"ILA 抓到的窗口里前面几拍还没跑完、末尾才是终态"。
    """
    buses = pack_probe_cols(fields)
    pbuses = pack_probe_cols(prefix_fields if prefix_fields is not None else fields)
    if style == "probe":
        head = ("Sample in Buffer,Sample in Window,TRIGGER,"
                "probe0[31:0],probe1[63:0],probe2[63:0],probe3[63:0]")
        radix = "Radix - UNSIGNED,UNSIGNED,UNSIGNED,HEX,HEX,HEX,HEX"
        def row(i, b):
            return "%d,%d,0,%x,%x,%x,%x" % (i, i, b["dbg0"], b["dbg1"], b["dbg2"], b["dbg3"])
    else:
        cols, ivals = [], []
        for dbg, fs in DBG_LAYOUT.items():
            for fname, w in fs:
                cols.append("%s[%d:0]" % (fname, w - 1))
                ivals.append(fields.get(fname, 0))
        head = "Sample in Buffer,Sample in Window,TRIGGER," + ",".join(cols)
        radix = "Radix - " + ",".join(["UNSIGNED"] * 3 + ["HEX"] * len(cols))
        def row(i, b):                       # named 形态只为验解码路径，过程态不再区分
            return "%d,%d,0," % (i, i) + ",".join("%x" % v for v in ivals)
    lines = [head, radix]
    for i in range(rows_prefix):
        lines.append(row(i, pbuses))
    lines.append(row(rows_prefix, buses))
    return "\n".join(lines)


def selftest():
    """离线自检：9 组。全部只吃**内存里生成的 CSV 文本**，不碰板卡、不碰网络、不写临时文件。"""
    ok = fail = 0
    results = []

    def base(**kw):
        f = dict(cam_ack=1, scan_a=127, step=7, ms=7, mclk_seen=1, mclk_self=0,
                 i2c_act=0, pwr_done=1, hit_n=3, rec0=0x02190000, hit_a0=0x20,
                 mclk_period=4096, mclk_npulse=27, rec1=0x11223344, hit_a1=0xA0,
                 hit_a2=0xB0, hit_a3=0, hit_a4=0, cam16_hi=0x0219,
                 cam8_rec=0x02190000, hit_a5=0, hit_a6=0)
        f.update(kw)
        return f

    def check(name, text, want_verdict, want_conn, extra=None):
        nonlocal ok, fail
        try:
            header, rows = load_csv_text(text, tag=name)
            decoded, notes = decode_rows(header, rows)
            best = pick_best(decoded)
            res = judge(best)
        except CsvError as exc:
            if want_verdict is None:
                ok += 1
                results.append("[ ok ] %s（按预期报错：%s）" % (name, exc))
            else:
                fail += 1
                results.append("[FAIL] %s：不该报错，却 %s" % (name, exc))
            return
        got_ok = (res["verdict"] == want_verdict and res["connected"] == want_conn)
        if extra:
            got_ok = got_ok and extra(res)
        if got_ok:
            ok += 1
            results.append("[ ok ] %s ⇒ %s" % (name, res["verdict"]))
        else:
            fail += 1
            results.append("[FAIL] %s：得到 %s（connected=%s），期望 %s"
                           % (name, res["verdict"], res["connected"], want_verdict))

    # T1 相机 + EEPROM + 时钟都在（= TB 场景 S1 的终态）
    check("T1 连上（probe 形态）", make_csv(base()),
          "CONNECTED_OK", True,
          lambda r: abs(r["cam_clk_hz"] - 50e6) < 1e3 and r["model_ok"])
    # T2 只有 EEPROM（= 场景 S2）
    check("T2 相机不应答（EEPROM 在）",
          make_csv(base(cam_ack=0, hit_n=2, hit_a0=0xA0, hit_a1=0xB0, hit_a2=0,
                        rec0=0x11223344, rec1=0x55667788, cam16_hi=0,
                        cam8_rec=0)),
          "CAMERA_NOT_ACK", False)
    # T3 相机在但没时钟（= 场景 S3）
    check("T3 相机不应答且无时钟",
          make_csv(base(cam_ack=0, hit_n=2, hit_a0=0xA0, hit_a1=0xB0, hit_a2=0,
                        mclk_seen=0, mclk_period=0, mclk_npulse=0)),
          "CAMERA_NO_CLOCK", False)
    # T4 整条总线没人应答
    check("T4 总线上无器件",
          make_csv(base(cam_ack=0, hit_n=0, hit_a0=0, hit_a1=0, hit_a2=0,
                        rec0=0, rec1=0, cam16_hi=0, cam8_rec=0)),
          "BUS_NO_DEVICE", False)
    # T5 逐信号表头形态：内容与 T1 相同，必须解出同一个结论
    check("T5 逐信号表头（probe 拆开的形态）", make_csv(base(), style="named"),
          "CONNECTED_OK", True,
          lambda r: r["model_ok"] and r["hit_addrs"][:1] == [0x20])
    # T6 窗口里前几拍还停在扫描中、末尾才是终态 ⇒ 必须挑终态那一行
    check("T6 采样窗口含过程态（挑终态）",
          make_csv(base(), rows_prefix=5,
                   prefix_fields=base(step=2, ms=2, cam_ack=0, mclk_seen=0,
                                      mclk_period=0, hit_n=0, rec0=0, cam16_hi=0,
                                      cam8_rec=0)),
          "CONNECTED_OK", True,
          lambda r: r["step"] == 7)
    # T7 24 MHz 时钟（period = 4096*50e6/24e6 = 8533）
    check("T7 时钟 24 MHz", make_csv(base(mclk_period=8533)),
          "CONNECTED_OK", True,
          lambda r: abs(r["cam_clk_hz"] - 24e6) < 2e3)
    # T8 破 CSV ⇒ 必须报人话，不许抛栈
    check("T8 坏 CSV 必须报错而不是崩", "hello\nworld\n", None, False)
    # T9 相机应答但型号读不回 ⇒ 可疑
    check("T9 应答但型号读不回",
          make_csv(base(cam16_hi=0, cam8_rec=0)),
          "CONNECTED_NO_MODEL", False)
    # T10 RTL 位序：直接用【手算的 RTL MSB-first 拼接值】构造 CSV（不经 pack_probe_cols），
    #    验证解码表与 pl_cam_detect.v 的 assign dbg0..dbg3 位序一致，防止静默反序。
    #    dbg0 = {7'b0, cam_ack=1, scan_a=0x55, step=0xA, ms=0x5, mclk_seen=1,
    #            mclk_self=0, i2c_act=1, pwr_done=0, hit_n=0xC}          = 0x0155A5AC
    #    dbg1 = {rec0=0x0219ABCD, hit_a0=0x20, mclk_period=8533, mclk_npulse=27}
    #                                                                   = 0x0219ABCD2021551B
    #    dbg2 = {rec1=0x11223344, hit_a1=0xA0, hit_a2=0xB0, 0, 0}       = 0x11223344A0B00000
    #    dbg3 = {cam16_rec[31:16]=0x0219, cam8_rec=0x02190000, 0, 0}    = 0x0219021900000000
    raw_rtl = ("Sample in Buffer,Sample in Window,TRIGGER,"
               "probe0[31:0],probe1[63:0],probe2[63:0],probe3[63:0]\n"
               "Radix - UNSIGNED,UNSIGNED,UNSIGNED,HEX,HEX,HEX,HEX\n"
               "0,0,0,155a5ac,219abcd2021551b,11223344a0b00000,219021900000000\n")
    check("T10 RTL 位序（手算 MSB-first 拼接值）", raw_rtl,
          "CONNECTED_OK", True,
          lambda r: (r["cam_ack"] is True and r["scan_a"] == 0x55 and
                     r["step"] == 0xA and r["ms"] == 0x5 and
                     r["mclk_seen"] is True and r["hit_n"] == 0xC and
                     r["rec0"] == 0x0219ABCD and r["hit_addrs"] == [0x20, 0xA0, 0xB0] and
                     r["model_id_16bit_path"] == 0x0219 and
                     abs(r["cam_clk_hz"] - 24e6) < 2e3))

    print("== check_camera.py --selftest ==")
    for line in results:
        print("  " + line)
    print("RESULT: %s (%d/%d)" % ("PASS" if fail == 0 else "FAIL", ok, ok + fail))
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    console_utf8()
    ap = argparse.ArgumentParser(
        description="把 ILA CSV 判读成「FFC 处的 MIPI 摄像头连上了没」")
    ap.add_argument("csv", nargs="?", help="read_ila.tcl 导出的 CSV")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不需要板卡）")
    ap.add_argument("--expect-model-id", default=DEFAULT_MODEL_ID, type=lambda s: int(s, 0),
                    help="期望的型号寄存器高 16 位（默认 0x0219 = IMX219）")
    ap.add_argument("--meas-n", default=4096, type=int, help="测频窗口（与 RTL 的 MEAS_N 一致）")
    ap.add_argument("--clk-hz", default=50_000_000, type=int, help="PL 时钟（默认 50 MHz）")
    ap.add_argument("--json", dest="emit_json", help="把结论写成 JSON（证据归档用）")
    ap.add_argument("--raw", action="store_true", help="额外打印前几个采样的解码结果")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.csv:
        ap.error("要么给 CSV 路径，要么 --selftest")
    try:
        return run_file(args.csv, args.expect_model_id, args.meas_n, args.clk_hz,
                        args.emit_json, args.raw)
    except CsvError as exc:
        print("CSV 读不动：%s" % exc)
        print("RESULT: FAIL（没能判读）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
