# 板端代码与工具

当前交付以 **Mizar-Z7020 + Buildroot Linux + 板端自治 + HTTP** 为主。最新软件包及未验收项见 [交付入口](../docs/usage.md)。

| 目录或文件 | 职责 |
|---|---|
| `autonomy/` | C/C++ 算法、视觉运行时、HTTP、配置导出及应用打包 |
| `cold_boot/` | SD 拷贝包、启动服务与文件校验 |
| `w7_buildroot/` | Linux/设备树/启动、USB 相机、DMA 与 PL 数据通路 |
| `s4_bench/` | 裸机固件与 JTAG 取证 |
| `video_link/` | 视频转运与 PC 语义模拟，辅助复现入口 |
| `sim/` | XSim 三 IP 直连对拍 |
| `overlay/` | PYNQ 或 mmap 访问工具；不代表本板已安装 PYNQ |
| `build_bd.tcl` | Vivado 设计构建；参数必须符合实际板型 |
| `bitstream/` | 本机生成的硬件产物，源码 RAR 不包含这些构建二进制 |

源码、寄存器与像素语义以 [接口契约](../docs/interface.md) 为准。五个 HLS IP 在 `../fpga/src/`；真实上板报告和日志保留在 `../fpga/report/`、`../metrics/evidence/`。

## 使用入口

- 部署已有 SD 软件包：[cold_boot/README.md](cold_boot/README.md)。
- 重建算法应用：[autonomy/README.md](autonomy/README.md)。
- Linux 与相机/PL 复现：[w7_buildroot/README.md](w7_buildroot/README.md)。
- 仿真：[sim/README.md](sim/README.md)。
- 裸机：[s4_bench/README.md](s4_bench/README.md)。

【未验证】最新持续运行版的独立实卡冷启动与恢复仍待验收；本轮整理不操作板卡。旧板型、旧相机路线和旧阶段结论从 [清理前备份](../docs/README.md) 追溯。
