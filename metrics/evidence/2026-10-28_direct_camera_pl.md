# 实时采样修复与摄像头直连 PL 验证（2026-10-28）

这是技术验证记录，不是由人类亲手填写的 `report/llm_log` 协作记录。
实物：Mizar-Z7 7020、DECXIN `0c45:64ab` 接开发板 USB-A、COM11、JTAG。
未写 SD/QSPI；没有保存或上传摄像头像素。

## 结论与边界

- 【已验证】实时管线改用采集侧的单调到达时间间隔计时，行为计时、输出 ts 与 rPPG 时间轴一致。
  目标帧率来自配置；按真实时间槽选择帧，慢源不补帧，高速源不冒充原生30fps。
- 【已验证】板端300个真实720p缓冲的独立采样实验：保留152帧、丢148帧，
  所选帧平均29.984710Hz、间隔p95为35.993ms；时间戳严格递增。
  原始记录：[采样日志](2026-10-28_usb_sampling_30.log)。本项没有 JPEG 解码/PL 处理开销。
- 【已验证】首次真实相机→JPEG→RGB→DMA→三个PL IP→DDR对拍，共27帧零差异。
  原始记录：[首次通过](2026-10-28_camera_pl_third.log)。
- 【已验证】优化诊断程序连续出队300个缓冲，其中299帧进入PL，299帧均零差异。
  首帧运动量明确无效，后续298帧运动量严格相等。
  原始记录：[连续实跑](2026-10-28_camera_pl_300.log)、[自动检查摘要](2026-10-28_camera_pl_300_summary.json)。
- 【已验证】该轮所选帧15.182701Hz、间隔p95为68.1033ms；解码/CPU参考/搬运加PL耗时中位数
  分别29.728/16.030/19.544ms。**整机30fps未通过**，不能引用为实时30fps或纯PL耗时。
- 相机仍协商原生60fps，严格原生30fps检查仍非零。门控30Hz只是采样策略，不会更改描述符。
- 本实验 ROI 为整帧；它证明硬件运算正确，不证明额头rPPG、疲劳算法、网页或整机质量门控已验收。
- 网线尚未接通。真实板端画面/PL结果→算法→网页、独立启动、生产DMA驱动与性能优化仍待完成。

## 实际比较的内容

1. JPEG 在 PS 上解码成 RGB888，PL与CPU参考消费同一份RGB，R/G/B顺序一致。
2. ROI整帧三个通道累加和、像素数、帧号严格比较。
3. 720p的3/8相位抽取与整数灰度式，129600个灰度字节逐点比较，容差0。
4. 首帧不比较未定义BRAM历史；后续帧比较 `diff_total`、严格大于阈值的运动像素数与Q16比例，容差0。
5. 灰度输出区事先写入参考字节的按位反值，防止旧数据或尚未写入的数据碰巧通过。
6. 驱动序列缺口与门控丢帧分开记录。该连续实跑有866个驱动序列缺口，不能声称无丢帧。

## Linux / PL 集成方式

当前BD没有DMA IRQ连接，旧主线DMA驱动初始化失败。此次不编造IRQ号，而建立独立用户态轮询实验：

- `direct_loop.dtsi` 关闭DMA内核节点，精确保留16MiB no-map内存。
- 程序核对设备树、System RAM范围、内核驱动所有者和进程锁后才映射寄存器/缓冲。
- JTAG在Linux启动前装载已验证的720p bitstream。
- 保留 `mem=512M cpuidle.off=1`，实验加 `clk_ignore_unused` 保持PL时钟。
- 现有通路抑制TLAST，S2MM使用余量；以实际DDR逐字节完成确认本帧，不把HALTED作为帧边界条件。

这不是生产DMA内核驱动修复，也没有改变业务JSON字段或接口版本。

## 保留的失败与纠正

- [第一次启动](2026-10-28_camera_pl_boot.log)：实验启动脚本漏了既有 `cpuidle.off=1`，停在内核入口。
  补回后[第二次启动](2026-10-28_camera_pl_boot_2.log)进入用户态并自动枚举相机。
- 构建检查发现 `build_dtb.ps1` 的相对输出落进临时目录、自定义UserDts文件未被实际包含。
  已修复，并用反编译后的真实DTB检查16MiB/no-map/profile/DMA独占设置。
- [第一次程序执行](2026-10-28_camera_pl_first.log)在内存保护检查处拒绝继续，没有访问PL寄存器。
  [只读核对](2026-10-28_camera_pl_memory.log)确认ARM把no-map区从System RAM移除，并不单列reserved资源；
  改为核对准确DT属性及不存在System RAM重叠。
- [第二次程序执行](2026-10-28_camera_pl_second.log)：三个IP均已完成，但等待无TLAST通路的S2MM HALTED超时。
  修正为观察实际DDR数据完成，再获得零差异结果。

## 采样软件修复

- `backend/sampling.py`：保留原始时刻及图像，输出帧号连续；记录丢帧、空时间槽与实际速率。
- OpenCV实时源使用单调到达时间；这不是传感器曝光时间。板端C探针使用V4L2缓冲时间戳。
- 实时 `--seconds` 按源时间结束，`--limit` 按所选帧数结束；离线回放保持既有确定性路径。
- rPPG对错误/缺失部分时间戳不再偷偷退回标称帧率；抖动超过现有配置阈值时拒绝出数。
- 覆盖 `--fps` 不会把30Hz冻结FIR系数冒充其他采样率；不匹配时给出 `fir_rate_mismatch` 并保持null。

## 复现与回归

完整构建、启动、实跑入口：[DIRECT_LOOP.md](../../board/w7_buildroot/DIRECT_LOOP.md)。
日志检查器把“硬件比较通过”“实时性通过”“原生模式通过”分别记录；
`--require-realtime`/`--require-native-mode` 不满足会返回非零。

本轮软件完整回归的原始输出：[pytest](2026-10-28_sampling_pytest.log)、[离线总检查](2026-10-28_sampling_check_all.log)。
最终结果：**332 passed, 2 warnings in 22.30s**；离线总检查 **PASS 26 / FAIL 0**（65.4s）。
两条为既有 Starlette/AnyIO 弃用警告。
另有 `sample_gate_test.c` 的C门控测试和 `tests/check_direct_dts.ps1` 的实际DTB检查。

最终镜像再次软件启动后，一键入口实跑成功：[启动](2026-10-28_camera_pl_final_boot.log)、
[实跑](2026-10-28_camera_pl_final_runner.log)、[自动摘要](2026-10-28_camera_pl_final_runner.log.summary.json)。
该次29帧零差异、28帧有效运动量比较通过；仍未达到30fps，不计为新增物理冷启动验收。

已测试的最终程序/镜像 SHA256：

- `vigi_camera_pl`：`734b7b1759f121937421375e86c10d64dfae1d50f2b9d7ba197d7b31d3b6f783`
- `uInitrd`：`14572f10424ab9cae6a429951861dc6f227bc634dd02ca82bc880d48eed9ae2f`
- `system.dtb`：`207e03f89b049ca8c38c66e81179edcd35c29f2c5625398ea1498f61059f4b2e`
- `boot_jtag.scr`：`af8f0a10859213d2a59670cdadc032ded622799dd4676dbe1583f22535f04937`

用户最终澄清目前没有网线连接；未修改电脑网络配置，网口联调未执行。

后续先完成网络通信与同源帧/PL结果传输，再接算法与网页；不能把其他视频的PL结果贴到本次图像上。
