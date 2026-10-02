# -*- coding: utf-8 -*-
"""
mmap_overlay.py —— 无 PYNQ 环境下用 /dev/mem mmap 驱动 AXI-Lite 寄存器（C 线 M3 上板用）

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

为什么需要它：
    `load_overlay.py` 的 `load()` 走 PYNQ 的 `Overlay()`。但 Mizar-Z7020 官方手册里
    **没有** PYNQ 镜像记载（开发环境写的是 Vivado 2018.3），所以板上很可能
    `import pynq` 失败。此时要用普通 Linux + `/dev/mem` mmap 直接读写 AXI-Lite 寄存器。

覆盖范围（诚实边界）：
    ✅ AXI-Lite 控制寄存器读写（`read_reg` / `write_reg` / `ap_*`）—— 这是
       `bringup_check.py` Stage 1/2（寄存器 smoke + 写回）所需的全部。
    ❌ DMA 数据通路（`run_pixel_chain`）与 axi_fifo_mm_s 喂样本（`run_fir_segment`）——
       它们需要 DMA 驱动 / UIO + CMA 物理连续内存 + 缓存一致性，**纯 /dev/mem mmap 做不了**，
       是单独的待补项（本文件只做寄存器面）。

【未验证】清单（上板前必须逐条核到通过）：
    1. `/dev/mem` 需 root 或 mem 组权限；Zynq 内核是否开放 PL 地址段到 /dev/mem 因内核配置而异。
    2. 每个 IP 的 AXI-Lite 基地址：必须来自 `build_bd.tcl` 跑通后 Vivado Address Editor 的分配
       （或导出的 .hwh），**不许猜**。本文件的 `parse_hwh()` 是 best-effort，拿到真实 .hwh 后先核对。
    3. 本文件离线能验的是"地址映射自洽（对齐/重叠/范围）+ mmap 读写逻辑"；"地址对不对"只能上板验证。

离线自检（无需板卡、无需 /dev/mem，用匿名 mmap 模拟）：
    python board/overlay/mmap_overlay.py --selftest
"""

from __future__ import annotations

import os as _os
import sys as _sys
import struct

# 让 `from overlay.load_overlay import ...` 与 load_overlay 里的 `import regmap` 都能解析：
# 本文件在 board/overlay/ 下，需要把 board/（父目录）放进 sys.path（与 imx219_sccb_check.py 同做法）。
_HERE = _os.path.dirname(_os.path.abspath(__file__))      # board/overlay/
_BOARD = _os.path.dirname(_HERE)                          # board/
for _p in (_BOARD, _HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

# 复用 load_overlay.py 的寄存器读写与控制协议（它们只依赖 ip.mmio.read/write + ip.name，
# 所以只要给它们一个带 .mmio 的句柄对象就能原样工作）。
from overlay.load_overlay import (    # noqa: E402
    read_reg, write_reg, ap_idle, ap_done, ap_start, ap_wait_done, read_valid,
)

PAGE_SIZE = 4096
REG_SPACE = 0x10000              # 每个 IP 的 AXI-Lite 地址空间 64 KB（Vivado 默认分配粒度）
# Zynq-7000 PL 从 GP0（0x4000_0000~0x7FFF_FFFF）与 GP1（0x8000_0000~0xBFFF_FFFF）可见的地址段。
ZYNQ_PL_AXI_RANGE = (0x4000_0000, 0xBFFF_FFFF)

# 逻辑名 → BD 实例名片段（与 build_bd.tcl 的实例命名、load_overlay._find_ip 的 frags 一致）
IP_FRAGMENTS = {
    "roi": "roi_statistic",
    "rgb": "rgb2gray",
    "mot": "motion_quality",
    "fir": "fir_filter",
    "dma": "axi_dma",
    "fifo_tx": "axi_fifo_mm_s",
    "fifo_rx": "axi_fifo_mm_s",
}


def _align_down(addr, align=PAGE_SIZE):
    return addr - (addr % align)


class MmapMMIO:
    """mmap 出来的 AXI-Lite 寄存器窗口（32-bit 小端、按字节偏移读写）。

    接口与 pynq 的 MMIO 一致（`.read(offset)` / `.write(offset, value)`），
    因此 `load_overlay.py` 的 `read_reg` / `write_reg` / `ap_*` 可以原样复用。
    """

    def __init__(self, base_addr, mmap_obj):
        self.base_addr = base_addr
        self._m = mmap_obj
        # 基地址可能不是 4 KB 对齐：mmap 对象从对齐地址起映射，访问时加页内偏移。
        self._off = base_addr - _align_down(base_addr)

    def _check(self, offset):
        if offset < 0 or offset + 4 > REG_SPACE:
            raise ValueError("AXI-Lite 偏移越界：0x%X（本窗口 0..0x%X）"
                             % (offset, REG_SPACE - 4))
        if self._off + offset + 4 > len(self._m):
            raise ValueError("mmap 窗口越界：base=0x%X offset=0x%X 超出 mmap 长度 %d"
                             % (self.base_addr, offset, len(self._m)))

    def read(self, offset):
        self._check(offset)
        return struct.unpack_from("<I", self._m, self._off + offset)[0]

    def write(self, offset, value):
        self._check(offset)
        struct.pack_into("<I", self._m, self._off + offset, int(value) & 0xFFFFFFFF)


class MmapIP:
    """一个 IP 的句柄：`name` + `mmio`（供 read_reg/write_reg 使用）。"""

    def __init__(self, name, base_addr, mmap_obj):
        self.name = name
        self.base_addr = base_addr
        self.mmio = MmapMMIO(base_addr, mmap_obj)


class OverlayLike:
    """伪装成 overlay 的最小对象，供 `pulse_pl_reset(ol, ...)` 这类以 ol 为参数的函数使用。"""

    def __init__(self, address_map):
        self.address_map = address_map


def _mmap_devmem(base, size):
    """在 Linux 上打开 `/dev/mem` 并 mmap 一段物理地址。

    ⚠️【未验证】需要 root 或 mem 组权限；且要先把基地址按 4 KB 对齐、跨度含页内偏移。
    """
    import mmap
    aligned = _align_down(base)
    span = size + (base - aligned)
    with open("/dev/mem", "r+b") as f:
        m = mmap.mmap(f.fileno(), span, offset=aligned, access=mmap.ACCESS_WRITE)
    return m


def validate_address_map(address_map):
    """校验地址映射：逻辑名齐全、基地址非空/页对齐/互不重叠/落在 Zynq PL 地址段。

    返回错误列表（空列表 = 通过）。这是"离线能验的那一半"；"地址数值对不对"只能上板验。
    """
    errors = []
    for key in IP_FRAGMENTS:
        if key not in address_map:
            errors.append("缺少逻辑名 %r" % key)
            continue
        base = address_map[key]
        if base is None:
            errors.append("逻辑名 %r 的基地址是 None（未填）" % key)
            continue
        if not (ZYNQ_PL_AXI_RANGE[0] <= base < ZYNQ_PL_AXI_RANGE[1]):
            errors.append("%r 基地址 0x%X 不在 Zynq PL 段 0x%X..0x%X"
                          % (key, base, ZYNQ_PL_AXI_RANGE[0], ZYNQ_PL_AXI_RANGE[1]))
        if base % PAGE_SIZE != 0:
            errors.append("%r 基地址 0x%X 未按 4 KB 页对齐" % (key, base))

    # 64 KB 窗口两两重叠检测（先按**地址**排序，不能按逻辑名排——那会把不相邻的窗口误判重叠）
    present = sorted(((k, address_map[k]) for k in IP_FRAGMENTS
                      if address_map.get(k) is not None),
                     key=lambda kv: kv[1])
    for i in range(len(present) - 1):
        k1, b1 = present[i]
        k2, b2 = present[i + 1]
        if b1 + REG_SPACE > b2:
            errors.append("%r(0x%X) 与 %r(0x%X) 的 64 KB 窗口重叠" % (k1, b1, k2, b2))
    return errors


def load_mmap(address_map, *, mmap_factory=_mmap_devmem):
    """用 `/dev/mem` mmap 建立 AXI-Lite 句柄。

    address_map: {"roi": 0x..., "rgb": ..., "mot": ..., "fir": ...,
                  "dma": ..., "fifo_tx": ..., "fifo_rx": ...} —— 必须完整给出基地址。
    返回 (OverlayLike, handles)；handles 里每个值是 `MmapIP`，可直接喂给
    `load_overlay.read_reg / write_reg / ap_*`（它们只看 `ip.mmio` / `ip.name`）。

    `mmap_factory(base, size)` 缺省是 `_mmap_devmem`（Linux）；离线自检传一个匿名 mmap 工厂。
    """
    errors = validate_address_map(address_map)
    if errors:
        raise ValueError("地址映射无效：\n  " + "\n  ".join(errors))
    handles = {}
    for key, base in address_map.items():
        handles[key] = MmapIP(key, base, mmap_factory(base, REG_SPACE))
    return OverlayLike(address_map), handles


# ---------------------------------------------------------------------------
# .hwh 地址映射解析（best-effort；拿到真实 .hwh 前不可当结论）
# ---------------------------------------------------------------------------

def _parse_hwh_xml(xml_bytes):
    """从 .hwh 内的 XML 字节解析 {BD实例名: AXI-Lite 基地址}。

    ⚠️【未验证】只匹配 C_S_AXI_CONTROL_BASEADDR / C_S_AXI_LITE_BASEADDR /
    C_S_AXI_BASEADDR / C_BASEADDR 这几个常见参数名；Vivado 版本不同可能命名不同。
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
    out = {}
    for ipinst in root.iter("ipinst"):
        inst = ipinst.findtext("instance") or ipinst.findtext("ipname")
        if not inst:
            continue
        base = None
        for param in ipinst.iter("parameter"):
            nm = param.findtext("name")
            if nm in ("C_S_AXI_CONTROL_BASEADDR", "C_S_AXI_LITE_BASEADDR",
                      "C_S_AXI_BASEADDR", "C_BASEADDR"):
                val = param.findtext("value")
                if val:
                    base = int(val, 0)
                    break
        if base is not None:
            out[inst] = base
    return out


def parse_hwh(hwh_path):
    """从 .hwh（ZIP 包，内含一个 .xml）解析 {BD实例名: 基地址}。"""
    import zipfile
    with zipfile.ZipFile(hwh_path) as z:
        xmls = [n for n in z.namelist() if n.lower().endswith(".xml")]
        if not xmls:
            raise ValueError("hwh 里没有 .xml 文件：%r" % z.namelist())
        return _parse_hwh_xml(z.read(xmls[0]))


def map_instances_to_logical(instance_map):
    """把 {BD实例名: 基地址} 映射成 {逻辑名: 基地址}（按 IP_FRAGMENTS 的片段匹配）。

    ⚠️ fifo_tx / fifo_rx 都是 axi_fifo_mm_s、无法靠片段区分，所以两者都返回**候选列表**
    （而不是单值），由调用方按 BD 实际接线显式指定 —— 与 load_overlay._find_ip 的限制一致。
    """
    logical = {}
    for key, frag in IP_FRAGMENTS.items():
        matches = [addr for inst, addr in instance_map.items()
                   if frag.lower() in inst.lower()]
        if not matches:
            logical[key] = None
        elif key in ("fifo_tx", "fifo_rx"):
            logical[key] = matches
        else:
            logical[key] = matches[0]
    return logical


# ---------------------------------------------------------------------------
# 离线自检（匿名 mmap 模拟 /dev/mem；Windows / Linux 都能跑）
# ---------------------------------------------------------------------------

def console_utf8():
    """让本文件输出在 Windows 控制台不崩（与 backend/console.py 同做法）。"""
    for _n in ("stdout", "stderr"):
        _s = getattr(_sys, _n, None)
        _r = getattr(_s, "reconfigure", None)
        if _r is None:
            continue
        try:
            _r(encoding="utf-8", errors="replace")
        except Exception:      # noqa: BLE001
            pass


def selftest():
    import mmap
    console_utf8()

    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    print("=" * 60)
    print("[离线自检] mmap_overlay 的寄存器窗口 / 地址校验 / .hwh 解析（不需要板卡）")
    print("=" * 60)

    # 1) MmapMMIO 读写（匿名 mmap）
    m = mmap.mmap(-1, REG_SPACE)
    dev = MmapMMIO(0x43C00000, m)
    dev.write(0x10, 0xDEADBEEF)
    chk("MmapMMIO 32bit 小端写后读回", dev.read(0x10) == 0xDEADBEEF)
    m2 = mmap.mmap(-1, REG_SPACE + 0x100)
    dev2 = MmapMMIO(0x43C00100, m2)          # 非页对齐基地址 → 页内偏移 0x100
    dev2.write(0x00, 0x12345678)
    chk("MmapMMIO 页内偏移读写（基地址非 4KB 对齐）", dev2.read(0x00) == 0x12345678)
    try:
        dev.write(REG_SPACE, 0)
        chk("越界写被 ValueError 拒绝", False)
    except ValueError:
        chk("越界写被 ValueError 拒绝", True)

    # 2) 地址映射校验
    good = {k: 0x43C00000 + i * REG_SPACE for i, k in enumerate(IP_FRAGMENTS)}
    chk("完整地址映射通过校验", validate_address_map(good) == [])
    bad_missing = {k: v for k, v in good.items() if k != "roi"}
    chk("缺逻辑名被检出", any("roi" in e for e in validate_address_map(bad_missing)))
    bad_overlap = dict(good)
    bad_overlap["rgb"] = bad_overlap["roi"]
    chk("64KB 窗口重叠被检出", any("重叠" in e for e in validate_address_map(bad_overlap)))
    bad_range = dict(good)
    bad_range["roi"] = 0x1000
    chk("不在 Zynq PL 段被检出", any("PL 段" in e for e in validate_address_map(bad_range)))
    bad_align = dict(good)
    bad_align["roi"] = 0x43C00100
    chk("未按 4KB 页对齐被检出", any("对齐" in e for e in validate_address_map(bad_align)))

    # 3) load_mmap + 复用 read_reg/write_reg/ap_*（匿名 mmap 当假 /dev/mem）
    def fake_factory(base, size):
        return mmap.mmap(-1, size)           # 每个 IP 独立匿名窗口，模拟各自物理页

    ol, handles = load_mmap(good, mmap_factory=fake_factory)
    write_reg(handles["roi"], 0x30, 640)
    chk("load_mmap + write_reg/read_reg 复用", read_reg(handles["roi"], 0x30) == 640)
    chk("ap_idle 读取（匿名 mmap 初值 0 ⇒ idle=False）", ap_idle(handles["roi"]) is False)
    chk("handles 各 IP 的 base_addr 正确",
        [handles[k].base_addr for k in IP_FRAGMENTS] == [good[k] for k in IP_FRAGMENTS])

    # 4) .hwh 解析（最小 XML 样本，不落盘）
    sample_xml = (
        '<?xml version="1.0" encoding="UTF-8"?><project>'
        '<ipinst><instance>roi_statistic_0</instance><parameters>'
        '<parameter><name>C_S_AXI_CONTROL_BASEADDR</name><value>0x43C00000</value></parameter>'
        '</parameters></ipinst>'
        '<ipinst><instance>axi_dma_0</instance><parameters>'
        '<parameter><name>C_S_AXI_LITE_BASEADDR</name><value>0x40400000</value></parameter>'
        '</parameters></ipinst>'
        '</project>'
    )
    inst_map = _parse_hwh_xml(sample_xml.encode("utf-8"))
    chk("parse_hwh 解析 roi_statistic_0 基地址",
        inst_map.get("roi_statistic_0") == 0x43C00000)
    chk("parse_hwh 解析 axi_dma_0 基地址（LITE 参数名）",
        inst_map.get("axi_dma_0") == 0x40400000)
    logical = map_instances_to_logical(inst_map)
    chk("instance→logical 映射（roi）", logical.get("roi") == 0x43C00000)
    chk("instance→logical 映射（dma）", logical.get("dma") == 0x40400000)
    chk("未匹配到的逻辑名为 None（fir）", logical.get("fir") is None)

    # 5) 汇总
    bad = [n for n, ok, _ in checks if not ok]
    print("")
    for n, ok, detail in checks:
        line = "  [%s] %s" % ("PASS" if ok else "FAIL", n)
        if detail:
            line += "   (%s)" % detail
        print(line)
    print("-" * 60)
    print("总计：%d 项，PASS %d，FAIL %d" % (len(checks), len(checks) - len(bad), len(bad)))
    print("RESULT:", "PASS" if not bad else "FAIL")
    return 0 if not bad else 1


if __name__ == "__main__":
    _sys.exit(selftest())
