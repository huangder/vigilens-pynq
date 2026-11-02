# 验证与证据

## 离线检查

在仓库根 PowerShell 执行：

```powershell
. .\env.ps1
python metrics/scripts/check_all.py
python -m pytest -q
node frontend/mock.js --selftest
node --test frontend/tests/*.test.js
node frontend/hr_display.test.js
```

`check_all.py` 区分回归失败和素材/硬件就绪状态。缺素材不是算法通过；通过离线回归也不等于上板通过。根 `pytest.ini` 默认只收集 `backend/tests`。

板端 Python 工具需显式收集：

```powershell
python -m pytest board/autonomy/tests board/cold_boot/tests board/w7_buildroot/tests -q
```

其中部分编译/HTTP/生命周期检查需要 Linux、编译器、第三方头文件或本地素材；跳过、依赖缺失和执行通过须分别记录。CMake/CTest、ASan/UBSan 及 ERT 生命周期入口见 [board/autonomy](../board/autonomy/README.md) 和该目录 `tests/`。

## HLS 与硬件

```powershell
python fpga/sim/gen_frames.py
```

随后在已载入 Vitis HLS 2026.1 环境的终端进入 `fpga/`，运行 `vitis-run --mode hls --tcl run_hls.tcl`。具体 IP 选择、仿真/综合/cosim 与报告入口见 [fpga/README.md](../fpga/README.md)。Vivado 构建入口为 `board/build_bd.tcl`，三 IP RTL 对拍见 [board/sim](../board/sim/README.md)。板型、档位、IP 版本与黄金参考须匹配；整数像素链按约定容差 0 比较。

## 已归档实测

以下引用的是指定版本的历史执行，不能用来替代当前源码的再次运行。

| 项目 | 真实记录与边界 |
|---|---|
| 板端真人短测 | 900 相机输入，分析 29.672780 fps，队列跳过 9 帧；890 合法契约、857 帧有人脸、200 帧 HR 非空；PL 审计 30 次、失败 0 次 |
| 首次 HR | 距首契约 13.147723 秒；没有同步参考心率，不报告准确率 |
| 显示状态回放 | 两轮真实契约回放，区分当前值、历史值及正在测量；属于显示逻辑验证 |
| SD 交付 | 构建与文件级检查已有记录；本版物理断电启动尚未验收 |

出处：[短测原始汇总](../metrics/evidence/2026-11-02_hr_continuous_live2_results.json)、[显示回放](../metrics/evidence/2026-11-02_hr_continuous_display_replay.json)、[C/Python 对拍](../metrics/evidence/2026-11-02_hr_continuous_live2_raw_verification.json)、[当版总检查](../metrics/evidence/2026-11-02_hr_continuous_check_all.json)、[SD 文件核验](../metrics/evidence/2026-11-02_cold_boot_delivery.json)。

## 尚需物理验收

1. 不用 JTAG 补发初始化，从完整断电状态 SD 启动，保存串口与服务日志。
2. 最新持续版在有人脸、无人脸、断网及相机拔插时运行，核对契约更新、拒报、重连与日志上限。
3. 同时报出采集、分析与有效测量的帧率、队列跳过和抖动，不以单项吞吐代替完整 30 fps。
4. 心率使用同步参考、行为使用独立人工标注；不同人物/姿态/光照分别统计，先确定指标再计算结果。

正式日志位于 `metrics/evidence/`、`fpga/report/`；`synthetic`、`stub`、单元测试和 PC 回放均须明确输入性质。原始人脸媒体保留本地，不上传公开仓库。
