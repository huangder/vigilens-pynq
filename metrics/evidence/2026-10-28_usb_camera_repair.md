# USB 摄像头板端修复与验证（2026-10-28）

板卡：MicroPhase Mizar-Z7 7020；串口 COM11/115200；JTAG；不写 QSPI/SD。

**结果【已验证】**：修复镜像完整软件复位启动后自动绑定主控、枚举 DECXIN `0c45:64ab`，
在 640×480、1280×720 下各实采 30 帧；没有保存或上传像素。
后续已完成 **3次物理冷启动**：每次均自动枚举、两档各取得30帧；**3次摄像头物理拔插均自动重新识别**。
**剩余限制**：MJPG 模式表这两档都只列 `1/60`，请求 `1/30` 仍协商回 `1/60`。
所以 USB 连接/数据链已修复，**严格的 720p30 模式仍未通过**，检查器不会把它标成 PASS。
本文件是技术验证记录，不是人类必填的 `report/llm_log` 协作记录。

## 已核对的硬件依据

- [厂商 Mizar-Z7_R11 原理图](https://github.com/MicroPhase/fpga-docs/blob/master/schematic/Mizar-Z7_R11.pdf)
  第 2/9 页，已在本地渲染核对：USB0=MIO28..39；RESETB=MIO46；Bank501 与 USB3320 VDDIO=1.8V；Y3=24MHz。
- USB-A 口由 TPS2051B 经 J5 供电。用户确认 J5 已短接；端口实际电压尚未用仪表测量。
- 外接摄像头尚未在电脑上交叉测试，不能声称相机已排除故障。

## 已修正的软件错误

1. GPIO 掩码必须只清除待写位的高半字掩码。MIO46 对应 Bank1 bit14：低电平写 `0xBFFF0000`，高电平写 `0xBFFF4000`；原 `0xFFFFxxxx` 不改任何位。
2. MIO bit9 是 IO_Type 的一部分，不是复用位；GPIO mux 在 bits7:1，bit0 为三态。`0x1001` 把 IO_Type 清成保留值。复位脚修复后读回 `0x1200`。
3. 读取实际 GPIO 电平使用 `0xE000A064`，不是输出锁存 `0xE000A044`。
4. USB 引脚原有 `0x1604/0x1605` 是 LVCMOS33，与原理图不符；启动前改为 LVCMOS18，Linux pinctrl 维持 1.8V。
5. 主线 Linux 6.6 使用 `usb-nop-xceiv` 的 `reset-gpios`，在主控初始化前复位；开机自检不再反复写复位/时钟寄存器。
6. ULPI 视口遵循实际内核 `phy-ulpi-viewport.c`：WAKEUP bit31、RUN bit30、读数据 bits15:8，vendor 地址 0/1、product 地址 2/3。
7. JTAG 系统复位后重新 halt CPU；装载完成后在 halt 状态设入口再运行。XSDB 会在 Tcl 错误后返回 0，PowerShell 因此还必须检查 `JTAG_BOOT_STARTED` 标记。
8. UART1 MIO48/49 同属 1.8V Bank501；纠正旧 LVCMOS33 配置后，PC→PS 串口输入恢复，已在板上成功登录 root 并执行诊断。
9. 当前 Linux 6.6 的 chipidea 在 legacy PHY 尚未注册时会从全局 fallback 得到 `-ENODEV`，随后返回 `-ENXIO`，不会延迟重试。带 GPIO 的 PHY 较晚注册，因而 `ci_hdrc.0` 存在但无 driver。
   在 PHY 就绪后手动 bind 已实证可立刻枚举相机；`S98usb_host` 将这一步变成仅针对未绑定 USB0 的一次开机恢复。已绑定主控不会被重置或重新绑定。

## 已执行的验证

- `xsdb board/w7_buildroot/tests/usb_phy_test.tcl`：22 条 PASS，包括冷启动基线防误报、掩码隔离、其他 GPIO/时钟保持、UART1电气配置、电平卡高时拒绝成功。
- `powershell -File board/w7_buildroot/check_dts.ps1`：PASS，包含 active-low GPIO46 与 1.8V pinctrl 检查。
- `python metrics/scripts/check_all.py`：**PASS 26 / FAIL 0**，工具自检 **10/10**，实际用时 88.1s。
- `python -m pytest -q`：**305 passed, 2 warnings in 24.39s**；两条为现有 Starlette/AnyIO 弃用警告。
- `uvc_probe.c` 已用现有 Buildroot ARM 交叉编译器以 `-Wall -Wextra -Werror` 编译，shell 语法检查通过。
- 板端物理 DAP 读回：RESETB 拉低时 PAD=`0x003C8000`，拉高时 PAD=`0x003CC000`；随后完整 prepare 输出 expected=0/sampled=0、expected=1/sampled=1。
- 正确视口操作在实际板卡读出 **ULPI_ID vendor=0x0424 product=0x0007**；因此旧“PHY 无应答”的问题已不成立。这不等于摄像头已经枚举或采帧。
- 最终完整软件复位启动：自动输出 `USB_HOST_READY: legacy PHY late-probe recovery`，识别 `Found UVC 1.00 device DECXIN (0c45:64ab)`，两个分辨率各收到 30 帧。`/dev/video1` 是非视频采集节点，检查器正确 SKIP。
- 开机恢复脚本再次运行：USB bus 注册计数 before=1/after=1，`USB_HOST_REENTRY_PASS`，没有重新绑定已工作的主控。原始输出：[重入检查](2026-10-28_usb_host_reentry.log)。
- 用户执行三次物理摄像头拔插后，内核分别自动枚举为 device 3/4/5，均识别 DECXIN `0c45:64ab`；末次插回后再次取得10帧720p。原始输出：[热插拔监测](2026-10-28_usb_hotplug_monitor.log)、[最终内核记录与采帧](2026-10-28_usb_hotplug_final.log)。
  逐轮采帧监测在第2次重连后又被快速拔出时终止，不能标成“三次逐轮采帧通过”；三次自动重新识别由随后读取的内核日志确认。
- 用户确认的第2次断电操作后，两档采帧仍成功，但JTAG复位前 `USB_MODE=3`、GPIO46仍为输出，保留了上一轮运行状态。该次只记“启动复验，冷启动属性未确认”，不计入3次冷启动。
  原始输出：[启动复验2](2026-10-28_usb_boot_attempt_2.log)、[其复位前寄存器](2026-10-28_usb_boot_attempt_2_jtag.log)。可能存在其他USB供电，未测量电源轨，不能定论。
  后续冷启动使用 `-RequireColdState`，在复位前检查USB/GPIO基线；若未观察到复位状态则停止，不会先软件复位再冒充物理冷启动。
- 一次“复用已装载镜像”的恢复操作实际发生在 Linux 已运行后，触发内核 paging Oops；该临时恢复路径已弃用，不计作启动通过。新脚本需从冷启动验证。

原始输出：[GPIO模型](2026-10-28_usb_gpio_model.log)、[实际引脚脉冲](2026-10-28_usb_reset_pad.log)、[离线总回归](2026-10-28_usb_fix_check_all.log)、[pytest](2026-10-28_usb_fix_pytest.log)、[最终板端启动与采帧](2026-10-28_usb_camera_board.log)、[JTAG装载与PHY ID](2026-10-28_usb_camera_jtag.log)。

## 冷启动与热插拔验收

三轮均由用户确认断电后恢复接线；复位前USB/GPIO状态符合复位基线。
第2/3轮加 `-RequireColdState`，记录 `COLD_USB_BASELINE_OK` 后才允许软件复位与装载。

| 冷启动 | 自动枚举 | 640×480实采 | 1280×720实采 | 原生30fps | 证据 |
|---|---|---|---|---|---|
| 1 | DECXIN 0c45:64ab | 30帧 | 30帧 | 未通过，协商1/60 | [串口](2026-10-28_usb_cold_boot_1.log) / [JTAG](2026-10-28_usb_cold_boot_1_jtag.log) |
| 2 | DECXIN 0c45:64ab | 30帧 | 30帧 | 未通过，协商1/60 | [串口](2026-10-28_usb_cold_boot_2.log) / [JTAG](2026-10-28_usb_cold_boot_2_jtag.log) |
| 3 | DECXIN 0c45:64ab | 30帧 | 30帧 | 未通过，协商1/60 | [串口](2026-10-28_usb_cold_boot_3.log) / [JTAG](2026-10-28_usb_cold_boot_3_jtag.log) |

热插拔：内核记录三次断开与新设备号3/4/5，全部识别同一VID/PID，末次又取得10帧720p。
这证明三次自动重新识别及末次实采；逐轮采帧脚本中途因快速再次拔出而结束，不能写成“三次逐轮采帧脚本全通过”。

## 镜像与复现

原始 `D:/w7/images` 保持不变；默认修复产物在 `.tmp/w7_usb_images/`。

```powershell
# 仓库根执行；断电重上电后运行。镜像缺失会直接失败。
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/build_dtb.ps1 -Out D:/Desktop/AMD/.tmp/w7_usb_images/system.dtb
wsl -d Ubuntu -e bash -lc 'bash /mnt/d/Desktop/AMD/board/w7_buildroot/repack_usb_initrd.sh /mnt/d/Desktop/AMD/.tmp/w7_usb_images'
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/boot_after_powercycle.ps1 -Port COM11
```

采帧器只输出协商模式、帧序号、字节数与时间戳；不保存/上传图像。
`CAPTURE_PASS` 表示请求的 30fps 模式也符合；`CAPTURE_FRAMES_OK` 只证明真实帧已取得，模式仍有差异，工具退出非零。
root hub 或 `/dev/video*` 存在本身不算采帧通过。

同一份已测试镜像的 SHA256：

- `uInitrd`: `b994ba8c2228404a28f0cbdaa913c304047c9eb135acbb09a157a9ce6d8e9b6e`
- `system.dtb`: `e97f0d2595a44e4320c8716f73db97a586160e217a7037be773eef3fe2c68daf`

## 尚未完成

- 摄像头原生 30fps 模式：本次描述符与协商均只给 60fps，不能靠修改断言凑绿；如后续需要 30Hz 分析输入，须明确设计独立的时间采样策略，不能误报成相机原生 30fps。
- 电脑侧同一摄像头交叉测试未做；板端三次物理拔插后的自动重新识别已确认。
- Vivado 中的 Bank501 配置脚本已同步1.8V，尚未重综合/重导XSA；本次实际验证的是JTAG初始化与新设备树。
- 板卡已完成枚举与实采，无证据要求更换板卡或摄像头；电压/时钟未做仪表实测，不据此宣称所有硬件参数已验收。
