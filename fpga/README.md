# FPGA / Vitis HLS

本目录维护五个 HLS IP：`roi_statistic`、`rgb2gray`、`motion_quality`、`fir_filter`、`frame_scale`。取消的 OpenMV/MIPI 路线不属于当前源码主链路。

| 位置 | 内容 |
|---|---|
| `src/` | HLS C++、头文件及冻结 FIR 系数 |
| `sim/tb_*.cpp` | C 仿真与 cosim 测试台 |
| `sim/gen_*.py` | 确定性向量和黄金参考生成器 |
| `sim/data*/` | 入库的 meta 与黄金 CSV；大二进制向量由脚本重建 |
| `sim/host_model_*.cpp` | 主机参考模型，不能代替 HLS/RTL 证据 |
| `run_hls.tcl` | HLS 构建入口 |
| `report/` | 实际综合、cosim、时序和上板报告及原始日志 |

## 构建与验证

工具链为 Vitis HLS 2026.1，器件 `xc7z020clg400-1`。先在工具终端载入对应环境，再在仓库根生成匹配档位的向量，进入 `fpga` 执行：

```bat
set HLS_IP=roi_statistic
vitis-run --mode hls --tcl run_hls.tcl
```

IP、档位、数据目录与 cosim 开关以 `run_hls.tcl` 和相应生成器参数为准；不能混用不同分辨率、抽取比例或 FIR 采样率的黄金参考。

灰度和运动量等整数链路要求与黄金参考逐点相等。RGB 通道顺序、半开 ROI、TUSER/TLAST 语义及寄存器定义均以 [接口契约](../docs/interface.md) 为准。

HLS 工程 `component_*` 和二进制向量是本机可重建产物，不进入源码包。现有导出 IP/本机工程仍保留，避免破坏 Vivado 或 XSim 的构建依赖。板级脚本在 `../board/`；RTL 直连仿真见 [board/sim](../board/sim/README.md)。

本轮只做项目整理与离线回归，没有重跑 csim/csynth/cosim 或硬件操作。历史结果应引用 `report/` 对应原始报告，不沿用旧 README 中的性能表作为当前结论。
