# Mizar-Z7 网口同源闭环验证（2026-10-29）

这是技术验证记录，不是人类亲笔填写的`report/llm_log`协作记录。
板卡：Mizar-Z7 7020；相机DECXIN `0c45:64ab`接板上USB；COM11和JTAG；PC直连网线。
没有保存或上传摄像头像素，没有烧写SD/QSPI，没有改PC网卡或防火墙。

## 已验证结论

- GEM0/RTL8211E地址01、100Mbps/Full。PC已有169.254.73.28/16，板端RAM配置169.254.73.29/16。
  修正版PHY设备树重启后自动限100Mbps，不再出现125MHz告警，ping3/3。
  [启动](2026-10-29_network_final_boot.log)、[链路](2026-10-29_network_final_link.log)。
- 同一帧真实MJPG经PS解码RGB→DMA→PL，灰度、ROI、有效运动量均严格对拍后，
  才把该RGB和硬件结果作为一个TCP帧包发送给PC。PC再次核对RGB三个通道累加、身份、时间和阈值。
- 长跑成功出队300个相机缓冲；时间门控保留296、丢4。296帧PL零差异，295帧有效运动量比较通过。
  不能把300个缓冲写成300次曝光或300帧无丢失；驱动序列缺口4182。
- PC接收296帧、正常结束标记计数吻合、错误为null。逐帧采集时间戳及PL运动量进入业务JSON均核对通过。
  使用MediaPipe真实引擎，指标API成功72次，同源预览成功293次，均无投递错误。
  [板端原始日志](2026-10-29_network_camera_pl_300.log)、[PC摘要](2026-10-29_network_pipeline_summary.json)、
  [指标JSONL](2026-10-29_network_metrics.jsonl)、[逐帧交叉检查](2026-10-29_network_check.json)、
  [一键入口摘要](2026-10-29_network_runner_summary.json)、[PC输出](2026-10-29_network_pipeline.log)。
- 本机仪表盘HTTP可访问，WebSocket首帧与API返回的真实末帧完全相同，Mock关闭。
  [投递检查](2026-10-29_network_delivery.json)。未通过截图宣称视觉渲染验收。
- 实际采集跨度75.446566秒，输出3.910052fps；间隔p95约271.999ms。
  JPEG解码/CPU参考/搬运加PL耗时中位数分别31.609/16.1875/19.630ms。
  100Mbps下每帧原始RGB传输有反压；这些数据不是纯PL性能，也不是30fps实时验收。
- 画面未检出人脸（0/296）。这里只证明算法引擎和网页数据接口已接入，不证明疲劳识别、
  心率/呼吸率或阈值准确性。相机仍原生60fps，严格30fps模式不满足，板端退出2被如实保留。

## 复现命令与软件验证

完整构建/启动：[NETWORK_LOOP.md](../../board/w7_buildroot/NETWORK_LOOP.md)。最终真实执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/run_network_demo.ps1 -Frames 300 -RunId 20261029_final_retry
```

PC退出0、板端原生模式检查退出2、闭环检查器退出0。已有RunId拒绝覆盖；新运行请省略RunId自动生成。
首帧运动量明确无效，PC令质量为0、状态unreliable、vital为null；断流不伪装完成。
实时源摘要另报真实采集时长，保留旧logical_duration_s仅供兼容，不能据其9.87秒计算本次实测速率。

软件实际验证：修改前完整pytest332 passed；加入帧源后独立完整pytest375 passed（两条既有弃用警告）；
新增跨日志检查后目标测试50 passed；最终离线总入口PASS26/FAIL0、工具自检10/10，
其中A线总入口含当前完整pytest。原始总检查：[日志](2026-10-29_network_check_all.log)。
GEM寄存器模型测试输出GEM0_MODEL_PASS；ARM程序以Wall/Wextra/Werror构建通过。

## 保留的失败与修复

1. 第一次网络镜像下载报unexpected end of file，与重打包同目录镜像重叠；停止产物写入后重试成功。
   后续禁止镜像生成与JTAG读取同目录并发。
2. 初版只在MAC节点写max-speed100，PHY仍协商千兆并告警125MHz。
   [初次ping](2026-10-29_network_ping.log)不算千兆时钟验收；先以[显式广告限速](2026-10-29_network_100m.log)验证，
   再把max-speed移到PHY@1节点，经重启自动生效。
3. 首次一键长跑因stdout缓冲未及时写出LISTEN，PC拒绝连接，板端等候超时，未误报成功。
   将setvbuf移到首次输出前，并在LISTEN后显式fflush；重建后上传RAM，SHA256核对一致，重跑通过。
4. 检查器测试夹具最初错误调用new_frame缺参，7项失败；改用仓库真实mock_frame接口后目标50项通过。
   测试夹具只验证拒绝逻辑，不作为物理数据证据。

## 产物身份与未完成项

本轮最终程序SHA256为`55b062102cd5d29803c72004a759b2240cbc6b1c89a9a44dacb7afad0ef57e62`，
经UART安装到板端RAM文件系统后实跑；[安装核验](2026-10-29_network_stream_install.log)。
已软件重启验证的网络DTB为`de41aec40b11b39e2ebeb7c75f55298b85c8ec3123945e3eab069ee5be182bfc`。
最后构建的含修复程序uInitrd为`fc2dbaf47d9c79fce5c364dae8ac8c1073d8b4745a30b604d65b534eea508d42`。
随后已从该最终镜像再次软件重启，并直接一键复跑30缓冲：保留28帧全部零差异，
PC退出0、闭环检查退出0，指标8次/预览25次投递成功，无需再次UART替换程序。
[最终镜像启动](2026-10-29_network_release_boot.log)、[复跑对拍](2026-10-29_network_release_camera.log)、
[复跑检查](2026-10-29_network_release_check.json)、[复跑入口摘要](2026-10-29_network_release_runner.json)。
这是软件重启复现，不冒充新增物理冷启动或SD独立启动。

仍需30fps性能优化/千兆一致时钟方案、真实有人脸素材与标注、阈值标定、独立启动和生产DMA驱动。
当前算法与网页在PC执行，板端执行采集/解码/PL；不能写成算法已独立跑在板端。
契约会签及人类亲笔协作记录仍由人类完成；正式9字段契约/版本没有改动。
