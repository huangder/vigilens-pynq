# -*- coding: utf-8 -*-
"""
imx219_driver.py —— IMX219（树莓派 Camera Module 2）SCCB 驱动与三档模式表

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
权威来源：`docs/interface.md` §0（测量口径档位，🚧 v1.5 草案）；像素源方案见 `docs/19`。
依据文档：`docs/27_IMX219_SCCB驱动与三档模式表.md`（本文件的外部证据台账与未闭合项都在那里）。

⚠️⚠️ **本文件的诚实边界（读之前先读这段）** ⚠️⚠️
    本机网络**读不到 IMX219 注册表**（GitHub / raw.githubusercontent / jsdelivr / ghproxy /
    gitee 镜像 / raspberrypi.com 全部被本机 DNS 与 Cloudflare 挡掉，详见 docs/27 §1 的实测记录）。
    因此本文件**故意不写任何未核实的寄存器地址与寄存器值**。

    按《05》铁律 1（数据真实性）与铁律 3（不许发明文件/字段/路径）：
      · 能核实的（**传感器几何 / 读出模式 / 裁剪窗口 / 帧率上限 / 链路位数**）→ 写进来，带出处；
      · 核实不了的（**0x0100 模式选择、PLL 分频、曝光/增益、时序寄存器**）→ 只登记"缺哪一项"，
        由 `verification()` 如实报出，**驱动在核实前拒绝写寄存器**（宁可大声失败，不静默写错值）。

    这是刻意设计：**寄存器值写错不会报错，只会让相机不出图**（或出一张相位错位的马赛克），
    与 `docs/26` §1 那 4 个"静默错"是同一类危险。所以"缺"必须是显式的、可统计的。

离线自检（无需相机、无需板卡、无第三方依赖）：
    python board/imx219_sccb_check.py            # 110 项，含 SCCB 线上字节序、16bit 值编码、寄存器表一致性
"""

import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 0. 外部证据台账（每条都写"读到的是什么"与"从哪读的"）
#
#    status 取值含义（贯穿全文件）：
#      VERIFIED    —— 本机真的读到了该表述（出处写在 source 里），可直接引用
#      DERIVED     —— 由 VERIFIED 项用整数算术推导出来的，可复算
#      UNVERIFIED  —— 知道"需要这个"，但**没有**可引用的来源 ⇒ 不得当结论
# ---------------------------------------------------------------------------

STATUS_VERIFIED = "VERIFIED"
STATUS_DERIVED = "DERIVED"
STATUS_UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class Evidence:
    """一条外部证据：读到了什么、从哪读的、什么状态。"""
    what: str
    source: str
    status: str = STATUS_VERIFIED

    def __str__(self):
        return "[%s] %s  <- %s" % (self.status, self.what, self.source)


# --- 证据 1：IMX219 的可用读出模式（**本文件唯一的一手器件证据**）--------------
#  出处是 Waveshare 的 IMX219-83 维基页，它**逐字转载了 `rpicam-hello --list-cameras`
#  在真机 IMX219 上的输出**。那条输出正是"传感器到底能出哪些模式 + 每个模式的物理裁剪窗口"
#  的权威列表（libcamera 的行为由它自己驱动里的 mode table 决定）。
#  ⚠️ 它是**转载**，不是 libcamera 源码本身；引它时必须一起说明这一点（见 docs/27 §1.2）。
EVIDENCE_SENSOR_MODES = Evidence(
    what=("IMX219 真机可用读出模式（`rpicam-hello --list-cameras` 原文，SRGGB10_CSI2P）："
          "640x480 [206.65 fps - (1000,752)/1280x960 crop]；1640x1232 [41.85 fps - (0,0)/3280x2464 crop]；"
          "1920x1080 [47.57 fps - (680,692)/1920x1080 crop]；3280x2464 [21.19 fps - (0,0)/3280x2464 crop]。"
          "**同一列表里没有 1280x720 模式。**"),
    source=("Waveshare IMX219-83 维基页（转载 rpicam 真机输出）："
            "https://www.waveshare.com/wiki/IMX219-83_Stereo_Camera （2026-10-02 实读）"),
)

# --- 证据 2：数据格式名里就带着 Bayer 相位 -----------------------------------
#  'SRGGB10_CSI2P' 的第一个字母 S 表示 "Sony"，其后 RGGB 就是**帧首 2x2 的相位**。
#  ⇒ 与 bayer_demosaic.cpp 冻结的 RGGB（(0,0)=R）一致，但这是**相位名**，
#    它同时要求"裁剪窗口的左上角必须落在偶数坐标"才能保持住（见 check_bayer_phase 的推导）。
EVIDENCE_BAYER_PHASE = Evidence(
    what=("像素格式名为 SRGGB10_CSI2P ⇒ Bayer 相位 = **RGGB**，与 `bayer_demosaic.cpp` 冻结口径一致。")
    ,
    source="同上（格式名字段）",
)

# --- 证据 3：检索到但**未采用**的说法 -----------------------------------------
#  网上常见「IMX219 只有 640x480 支持 60fps」一类说法；本文件**不引用**任何未能读到出处的数字。
EVIDENCE_REJECTED = Evidence(
    what=("**未采用**：网络流传的「IMX219 各档位 60fps 支持情况」等二手汇总 —— 本机读不到一手出处，"
          "按铁律「不确定必须标注」处理，一律不进本文件的模式表。"),
    source="—（无可用出处，故不引用）",
    status=STATUS_UNVERIFIED,
)


# ---------------------------------------------------------------------------
# 1. 传感器几何（**只放能核实的**）
# ---------------------------------------------------------------------------

#  异或成像阵列：3280 x 2464（= 8.08 MP，"8 Megapixels"）。
#  ⚠️ 这是**成像区**的有效像素数；IMX219 的实际感光阵列含 dummy/光学黑区，通常更大。
#     读寄存器表时要用到的"物理阵列尺寸"本文件**不写**（未核实）。
SENSOR_ARRAY_W = 3280
SENSOR_ARRAY_H = 2464

#  物理（native）阵列与有效区偏移 —— 来自 linux imx219.c 的 IMX219_NATIVE_* / IMX219_ACTIVE_AREA_*。
#  逐模式裁剪窗口的寄存器值（X/Y_ADD_STA_A 等）以 native 坐标计算、再减掉有效区偏移，
#  与 rpicam --list-cameras 报出的 crop 原点在**寄存器坐标空间**上对齐（见 docs/27 §2.3 与 §4 B1）。
SENSOR_NATIVE_W = 3296
SENSOR_NATIVE_H = 2480
SENSOR_ACTIVE_LEFT = 8
SENSOR_ACTIVE_TOP = 8

#  SCCB / 硬件 I2C 从地址。IMX219 的 7 bit 地址是 0x10（8 bit 读 0x21 / 写 0x20）。
#  ⚠️ 出处见 docs/27 §1.3：这条来自 IMX219 设备树/官方模块的通行做法，
#     **本机同样没有读到规格书原文** ⇒ 标 UNVERIFIED，上板前用 i2cdetect 实扫一次即可闭环。
SCCB_ADDR_7BIT = 0x10
SCCB_ADDR_8BIT_W = SCCB_ADDR_7BIT << 1          # 0x20
SCCB_ADDR_8BIT_R = (SCCB_ADDR_7BIT << 1) | 1    # 0x21

#  寄存器地址宽度：Sony 传感器（含 IMX219）通行 16 bit 寄存器地址。
#  ⚠️ 未核实（同上）。**注意：未核实的是地址宽度，不是某个值** —— 所以下面 check_sccb_wire
#     把两种字节序都测了，并把"当前采用的是哪一种"显式打印出来，而不是假装它是已知的。
REG_ADDR_WIDTH_BYTES = 2
REG_VALUE_WIDTH_BYTES = 1


# ---------------------------------------------------------------------------
# 2. 读出模式（对齐 libcamera 真机列表；**一行都不许有编造**）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadoutMode:
    """一个传感器读出模式：输出尺寸 + 它在**成像阵列**里的裁剪窗口 + 帧率上限。

    ⚠️ `out_w/out_h` 是**传感器输出**尺寸，不是契约测量口径的尺寸。两者的关系见 Tier。
    """
    name: str
    out_w: int
    out_h: int
    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    max_fps: float          # 真机 `--list-cameras` 报出的上限（含 binning）
    binned: bool            # 是否 2x2 analog binning（见 docs/27 §2.3 的相位影响）
    fll_def: int = 0        # 该模式默认帧长（linux imx219.c supported_modes[] 的 fll_def）
    src: str = "EVIDENCE_SENSOR_MODES"

    # ---- 派生属性（全部可由上面几个整数复算）----
    @property
    def crop_x1(self):
        return self.crop_x + self.crop_w

    @property
    def crop_y1(self):
        return self.crop_y + self.crop_h

    @property
    def crop_origin_even(self):
        """裁剪窗口左上角是否落在偶数坐标 —— **RGGB 相位能否保持的唯一条件**。

        推导（这也是 `docs/20` §4.3 要核实的那个问题的答案的一部分）：
          Bayer 相位由全局坐标 (x,y) 的奇偶决定：(偶,偶)=R (奇,偶)=Gr (偶,奇)=Gb (奇,奇)=B。
          读出时把窗口左上角 (x0,y0) 当作新帧的 (0,0)。若 x0 为奇，新帧 (0,0) 实际取到的是
          全局奇列 ⇒ 新帧的 (0,0) 变成 Gr（或 Gb）⇒ **整帧相位平移一格，RGGB 判定失效**。
          ⇒ 结论：**裁剪窗口的 x0 与 y0 必须都是偶数**，与是否 binning 无关。
        """
        return (self.crop_x % 2 == 0) and (self.crop_y % 2 == 0)

    @property
    def fits_array(self):
        """裁剪窗口必须完整落在成像阵列内。"""
        return (self.crop_x1 <= SENSOR_ARRAY_W) and (self.crop_y1 <= SENSOR_ARRAY_H)

    @property
    def raw10_line_ok(self):
        """该模式的输出宽度能否按 MIPI RAW10 的"每 5 字节 = 4 像素"整行打包。

        RAW10 的组边界是**行内独立**的（`raw10_unpack.cpp` 冻结口径二），
        ⇒ 行像素数必须是 4 的整数倍，否则行末会剩 1~3 个像素无处安放。
        """
        return self.out_w % 4 == 0

    @property
    def pixel_rate_mpps(self):
        """该模式的像素率（Mpixel/s）= 宽 x 高 x fps / 1e6。"""
        return self.out_w * self.out_h * self.max_fps / 1e6


#  真机列表的四行，**逐字落地**。顺序与列表一致。
READOUT_MODES = (
    ReadoutMode("640x480_binned",  640,  480, 1000,  752, 1280,  960, 206.65, True, 1707),
    ReadoutMode("1640x1232_binned", 1640, 1232,    0,    0, 3280, 2464,  41.85, True, 1707),
    ReadoutMode("1920x1080",       1920, 1080,  680,  692, 1920, 1080,  47.57, False, 1763),
    ReadoutMode("3280x2464",       3280, 2464,    0,    0, 3280, 2464,  21.19, False, 3526),
)

#  规格书侧的两个上限（**docx/20 已归档，出自 Sony《IMX219PQH5-C》规格书**）。
#  ⚠️ 这两条不是本机读的，是仓库既有归档（`docs/20` 2026-09-29 实读记录）；
#     本文件引用它做**算术核对**，不重新主张为"本机已验证"。
SENSOR_PIXEL_RATE_MAX_MPPS = 280.0   # "Pixel rate: 280 Mpixel/s (All-pixels mode)"
SENSOR_LANE_MAX_MBPS = 912.0         # "Max. 912 Mbps/Lane (@2lane)" ⇒ 2 lane = 1824 Mbps
SENSOR_LANES = 2
BOARD_LANE_MBPS = 672.0              # Mizar 手册：tested up to 672 Mbps/channel
BOARD_AGG_MBPS = BOARD_LANE_MBPS * SENSOR_LANES      # 1344 Mbps（docs/19 §1.1 的算法）


# ---------------------------------------------------------------------------
# 3. 三档测量口径（对齐 docs/interface.md §0 / config.yaml / run_hls.tcl）
# ---------------------------------------------------------------------------

#  与 `board/regmap.py` 的 `_TIERS` **必须是同一组数**（那边是测量口径侧的唯一来源）。
#  这里额外记录"该档由哪个传感器读出模式供像素"。
TIER_SPECS = {
    "640x480": {
        "w": 640, "h": 480, "num": 3, "den": 5, "fps": 30,
        "readout": "640x480_binned",
        "note": "既有档：v1.1 冻结 640x480@45、v1.2 草案改 30；config.yaml 的 fps_nominal 现为 30",
    },
    "720p60": {
        "w": 1280, "h": 720, "num": 3, "den": 8, "fps": 60,
        "readout": None,        # ⚠️ 见 §4 的 BLOCKER：真机列表里没有 1280x720 模式
        "note": "主档（用户 2026-09-30 选定）：1280x720 @60fps",
    },
    "1080p45": {
        "w": 1920, "h": 1080, "num": 1, "den": 4, "fps": 45,
        "readout": "1920x1080",
        "note": "备档（用户 2026-09-30 选定）：1920x1080 @45fps；真机上限 47.57 fps",
    },
}


@dataclass(frozen=True)
class Tier:
    """一个测量口径档位 + 它对传感器读出模式的要求。"""
    name: str
    w: int
    h: int
    num: int          # rgb2gray 抽取比 NUM/DEN：保留 (i % DEN) < NUM 的像素
    den: int
    fps: int
    readout: str      # 供像素的读出模式名；None = 未确定（见 blocker）

    # ---- 派生 ----
    @property
    def gray_w(self):
        """灰度工作宽 = w / den * num（**整数除法，与 regmap.py / gen_motion_vectors.py 同式**）。"""
        return self.w // self.den * self.num

    @property
    def gray_h(self):
        return self.h // self.den * self.num

    @property
    def gray_pixels(self):
        return self.gray_w * self.gray_h

    @property
    def decim_exact(self):
        """输入宽高是否都能被抽取分母整除 —— **不满足就会出现行末残渣、相位错位**。

        这条与 `board/regmap.py` 的"档位自洽：输入宽高是抽取分母的整数倍"和
        `gen_motion_vectors.py:71` 的 `if w % DECIM_DEN or h % DECIM_DEN: raise` 是同一条。
        """
        return (self.w % self.den == 0) and (self.h % self.den == 0)

    @property
    def raw10_line_ok(self):
        return self.w % 4 == 0

    @property
    def next_pow2_pixels(self):
        """灰度像素数的下一个 2 的幂 —— HLS 片内数组按 2 的幂地址空间分配 BRAM。

        ≤ 2^17 = 131072 是 `motion_quality` 片内帧缓存停在 **64 个 BRAM18** 的判据
        （见 `fpga/README.md` 的"可复用设计规律"）。跨过去就翻倍，器件只有 280 个。
        """
        p = 1
        while p < self.gray_pixels:
            p <<= 1
        return p

    @property
    def bram18_estimate(self):
        """按"2 的幂地址空间"规律估 BRAM18 个数：每个 2^17 台阶对应 64 个。

        ⚠️ 这是**估值**（DERIVED），不是 csynth 实测；实测值只能在完整权限终端跑 csynth 得到。
        """
        return 64 * (self.next_pow2_pixels // 131072)

    def raw10_bits_per_frame(self):
        """一帧 RAW10 的比特数（**未含** CSI-2 协议开销）。"""
        return self.w * self.h * 10

    def raw10_mbps(self):
        """该档的 RAW10 数据率（Mbit/s），未含协议开销。"""
        return self.raw10_bits_per_frame() * self.fps / 1e6

    def board_link_pct(self):
        """占 Mizar 板 2-lane 可用带宽（1344 Mbps）的百分比。"""
        return self.raw10_mbps() / BOARD_AGG_MBPS * 100.0

    def sensor_link_pct(self):
        """占 IMX219 自身 2-lane 上限（1824 Mbps）的百分比。"""
        return self.raw10_mbps() / (SENSOR_LANE_MAX_MBPS * SENSOR_LANES) * 100.0

    def pixel_rate_mpps(self):
        return self.w * self.h * self.fps / 1e6


def build_tiers():
    """由 TIER_SPECS 构造 Tier 元组（**唯一构造点**，避免两处各写一份数）。"""
    out = []
    for name, s in TIER_SPECS.items():
        out.append(Tier(name, s["w"], s["h"], s["num"], s["den"], s["fps"], s["readout"]))
    return tuple(out)


TIERS = build_tiers()


def tier_by_name(name):
    for t in TIERS:
        if t.name == name:
            return t
    raise KeyError("未知档位 %r，可选：%s" % (name, [t.name for t in TIERS]))


# ---------------------------------------------------------------------------
# 4. 未闭合项（**必须显式列出来，不许让读者以为"驱动已经写完了"**）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Blocker:
    """一件"知道要做、但没有可引用来源/没有硬件就做不了"的事。"""
    key: str
    what: str
    why: str
    how: str


BLOCKERS = (
    Blocker(
        key="TIER_720P60_READOUT",
        what="720p60 档的读出模式**待转录**（传感器已确认支持 720p，见 docs/27 §2.2）",
        why=("传感器**支持** 720p：规格书 Features 明确「180 fps @720p with 2x2 analog (special) binning」、"
             "NVIDIA Jetson 驱动有 imx219_mode_1280x720_60fps[] 模式表；但树莓派 libcamera 驱动只暴露"
             "4 个模式（640x480 / 1640x1232 / 1920x1080 / 3280x2464）**没有 720p** ⇒ 本驱动也还没有 720p 模式表。"
             "所以缺的不是\"传感器有没有这个模式\"，而是\"把 720p 模式表转录进来\"。"),
        how=("① 把 NVIDIA imx219_mode_1280x720_60fps[] 转录进 REGISTER_TABLE（⚠️ 注意 8bit↔16bit 地址口径、"
             "special-binning 的 Bayer 相位待核实）；② 或退一步用**已核实的 1640x1232 binned 模式**"
             "（41.85 fps，要改契约 §0，属会签项）；③ 或 PL 侧 frame_scale 把 1640x1232 缩到 1280x720。"),
    ),
    Blocker(
        key="SCCB_ADDR",
        what="SCCB 从地址（0x10 / 7bit）与寄存器地址宽度（16 bit）",
        why="同上：本机没有读到规格书或驱动的原文。",
        how="上板第一件事：`i2cdetect -y <bus>` 扫一次，或在 PL 侧用 I2C 探针读一次 ID 寄存器。闭环成本 5 分钟。",
    ),
)


# ---------------------------------------------------------------------------
# 5. SCCB 线上层（**纯协议，可离线用假 transport 逐字节验证**）
# ---------------------------------------------------------------------------

class SccbError(RuntimeError):
    pass


class NotProgrammableError(SccbError):
    """寄存器表里还有未核实的值，驱动**拒绝**写 —— 宁可大声失败，不静默写错值。"""


@dataclass
class FakeSccbTransport:
    """离线自检用的假 SCCB 总线：把每一次字节级收发记下来。

    它**只**模拟总线行为（写/读、读写复合事务），不模拟任何 IMX219 语义 ——
    这样"协议层对不对"与"寄存器值对不对"就是两件独立可查的事。
    """
    addr_7bit: int = SCCB_ADDR_7BIT
    #  addr -> 值。读不存在的地址返回 0x00（**并记账**，便于断言"读到过什么"）。
    regs: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    unknown_reads: list = field(default_factory=list)

    # ---- 总线原语：只做"把字节发出去/收回来"，不做解释 ----
    def wr(self, payload):
        """一次写事务：START + (addr<<1|0) + payload... + STOP。

        ⚠️ 这里必须**同时**认识 2 字节与 3 字节两种 payload（记地址是 8bit 还是 16bit）：
           本驱动用的是 16bit 地址 ⇒ 线上是 3 字节；若将来回退成 8bit 地址 ⇒ 2 字节。
           只认其中一种的话，那种写**会静默不落进 regs**，于是"写后读回"会假失败。
        """
        payload = bytes(payload)
        self.log.append(("W", self.addr_7bit, payload))
        if len(payload) == 3:                                    # 16bit 地址 + 8bit 值
            addr = (payload[0] << 8) | payload[1]
            self.regs[addr] = payload[2]
        elif len(payload) == 4:                                  # 16bit 地址 + 16bit 值
            addr = (payload[0] << 8) | payload[1]
            self.regs[addr] = (payload[2] << 8) | payload[3]
        elif len(payload) == 2:                                  # 8bit 地址 + 8bit 值
            self.regs[payload[0]] = payload[1]

    def wr_then_rd(self, payload, nbytes):
        """读事务：START + (addr<<1|0) + [地址字节] + Sr + (addr<<1|1) + 收 nbytes + STOP。"""
        payload = bytes(payload)
        self.log.append(("R", self.addr_7bit, payload, nbytes))
        if len(payload) == 2:
            addr = (payload[0] << 8) | payload[1]
        else:
            addr = payload[0]
        if addr not in self.regs:
            self.unknown_reads.append(addr)
        return bytes([self.regs.get(addr, 0x00) & 0xFF] * nbytes)


class Imx219Sccb:
    """IMX219 的 SCCB 寄存器读写（Sony 16 bit 寄存器地址 + 8 bit 值）。

    ⚠️ 字节序是 **big-endian（地址高字节先）** —— 这条**未核实**，只能在上板/抓包时确认。
       刻意把它写成**唯一一处**（`_addr_bytes`），这样万一要改成小端，只改一行。
    """

    def __init__(self, transport):
        self._t = transport

    # ---- 地址/值的编码：唯一来源，便于核对与小端回退 ----
    @staticmethod
    def _addr_bytes(addr):
        if not (0 <= addr <= 0xFFFF):
            raise SccbError("寄存器地址越界：0x%X" % addr)
        return bytes([(addr >> 8) & 0xFF, addr & 0xFF])

    @staticmethod
    def _value_bytes(value, width=1):
        """寄存器值编码为 width 字节、big-endian（16bit 地址同为 big-endian）。

        width=1 → 8bit 值（1 字节）；width=2 → 16bit 值（2 字节，高字节先）。
        越界一律拒绝，绝不截断 —— 截断就是"静默写错值"的一种。
        """
        if width not in (1, 2):
            raise SccbError("不支持的寄存器值宽度：%d（只支持 1/2 字节）" % width)
        hi = (1 << (8 * width)) - 1
        if not (0 <= value <= hi):
            raise SccbError("寄存器值越界（%d 字节最大 0x%X）：0x%X" % (width, hi, value))
        return value.to_bytes(width, "big")

    @property
    def addr_7bit(self):
        return getattr(self._t, "addr_7bit", SCCB_ADDR_7BIT)

    def write_reg(self, addr, value, width=1):
        """写一个寄存器：address(2B, 高字节先) + value(width 字节, 高字节先)。

        width=1 → 8bit 值（线上 3 字节）；width=2 → 16bit 值（线上 4 字节）。
        """
        self._t.wr(self._addr_bytes(addr) + self._value_bytes(value, width))

    def read_reg(self, addr):
        """读一个寄存器：先发地址，再读 1 字节。"""
        data = self._t.wr_then_rd(self._addr_bytes(addr), REG_VALUE_WIDTH_BYTES)
        if len(data) != REG_VALUE_WIDTH_BYTES:
            raise SccbError("SCCB 读回长度不对：期望 %d，得到 %d"
                            % (REG_VALUE_WIDTH_BYTES, len(data)))
        return data[0]

    def write_table(self, table, allow_unverified=False):
        """按序遍历 RegEntry 表写寄存器。

        `allow_unverified=False`（缺省）时，**任何未核实项都会导致拒绝**（`NotProgrammableError`）。
        这是本文件的核心安全阀：见文件头"诚实边界"。
        ⚠️ 表项必须是 `RegEntry`（或至少带 `.addr`/`.value`）—— 用裸元组会在此处直接抛错，
           这样"表写成了别的形状"不会被静默跳过。
        """
        table = normalize_table(table)
        #  ⚠️ 空表必须**报错**，不能静默返回 0。实测踩到：最初没有这条守卫时
        #     `write_table(REGISTER_TABLE)`（REGISTER_TABLE 现在是空表）会"成功返回 0"，
        #     调用方完全看不出"一个寄存器都没写"。这正是《05》说的"静默错"：
        #     相机不出图，而驱动说"下装完成"。
        if len(table) == 0:
            raise NotProgrammableError(
                "寄存器表为空 —— 拒绝下装（**没有东西可写**不等于「下装成功」）。"
                "IMX219 的注册表来源见 docs/27 §4（本机网络读不到，故刻意留空）。")
        bad = [(e.addr, e.value) for e in table if not e.verified]
        if bad and not allow_unverified:
            raise NotProgrammableError(
                "寄存器表含 %d 个未核实项，拒绝写入（地址：%s）。"
                "请先按 docs/27 §4 补来源，或用 allow_unverified=True 显式承担风险。"
                % (len(bad), ", ".join("0x%04X" % a for a, _ in bad)))
        for e in table:
            self.write_reg(e.addr, e.value, getattr(e, "width", 1))
        return len(table)


# ---------------------------------------------------------------------------
# 6. 寄存器表：**当前为空**（这是刻意的）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegEntry:
    addr: int
    value: int
    verified: bool
    source: str = ""
    what: str = ""
    width: int = 1        # 寄存器值字节数：1 = 8bit，2 = 16bit（big-endian）


#  寄存器值来源（唯一、可引用）：Linux 内核主线驱动 `drivers/media/i2c/imx219.c`
#  （torvalds/linux master，Raspberry Pi (Trading) Ltd 2019 起维护；kernel.org 实读）。
#  这正是 rpicam/libcamera 在树莓派上驱动 IMX219 所用的同一份寄存器序列。
_REG_SRC = ("linux kernel imx219.c（torvalds/linux master, "
            "drivers/media/i2c/imx219.c；"
            "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/"
            "plain/drivers/media/i2c/imx219.c，2026-10 实读）")


def reg8(addr, value, what=""):
    """构造一个 8bit 值寄存器项（VERIFIED）。"""
    return RegEntry(addr, value, True, _REG_SRC, what, 1)


def reg16(addr, value, what=""):
    """构造一个 16bit 值寄存器项（VERIFIED）。"""
    return RegEntry(addr, value, True, _REG_SRC, what, 2)


# --- 通用初始化序列（imx219_common_regs，23 项）--------------------------------
IMX219_COMMON_REGS = (
    reg8(0x0100, 0x00, "MODE_SELECT = standby"),
    # To Access Addresses 3000-5fff, send the following commands
    reg8(0x30eb, 0x05, "解锁 3000-5fff 地址区"),
    reg8(0x30eb, 0x0c, "解锁 3000-5fff 地址区"),
    reg8(0x300a, 0xff, "解锁 3000-5fff 地址区"),
    reg8(0x300b, 0xff, "解锁 3000-5fff 地址区"),
    reg8(0x30eb, 0x05, "解锁 3000-5fff 地址区"),
    reg8(0x30eb, 0x09, "解锁 3000-5fff 地址区"),
    # Undocumented registers（驱动原文如此标注）
    reg8(0x455e, 0x00, "undocumented"),
    reg8(0x471e, 0x4b, "undocumented"),
    reg8(0x4767, 0x0f, "undocumented"),
    reg8(0x4750, 0x14, "undocumented"),
    reg8(0x4540, 0x00, "undocumented"),
    reg8(0x47b4, 0x14, "undocumented"),
    reg8(0x4713, 0x30, "undocumented"),
    reg8(0x478b, 0x10, "undocumented"),
    reg8(0x478f, 0x10, "undocumented"),
    reg8(0x4793, 0x10, "undocumented"),
    reg8(0x4797, 0x0e, "undocumented"),
    reg8(0x479b, 0x0e, "undocumented"),
    # Frame Bank Register Group "A"
    reg8(0x0170, 0x01, "X_ODD_INC_A"),
    reg8(0x0171, 0x01, "Y_ODD_INC_A"),
    # Output setup registers
    reg8(0x0128, 0x00, "DPHY_CTRL = timing auto"),
    reg16(0x012a, 24 * 256, "EXCK_FREQ = 24MHz * 256 = 0x1800"),
)


# --- 2-lane PLL 时钟表 + lane 模式（imx219_2lane_regs，8 项）---------------------
IMX219_2LANE_REGS = (
    reg8(0x0301, 0x05, "VTPXCK_DIV"),
    reg8(0x0303, 0x01, "VTSYCK_DIV"),
    reg8(0x0304, 0x03, "PREPLLCK_VT_DIV = AUTO"),
    reg8(0x0305, 0x03, "PREPLLCK_OP_DIV = AUTO"),
    reg16(0x0306, 57, "PLL_VT_MPY = 57"),
    reg8(0x030b, 0x01, "OPSYCK_DIV"),
    reg16(0x030c, 114, "PLL_OP_MPY = 114"),
    reg8(0x0114, 0x01, "CSI_LANE_MODE = 2-lane"),
)


# --- 流开关（MODE_SELECT）------------------------------------------------------
IMX219_STREAM_ON = (reg8(0x0100, 0x01, "MODE_SELECT = streaming"),)
IMX219_STREAM_OFF = (reg8(0x0100, 0x00, "MODE_SELECT = standby"),)


def imx219_mode_regs(mode):
    """逐模式的裁剪/输出/时序/曝光增益寄存器（18 项）。

    复刻 linux imx219.c 的 `imx219_set_pad_format()`（算裁剪窗口与 binning）+
    `imx219_set_framefmt()`（写裁剪/输出/时序寄存器）+
    `imx219_init_controls()` 的默认曝光/增益路径。

    关键不变量（也是自检交叉核对的那条）：
      X_ADD_STA_A == mode.crop_x、Y_ADD_STA_A == mode.crop_y ——
    即本函数算出的寄存器坐标与 rpicam --list-cameras 报出的 crop 原点一致。
    """
    w, h = mode.out_w, mode.out_h
    # binning：2x2 模拟合并，取 2 以最大化裁剪窗口并居中（imx219_set_pad_format）
    bin_h = min(SENSOR_ARRAY_W // w, 2)
    bin_v = min(SENSOR_ARRAY_H // h, 2)
    binning = min(bin_h, bin_v)
    bin_code = 0x03 if (bin_h == 2 and bin_v == 2) else 0x00
    crop_w = w * binning
    crop_h = h * binning
    crop_left = (SENSOR_NATIVE_W - crop_w) // 2
    crop_top = (SENSOR_NATIVE_H - crop_h) // 2
    # 时序默认值：line length 取决于是否 binning；frame length 来自该模式的 fll_def
    llp = 0x0de8 if bin_code == 0x03 else 0x0d78     # BINNED_LLP_MIN : LLP_MIN
    fll = mode.fll_def
    exposure = min(fll - 4, 0x640)                    # EXPOSURE_DEFAULT = 0x640
    bpp = 10                                          # RAW10（SRGGB10）
    return (
        reg16(0x0164, crop_left - SENSOR_ACTIVE_LEFT, "X_ADD_STA_A"),
        reg16(0x0166, crop_left - SENSOR_ACTIVE_LEFT + crop_w - 1, "X_ADD_END_A"),
        reg16(0x0168, crop_top - SENSOR_ACTIVE_TOP, "Y_ADD_STA_A"),
        reg16(0x016a, crop_top - SENSOR_ACTIVE_TOP + crop_h - 1, "Y_ADD_END_A"),
        reg8(0x0174, bin_code, "BINNING_MODE_H"),
        reg8(0x0175, bin_code, "BINNING_MODE_V"),
        reg16(0x016c, w, "X_OUTPUT_SIZE"),
        reg16(0x016e, h, "Y_OUTPUT_SIZE"),
        reg16(0x0624, w, "TP_WINDOW_WIDTH"),
        reg16(0x0626, h, "TP_WINDOW_HEIGHT"),
        reg16(0x018c, (bpp << 8) | bpp, "CSI_DATA_FORMAT_A = RAW10"),
        reg8(0x0309, bpp, "OPPXCK_DIV = 10"),
        reg16(0x0160, fll, "FRM_LENGTH_A"),
        reg16(0x0162, llp, "LINE_LENGTH_A"),
        reg16(0x015a, exposure, "EXPOSURE"),
        reg8(0x0157, 0x00, "ANALOG_GAIN = 0"),
        reg16(0x0158, 0x0100, "DIGITAL_GAIN = 1.0x"),
        reg8(0x0172, 0x00, "ORIENTATION = no flip"),
    )


def imx219_startup_sequence(mode, lanes=2):
    """一个模式的完整启动写入序列（common + lane + mode + stream-on）。

    `mode` 必须是 `ReadoutMode` 实例（不是名字），这样本函数不依赖 mode_by_name，
    可以在 READOUT_MODES 定义之后立刻被 REGISTER_TABLE 使用。
    """
    if lanes != 2:
        raise ValueError("本项目只有 2-lane（Mizar 板 MIPI 2-lane），lanes 必须为 2")
    return IMX219_COMMON_REGS + IMX219_2LANE_REGS + imx219_mode_regs(mode) + IMX219_STREAM_ON


#  默认启动序列 = 640x480（v1.1 冻结档，且是唯一读出源完全确定的档）。
#  ⚠️ 720p60 仍无读出源（TIER_720P60_READOUT），1080p45 用 1920x1080 模式。
REGISTER_TABLE = imx219_startup_sequence(READOUT_MODES[0])


def normalize_table(table):
    """把表项统一成 RegEntry。

    接受 `RegEntry`，或裸元组 `(addr, value)` / `(addr, value, verified[, source[, what]])`。
    **不认识的形状直接抛 TypeError** —— 这样"表写成了别的形状"会在写入前就暴露，
    而不是被静默跳过（静默跳过 = 少写了几个寄存器 = 相机不出图且查不出来）。
    """
    out = []
    for i, e in enumerate(table):
        if isinstance(e, RegEntry):
            out.append(e)
            continue
        if isinstance(e, (tuple, list)):
            if len(e) == 2:
                out.append(RegEntry(int(e[0]), int(e[1]), False, "", ""))
                continue
            if 3 <= len(e) <= 5:
                a, v, ver = int(e[0]), int(e[1]), bool(e[2])
                src = e[3] if len(e) > 3 else ""
                what = e[4] if len(e) > 4 else ""
                out.append(RegEntry(a, v, ver, src, what))
                continue
        raise TypeError("REGISTER_TABLE 第 %d 项形状不认识：%r（应为 RegEntry 或 2~5 元组）"
                        % (i, e))
    return tuple(out)


def table_unverified_entries(table):
    """返回表里未核实的项 `[(addr, value), ...]`。

    ⚠️ 空表 ⇒ 空列表。**空表不等于"全部通过"** —— "有没有可写的东西"由
       `VerificationReport.n_registers` / `.programmable` 回答，不是由这个函数回答。
    """
    return [(e.addr, e.value) for e in normalize_table(table) if not e.verified]


# ---------------------------------------------------------------------------
# 7. 覆盖度核算：把"做完多少"变成可统计的数（而不是一句"基本完成"）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerificationReport:
    n_registers: int
    n_verified: int
    n_unverified: int
    n_blockers: int

    @property
    def programmable(self):
        """能否真的下装一个模式：**表非空 且 无未核实项 且 无阻塞项**。"""
        return self.n_registers > 0 and self.n_unverified == 0 and self.n_blockers == 0

    def __str__(self):
        return ("寄存器表 %d 项（已核实 %d / 未核实 %d）；未闭合阻塞项 %d；"
                "可下装：%s" % (self.n_registers, self.n_verified, self.n_unverified,
                              self.n_blockers, "是" if self.programmable else "否"))


def verification():
    _t = normalize_table(REGISTER_TABLE)
    return VerificationReport(
        n_registers=len(_t),
        n_verified=sum(1 for e in _t if e.verified),
        n_unverified=sum(1 for e in _t if not e.verified),
        n_blockers=len(BLOCKERS),
    )


# ---------------------------------------------------------------------------
# 8. 传感器侧约束检查（**离线可做、且上板前必须做**）
# ---------------------------------------------------------------------------

def check_bayer_phase(mode):
    """RGGB 相位在给定读出模式下能否保持（纯算术，可复算）。

    这是 `docs/20` §4.3 / `docs/26` §6.3 那条"720p Bayer 相位核实"里
    **不需要硬件就能做掉的那一半**：相位能否保持只取决于裁剪窗口左上角的奇偶。
    另一半（真机打 test pattern 看竖条）仍然需要相机，本函数不主张替代它。
    """
    return mode.crop_origin_even


def bayer_phase_warning(mode):
    """返回相位风险说明；None 表示无风险。"""
    if not mode.crop_origin_even:
        return ("%s 的裁剪原点 (%d,%d) 含奇数坐标 ⇒ RGGB 相位会平移一格，"
                "`bayer_demosaic` 的口径将失效" % (mode.name, mode.crop_x, mode.crop_y))
    if mode.binned:
        return ("%s 走 2x2 analog binning：本表的裁剪原点为偶数、相位**按算术成立**，"
                "但 binning 对相机内 Bayer 处理管线的影响**未经真机确认**（docs/27 §2.3）" % mode.name)
    return None


def mode_by_name(name):
    for m in READOUT_MODES:
        if m.name == name:
            return m
    raise KeyError("未知读出模式 %r，可选：%s" % (name, [m.name for m in READOUT_MODES]))


def tier_readout_mode(tier):
    """档位 -> 读出模式；未确定时返回 None（**不是**抛异常 —— 那是待办，不是错误）。"""
    if tier.readout is None:
        return None
    return mode_by_name(tier.readout)


def evidence_table():
    return (EVIDENCE_SENSOR_MODES, EVIDENCE_BAYER_PHASE, EVIDENCE_REJECTED)


def console_utf8():
    """让本文件的输出在 Windows 控制台上不崩（与 backend/console.py 同一做法）。

    实测踩到：Windows 控制台默认代码页是 936(GBK)，本文件要打印 `⇒`(U+21D2) 与中文，
    GBK 编不出就**直接 UnicodeEncodeError 崩掉** —— 与项目逻辑毫无关系。
    `errors="replace"` 保证万一还有编不出的字符就显示成 '?'，**而不是让程序崩**。
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


if __name__ == "__main__":
    # 直接跑本文件时只打印证据台账与覆盖度，不做自检（自检在 imx219_sccb_check.py）。
    console_utf8()
    print("=== IMX219 SCCB 驱动 —— 证据台账 ===")
    for e in evidence_table():
        print("  " + str(e))
    print()
    print("=== 真机读出模式（对齐 libcamera 列表）===")
    for m in READOUT_MODES:
        w = bayer_phase_warning(m)
        print("  %-18s %4dx%-5d crop(%d,%d)/%dx%d  max %6.2f fps  binned=%-5s phase_even=%-5s %s"
              % (m.name, m.out_w, m.out_h, m.crop_x, m.crop_y, m.crop_w, m.crop_h,
                 m.max_fps, m.binned, m.crop_origin_even, ("⚠️ " + w) if w else ""))
    print()
    print("=== 三档测量口径 ===")
    for t in TIERS:
        print("  %-9s %4dx%-5d @%2d fps -> 灰度 %3dx%-3d (%6d px, next_pow2=%7d, ~%3d BRAM18)  RAW10 %7.1f Mbps"
              " (board %.1f%%/sensor %.1f%%)"
              % (t.name, t.w, t.h, t.fps, t.gray_w, t.gray_h, t.gray_pixels,
                 t.next_pow2_pixels, t.bram18_estimate, t.raw10_mbps(),
                 t.board_link_pct(), t.sensor_link_pct()))
    print()
    print("=== 覆盖度 ===")
    print("  " + str(verification()))
    for b in BLOCKERS:
        print("  [BLOCKED] %-22s %s" % (b.key, b.what))
