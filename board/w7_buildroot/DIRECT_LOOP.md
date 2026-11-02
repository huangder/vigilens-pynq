# 摄像头直连 Linux → PL 实验入口

> **2026-10-29 增量【已验证】**：GEM0 网口已以 100Mbps/Full 联通，真实板端相机 RGB 与同包 PL 结果
> 已经接到 PC MediaPipe、契约 API 与同源网页预览。300缓冲保留296帧，全部零差异，约3.91fps；不能声称30fps或疲劳算法已验收。
> 网络构建、启动与复跑见 [NETWORK_LOOP.md](NETWORK_LOOP.md)。下文“尚未连接/尚未接网页”是10月28日历史状态。

板卡为 Mizar-Z7 7020。摄像头接开发板 USB-A；JTAG、COM11 串口连接电脑。
当前是 RAM 内的用户态轮询原型，不烧写 SD/QSPI。
真实证据见 [2026-10-28 技术记录](../../metrics/evidence/2026-10-28_direct_camera_pl.md)。

## 已验证与限制

- 【已验证】相机真实 MJPG → PS 解码 RGB888 → AXI DMA → ROI/rgb2gray/motion_quality → DDR，
  与同一份解码 RGB 的 CPU 整数参考逐字节比较。首次27帧、后续299帧均零差异。
- 首帧运动量无有效前帧，明确标 `motion_valid=0`，不作为有效运动量比较证据。
- 【已验证】带每帧全量CPU参考的优化诊断运行约15.18fps，**未达到30fps**。
  `VIGI_JPEG_FAST=1` 采用 IFAST、关闭 fancy upsampling；解码 RGB 可能与准确模式不同，日志明确记录。
- 相机原生 MJPG 仍协商60fps。时间槽采样不等于原生30fps模式通过。
- ROI 此处为整帧，仅证明硬件累加正确，不能作为额头 rPPG 输入或疲劳测量结果。
- 真实板端画面/PL结果接网页、独立启动和生产 DMA 驱动仍待完成。网线尚未连接。

## 1. JPEG 构建依赖（WSL，现有 Buildroot）

先备份 `/root/vigilens/buildroot/.config`。当前工具链已完成以下构建：

```bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd /root/vigilens/buildroot
./utils/config --enable BR2_PACKAGE_JPEG --enable BR2_PACKAGE_JPEG_TURBO
make olddefconfig
make jpeg-turbo JPEG_TURBO_CONF_OPTS=-DENABLE_STATIC=ON
```

静态链接 libjpeg，避免板上另装运行库。摄像头像素始终留在本机/开发板内存中，工具不保存或上传图像。

## 2. 生成独立实验镜像

先按本目录 README 生成已修复 USB 的 `.tmp/w7_usb_images/`，再执行：

```powershell
wsl -d Ubuntu -e bash -lc 'bash /mnt/d/Desktop/AMD/board/w7_buildroot/repack_direct_loop.sh /mnt/d/Desktop/AMD/.tmp/direct_loop'
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/tests/check_direct_dts.ps1
```

`direct_loop.dtsi` 精确保留 `0x12000000..0x12ffffff`（16MiB、no-map）。
程序检查设备树、该区不属于 System RAM、DMA 无内核驱动所有者后才访问 MMIO。
当前 BD 没接 DMA IRQ，故本实验明确关闭原 DMA 平台节点，由轮询程序独占；没有编造中断号。
`clk_ignore_unused` 用于保持此实验未绑定驱动的 PL 时钟；保留既有 `mem=512M cpuidle.off=1` 启动参数。

## 3. 装载匹配的 PL 和 Linux

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/boot_after_powercycle.ps1 -Port COM11 -ImageDir .tmp/direct_loop -Bitstream board/bitstream/system_wrapper_720p60.bit -Out metrics/logs/_direct_loop_boot.log
```

该命令会软件重启板卡。加载顺序为系统复位 → 匹配的720p bitstream → PS初始化与USB修复 → Linux镜像。
启动脚本可能返回2：表示实际取帧成功而严格原生30fps未通过；须看完整日志，不把退出2当作原生模式通过。
这不是新的物理冷启动验收。

## 4. 实跑与分层验收

```powershell
# 每帧严格对拍，准确 JPEG 解码
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/run_camera_pl.ps1 -Frames 30
# 明确启用快速 JPEG 诊断模式；仍做每帧全量对拍
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/run_camera_pl.ps1 -Frames 300 -FastJpeg
# 增加实时性验收；当前实测应非零退出，不能标为30fps通过
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/run_camera_pl.ps1 -Frames 300 -FastJpeg -RequireRealtime
```

原始 C 命令的 `frames` 是从 V4L2 成功出队的缓冲数，不是硬件比较帧数或相机总曝光数。
日志分别记录门控丢帧、驱动序列缺口、所选帧时间戳及最终实际比较帧数。
`check_camera_pl_log.py` 还会拒绝截断日志、错误退出、帧号/时间戳回退与任何非零差异。
串口工具执行成功本身不等于板上命令成功。

完成判据是 `CAMERA_PL_PASS` + 每帧零差异 + 日志检查器通过。
实时性与原生模式分别检查，均未通过时不会改变硬件数据正确的事实。
