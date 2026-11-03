# 运行与构建

## PC 快速演示

需要 Python 3.12、`requirements.txt` 依赖、现代浏览器；前端命令检查还需 Node.js。在仓库根的 PowerShell 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
. .\env.ps1
python metrics/scripts/run_demo.py
```

默认输入为合成帧，只验证软件链路。真实相机先查询设备，再将实际编号传给 `--source`：

```powershell
python metrics/scripts/run_demo.py --list
python metrics/scripts/run_demo.py --source 0
```

运行摘要中的 `landmark_source=stub` 表示占位关键点，该次 EAR/MAR 等不得作为算法结果。PC 管线的图像旁路独立于指标契约；具体选项见 `run_demo.py --help` 及 [backend/README.md](../backend/README.md)。

## 板端重建

当前主链路在板端计算，由 HTTP 展示，PC 无需生产测量数据。源码入口为 [board/autonomy](../board/autonomy/README.md)，Linux/PL 依赖见 [board/w7_buildroot](../board/w7_buildroot/README.md)。

构建依赖 WSL/Linux、已有 Buildroot、ARM 交叉编译器、NCNN 和相应模型；不同运行时还需要 MNN 或 dlib。`build_native_vision.sh` 使用本地缓存，不自动准备完整依赖。`VG_DEPS`、`VG_DLIB`、`VG_CONFIG` 可覆盖部分路径；脚本中仍有本机 Buildroot 路径，新机器需要配置。

在已准备依赖的 Linux 环境、仓库根执行：

```sh
bash board/autonomy/build_native_vision.sh arm .tmp/autonomy/rebuild ert_temporal
```

单独封包前查看 `python board/autonomy/package_native_vision.py --help`，指定匹配的构建、模型、libgomp 和运行时。封包检查配置与程序哈希。不能直接复制未内嵌共享脚本的页面模板上板。

## SD 构建与启动

完整入口为 [board/cold_boot/README.md](../board/cold_boot/README.md)：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File board/cold_boot/Build-Delivery.ps1
```

该入口还依赖 AMD Bootgen、既有 Linux/PL 产物及模型；本仓库不附带大型启动二进制。相关构建步骤已有文件级检查证据，尚未完成无缓存新环境的从零整体复现。

已有交付包使用 `copy_to_sd`：将其内部全部内容复制到 SD 卡第一 FAT32 主分区根目录。校验与连接要求按冷启动 README 执行；默认页面为 `http://169.254.73.29:8080/`，指标原文为 `/api/latest`，最新相机预览为 `/api/frame.jpg`。预览仅保留RAM中的一帧，缺失或过期返回503。

启动参数来自 `config.yaml:cold_boot`，算法和部分启动配置会编入产物。改 YAML 后须重新导出、编译与封包，单独编辑卡上的 YAML 或 `boot.env` 不会完整生效。

【已验证】2026-11-03 修复包完成一次用户操作的物理断电 SD 启动，无 JTAG 或启动后 RAM 补丁；网页、递增指标帧及变化的640×480 JPEG均自动恢复，SD应用分区只读。对应文件哈希与日志见[本次验证](../metrics/evidence/2026-11-03_sd_preview_verification.json)。

【已验证】同日板端网页恢复2026-11-02拍摄页的紧凑布局，继续使用真实板端指标；六种桌面视口的一屏显示、13张可见指标卡与4:3实时画面通过DOM检查。见[浏览器检查](../metrics/evidence/2026-11-03_sd_dashboard_layout.json)和[SD文件校验](../metrics/evidence/2026-11-03_sd_dashboard_layout_install.log)。此项不代表心率或眨眼效果已通过人体测试。

【不确定】重复冷启动、长时稳定性、物理相机拔插恢复与测量准确性仍待相应验收；详见[验证](validation.md)与[限制](limitations.md)。
