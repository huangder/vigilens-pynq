# T8 OpenMV → 网页 实时画面链路：**已打通**（2026-09-26）

> **执行**：AI，通过 **COM10 串口直驱相机**（不经 OpenMV IDE）
> **结论**：**相机（MicroPython 模式）→ USB CDC → 帧协议 → PC → 网页旁路画面** 全链路跑通，
> **不需要 UVC 固件**（因此**不用降级固件、不冒变砖风险**，而且**脚本能力保留**）。
> **相关**：`docs/16_测试问题台账.md`（BUG-005 / BUG-017 / BUG-023）、`board/openmv/README.md`、`docs/10` §6（短期方案）

---

## 0. 判据（实测）

```
bursts=3  frames=240  posted=240  saved=3  crc_bad=1  elapsed=15.2s
payload: min=8642 max=8785 mean=8714.1 B   frame_id: 62 -> 304

GET /api/video_status
{ "ok": true, "has_video": true, "age_s": 0.305, "stale_after_s": 1.5, "bytes": 8712 }
```

- ✅ **`has_video: true`** —— 网页的旁路画面区拿到了真实画面；
- ✅ **240/240 帧成功投递**到 `POST /api/frame`（0 丢）；
- ✅ 每帧 **~8.7 KB**（QVGA JPEG，quality=80）；`crc_bad=1` 是文本日志里偶然出现的 `5A A5`，重同步即可，无害。

**相机侧原生速率**（`[stream]` 行，原样）：

```
[stream] jpeg=True usb=True QVGA roi=(0, 0, 640, 480)
[stream] fid=26   瞬时 25.7 fps   已发 225.4 KB   累计 1.0 s
[stream] fid=88   瞬时 30.2 fps   已发 762.3 KB   累计 3.1 s
[stream] fid=180  瞬时 30.1 fps   已发 1559.6 KB  累计 6.1 s
```
⇒ **QVGA JPEG @ ~30 fps、~270 KB/s**（USB CDC 12 Mb/s 完全吃得下）。

---

## 1. 这条链路长什么样

```
OpenMV Cam H7（MicroPython v5.0.0）
   sensor(GRAYSCALE/RGB565) → img.compress(quality=80) → JPEG blob
   vigilens_link.encode(TYPE_JPEG, fid, blob)   ← 12B 头 + payload + CRC-16
   pyb.USB_VCP().send(frame)                    ← USB CDC（虚拟串口）
        │
        ▼  COM10
PC：metrics\logs\_omv_stream_recv.ps1（.NET SerialPort，**不用 pyserial**）
   解析 magggic/头/CRC → 取 JPEG → POST /api/frame
        │
        ▼
backend/api.py（--no-mock）→ GET /video.mjpg（MJPEG）→ 网页画面区
```

**关键点：画面走的是"旁路通道"**（与契约帧完全无关）——
契约 §1 的帧仍然只有那 9 个顶层字段，这条链路**一个像素都没进契约帧** ✓。

---

## 2. 怎么复现（命令）

```powershell
# 0) 前提：关掉 OpenMV IDE（COM10 只能被一个程序占用）
cd D:\Desktop\AMD
. .\env.ps1

# 1) 起网页服务（B 线，--no-mock 很关键，否则会混入 mock 数据）
python backend/api.py --no-mock --host 127.0.0.1 --port 8031

# 2) 把协议实现与流脚本放进相机盘（**这是 openmv_stream.py 文档钦点的用法**）
Copy-Item board\openmv\vigilens_link.py 'E:\vigilens_link.py' -Force
Copy-Item board\openmv\openmv_stream.py 'E:\openmv_stream.py' -Force
# ⚠️ 拷完**必须让相机重新挂载**（软复位 Ctrl-D 或拔插），否则 MicroPython 看不到新文件

# 3) 起流 + 收帧投网页（两个 ps1 都 . 一下）
. .\metrics\logs\_omv_drive.ps1
. .\metrics\logs\_omv_stream_recv.ps1
#   启动相机侧流（把 MODE 改成 usb_jpeg）
. .\metrics\logs\_omv_start_stream.ps1      # 或照 §2 的 5 行内联脚本
Send-OMVFramesToApi -TotalSeconds 180 -BurstSeconds 1.0 -ApiBase http://127.0.0.1:8031
```

> ⚠️ **`/api/video_status` 的过期窗口只有 `1.5 s`**：一旦停止投递，网页就会判定"无画面"并**隐藏画面**（刻意设计，不显示冻结旧帧）。
> 所以演示/截图期间**必须让收帧器一直跑**。

---

## 3. 过程中修掉的三个**真 bug**（都在仓库代码里）

| # | 位置 | 问题 | 修法 |
|---|---|---|---|
| **A** | `board/openmv/vigilens_link.py`（4 处） | 用了 **`struct.Struct(...)`，MicroPython 的 `struct` 没有这个类** ⇒ 相机上一 import 就 `AttributeError`。**整条串口协议链路在相机上从来跑通过**（自检只跑 CPython，所以一直没暴露） | 改成模块级 `struct.pack/unpack` + `struct.calcsize`，格式串 `<HBBII` 不变 ⇒ 字节布局完全一致（`--selftest` 仍 **9/9**） |
| **B** | `board/openmv/openmv_stream.py:221-225` | JPEG 模式去设 **`sensor.set_pixformat(sensor.JPEG)`**，本固件直接抛 `RuntimeError: Sensor control failed.`；而**下面 248 行本来就用 `img.compress()` 出 JPEG** ⇒ 代码自相矛盾 | 永远设 `RGB565`，JPEG 交给 `img.compress()`（见 `docs/16` **BUG-017**） |
| **C** | `metrics/logs/_omv_stream_recv.ps1`（我的收帧器） | `[byte] -shl 8` 在 PowerShell 里**保留左操作数类型** ⇒ `[byte]0xB3 -shl 8` 截断成 `0x00`，CRC 永远对不上（表现为"0 帧 + 上百次 CRC 错"） | 先 `[int]` 转换再移位 |

> ✅ **A 和 B 都在仓库里**，已用 `vigilens_link.py --selftest`（9/9）与真机实测双重验证。

---

## 4. 环境坑清单（都是"与代码无关、但会耗掉半天"的那类）

| 现象 | 真因 | 处理 |
|---|---|---|
| `AttributeError: 'module' object has no attribute 'Struct'` | MicroPython 的 `struct` 只有函数，没有 `Struct` 类 | 用 `struct.pack/unpack` |
| `TypeError: can't create 'module' instances` | `type(sys)("名字")` 在 MicroPython 里不成立 | 别自己造模块对象；**把文件放进相机盘**让它正常 `import` |
| 发送多行脚本时，`>>>` 变成一个 `...` 之后所有行被吞 | 友好的 REPL 遇到**复合语句**（`class`/`def`/`if`/`try`）会进续行模式 | ① 大脚本用 **raw 模式**（`Ctrl-A`，整段一起编译）；② 或让发送端见到 `...` 就补一个空行 |
| 往 `E:\` 拷了文件，相机 `os.listdir('/flash')` 看不到 | **USB 大容量存储的写入，MicroPython 的 VFS 要重新挂载才可见** | 拷完**软复位（Ctrl-D）或拔插一次** |
| `Access to the port 'COM10' is denied` | **OpenMV IDE 独占串口** | 用串口驱动相机时，先在 IDE 里断开/关掉 IDE |
| 重刷固件后 REPL 又打不进字 | 出厂 **`/flash/main.py`（`while True` 的 LED 循环）被写回**，上电即占住 REPL | 改名 `main_off.py`（可通过 `E:\` 改，不必占串口）；见 `docs/16` BUG-016 |
| `Get-Content -Raw` 读 UTF-8 源码读出乱码 | 中文 Windows 的 Windows PowerShell 5.1 默认按 GBK 解码 | 用 `[IO.File]::ReadAllBytes` + `Encoding.UTF8.GetString` |
| `$p.ReadBufferSize = 1 << 20` 解析报错 | PowerShell 里 `<<` **不是运算符** | 用 `-shl` 或 `1MB` |
| `New-Object System.Net.Http.HttpClient` 失败 | Windows PowerShell 5.1 没自动加载该程序集 | 用 `System.Net.HttpWebRequest` |

---

## 5. 当前边界（**不要越界引用**）

| 能做到 | **不能**做 |
|---|---|
| 网页看到**相机实时画面**（旁路通道） | **指标拿不到**：相机只推 JPEG，A 线的 `run_pipeline.py` 认不了串口帧（`capture.py` 只认 合成/摄像头序号/视频文件）；要指标得**给 A 线加一个"串口 JPEG 帧源"** |
| 保留 MicroPython 脚本能力（两者**不互斥**） | **与 FPGA/PL 无关**：这条链路一个像素都没进 PL，不能当"上板验证" |
| 采集侧 ~30 fps / ~8.7 KB/帧 的真实数字（QVGA JPEG q80） | **不是契约 §0 口径**：640×480 RGB888 它装不下（`docs/16` 已多次实测） |

---

## 6. 诚实边界

- 本文件所有数字**都是本次真实运行的原样输出**（命令见 §2）；未做任何修改。
- **画面本身我没有看过、也不会看**（含人脸的图像不外传，`AGENTS.md` 铁律 4）—— 截图与"画面对不对"需**人类**确认。
- `metrics/logs/_omv_stream_recv.ps1` 是我的**临时工具**（在 `.gitignore` 覆盖的目录里），
  **不是**正式代码；若要长期使用，建议让它进 `metrics/scripts/` 并补自检。
