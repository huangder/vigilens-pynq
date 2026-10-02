# -*- coding: utf-8 -*-
"""
imx219_sccb_check.py —— IMX219 SCCB 驱动的**离线自检**（无需相机、无需板卡、无第三方依赖）

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
被测对象：`board/imx219_driver.py`（证据台账 + 读出模式 + 三档口径 + SCCB 线上层 + 安全阀）
依据文档：`docs/27_IMX219_SCCB驱动与三档模式表.md`

这个脚本回答的问题（**每条都能在没有硬件的本机上给出确定答案**）：
    ① 真机读出模式表里的裁剪窗口是否自洽（落在成像阵列内、原点为偶数 = RGGB 相位保持）；
    ② 三档测量口径的几何与 BRAM 台阶是否自洽（与 `board/regmap.py` 同一组数交叉核对）；
    ③ 三档的链路预算是否同时满足 **IMX219 自身像素率上限** 与 **2-lane 带宽** ——
       这一条在 `docs/19` §1.2 是【算术，非实测】，本脚本把它**钉成可复跑的算术**；
    ④ SCCB 线上层是否逐字节正确（地址/值编码、写事务、读事务、越界拒绝）；
    ⑤ **安全阀是否真的会拦人**：寄存器表未核实时驱动必须拒绝下装，而不是静默写错值。

⚠️ 它**不是**「相机能用」的证据：没有真机，出图与否无法验证（寄存器表已按 linux imx219.c 补齐，但出图仍需硬件，见 docs/27 §4）。
   `bayer_demosaic` 相位那一条，本脚本只能做掉「裁剪原点奇偶」这一半，另一半仍需真机 test pattern。

用法：
    python board/imx219_sccb_check.py
    python board/imx219_sccb_check.py -v        # 打印每条明细
退出码：0 = 全过；1 = 有 FAIL
"""

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from imx219_driver import (          # noqa: E402
    BLOCKERS, EVIDENCE_REJECTED, EVIDENCE_SENSOR_MODES, REGISTER_TABLE,
    READOUT_MODES, SCCB_ADDR_7BIT, SCCB_ADDR_8BIT_R, SCCB_ADDR_8BIT_W,
    SENSOR_ARRAY_H, SENSOR_ARRAY_W, SENSOR_PIXEL_RATE_MAX_MPPS,
    TIERS, FakeSccbTransport, Imx219Sccb, NotProgrammableError,
    RegEntry, SccbError, bayer_phase_warning, console_utf8, evidence_table,
    mode_by_name, table_unverified_entries, tier_by_name, verification,
)


# ---------------------------------------------------------------------------
# 极简断言框架（与 board/regmap.py 的打印风格一致；不引入 pytest 依赖）
# ---------------------------------------------------------------------------
class Results:
    def __init__(self, verbose=False):
        self.n_pass = 0
        self.n_fail = 0
        self.verbose = verbose
        self.failures = []

    def check(self, name, got, want):
        ok = (got == want)
        if ok:
            self.n_pass += 1
        else:
            self.n_fail += 1
            self.failures.append((name, got, want))
        if self.verbose or not ok:
            print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
            if not ok:
                print("         got  = %r" % (got,))
                print("         want = %r" % (want,))
        return ok

    def section(self, title):
        print("---- %s ----" % title)


def _isclose(a, b, tol=0.01):
    return abs(a - b) <= tol


def _raises_type(fn):
    """断言 fn 抛 TypeError（用于"形状不认识就早失败"这类检查）。"""
    try:
        fn()
        return False
    except TypeError:
        return True


# ---------------------------------------------------------------------------
# 1. 传感器几何
# ---------------------------------------------------------------------------
def section_geometry(r):
    r.section("1. 传感器几何（对齐 rpicam 真机模式列表）")
    r.check("成像阵列为 3280x2464（8.08 MP）",
            (SENSOR_ARRAY_W, SENSOR_ARRAY_H), (3280, 2464))
    r.check("读出模式恰好 4 个（与真机列表逐行对应）",
            [m.name for m in READOUT_MODES],
            ["640x480_binned", "1640x1232_binned", "1920x1080", "3280x2464"])
    r.check("读出模式输出尺寸（真机原文）",
            [(m.out_w, m.out_h) for m in READOUT_MODES],
            [(640, 480), (1640, 1232), (1920, 1080), (3280, 2464)])
    r.check("读出模式裁剪窗口（真机原文，含 crop 原点）",
            [(m.crop_x, m.crop_y, m.crop_w, m.crop_h) for m in READOUT_MODES],
            [(1000, 752, 1280, 960), (0, 0, 3280, 2464),
             (680, 692, 1920, 1080), (0, 0, 3280, 2464)])
    r.check("帧率上限（真机原文）",
            [round(m.max_fps, 2) for m in READOUT_MODES],
            [206.65, 41.85, 47.57, 21.19])
    r.check("每个读出模式的裁剪窗口都完整落在成像阵列内（crop_x1<=3280 且 crop_y1<=2464）",
            [m.fits_array for m in READOUT_MODES], [True, True, True, True])
    r.check("每个读出模式的输出宽度都能按 RAW10 整行打包（width % 4 == 0）",
            [m.raw10_line_ok for m in READOUT_MODES], [True, True, True, True])
    #  相位可保持的**唯一条件**：裁剪原点两个坐标都为偶数。
    r.check("裁剪原点均为偶数 ⇒ RGGB 相位可保持",
            [(m.name, m.crop_origin_even) for m in READOUT_MODES],
            [("640x480_binned", True), ("1640x1232_binned", True),
             ("1920x1080", True), ("3280x2464", True)])
    #  反例自证：把原点挪一格必须被判为坏 —— 否则上面的检查是"恒真"的废检查。
    from imx219_driver import ReadoutMode
    _shifted = ReadoutMode("shifted_probe", 1920, 1080, 681, 692, 1920, 1080, 47.57, False)
    r.check("反例自证：裁剪原点含奇数坐标时相位检查必须判 FAIL（防恒真断言）",
            (_shifted.crop_origin_even, _shifted.fits_array), (False, True))
    r.check("反例自证：裁剪窗口越界时必须判 FAIL",
            ReadoutMode("oob_probe", 1920, 1080, 1400, 692, 1920, 1080, 47.57, False).fits_array,
            False)


# ---------------------------------------------------------------------------
# 2. 三档测量口径几何（与 board/regmap.py 同一组数）
# ---------------------------------------------------------------------------
_EXPECT = {
    "640x480": dict(w=640, h=480, num=3, den=5, fps=30, gray_w=384, gray_h=288,
                    gray_px=110592, pow2=131072, bram=64, raw10_mbps=92.16),
    "720p60": dict(w=1280, h=720, num=3, den=8, fps=60, gray_w=480, gray_h=270,
                   gray_px=129600, pow2=131072, bram=64, raw10_mbps=552.96),
    "1080p45": dict(w=1920, h=1080, num=1, den=4, fps=45, gray_w=480, gray_h=270,
                    gray_px=129600, pow2=131072, bram=64, raw10_mbps=933.12),
}


def section_tiers(r):
    r.section("2. 三档测量口径几何（与 board/regmap.py / config.yaml 同一组数）")
    r.check("档位名集合", sorted(t.name for t in TIERS), sorted(_EXPECT.keys()))
    for name, e in _EXPECT.items():
        t = tier_by_name(name)
        r.check("%s：测量口径 %dx%d @%d fps" % (name, e["w"], e["h"], e["fps"]),
                (t.w, t.h, t.fps), (e["w"], e["h"], e["fps"]))
        r.check("%s：抽取比 NUM/DEN = %d/%d" % (name, e["num"], e["den"]),
                (t.num, t.den), (e["num"], e["den"]))
        r.check("%s：抽取相位完备（w%%den==0 且 h%%den==0）" % name, t.decim_exact, True)
        r.check("%s：RAW10 整行打包完备（w%%4==0）" % name, t.raw10_line_ok, True)
        r.check("%s：灰度尺寸 %dx%d" % (name, e["gray_w"], e["gray_h"]),
                (t.gray_w, t.gray_h), (e["gray_w"], e["gray_h"]))
        r.check("%s：灰度像素数 %d" % (name, e["gray_px"]), t.gray_pixels, e["gray_px"])
        r.check("%s：next_pow2 = %d（BRAM 台阶）" % (name, e["pow2"]),
                t.next_pow2_pixels, e["pow2"])
        r.check("%s：BRAM18 估值 = %d（由台阶独立算出，非自我比较）" % (name, e["bram"]),
                t.bram18_estimate, 64 * (e["pow2"] // 131072))
        r.check("%s：RAW10 数据率 = %.2f Mbps" % (name, e["raw10_mbps"]),
                round(t.raw10_mbps(), 2), e["raw10_mbps"])


# ---------------------------------------------------------------------------
# 3. 与 board/regmap.py 交叉核对（真读对方，不是抄一份）
# ---------------------------------------------------------------------------
def section_cross_regmap(r):
    r.section("3. 与 board/regmap.py 交叉核对（真 import，每个档位一个子进程）")
    py = sys.executable
    for name, e in _EXPECT.items():
        env = dict(os.environ)
        env["VIGILENS_TIER"] = name
        code = ("import regmap as R;"
                "print('%d %d %d %d %d %d' % (R.FRAME_W, R.FRAME_H, R.GRAY_W, R.GRAY_H,"
                " R.GRAY_PIXELS, R.FPS))")
        try:
            out = subprocess.run([py, "-c", code], cwd=_HERE, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=60)
            got = out.stdout.decode("utf-8", "replace").strip()
            got = tuple(int(x) for x in got.split()) if got else ("<empty>",)
        except Exception as ex:                                   # noqa: BLE001
            got = ("<error: %s>" % ex,)
        want = (e["w"], e["h"], e["gray_w"], e["gray_h"], e["gray_px"], e["fps"])
        r.check("VIGILENS_TIER=%s 时 regmap 给出 (W,H,GRAY_W,GRAY_H,PIXELS,FPS)=%s"
                % (name, want), got, want)


# ---------------------------------------------------------------------------
# 4. 链路预算：像素率上限 + 2-lane 带宽（把 docs/19 §1.2 钉成可复跑算术）
# ---------------------------------------------------------------------------
def section_link_budget(r):
    r.section("4. 链路预算（IMX219 自身上限 + Mizar 板 2-lane）")
    #  tier 像素率必须 <= 规格书 280 Mpixel/s；1080p60 = 124.4 > 上限也一并验（v1.5 曾据此否掉 1080p60）
    r.check("三档像素率都 <= IMX219 像素率上限 280 Mpixel/s",
            [(t.name, t.pixel_rate_mpps() <= SENSOR_PIXEL_RATE_MAX_MPPS) for t in TIERS],
            [("640x480", True), ("720p60", True), ("1080p45", True)])
    r.check("1080p@60 的像素率 124.4 Mpixel/s 仍在上限内（故「装不下」是带宽问题，不是像素率问题）",
            _isclose(1920 * 1080 * 60 / 1e6, 124.416, 0.01), True)
    #  ⚠️ 这里**故意不**断言"四个模式的像素率之和 < 上限" —— 我第一次就是这么写的，实测 417.9 Mp/s，
    #     远超 280，于是自检报了 FAIL。**错的是那条断言**：binned 模式下传感器是 2x2 模拟合并读出，
    #     真正的像素率不是 out_w*out_h*fps（合并是在电荷域做的），四个模式也本就是**互斥档位**，
    #     不该相加。保留这段说明，免得下一个人再写一次同样的错检查。
    r.check("非 binning 的全像素模式 3280x2464 @21.19fps 的像素率 <= 上限 280（唯一可这样算的模式）",
            mode_by_name("3280x2464").pixel_rate_mpps <= SENSOR_PIXEL_RATE_MAX_MPPS, True)
    r.check("1920x1080 真机上限 47.57 fps >= 本档 45 fps（1080p45 档在传感器能力内）",
            mode_by_name("1920x1080").max_fps >= tier_by_name("1080p45").fps, True)
    r.check("三档都满足 Mizar 板 2-lane 带宽（1344 Mbps）",
            [(t.name, t.raw10_mbps() < 1344.0) for t in TIERS],
            [("640x480", True), ("720p60", True), ("1080p45", True)])
    r.check("1080p@60 的 RAW10 数据率 1244.16 Mbps 会超板带宽 1344 Mbps 的 92%（<100% 但无余量）",
            _isclose(1920 * 1080 * 10 * 60 / 1e6, 1244.16, 0.01), True)
    r.check("1080p@60 数据率占板带宽比例（1244.16/1344）",
            round(1920 * 1080 * 10 * 60 / 1e6 / 1344.0 * 100, 2), 92.57)


# ---------------------------------------------------------------------------
# 5. SCCB 线上层（逐字节）
# ---------------------------------------------------------------------------
def section_sccb(r):
    r.section("5. SCCB 线上层（逐字节；点分十进制地址语义与 IMX219 通行读法一致）")
    r.check("7bit 从地址 = 0x10 ⇒ 8bit 写 0x20 / 读 0x21",
            (SCCB_ADDR_7BIT, SCCB_ADDR_8BIT_W, SCCB_ADDR_8BIT_R), (0x10, 0x20, 0x21))
    r.check("地址编码为 big-endian（高字节先）：0x0100 -> b'\\x01\\x00'",
            Imx219Sccb._addr_bytes(0x0100), b"\x01\x00")
    r.check("地址编码：0x30EB -> b'\\x30\\xeb'",
            Imx219Sccb._addr_bytes(0x30EB), b"\x30\xeb")
    r.check("值编码：0xAA -> b'\\xaa'", Imx219Sccb._value_bytes(0xAA), b"\xaa")
    r.check("值编码（16bit，big-endian）：0x1800 -> b'\\x18\\x00'",
            Imx219Sccb._value_bytes(0x1800, 2), b"\x18\x00")
    r.check("值编码（16bit，big-endian）：57 -> b'\\x00\\x39'",
            Imx219Sccb._value_bytes(57, 2), b"\x00\x39")

    #  写事务：线上恰好是 3 字节 [addr_hi, addr_lo, value]
    bus = FakeSccbTransport()
    dev = Imx219Sccb(bus)
    dev.write_reg(0x0100, 0x00)
    r.check("写事务线上字节 = [0x01, 0x00, 0x00]（3 字节：地址 2B + 值 1B）",
            bus.log[-1][2], b"\x01\x00\x00")
    dev.write_reg(0x012a, 0x1800, width=2)
    r.check("16bit 值写事务线上字节 = [0x01, 0x2a, 0x18, 0x00]（4 字节：地址 2B + 值 2B）",
            bus.log[-1][2], b"\x01\x2a\x18\x00")

    #  读事务：线上先发 2 字节地址，再取 1 字节
    bus2 = FakeSccbTransport(regs={0x0100: 0x37})
    dev2 = Imx219Sccb(bus2)
    val = dev2.read_reg(0x0100)
    r.check("读事务线上地址字节 = [0x01, 0x00]，读回长度 = 1",
            (bus2.log[-1][2], bus2.log[-1][3]), (b"\x01\x00", 1))
    r.check("读事务取回的值 = 0x37（bus regs 里预置的值）", val, 0x37)

    #  往返 + 默认值（默认值那条防"读到什么都当成功"）
    bus3 = FakeSccbTransport()
    dev3 = Imx219Sccb(bus3)
    dev3.write_reg(0x0110, 0x5A)
    r.check("写后读回同一寄存器 = 0x5A", dev3.read_reg(0x0110), 0x5A)
    r.check("读一个从未写过的地址返回 0x00（总线默认值，不是随机）",
            dev3.read_reg(0x3FFF), 0x00)

    #  越界必须拒绝（防"截断写"这种静默错）
    def _raises(fn):
        try:
            fn()
            return False
        except SccbError:
            return True

    r.check("寄存器地址 > 0xFFFF 被拒绝", _raises(lambda: dev3.write_reg(0x10000, 0x00)), True)
    r.check("寄存器值 > 0xFF 被拒绝（8bit 默认宽度）", _raises(lambda: dev3.write_reg(0x0100, 0x100)), True)
    r.check("16bit 值越界（0x10000 > 0xFFFF）被拒绝",
            _raises(lambda: dev3.write_reg(0x012a, 0x10000, width=2)), True)
    r.check("非法值宽度 3 被拒绝", _raises(lambda: dev3.write_reg(0x012a, 0x00, width=3)), True)

    #  读回长度不符必须报错（假 transport 返回 2 字节）
    class _BadLen(FakeSccbTransport):
        def wr_then_rd(self, payload, nbytes):
            return b"\x00\x00"

    r.check("读回长度不符时抛 SccbError（不是把第 1 字节当结果）",
            _raises(lambda: Imx219Sccb(_BadLen()).read_reg(0x0100)), True)


# ---------------------------------------------------------------------------
# 6. 安全阀：未核实时必须拒绝下装
# ---------------------------------------------------------------------------
def section_safety_valve(r):
    r.section("6. 安全阀（空表 / 未核实项 / 值宽度越界都要拒绝下装）")
    v = verification()
    r.check("REGISTER_TABLE 非空（B1 已按 linux imx219.c 补齐）", len(REGISTER_TABLE), 50)
    r.check("寄存器表全核实：n_verified=50 且 n_unverified=0",
            (v.n_verified, v.n_unverified), (50, 0))
    r.check("programmable 仍 == False（还有 2 个未闭合阻塞项，不是表的问题）",
            v.programmable, False)
    r.check("阻塞项恰好 2 条，且不含 REG_TABLE",
            [b.key for b in BLOCKERS],
            ["TIER_720P60_READOUT", "SCCB_ADDR"])

    bus = FakeSccbTransport()
    dev = Imx219Sccb(bus)

    def _refuses(table):
        try:
            dev.write_table(table)
            return False
        except NotProgrammableError:
            return True

    r.check("空表下装被 NotProgrammableError 拒绝", _refuses(()), True)
    #  ⚠️ 表项用 RegEntry（不是裸元组）—— 用裸元组时 `write_table` 会直接抛 TypeError，
    #     那正是我们想要的"形状不认识就早失败"，但这里要测的是安全阀，所以给正确形状。
    _unverified = (RegEntry(0x0100, 0x00, False, "", ""),
                   RegEntry(0x0110, 0x5A, True, "probe", "probe"))
    r.check("含 1 个未核实项的模拟表被拒绝", _refuses(_unverified), True)
    r.check("被拒绝时**一个寄存器都没写出去**（不许「写一半就报错」）", len(bus.log), 0)
    r.check("显式 allow_unverified=True 时才会写（逃生门存在，但要自己承担）",
            dev.write_table(_unverified, allow_unverified=True), 2)
    r.check("逃生门写入后总线日志恰好 2 条", len(bus.log), 2)
    r.check("table_unverified_entries 对全核实表返回空",
            table_unverified_entries((RegEntry(0x0100, 0x00, True, "src", "what"),)), [])
    r.check("形状不认识的表项直接抛 TypeError（不许被静默跳过）",
            _raises_type(lambda: dev.write_table((object(),))), True)

    #  完整启动序列的"下装冒烟"：REGISTER_TABLE 现在是 640x480 的完整序列（50 项），
    #  应能成功下装且写入字节数与表项一致 —— 这钉住"16bit 值也真写下去了"。
    _bus2 = FakeSccbTransport()
    _dev2 = Imx219Sccb(_bus2)
    r.check("640x480 完整启动序列可下装（不再被空表/未核实拒绝）",
            _dev2.write_table(REGISTER_TABLE), len(REGISTER_TABLE))
    r.check("下装后总线日志条数 == 表项数（每一项都真写下去了）",
            len(_bus2.log), len(REGISTER_TABLE))
    r.check("16bit 值 EXCK_FREQ 落盘为 0x1800（不是被截断成 8bit）",
            _bus2.regs.get(0x012a), 0x1800)


# ---------------------------------------------------------------------------
# 7. 证据台账与相位告警文案
# ---------------------------------------------------------------------------
def section_evidence(r):
    r.section("7. 证据台账与相位告警")
    ev = evidence_table()
    r.check("证据台账恰好 3 条（含 1 条「检索到但未采用」）", len(ev), 3)
    r.check("被否决的那条状态是 UNVERIFIED", EVIDENCE_REJECTED.status, "UNVERIFIED")
    r.check("读出模式证据的状态是 VERIFIED", EVIDENCE_SENSOR_MODES.status, "VERIFIED")
    r.check("证据台账每条都有非空 source",
            [bool(e.source.strip()) for e in ev], [True, True, True])
    #  binning 模式必须给出"未经真机确认"的告警；非 binning 且原点偶数则无告警。
    r.check("1920x1080（非 binning、原点偶）无相位告警", bayer_phase_warning(mode_by_name("1920x1080")), None)
    r.check("1640x1232（binning）必须给出告警（含「未经真机确认」字样）",
            "未经真机确认" in (bayer_phase_warning(mode_by_name("1640x1232_binned")) or ""), True)
    r.check("四个模式里没有任何一个触发「相位平移」的硬告警（原点全为偶数）",
            [("相位平移" in (bayer_phase_warning(m) or "")) for m in READOUT_MODES],
            [False, False, False, False])


# ---------------------------------------------------------------------------
# 8. 寄存器表与 linux 驱动一致性（B1）
# ---------------------------------------------------------------------------
def section_register_table(r):
    r.section("8. 寄存器表（来源 = linux imx219.c，逐模式裁剪与 rpicam 真机交叉核对）")
    from imx219_driver import (      # noqa: E402
        IMX219_2LANE_REGS, IMX219_COMMON_REGS, IMX219_STREAM_ON,
        imx219_mode_regs, imx219_startup_sequence,
    )
    r.check("common regs 恰好 23 项（imx219_common_regs）", len(IMX219_COMMON_REGS), 23)
    r.check("2lane regs 恰好 8 项（imx219_2lane_regs）", len(IMX219_2LANE_REGS), 8)
    r.check("stream-on 恰好 1 项", len(IMX219_STREAM_ON), 1)
    c = {e.addr: e for e in IMX219_COMMON_REGS}
    r.check("EXCK_FREQ = 24MHz*256 = 0x1800，且是 16bit 寄存器",
            (c[0x012a].value, c[0x012a].width), (6144, 2))
    r.check("MODE_SELECT 首项 = standby（0x00，8bit）",
            (c[0x0100].value, c[0x0100].width), (0x00, 1))
    l = {e.addr: e for e in IMX219_2LANE_REGS}
    r.check("2-lane PLL：VT_MPY=57 / OP_MPY=114 / LANE_MODE=1",
            (l[0x0306].value, l[0x030c].value, l[0x0114].value), (57, 114, 1))
    r.check("PLL_VT_MPY / PLL_OP_MPY 是 16bit 寄存器（CCI_REG16）",
            (l[0x0306].width, l[0x030c].width), (2, 2))
    #  逐模式裁剪交叉核对：imx219_mode_regs 算出的 X/Y_ADD_STA 必须 == rpicam 真机的 crop 原点。
    for m in READOUT_MODES:
        regs = {e.addr: e for e in imx219_mode_regs(m)}
        r.check("%s：X_ADD_STA_A == crop_x(%d)" % (m.name, m.crop_x),
                regs[0x0164].value, m.crop_x)
        r.check("%s：Y_ADD_STA_A == crop_y(%d)" % (m.name, m.crop_y),
                regs[0x0168].value, m.crop_y)
        r.check("%s：X_OUTPUT_SIZE == out_w(%d)" % (m.name, m.out_w),
                regs[0x016c].value, m.out_w)
        r.check("%s：Y_OUTPUT_SIZE == out_h(%d)" % (m.name, m.out_h),
                regs[0x016e].value, m.out_h)
    r.check("完整启动序列项数 = 23 + 8 + 18 + 1 = 50",
            len(imx219_startup_sequence(READOUT_MODES[0])), 50)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = ("-v" in argv) or ("--verbose" in argv)
    console_utf8()

    print("=== IMX219 SCCB 驱动离线自检（board/imx219_driver.py）===")
    print("（本脚本不需要相机、不需要板卡；它验的是**几何/协议/安全阀**，不是「相机能出图」）")
    print()

    r = Results(verbose=verbose)
    section_geometry(r)
    section_tiers(r)
    section_cross_regmap(r)
    section_link_budget(r)
    section_sccb(r)
    section_safety_valve(r)
    section_register_table(r)
    section_evidence(r)

    total = r.n_pass + r.n_fail
    print("-" * 60)
    print("总计：%d 项，PASS %d，FAIL %d" % (total, r.n_pass, r.n_fail))
    if r.n_fail:
        print("失败项：")
        for name, got, want in r.failures:
            print("  - %s\n      got  = %r\n      want = %r" % (name, got, want))
    print("RESULT:", "PASS" if r.n_fail == 0 else "FAIL (%d/%d)" % (r.n_fail, total))
    print()
    print("⚠️ 未闭合项（本自检**不覆盖**，见 docs/27 §4）：")
    for b in BLOCKERS:
        print("   [%s] %s" % (b.key, b.what))
    return 0 if r.n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
