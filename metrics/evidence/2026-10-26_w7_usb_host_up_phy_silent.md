# W7 上板：USB 主控起来了，但 ULPI PHY 不应答（A3 现状）

> **2026-10-28 更正：本文的“已排除软件、只剩硬件”结论撤回。**
> 旧脚本把 GPIO mask 全置 1（禁止写入），误解 MIO bit9 并写入保留的 IO_Type，
> 还把输出锁存寄存器当成引脚输入；旧 ULPI 注释也把 bit31 错标为 RUN。
> 正确位定义为 WAKEUP=31、RUN=30；读回数据在 15:8，产品 ID 低字节地址是 2。
> 厂商 R11 原理图第 2/9 页确认 Bank501 与 USB3320 VDDIO 都是 1.8V，
> 当时生成的 USB MIO 配置却是 LVCMOS33。因此下文保留作历史现场，不能据此判板卡损坏。
> 当前修复与验证见 [2026-10-28 排障记录](2026-10-28_usb_camera_repair.md)。

- 日期：2026-10-26
- 板卡：Mizar-Z7020（`DRAM: ECC disabled 512 MiB`）
- 启动方式：JTAG 直启（`board/w7_buildroot/boot_after_powercycle.ps1` + `D:\w7\reload7.tcl`）
- 根文件系统：Buildroot initramfs（`uInitrd`，内含自述脚本 `board/w7_buildroot/init/S99vigi`）
- 串口：`COM11` @115200，**只读**（PC→PS 方向不通，见 docs/42 §11/§13）

## 1. 结论（一句）

【历史观察】当时 root hub 存在、旧探针访问 ULPI 超时。复位、引脚电气标准和探针均有软件缺陷，不能由此排除软件或判断 PHY 损坏。

## 2. 做对了什么（两个真 bug）

### 2.1 PHY 的 compatible 写错（Linux 侧从此静默失败）

- 原来：`usb_phy0: phy0@e0002000 { compatible = "ulpi-phy"; ... }`
- 问题：Buildroot 用的是**主线 6.6.158**，`drivers/` 下 grep `ulpi-phy` **0 命中**（那是 linux-xlnx 的私有驱动）
  ⇒ PHY 永远不注册 ⇒ chipidea 的 `usb_phy` 拿不到 ⇒ `ci_hdrc.0` **存在但不绑定**，**dmesg 一句报错都没有**。
- 改成主线写法（与 `arch/arm/boot/dts/xilinx/zynq-zed.dts` 完全一致）：

```dts
usb_phy0: phy0 {
    compatible = "usb-nop-xceiv";
    #phy-cells = <0>;
};
```

- 结果（真实日志）：

```
[1.609603] ci_hdrc ci_hdrc.0: EHCI Host Controller
[1.614538] ci_hdrc ci_hdrc.0: new USB bus registered, assigned bus number 1
[1.670524] ci_hdrc ci_hdrc.0: USB 2.0 started, EHCI 1.00
sysfs: 1-0:1.0  usb1
```

### 2.2 `ps7_init` 把 GPIO 时钟门了（写寄存器"没反应"的真因）

- `APER_CLK_CTRL (0xF800012C)` 实测 = `0x00200004`（GPIO 位 = 0）
- 写 `0x01FFCCCD` 后，`DIRM_1`、`OEN_1` 才写得进去（`0x00004000`）
- 与之前 UART0 那次是**同一个病**：时钟门了 ⇒ 读回 0 / 写不进

### 2.3 MIO46 的复用位（决定能不能软件控 RESETB）

- `MIO46 CR (0xF80007B8)` = `0x1201`，其中 **bit9（L3）= 1 ⇒ 该脚复用为 USB0 RESET 输出**，不是 GPIO
  ⇒ 只写 GPIO 的 `DIRM/OEN/DATA` 驱动不动 pad（读回恒 0，这正是前几轮"没反应"的原因）
- 把 CR 改成 `0x1001`（清 L3，L0 仍是 GPIO）后，`DATA_1` 读回 **`0x003CC000`**（bit14 = 1）
  ⇒ **RESETB 已由我们拉高**

## 3. 仍未解决：ULPI 视口永远超时

在 RESETB 拉高、USB 引脚已复用（`MIO28 CR=0x1604`、`MIO36 CR=0x1605`）的前提下：

```
ULPI viewport (0xE0002170) 读完 vendor-lo 后 = 0xC8000000   ← bit31(run) 一直是 1，永远不完成
ULPI product-lo                              = 0xC8010000
usb devices (sysfs)                          = 1-0:1.0 usb1 ← 只有 root hub，相机不枚举
```

与 U-Boot 早期报的 `ULPI` 超时是同一个物理现象。可能原因（按概率）：

1. **PHY 没有 REFCLK（24/26 MHz）**——ULPI 的 60 MHz 由 PHY 供给 PS（MIO36），PHY 没时钟就整个不响应；
2. PHY 没供电（相机无电源灯，无法用灯判断 VBUS）；
3. 板上 ULPI 信号并非接在 MIO28…39（与 PS7 配置不符）。

## 4. 这张证据不能证明什么

- 不能证明相机坏 / 相机不能工作（**根本没到 UVC 枚举那一步**）；
- 不能证明驱动有问题（`ci_hdrc` 已正常绑定并起 root hub）；
- 不能证明 RESETB 是唯一原因（拉高后依然超时）。

## 5. 复核命令（可重跑）

```powershell
. .\board\w7_buildroot\w7env.ps1
powershell -File board\w7_buildroot\boot_after_powercycle.ps1 -CaptureSeconds 145 -WaitSeconds 120 -Out D:\w7\cap.txt -Tcl D:/w7/reload7.tcl
```

串口日志里搜 `== VIGI: usb phy reset experiment` 与 `ULPI`。
