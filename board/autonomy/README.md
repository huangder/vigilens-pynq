# 板端计算与视觉运行时

当前 SD 交付候选使用 `ert_temporal`：相机/PL → 检测与 ERT 关键点/跟踪 → PnP/行为/质量/心率 → 契约 → HTTP。当前状态见 [交付入口](../../docs/usage.md)、[心率显示](../../docs/architecture.md) 和 [SD交付](../../docs/usage.md)。

## 核心文件

| 文件 | 职责 |
|---|---|
| `autonomy_engine.c`、`measure.c` | 同帧计算与状态集成 |
| `behavior.c`、`quality.c`、`decision.c` | 行为、质量和规则判定 |
| `vital.c`、`hr_filter.h` | rPPG 与渐进滤波；无效时拒报 |
| `pose_pnp.c`、`face68_3d.*` | 姿态解算及参考几何 |
| `face_detector.cpp`、`ert_runtime.cpp` | 检测与当前 ERT 运行时 |
| `vision_runtime.cpp`、`tracked_runtime.cpp`、`landmark_runner.cpp` | 保留的其他运行时和模型路线 |
| `point_tracker.cpp`、`track_*.hpp` | 跟踪状态与恢复 |
| `autonomy_camera.c`、`camera_local.c` | 相机与计算模块连接 |
| `contract_json.c`、`metrics_http.c` | 九字段序列化与只读 HTTP |
| `display.html` | 页面模板；打包时内嵌共享心率显示脚本 |
| `export_*config.py` | 从根配置生成 C 参数与来源哈希 |
| `build_native_vision.sh` | 本地/ARM 编译入口 |
| `package_native_vision.py` | 应用、模型、页面与清单封装 |
| `tests/`、`CMakeLists.txt` | Python、C/C++、生命周期与边界回归 |

## 构建依赖与入口

构建脚本使用已有本地依赖，不自动安装或下载。WSL 内需要交叉编译器、Buildroot、NCNN/MNN；ERT 模式还需 dlib 和模型。`VG_DEPS`、`VG_DLIB`、`VG_CONFIG` 可覆盖脚本中的本机默认路径；完整参数以脚本为准。

```sh
bash board/autonomy/build_native_vision.sh arm .tmp/autonomy/rebuild ert_temporal
```

该命令是重建入口，本轮整理未重跑 ARM 编译。仅解压源码 RAR 不会获得模型或第三方依赖。

完整 SD 交付使用 `board/cold_boot/Build-Delivery.ps1`。单独打包应用时运行 `package_native_vision.py --help`，显式指定匹配的构建、模型、libgomp 与运行时；打包器会核对配置和程序哈希。不能直接复制未内嵌脚本的 `display.html` 模板上板。

## 验证边界

`python -m pytest board/autonomy/tests -q` 检查工具与参考实现；CMake/CTest、ARM 执行与相机实测是不同层次。默认根 pytest 不会自动收集本目录。

真实证据保留在 `metrics/evidence/`。历史单模型性能、PC 对拍和有限帧数运行不能代替最新持续版冷启动验收；RR 尚未实现，当前 HR 拒报仍输出 null，历史缓存只用于显示。

探针和候选代码保留用于构建、测试及回归；其存在不代表全部已部署。已删除的阶段说明从 [本地备份](../../docs/README.md) 追溯。
