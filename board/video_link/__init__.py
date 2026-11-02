"""主机侧「视频 → 开发板」链路（2026-10-26）。

板端相机不可用（USB PHY 不应答），摄像头改在**主机侧**采集，视频打包成
RGB888 帧序列后经 JTAG 送进 DDR，由 PL 逐帧做 ROI/灰度/运动量。

模块划分：
    video_clip.py   帧序列打包、manifest、sha256、灰度参考模型
    make_clip.py    命令行：摄像头/视频文件 → clip 目录
    run_video.py    命令行：clip → xsdb → 板上 phase 10 → 哈希比对
"""
