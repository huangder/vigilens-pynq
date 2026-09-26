# 相机内部 FAT 的备份（2026-09-26，刷 uvc.bin 之前）

> **为什么有这份东西**：刷 `uvc.bin` 会**擦除相机内部 FAT 文件系统**（相机盘上的文件会没）。
> 这里是刷机前从相机盘（Windows 盘符 **`E:`**，卷标 **`OPENMV`**，总容量 **0.11 MB**）**逐字节复制**下来的内容。
> **备份方式**：直接文件复制（相机把内部 FAT 挂成 USB 可移动盘）→ **只读相机，未做任何写操作**。
> **校验**：每个文件都与相机盘上的原件做过 `Get-FileHash` 比对，**全部一致**。

## 文件清单

| 文件 | 大小 | sha256 |
|---|---|---|
| `.openmv_disk` | 0 B | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `main_off.py` | 218 B | `55308872362c67e218d9cdbf09823aae5741fd80053fa405d25a807b520c27ce` |
| `README.txt` | 351 B | `21d8448030d736952b64ffdef07fe49b2f6d590e870b4754d220c72773fbf09e` |
| `vigilens_report.json` | 1235 B | `4fa8886e16787b207aec351eed9046e3a2f27d58997457e2f7754f9c89f6fede` |

## 每个文件是什么

| 文件 | 说明 |
|---|---|
| `README.txt` | OpenMV 出厂说明（属于固件自带） |
| `.openmv_disk` | OpenMV 的盘标识文件（0 字节） |
| `vigilens_report.json` | `openmv_capture_test.py` 写的报告。⚠️ **这一份是截断的**（见下） |
| `main_off.py` | **原厂 `main.py`**（一个 `while True:` 的蓝色 LED 心跳循环）。我们把它**改名为 `main_off.py`** 以阻止上电时占住 REPL —— 详见 `docs/16` 的 **BUG-016** |

## ⚠️ 两个必须知道的点

1. **`vigilens_report.json` 这一份是截断的（1235 B，内容不完整）**
   - 用 `ast.literal_eval` 解析会报 `'{' was never closed`；
   - **完整版**在 `fpga/report/logs/2026-09-26_openmv_vigilens_report_recovered.json`（2955 B，从串口回收）；
   - ⇒ 这本身是一个问题，记在 `docs/16` 的 **BUG-019**（`write_report` 的可靠性）。
2. **文件名叫 `.json` 却不是 JSON**：它是 Python repr（单引号），用 `json.load()` 会失败 —— 同属 **BUG-019**。

## 怎么还原（需要时）

刷回 MicroPython 固件后（`metrics/logs/_firmware_openmv4_rollback.dfu`），把文件复制回相机盘即可：

```powershell
# 相机盘通常会重新挂成 E:（卷标 OPENMV）
Copy-Item board\openmv\flash_backup_20260926\main_off.py E:\main.py -Force   # ← 恢复"原厂行为"
Copy-Item board\openmv\flash_backup_20260926\README.txt E:\README.txt -Force
```

⚠️ **要不要恢复 `main.py` 自己想清楚**：恢复后**每次上电它都会跑 LED 死循环并占住 REPL**（BUG-016 的原始症状）。
建议**保持不恢复**（即保持 `main_off.py`），需要时再临时改回。

> 📌 未备份 `System Volume Information`：那是 Windows 在这个 FAT 上建的元数据目录，不是相机的内容。