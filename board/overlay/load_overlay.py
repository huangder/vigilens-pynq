# -*- coding: utf-8 -*-
"""
load_overlay.py —— 板级加载器 + 驱动 SDK（C 线 M3 上板用）

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

板卡：**Mizar-Z7020**（MicroPhase Mizar-Z7 7020 版）
  🚧 v1.3 草案（2026-09-23，待 A/B 会签）：目标板卡由 PYNQ-Z2 改指本板。
  器件相同（`xc7z020clg400-1`），所以本文件的寄存器/驱动逻辑**不受影响**；
  **但"是否有 PYNQ 环境"这件事必须先在板上确认**（见下）。

依赖与【未验证】的重要前提：
  本文件在运行时才 `import pynq`，因此可以在 PC 上 `py_compile` / 静态检查。
  ⚠️ **但 PYNQ 不是本板的既定前提**：MicroPhase 官方手册里**没有** Mizar 的 PYNQ 镜像记载
  （开发环境写的是 Vivado 2018.3）。所以上板第一件事是在板子的 Linux 里跑：
      python3 -c "import pynq; print(pynq.__version__)"
  · 有 → 本文件的 `Overlay()` 路线可用。
  · 无 → **走 `mmio` 回退路线**：普通 Linux + `/dev/mem` mmap（或 UIO）读写 AXI-Lite，
          DMA 缓冲用 `mmap` 的物理连续内存。这条路线**本文件尚未实现**，是 M3 的待补工作。

⚠️ 纪律提示（AGENTS.md / docs/interface.md §5.3）：
   - 所有寄存器偏移来自 `board/regmap.py`（PS 侧镜像），不要在本文件里写死魔法数字。
   - 标有【未验证】的段落是"上板前无法在本机确认"的部分（DMA 握手顺序、axi_fifo_mm_s
     寄存器语义、PL 复位源），必须上板逐条核到通过为止，不得当成既成事实引用。

分层：
   load()              —— 加载 bitstream，返回 overlay + 按名字检索到的 IP 句柄
   read_reg/write_reg  —— 底层 MMIO（字节偏移）
   ap_*                —— HLS s_axilite 控制协议（ap_start / ap_done / ap_idle）
   read_valid          —— 读数据寄存器 + 其 *_ctrl(ap_vld) bit0
   run_pixel_chain     —— DMA 喂一帧 RGB → roi→rgb2gray→motion_quality 链路
   run_fir_segment     —— axi_fifo_mm_s 喂样本给 fir_filter，读回输出
   pulse_pl_reset      —— 触发一次 PL 复位（复位语义自检用）
"""

from __future__ import annotations

import time

import regmap as R


# ===========================================================================
# 加载
# ===========================================================================

def load(bitfile: str | None = None, *, ip_fragments: dict | None = None):
    """加载 bitstream，返回 (overlay, ip_handles)。

    ip_fragments: {"roi": "roi_statistic", "rgb": "rgb2gray", ...}
    默认按本项目的 BD 实例命名习惯检索（见 build_bd.tcl）。
    """
    from pynq import Overlay

    if bitfile is None:
        # 板上常用默认：当前目录下的 system.bit（Mizar-Z7020 与 PYNQ-Z2 都用这个约定）
        bitfile = "system.bit"
    ol = Overlay(bitfile)

    frags = ip_fragments or {
        "roi": "roi_statistic",
        "rgb": "rgb2gray",
        "mot": "motion_quality",
        "fir": "fir_filter",
        "dma": "axi_dma",
        "fifo_tx": "axi_fifo_mm_s",   # 注意：tx/rx 两个 fifo 都含 "axi_fifo_mm_s"
        "fifo_rx": "axi_fifo_mm_s",
    }
    handles = {}
    for key, frag in frags.items():
        ip = _find_ip(ol, frag, key=key)
        handles[key] = ip
    return ol, handles


def _find_ip(ol, fragment: str, *, key: str):
    """在 overlay 里按名称片段检索 IP。

    HLS IP 在 PYNQ 里通常以组件名出现在 ip_dict 中；DMA/FIFO 是 axi_dma_0 /
    axi_fifo_mm_s_0 之类的实例名。用片段匹配更稳，避免记死实例后缀。
    """
    ip_dict = getattr(ol, "ip_dict", {})
    matches = [name for name in ip_dict if fragment.lower() in name.lower()]
    if not matches:
        raise RuntimeError(f"未在 overlay 中找到含 '{fragment}' 的 IP（key={key}）。"
                           f"实际 ip_dict 键：{sorted(ip_dict.keys())}")
    if key in ("fifo_tx", "fifo_rx"):
        # 两个同类型 FIFO，靠不了片段区分 —— 返回全部，由调用方按 M_AXIS/S_AXIS 定向。
        return [ol[name] for name in matches]
    if len(matches) > 1:
        # 同名多个实例时取第一个并提示
        print(f"[warn] '{fragment}' 匹配到多个 IP：{matches}，使用第一个 {matches[0]}")
    return ol[matches[0]]


# ===========================================================================
# 底层 MMIO（字节偏移）
# ===========================================================================

def read_reg(ip, offset: int) -> int:
    m = getattr(ip, "mmio", None)
    if m is None:
        raise RuntimeError(f"IP {getattr(ip, 'name', ip)} 没有 mmio 接口，检查 .hwh / 接线")
    return int(m.read(offset))


def write_reg(ip, offset: int, value: int) -> None:
    m = getattr(ip, "mmio", None)
    if m is None:
        raise RuntimeError(f"IP {getattr(ip, 'name', ip)} 没有 mmio 接口，检查 .hwh / 接线")
    m.write(offset, int(value) & 0xFFFFFFFF)


# ===========================================================================
# HLS s_axilite 控制协议（CTRL = 0x00）
# ===========================================================================

def ap_idle(ip) -> bool:
    return bool((read_reg(ip, R.CTRL) >> R.AP_IDLE) & 1)


def ap_done(ip) -> bool:
    return bool((read_reg(ip, R.CTRL) >> R.AP_DONE) & 1)


def ap_start(ip) -> None:
    """拉高 ap_start（写 CTRL bit0=1）。下一次调用前若 ap_done 仍为 1，会自动清掉。"""
    write_reg(ip, R.CTRL, 1 << R.AP_START)


def ap_wait_done(ip, timeout_s: float = 10.0) -> bool:
    """轮询 ap_done，超时返回 False。流式 IP 会在喂完一整帧/一段后才置 done。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if ap_done(ip):
            return True
        time.sleep(0.001)
    return False


def read_valid(ip, data_offset: int):
    """读数据寄存器与其后紧跟的 *_ctrl(ap_vld) 寄存器，返回 (value, valid)。

    HLS 把每个输出实现为 `s_axilite & ap_vld`，于是每个输出多一个 ctrl 寄存器
    （偏移 = 数据 + 4），其 bit0 是 ap_vld。契约 §3.2：判断"值是否刷新"看它。
    """
    val = read_reg(ip, data_offset)
    valid = bool(read_reg(ip, data_offset + 4) & 0x1)
    return val, valid


# ===========================================================================
# 像素链：DMA 喂一帧 RGB → roi_statistic → rgb2gray → motion_quality
# ===========================================================================

def run_pixel_chain(ol, handles, frame_rgb, *, roi=(0, 0, R.FRAME_W, R.FRAME_H),
                    motion_thresh: int = 16, timeout_s: float = 15.0):
    """跑一帧，返回三个 IP 的寄存器结果字典。

    frame_rgb: bytes-like，长度必须 == RGB_BYTES（640*480*3），RGB888 顺序。
    roi: (x0, y0, x1, y1)，半开区间 [x0,x1)。
    """
    roi_ip, rgb_ip, mot_ip = handles["roi"], handles["rgb"], handles["mot"]
    dma = handles["dma"]

    if len(frame_rgb) != R.RGB_BYTES:
        raise ValueError(f"frame_rgb 长度 {len(frame_rgb)} != {R.RGB_BYTES}（RGB888 640x480）")

    # ---- 1) 配置输入寄存器 ----
    x0, y0, x1, y1 = roi
    for ip, off, val in (
        (roi_ip, R.RoiStatistic.roi_x0, x0),
        (roi_ip, R.RoiStatistic.roi_y0, y0),
        (roi_ip, R.RoiStatistic.roi_x1, x1),
        (roi_ip, R.RoiStatistic.roi_y1, y1),
        (roi_ip, R.RoiStatistic.width, R.FRAME_W),
        (roi_ip, R.RoiStatistic.height, R.FRAME_H),
        (rgb_ip, R.Rgb2Gray.width, R.FRAME_W),
        (rgb_ip, R.Rgb2Gray.height, R.FRAME_H),
        (mot_ip, R.MotionQuality.width, R.GRAY_W),
        (mot_ip, R.MotionQuality.height, R.GRAY_H),
        (mot_ip, R.MotionQuality.motion_thresh, motion_thresh),
    ):
        write_reg(ip, off, val)

    # ---- 2) 启动三个 IP（它们会在 stream read 上阻塞等数据）----
    ap_start(roi_ip)
    ap_start(rgb_ip)
    ap_start(mot_ip)

    # ---- 3) DMA：先武装 S2MM 回读（收灰度流），再武装 MM2S（发 RGB 帧）----
    # 【未验证】arm 顺序 / 是否需要在 start IP 之后再 arm DMA，需上板按门限 6 调通。
    from pynq import allocate
    in_buf = allocate(shape=(R.RGB_BYTES,), dtype="u1")
    out_buf = allocate(shape=(R.GRAY_PIXELS,), dtype="u1")
    in_buf[:] = frame_rgb

    recv = getattr(dma, "recvchannel", None)
    send = getattr(dma, "sendchannel", None)
    if send is None or recv is None:
        raise RuntimeError("DMA 句柄缺少 sendchannel/recvchannel，确认 axi_dma 驱动已加载")

    recv.transfer(out_buf)
    send.transfer(in_buf)
    send.wait()
    recv.wait()

    # ---- 4) 等三个 IP 的 ap_done，然后读结果 ----
    ok = all(ap_wait_done(ip, timeout_s) for ip in (roi_ip, rgb_ip, mot_ip))
    if not ok:
        print("[warn] 有 IP 在超时内未置 ap_done（可能流长度/尺寸不匹配，见契约 §3.2 边界）")

    res = {
        "gray_out": bytes(out_buf.tobytes()),          # 384x288 灰度（硬件产出，供比对）
        "roi": {
            "sum_r": read_reg(roi_ip, R.RoiStatistic.sum_r),
            "sum_g": read_reg(roi_ip, R.RoiStatistic.sum_g),
            "sum_b": read_reg(roi_ip, R.RoiStatistic.sum_b),
            "count": read_reg(roi_ip, R.RoiStatistic.count),
            "frame_id": read_reg(roi_ip, R.RoiStatistic.frame_id),
        },
        "rgb": {
            "out_width": read_reg(rgb_ip, R.Rgb2Gray.out_width),
            "out_height": read_reg(rgb_ip, R.Rgb2Gray.out_height),
            "pixel_count": read_reg(rgb_ip, R.Rgb2Gray.pixel_count),
            "sum_gray": read_reg(rgb_ip, R.Rgb2Gray.sum_gray),
            "frame_id": read_reg(rgb_ip, R.Rgb2Gray.frame_id),
        },
        "mot": {
            "diff_total": read_reg(mot_ip, R.MotionQuality.diff_total),
            "motion_pixels": read_reg(mot_ip, R.MotionQuality.motion_pixels),
            "motion_ratio_q16": read_reg(mot_ip, R.MotionQuality.motion_ratio_q16),
            "count": read_reg(mot_ip, R.MotionQuality.count),
            "frame_id": read_reg(mot_ip, R.MotionQuality.frame_id),
        },
        "all_done": ok,
    }
    return res


# ===========================================================================
# FIR 路径：axi_fifo_mm_s 喂样本给 fir_filter，读回输出
# ===========================================================================

# axi_fifo_mm_s（Xilinx PG080）寄存器偏移。⚠️【未验证】需按 Vivado 2026.1 实际
# 实例化的 IP 版本核对这些偏移，并以 .hwh 地址空间为准。
FIFO_SRR = 0x00   # 软复位（写 0x000000A5）
FIFO_TX = 0x10   # TxFIFO 写数据
FIFO_TXV = 0x14   # TxFIFO vacancy（bit31=full 语义以 PG080 为准）
FIFO_RX = 0x18   # RxFIFO 读数据
FIFO_RXO = 0x1C   # RxFIFO occupancy（bit31=empty 语义以 PG080 为准）


def run_fir_segment(ol, handles, samples, *, reset: bool = True, timeout_s: float = 15.0):
    """把 int16 样本序列喂给 fir_filter，读回输出（与输入一一对应、含瞬态）。

    samples: list/array of int16（Q1.15）。长度 1..65535。
    reset=True 时先写 reset 寄存器（清延迟线），保证段起点确定（契约 §3.5）。
    """
    fir_ip = handles["fir"]
    fifos = handles["fifo_tx"]   # 检索到的是含两个 axi_fifo_mm_s 的 list（见 _find_ip）
    n = len(samples)

    # 区分 tx/rx 两个 FIFO：tx 的 M_AXIS 接 fir_in，rx 的 S_AXIS 接 fir_out。
    # 【未验证】两个同类型 FIFO 无法靠名字片段区分，需按 BD 实例名显式定向；
    #           这里提供显式覆盖入口（handles 里可直接放句柄）。
    if isinstance(fifos, list):
        raise RuntimeError(
            "axi_fifo_mm_s 有 tx/rx 两个实例，无法靠片段自动区分。"
            "请在 load() 里显式给出 fifo_tx / fifo_rx 的精确实例名（见 build_bd.tcl 命名）。"
        )
    fifo_tx = handles.get("fifo_tx")
    fifo_rx = handles.get("fifo_rx")
    if fifo_tx is None or fifo_rx is None:
        raise RuntimeError("缺少 fifo_tx / fifo_rx 句柄")

    # ---- 配置 + 启动 FIR ----
    write_reg(fir_ip, R.FirFilter.n_samples, n)
    write_reg(fir_ip, R.FirFilter.reset, 1 if reset else 0)
    ap_start(fir_ip)

    # ---- 喂样本（先 reset=1 建立确定性起点）----
    for s in samples:
        write_reg(fifo_tx, FIFO_TX, int(s) & 0xFFFF)   # 【未验证】16bit 样本的写宽度映射

    # ---- 等 FIR done，读回输出 ----
    if not ap_wait_done(fir_ip, timeout_s):
        print("[warn] fir_filter 在超时内未置 ap_done")

    out = []
    for _ in range(n):
        out.append(read_reg(fifo_rx, FIFO_RX) & 0xFFFF)  # 【未验证】读宽度映射

    seg_id = read_reg(fir_ip, R.FirFilter.seg_id)
    out_count = read_reg(fir_ip, R.FirFilter.out_count)
    sat_count = read_reg(fir_ip, R.FirFilter.saturation_count)
    return {
        "out": out,
        "seg_id": seg_id,
        "out_count": out_count,
        "saturation_count": sat_count,
    }


# ===========================================================================
# PL 复位（复位语义自检，门限 4）
# ===========================================================================

def pulse_pl_reset(ol, reset_gpio_pin: int | None = None):
    """触发一次 PL 复位（拉低再释放），让四个 IP 的 frame_id/seg_id 归零。

    【未验证】复位源取决于 BD 实际接线：
      - 若把 Processor System Reset 的 ext_reset_in 接到 AXI GPIO 某一位：
        pulse_pl_reset(ol, reset_gpio_pin=<bit>)
      - 若用 PS 的 EMIO GPIO：需要另写。
    上板前请按 build_bd.tcl 的实际复位接线补齐本函数，并回填证据。
    """
    if reset_gpio_pin is not None:
        gpio = _find_ip(ol, "axi_gpio", key="reset_gpio")
        # 拉低 → 保持几拍 → 释放
        write_gpio(gpio, reset_gpio_pin, 0)
        time.sleep(0.01)
        write_gpio(gpio, reset_gpio_pin, 1)
        time.sleep(0.01)
        return

    # 兜底：尝试 PYNQ 常暴露的 reset GPIO
    ps_reset = getattr(ol, "ps_reset", None)
    if ps_reset is not None:
        ps_reset.write(0)
        time.sleep(0.01)
        ps_reset.write(1)
        time.sleep(0.01)
        return

    raise RuntimeError("未找到 PL 复位源；请显式传入 reset_gpio_pin 或补齐 pulse_pl_reset。")


def write_gpio(ip, pin: int, value: int):
    """AXI GPIO 单 bit 写（最简实现）。【未验证】按实际 GPIO 位宽/channel 核对。"""
    # GPIO_DATA(channel1)=0x0；GPIO_TRI(channel1)=0x4（0=输出）
    write_reg(ip, 0x4, 0x0)              # 全部设为输出
    cur = read_reg(ip, 0x0)
    if value:
        cur |= (1 << pin)
    else:
        cur &= ~(1 << pin)
    write_reg(ip, 0x0, cur)
