# -*- coding: utf-8 -*-
"""vigilens_link.py —— OpenMV ↔ 上位机（笔记本 / Z7020 PS）的串口帧协议。

项目：知倦 / VigiLens —— OpenMV 采集链路首次联调（M3 前的低风险前置）

⚠️ **本文件必须同时能在 CPython 与 MicroPython 上 import。**
   因此它刻意**不使用任何类型注解**（MicroPython 对 `list[int] | None` 这类现代注解
   会在求值/解析阶段直接报错），也刻意只依赖 `struct`。改本文件时请守住这条 ——
   协议只允许有**一处实现**，绝不允许"OpenMV 一份、上位机一份"两边各写一遍。

设计目标（为什么不是"随便发点数据"）：
  1. **有帧边界**：串口是字节流，裸发图像/统计值无法判断"从哪开始、到哪结束"。
     本协议给出 12 字节定长头 + 2 字节 CRC，上位机可独立重新同步（resync）。
  2. **有帧号**：frame_id 单调递增，用来算**真实丢帧率** —— 这是"OpenMV 到底能跑多少 fps"
     这条待办项（docs/interface.md §5.2 第 12 项）唯一能落地的判据。
  3. **有 CRC**：串口 + 杜邦线在 3 Mb/s 以上很容易出错，没有 CRC 的"帧率"是假帧率。
  4. **零依赖**：只用 struct。

⚠️ 本文件**不产生任何"实测数字"**。它只是协议实现；真实速率必须由 `host_capture_test.py`
   在真实硬件上跑出来。自检（--selftest）验证的是**协议逻辑**，不是硬件性能。

帧格式（小端）::

    偏移  长度  字段            说明
    0     2     magic           固定 0xA55A，**线上字节序为 5A A5**（小端）；靠它重新同步
    2     1     type            见 TYPE_* 常量
    3     1     flags           保留（按位定义，当前全 0）
    4     4     frame_id        uint32，从 1 开始单调递增（对齐 docs/interface.md §3.7 计数器语义）
    8     4     payload_len     uint32，payload 字节数
    12    N     payload         类型相关
    12+N  2     crc16           CRC-16/CCITT-FALSE，覆盖 [0, 12+N) 全部字节

    → 固定开销 14 字节。空 payload 的帧正好 14 字节。

CRC 参数（冻结，两边必须一致）：
    多项式 0x1021、初值 0xFFFF、不反转、无输出异或 → 标准名 CRC-16/CCITT-FALSE
    检查值：crc16_ccitt(b"123456789") == 0x29B1

离线自检（无需串口、无需硬件）::

    python board/openmv/vigilens_link.py --selftest
"""

import struct

# ---------------------------------------------------------------------------
# 常量（改动 = 改协议，两边必须同时改）
# ---------------------------------------------------------------------------

MAGIC = 0xA55A
# 注意：首部按小端打包，所以 0xA55A 落到线上是 5A A5。
# 只允许从这里派生同步字节，不要在别处再手写一遍（T5 自检就是在钉这条）。
MAGIC_BYTES = struct.pack("<H", MAGIC)

_HDR_FMT = "<HBBII"  # magic, type, flags, frame_id, payload_len
# ⚠️ 刻意**不用** `struct.Struct(...)`：MicroPython 的 struct 模块**没有 Struct 类**
#    （只有 pack/unpack/calcsize），用了它这一行就会在相机上直接
#    `AttributeError: 'module' object has no attribute 'Struct'`。
#    2026-09-26 真机实测踩到；自检跑在 CPython 上，所以一直没暴露。
HDR_LEN = struct.calcsize(_HDR_FMT)  # 12
CRC_LEN = 2
MIN_FRAME_LEN = HDR_LEN + CRC_LEN  # 14（空 payload）

PROTOCOL_VERSION = 1

# frame type
TYPE_STATS = 0x01  # payload: 小端 int32 数组，见 pack_stats()/unpack_stats()
TYPE_JPEG = 0x02  # payload: 一帧 JPEG 字节流
TYPE_GRAY = 0x03  # payload: 灰度像素，见 pack_gray()/unpack_gray()
TYPE_PING = 0x7F  # payload: 任意；对端应回 TYPE_PONG 并原样带回 payload
TYPE_PONG = 0x7E  # payload: 原样返回
TYPE_ACK = 0x7D  # payload: 可选文本
TYPE_ERR = 0x7C  # payload: UTF-8 错误说明

TYPE_NAMES = {
    TYPE_STATS: "STATS",
    TYPE_JPEG: "JPEG",
    TYPE_GRAY: "GRAY",
    TYPE_PING: "PING",
    TYPE_PONG: "PONG",
    TYPE_ACK: "ACK",
    TYPE_ERR: "ERR",
}


def type_name(t):
    return TYPE_NAMES.get(t, "0x%02X" % (t & 0xFF))


# ---------------------------------------------------------------------------
# CRC-16/CCITT-FALSE（表驱动，MicroPython 也够快）
# ---------------------------------------------------------------------------

_CRC_TABLE = None


def _build_crc_table():
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return table


def crc16_ccitt(data, crc=0xFFFF):
    """CRC-16/CCITT-FALSE。data 可为 bytes / bytearray / memoryview。"""
    global _CRC_TABLE
    if _CRC_TABLE is None:
        _CRC_TABLE = _build_crc_table()
    table = _CRC_TABLE
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ table[((crc >> 8) ^ b) & 0xFF]
    return crc


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------


def encode(msg_type, frame_id, payload=b"", flags=0):
    """把一个逻辑帧编码成待发送的字节串。"""
    payload = bytes(payload)
    head = struct.pack(_HDR_FMT, MAGIC, msg_type & 0xFF, flags & 0xFF, frame_id & 0xFFFFFFFF, len(payload))
    body = head + payload
    return body + struct.pack("<H", crc16_ccitt(body))


class Decoder:
    """增量式解码器：喂任意切分的字节块，吐出完整的逻辑帧。

    **不要**假设一次 read() 正好拿到一整帧 —— 串口是流，切分位置完全随机。
    本类内部维护缓冲区，因此 byte-by-byte 喂和一次性喂结果必须完全一致
    （自检 T3 就是在钉这条）。
    """

    def __init__(self, max_payload=4 << 20):
        self._buf = bytearray()
        self.max_payload = max_payload
        self.frames = 0
        self.crc_errors = 0
        self.dropped_bytes = 0
        self.oversize_rejected = 0

    def reset(self):
        self._buf = bytearray()
        self.frames = 0
        self.crc_errors = 0
        self.dropped_bytes = 0
        self.oversize_rejected = 0

    def feed(self, data):
        """喂入字节，返回本次新解出的 [(type, frame_id, payload), ...]。"""
        self._buf.extend(data)
        out = []

        while True:
            buf = self._buf
            if len(buf) < MIN_FRAME_LEN:
                break

            # 1) 重新同步：把游标推到下一个 magic
            if buf[0] != MAGIC_BYTES[0] or buf[1] != MAGIC_BYTES[1]:
                nxt = buf.find(MAGIC_BYTES)
                if nxt < 0:
                    # 保留最后 1 字节（可能是半个 magic）
                    keep = 1 if (len(buf) and buf[-1] == MAGIC_BYTES[0]) else 0
                    self.dropped_bytes += len(buf) - keep
                    del buf[: len(buf) - keep]
                    break
                self.dropped_bytes += nxt
                del buf[:nxt]
                continue

            # 2) 解头
            _magic, mtype, _flags, fid, plen = struct.unpack(_HDR_FMT, bytes(buf[:HDR_LEN]))

            if plen > self.max_payload:
                # 明显是垃圾/错位：丢 1 字节重新找 magic，绝不按 plen 去等
                self.oversize_rejected += 1
                self.dropped_bytes += 1
                del buf[:1]
                continue

            total = HDR_LEN + plen + CRC_LEN
            if len(buf) < total:
                break  # 等更多字节

            body = bytes(buf[: HDR_LEN + plen])
            want = struct.unpack("<H", bytes(buf[HDR_LEN + plen : total]))[0]
            if crc16_ccitt(body) != want:
                self.crc_errors += 1
                self.dropped_bytes += 1
                del buf[:1]
                continue

            del buf[:total]
            self.frames += 1
            out.append((mtype, fid, body[HDR_LEN:]))

        return out


# ---------------------------------------------------------------------------
# 载荷打包（STATS / GRAY）
# ---------------------------------------------------------------------------


def pack_stats(values):
    """STATS 载荷：N 个小端 int32。

    建议的字段顺序（OpenMV 侧按此填，上位机按此读 —— 这是"链路最小时延"模式：
    像素留在 OpenMV，只把**统计量**送出来，带宽需求从 27.6 MB/s 降到几十 B/帧）：

        0: frame_id（镜像，冗余校验用）
        1: roi_x0   2: roi_y0   3: roi_x1   4: roi_y1
        5: sum_r    6: sum_g    7: sum_b    8: count
        9: mean_g_q8      —— ROI 绿通道均值 × 256（整数，避免浮点；对齐契约 §4.6 的中心 128）
       10: ms_elapsed    —— 本帧采集+统计耗时（ms）
    """
    return struct.pack("<%di" % len(values), *values)


STATS_FIELDS = (
    "frame_id",
    "roi_x0",
    "roi_y0",
    "roi_x1",
    "roi_y1",
    "sum_r",
    "sum_g",
    "sum_b",
    "count",
    "mean_g_q8",
    "ms_elapsed",
)


def unpack_stats(payload):
    n = len(payload) // 4
    vals = struct.unpack("<%di" % n, payload[: n * 4])
    return {STATS_FIELDS[i] if i < len(STATS_FIELDS) else "extra_%d" % i: v for i, v in enumerate(vals)}


def pack_gray(width, height, pixels):
    """GRAY 载荷：4 字节宽 + 4 字节高 + 逐行逐列灰度（uint8）。

    布局与契约 §4.1 的 `frames.bin` 一致（帧内：行序 → 列序），
    但**灰度**而非 RGB888 —— 因为 UART 带宽根本装不下 RGB888（见 README 的带宽算术）。
    """
    return struct.pack("<ii", width, height) + bytes(pixels)


def unpack_gray(payload):
    w, h = struct.unpack("<ii", payload[:8])
    return w, h, payload[8:]


# ---------------------------------------------------------------------------
# 自检（纯逻辑，无硬件）
# ---------------------------------------------------------------------------


def _selftest():
    import random

    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # T1 CRC 标准检查值
    got = crc16_ccitt(b"123456789")
    chk("T1 CRC-16/CCITT-FALSE 检查值 == 0x29B1", got == 0x29B1, "got 0x%04X" % got)

    # T2 往返：每种类型、含空载荷与随机载荷
    rnd = random.Random(20260920)
    ok_rt = True
    detail_rt = ""
    for t in (TYPE_STATS, TYPE_JPEG, TYPE_GRAY, TYPE_PING, TYPE_PONG, TYPE_ACK, TYPE_ERR):
        for plen in (0, 1, 13, 255, 4096):
            payload = bytes(rnd.randrange(256) for _ in range(plen))
            fid = rnd.randrange(1, 2**32 - 1)
            blob = encode(t, fid, payload)
            d = Decoder()
            got_frames = d.feed(blob)
            if len(got_frames) != 1:
                ok_rt, detail_rt = False, "type=0x%02X plen=%d 解出 %d 帧" % (t, plen, len(got_frames))
                break
            mt, mf, mp = got_frames[0]
            if (mt, mf, mp) != (t, fid, payload):
                ok_rt, detail_rt = False, "type=0x%02X plen=%d 内容不一致" % (t, plen)
                break
            if d.crc_errors or d.dropped_bytes:
                ok_rt, detail_rt = False, "type=0x%02X plen=%d 干净帧却报错" % (t, plen)
                break
        if not ok_rt:
            break
    chk("T2 编码→解码 往返一致（7 类型 × 5 长度，含空载荷）", ok_rt, detail_rt)

    # T3 逐字节喂 vs 一次性喂：结果必须完全相同
    blob = b"".join(
        encode(t, i + 1, bytes([i]) * (i * 7)) for i, t in enumerate([TYPE_STATS, TYPE_JPEG, TYPE_GRAY])
    )
    d1 = Decoder()
    once = d1.feed(blob)
    d2 = Decoder()
    bytewise = []
    for i in range(len(blob)):
        bytewise.extend(d2.feed(blob[i : i + 1]))
    chk("T3 逐字节喂 == 一次性喂（流式切分无关）", once == bytewise and len(once) == 3,
        "once=%d bytewise=%d" % (len(once), len(bytewise)))

    # T4 载荷被破坏 → 报 CRC 错，且能恢复后面那帧
    good1 = encode(TYPE_STATS, 1, b"\x01\x02\x03\x04")
    bad = bytearray(encode(TYPE_STATS, 2, b"\x05\x06\x07\x08"))
    bad[6] ^= 0xFF  # 破坏首部中的 frame_id 区，同样应被抓
    good2 = encode(TYPE_STATS, 3, b"\x09\x0A\x0B\x0C")
    d = Decoder()
    got_frames = d.feed(good1 + bytes(bad) + good2)
    ids = [f[1] for f in got_frames]
    chk("T4 坏帧被 CRC 拦住，且后续好帧仍能解出", ids == [1, 3] and d.crc_errors == 1,
        "ids=%s crc_errors=%d" % (ids, d.crc_errors))

    # T5 前导垃圾 → 重新同步
    d = Decoder()
    got_frames = d.feed(b"\x00\x11\x22\xA5\x00\x33" + encode(TYPE_PING, 7, b"hi"))
    chk("T5 前导垃圾可重新同步", len(got_frames) == 1 and got_frames[0][1] == 7 and d.dropped_bytes == 6,
        "frames=%d dropped=%d" % (len(got_frames), d.dropped_bytes))

    # T6 超大 payload_len 不允许把解码器卡死（不能按它去等）
    evil = struct.pack(_HDR_FMT, MAGIC, TYPE_JPEG, 0, 1, 0xFFFFFF) + b"\x00" * 20
    d = Decoder(max_payload=1 << 20)
    got_frames = d.feed(evil + encode(TYPE_PING, 9, b""))
    chk("T6 恶意超长 payload_len 不会卡死，且不误吞后续帧",
        len(got_frames) == 1 and got_frames[0][1] == 9 and d.oversize_rejected >= 1,
        "frames=%d oversize=%d" % (len(got_frames), d.oversize_rejected))

    # T7 STATS 载荷往返
    vals = [1234, 0, 0, 640, 480, 100, 200, 300, 307200, 128 * 256, 33]
    st = unpack_stats(pack_stats(vals))
    chk("T7 STATS 打包/解包一致", [st[k] for k in STATS_FIELDS] == vals, str(st)[:80])

    # T8 位宽上界：frame_id 用满 uint32 不溢出
    blob = encode(TYPE_STATS, 0xFFFFFFFF, b"")
    d = Decoder()
    chk("T8 frame_id 上界 0xFFFFFFFF 保真", d.feed(blob)[0][1] == 0xFFFFFFFF, "")

    # T9 空 payload 帧长正好 14 字节
    chk("T9 空载荷帧长 == 14 字节（固定开销）", len(encode(TYPE_PING, 1, b"")) == MIN_FRAME_LEN,
        "len=%d" % len(encode(TYPE_PING, 1, b"")))

    failed = [c for c in checks if not c[1]]
    print("=" * 70)
    print("vigilens_link.py 协议自检  (PROTOCOL_VERSION=%d)" % PROTOCOL_VERSION)
    print("=" * 70)
    for name, ok, detail in checks:
        line = "  [%s] %s" % ("PASS" if ok else "FAIL", name)
        if detail and not ok:
            line += "\n         -> " + detail
        print(line)
    print("-" * 70)
    print("RESULT: %s  (%d/%d)" % ("PASS" if not failed else "FAIL",
                                   len(checks) - len(failed), len(checks)))
    return not failed


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print(__doc__)
    print("用法: python board/openmv/vigilens_link.py --selftest")
