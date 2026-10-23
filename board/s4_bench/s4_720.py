"""720p60 档（rgb2gray 3/8 抽取 → 480×270 灰度）的板上单帧验证。

与 `s4_run.py` 的唯一区别是**常量**：换 bitstream、换固件、换测试向量与黄金参考。
流程完全复用（xsdb 编程 PL → ps7_init → 灌帧/黄金 → 跑相位 → 取回结果块），
因为"怎么上板"这件事已经在 S4 里跑通过 6 次，不需要第二套。

判据（`recm` = 相位 7 = 推荐接法"长度留 384 B 余量 + 帧末软件停 S2MM"）：
  · ROI/RGB/MOT 三个 IP 的 CTRL 都到 0x4（ap_done=1、ap_idle=1）
  · 日志里的 `CMP mismatch=0`（ARM 用 480×270=129,600 字节逐字节比 `gray.bin`）
  · `s2mm_stopped_cr` 的 RS 位已清

用法：python board\\s4_bench\\s4_720.py recm
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s4_run as S  # noqa: E402

# ---- 换档的全部内容：4 个路径 + 4 个几何数 -----------------------------------
S.BIT = S.ROOT / "board" / "bitstream" / "system_wrapper_720p60.bit"
S.PROG = S.S4 / "s4_bench_720.bin"
S.FRAME_SRC = S.ROOT / "fpga" / "sim" / "data_motion_720p60" / "rgb_frames.bin"
S.GOLD_SRC = S.ROOT / "fpga" / "sim" / "data_motion_720p60" / "gray.bin"
S.W, S.H = 1280, 720
S.GW, S.GH = 480, 270
S.FRAME_BYTES = S.W * S.H * 3      # 2,764,800
S.GRAY_BYTES = S.GW * S.GH         # 129,600

if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "recm"
    for p in (S.BIT, S.PROG, S.FRAME_SRC, S.GOLD_SRC):
        if not p.exists():
            print("缺文件：%s" % p)
            sys.exit(1)
    # 旧产物先删：否则一次失败的运行会把上一轮的 res.bin 当成"本次结果"报出来
    for name in ("res.bin", "gray_out.bin"):
        q = S.S4 / name
        if q.exists():
            q.unlink()
    tcl = S.build(stage)
    rc = S.run_xsdb(tcl)
    if "ABORT_PS_WEDGED_NEED_POWER_CYCLE" in S.LAST_OUT:
        print("【PS 挂死】只能断电重上电，再重跑本命令。")
        sys.exit(2)
    if not (S.S4 / "res.bin").exists():
        print("没有 res.bin ⇒ 这次没跑出结果")
        sys.exit(rc)
    sys.exit(S.report(stage))
