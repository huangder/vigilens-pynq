# Mizar-Z7 GEM0 独立实验配置

> **当前推荐（2026-10-29）【已验证】**：`-Codec jpeg -Pipeline threaded -DecodeWorkers 2 -PlWait yield -PreviewMode latest -AuditEvery 30`。
> 1800 相机缓冲长跑：909 帧、31 帧审计零差异，采样 30.00 fps、间隔 p95 36.85 ms，R1 通过。
> PC 全程吞吐 29.7 fps，严格 R2 未过；R3 延迟、有人脸负载和原生 30 fps 尚未验收。
> [方案与回退](../../docs/README.md)、[真实证据](../../metrics/evidence/2026-10-29_parallel_pipeline.md)。
> 下方 25.29 / 3.91 fps 为此前运行的历史结果。

> **帧率改造复核（2026-10-29）【已验证】**：JPEG + 三线程 + 每 30 帧审计已上板。
> 300 相机缓冲交付 236 帧、8 帧审计零差异，采样 25.29 fps、PC 全程吞吐 22.5 fps。
> 实时帧率/抖动门限未过；原生 30 fps、疲劳准确性与独立启动仍未通过。
> [修复与真实证据](../../metrics/evidence/2026-10-29_fps_review.md)，[模式与复现](../../docs/README.md)。
> 下方 3.91 fps / 全量严格对拍为此前 raw 串行运行的结果。

> **2026-10-29 当前结果【已验证】**：修正版PHY设备树重启后自动协商100Mbps/Full，
> 无125MHz告警；300个相机缓冲保留296帧，全部PL严格零差异，PC逐帧运动量/时间戳核对通过，
> MediaPipe→指标API72次、同源预览293次投递成功，完整正常结束。
> 实际3.91fps，未检出人脸，30fps与疲劳算法验收均未通过。
> 完整边界与证据：[技术记录](../../metrics/evidence/2026-10-29_network_closed_loop.md)。

## 构建、启动、复跑

构建时不能有另一个进程正在从同目录下载镜像。先完成构建，再启动：

```powershell
wsl -d Ubuntu -e bash -lc 'bash /mnt/d/Desktop/AMD/board/w7_buildroot/repack_direct_loop.sh /mnt/d/Desktop/AMD/.tmp/net_loop'
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/build_dtb.ps1 -UserDts D:/Desktop/AMD/board/w7_buildroot/network_loop.dtsi -Out D:/Desktop/AMD/.tmp/net_loop/system.dtb
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/boot_after_powercycle.ps1 -ImageDir .tmp/net_loop -Tcl board/w7_buildroot/network_jtag_boot.tcl -Bitstream board/bitstream/system_wrapper_720p60.bit -Port COM11
```

启动入口的严格相机模式检查仍可能返回2：实采成功但相机原生30fps未满足，须查看日志。
当前仍为JTAG加载RAM，不能表述为SD独立启动。板端Linux就绪后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/run_network_demo.ps1 -Frames 300
```

默认板IP为169.254.73.29，PC已有IP为169.254.73.28（/16）；其他环境显式传`-BoardIp/-PeerIp`。
入口不改PC网卡/防火墙，不自动重启板卡；检查相机、100Mbps/Full和无其他采集进程后，
使用config.yaml的帧率/运动阈值启动一次板端服务，确认监听再连接PC管线。
已启动的本机ingest服务会复用；缺服务时仅在127.0.0.1启动并保留。
输出在`metrics/logs/network_demo_运行编号/`，已有运行编号会拒绝覆盖。
只生成可审查命令可加`-PrepareOnly`；旧镜像临时限速可加`-Set100Full`，需镜像包含network_ctl。

打开[本机仪表盘](http://127.0.0.1:8000/)，点击“连接WebSocket”；帧源结束后显示完成/过期状态。
API/WS投递已验证，未保存人脸截图，也未将DOM检查冒充视觉渲染验收。

VLP1为长度前缀+JSON+送入PL的原始RGB；VLE1带实际比较帧数，只有计数吻合才正常结束。
这两个包是实验帧源协议，冻结的9字段业务契约没有改动。像素只在开发板/本机内存内传输。
网络反压会降低实际采样率，不能按标称30fps重建时间轴或宣称无丢帧。

## 配置依据与首次排障

仅供 Mizar-Z7 7020、RAM/JTAG 启动；默认 USB/PL 配置不变。不写 SD/QSPI。

【已验证：资料核对】本机 `Mizar-Z7_R11.pdf` 第 2/8 页：

- Bank501 与 PHY VDDIO 为 1.8V；GEM0 RGMII=MIO16..27，MDC=52、MDIO=53。
- PHY 为 RTL8211E_VL，PHY 地址 strap=001；TX No Delay，RXDLY 有上拉。
- ETH_nRST 通过 D12 接 PS_POR_B，并由 R68 上拉；不是 GPIO。配置不虚构 reset-gpios。
- `rgmii-id` 由现有 Linux 6.6.158 `drivers/net/phy/realtek.c` 的
  `rtl8211e_config_init()` 显式开启 TX/RX delay，覆盖 strap。

【已验证：离线】`build_gem0_ps.tcl` 在独立最小 PS 工程生成 MIO/时钟字段，未综合 PL。
生成的完整 ps7_init 含默认 DDR/PLL，**不得直接应用**。`gem0_init.tcl` 只补 GEM0：
MIO、电压、APER bit6、RX 时钟、TX 分频、GEM0 复位释放。现有 validated init 的
IO PLL FBDIV=48（33.333333 MHz 输入）；初联通限制 100Mbps，以 /8 /8 得到 25MHz。
这个 25MHz 是按现有 PLL 与 GEM 分频字段计算，尚需板上读回/链路验证；最小 PS 生成器
会自动重算 PLL，不能将其默认 /8 对应的 125MHz 直接套到现有 init。

编译独立设备树（不覆盖默认产物）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/build_dtb.ps1 -UserDts D:/Desktop/AMD/board/w7_buildroot/network_loop.dtsi -Out D:/Desktop/AMD/.tmp/net_loop/system.dtb
```

`network_loop.dtsi` 包含 direct_loop，并启用 GEM0、PHY@1、PHY 节点的 100Mbps 上限与
实验 local-mac-address `02:56:49:47:49:01`。U-Boot 可能在启动时写入 MAC，以板上读回为准。
复制/重打其余 Linux 镜像到独立目录后，启动时显式传
`boot_after_powercycle.ps1 -ImageDir ... -Tcl .../network_jtag_boot.tcl`，以及匹配 PL 的 `-Bitstream`。
`network_jtag_boot.tcl` 复用原启动脚本，在 USB prepare 后调用 GEM prepare；原入口不变。
启动标记应含 `GEM0_PREPARE_OK`。镜像正在下载时禁止重打包或覆盖该目录。

板上由唯一硬件控制者执行（示例 PC 已有 169.254.73.28/16）：

```sh
dmesg | grep -Ei 'macb|gem|realtek|phy|eth0'
ifconfig eth0 169.254.73.29 netmask 255.255.0.0 up
cat /sys/class/net/eth0/carrier
cat /sys/class/net/eth0/speed
cat /sys/class/net/eth0/duplex
ping -c 3 169.254.73.28
```

【主代理已实测，见 `metrics/evidence/2026-10-29_network_ping.log`】初版设备树
PHY 地址 01、板到 PC ping 3/3；但只在 MAC 父节点加 max-speed 没有限制成功，
实际协商 1Gbps 并报告无法生成 125MHz。现已把限制移到 PHY 子节点，依据本机
Linux `drivers/net/phy/phy-core.c` 的 `of_set_phy_supported()`。
修正版设备树另输出 `.tmp/net_profile_100/system.dtb`，未覆盖正在使用的 net_loop。

【已验证】修正版PHY子节点限速自动生效，软件重启后Link Up100Mbps/Full，ping3/3。
300缓冲相机/PL/网络并行已实跑，见顶部证据。千兆时钟、原生30fps和长期稳定性仍未验收。
协商速度不等于应用吞吐；初版能 ping 不代表千兆时钟已经通过验收。

离线寄存器模型测试（无需板卡）：

```powershell
& D:/Xilinx/2026.1/tps/win64/git-2.53.0/mingw64/bin/tclsh.exe board/w7_buildroot/tests/test_gem0_init.tcl
```

真实运行得到 `GEM0_MODEL_PASS`：覆盖 TX/RX mux/电压、分频、共享寄存器位保留、
不写 PLL/DDR、错误 PLL 时零写入拒绝。设备树编译与反编译已通过；基础生成树的
unit-address 等历史 warning 仍存在，未将其冒充无警告验证。

## 无 ethtool 时临时限制广告

`network_ctl.c` 默认只读，只有显式 `--100-full` 会改当前 RAM 会话中的 PHY 广告：

```sh
/tmp/network_ctl eth0
/tmp/network_ctl eth0 --100-full
# 协商异步完成后再查询
/tmp/network_ctl eth0
cat /sys/class/net/eth0/speed
```

交叉编译（现有 Buildroot 工具链）：

```sh
/root/vigilens/buildroot/output/host/bin/arm-linux-gcc -std=c11 -O2 -Wall -Wextra -Werror board/w7_buildroot/network_ctl.c -o .tmp/net_link/network_ctl
/root/vigilens/buildroot/output/host/bin/arm-linux-strip .tmp/net_link/network_ctl
```

【已验证】上述编译通过；产物为 ARM EABI5、使用 `/lib/ld-linux-armhf.so.3`。
该工具先 GSET，确认支持 100baseT Full/Autoneg 后，保留 Pause/AsymPause 与媒体能力，
仅广播 100baseT Full，SSET 后再次 GSET 核对广告。并不修改 PC 网络或持久启动配置。
