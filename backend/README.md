# PC 参考算法与服务

本目录保留 Python 参考实现、PC 演示和辅助功能。板端主链路由 `board/autonomy/` 的 C/C++ 实现，二者的部署位置须区分。

| 模块 | 职责 |
|---|---|
| `capture.py`、`board_source.py` | 文件、合成、UVC、板端帧源 |
| `face_landmark.py` | MediaPipe 检测与关键点；缺依赖时可退回 stub |
| `behavior_metrics.py`、`quality.py`、`decision.py` | 行为指标、质量门控和规则融合 |
| `vital.py`、`rppg_color.py`、`sampling.py`、`hr_filter.py` | rPPG、时间基准、颜色投影与滤波 |
| `contract.py`、`config.py` | 契约验证与根配置读取 |
| `publish.py`、`storage.py`、`hub.py` | 发布、存储与帧总线 |
| `api.py`、`websocket.py`、`mock.py` | HTTP/WS 服务与演示帧 |
| `age_*`、`fatigue_*` | 年龄与疲劳旁路，不增加契约顶层字段 |
| `run_pipeline.py` | 管线命令行入口 |
| `tests/` | 契约、算法、时间与集成回归 |

## 演示与测试

在仓库根执行：

```powershell
. .\env.ps1
python metrics/scripts/run_demo.py
python metrics/scripts/run_demo.py --list
python metrics/scripts/run_demo.py --source 0
python -m pytest -q
python metrics/scripts/check_all.py
```

合成输入和 `landmark_source=stub` 仅验证程序链路，不能作为测量结果。指标 JSON 与预览画面走独立通道，九字段契约不允许夹带图片。

阈值来源为根 `config.yaml`；修改需有真实依据，不能靠放宽门槛制造出数。标定流程见 [P4_CALIBRATION_GUIDE.md](P4_CALIBRATION_GUIDE.md) 与 [rPPG诊断](../docs/calibration.md)。

当前交付边界、源码依赖及板端测试见 [交付入口](../docs/usage.md)，历史开发步骤已移出主目录，恢复方法见 [清理清单](../docs/README.md)。
