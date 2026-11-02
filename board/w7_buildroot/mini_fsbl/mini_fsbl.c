/*
 * mini_fsbl.c —— 最小一级引导（自有实现，**不是** Xilinx zynq_fsbl）
 *
 * 它做三件事，顺序不能换：
 *   1) ps7_init()      —— 由 Vivado/Vitis 从硬件描述生成（`ps7_init.c`，含 DDR/时钟/MIO 序列）
 *   2) 初始化并往 UART1 打一行字 —— 这是唯一不需要示波器的"我还活着"证据
 *   3) 跳到 U-Boot 的入口（本仓库的 u-boot.elf 链接在 0x04000000，见 UBOOT_ENTRY）
 *
 * 能证明什么：PS7 初始化序列能跑完、UART 的 MIO 分配是对的（串口能出字）、
 *             跳转前的状态正常。
 * 不能证明什么：Linux 能不能起（那要 zImage + dtb）、UVC 能不能用、PL 数据通路对不对。
 *
 * 【为什么不用 Xilinx 的 FSBL】Vitis 2026.1 的 headless 建 platform 在报
 *   `Error in generating Processor List.`（docs/41 §18.5）；而这份代码只依赖
 *   `ps7_init.c/h` + `xil_io.h`，可以用 arm-none-eabi-gcc 直接编出来。
 *   它是"能上板的最小验证物"，不是要替代正式 FSBL：正式 FSBL 还要管 SD 分区解析、
 *   镜像校验、QSPI/NAND 备份启动等，见 UG821。
 *
 * 寄存器口径（Zynq-7000 TRM UG585 第 19 章 Cadence UART）：
 *   CR=0x00 MR=0x04 BAUDGEN=0x18 BAUDDIV=0x34 SR=0x2C FIFO=0x30
 *   115200 @ 100 MHz 参考时钟 ⇒ CD=124（0x7C）、BDIV=6
 */

#include "ps7_init.h"     /* ps7_init() / ps7_post_config() / PS7_INIT_* */
#include "xil_io.h"       /* Xil_Out32 / Xil_In32（standalone 的 always_inline） */

#define UART1_BASE   0xE0001000UL   /* PS7 UART1；与 build_bd.tcl 的 UART1 一致 */
#define UBOOT_ENTRY  0x04000000UL   /* 必须与 u-boot.elf 的链接地址一致；本仓库实测 entry=0x04000000
                                     * （`arm-none-eabi-readelf -h u-boot.elf` → `Entry point address: 0x4000000`）。
                                     * ⚠️ 两种用法不同：
                                     *   [JTAG 直启] 先 `dow u-boot.elf` 把它放到 0x04000000，再跑本 FSBL → 跳转有效
                                     *   [SD 启动]   BootROM 只把 **本 FSBL** 装进 OCM，u-boot 分区要靠 FSBL 自己
                                     *               读镜像头表+从卡里搬运（本文件**没实现**）⇒ 这条路目前走不通，
                                     *               要等 SPL 方案或补分区搬运 */

#define UART_CR      0x00
#define UART_MR      0x04
#define UART_BAUDGEN 0x18
#define UART_SR      0x2C
#define UART_FIFO    0x30
#define UART_BAUDDIV 0x34

#define CR_RXRST     0x00000001
#define CR_TXRST     0x00000002
#define CR_RX_EN     0x00000004
#define CR_RX_DIS    0x00000008
#define CR_TX_EN     0x00000010
#define CR_TX_DIS    0x00000020
#define CR_RST_TO    0x00000040
#define SR_TXEMPTY   0x00000008
#define SR_TXFULL    0x00000010

static void uart1_init(void)
{
    /* 先复位收发通道，再设 8N1、分频，最后使能。顺序与 UG585 19.5 一致 */
    Xil_Out32(UART1_BASE + UART_CR, CR_TX_DIS | CR_RX_DIS);
    Xil_Out32(UART1_BASE + UART_CR, CR_TXRST | CR_RXRST | CR_RST_TO);
    /* 等 TX/RX 复位自清（有上界，防止配置没生效时死等） */
    for (volatile int i = 0; i < 1000000; i++) {
        if ((Xil_In32(UART1_BASE + UART_CR) & (CR_TXRST | CR_RXRST)) == 0U) {
            break;
        }
    }
    Xil_Out32(UART1_BASE + UART_MR, 0x00000020);   /* 无校验、1 停止位、8 数据位 */
    Xil_Out32(UART1_BASE + UART_BAUDGEN, 124);     /* 100 MHz / (124 * (6+1)) ≈ 115200 */
    Xil_Out32(UART1_BASE + UART_BAUDDIV, 6);
    Xil_Out32(UART1_BASE + UART_CR, CR_TX_EN | CR_RX_EN);
}

static void uart1_putc(char c)
{
    /* 有界等待：FIFO 满就自旋，但绝不无限等 */
    for (volatile int i = 0; i < 2000000; i++) {
        if ((Xil_In32(UART1_BASE + UART_SR) & SR_TXFULL) == 0U) {
            break;
        }
    }
    Xil_Out32(UART1_BASE + UART_FIFO, (unsigned int)(unsigned char)c);
}

static void uart1_puts(const char *s)
{
    while (*s != '\0') {
        if (*s == '\n') {
            uart1_putc('\r');
        }
        uart1_putc(*s++);
    }
}

static void uart1_puthex(unsigned int v)
{
    static const char hex[] = "0123456789abcdef";
    uart1_puts("0x");
    for (int shift = 28; shift >= 0; shift -= 4) {
        uart1_putc(hex[(v >> shift) & 0xFU]);
    }
}

int main(void)
{
    int rc;

    rc = ps7_init();                 /* DDR + PLL + MIO + 外设初始化；返回 0 才算成功 */
    uart1_init();                    /* 再自己兜一次底：ps7_init 没开 UART 也能出字 */

    uart1_puts("\n");
    uart1_puts("[mini_fsbl] VigiLens W7 first stage\n");
    uart1_puts("[mini_fsbl] ps7_init rc = ");
    uart1_puthex((unsigned int)rc);
    uart1_puts(rc == PS7_INIT_SUCCESS ? "  (PS7_INIT_SUCCESS)\n"
                                      : "  (NOT success -- 见 ps7_init.h 的错误码)\n");

    ps7_post_config();               /* 把 PLL/时钟再锁一次（DDR 训练后的既定收尾） */
    uart1_puts("[mini_fsbl] ps7_post_config done\n");
    uart1_puts("[mini_fsbl] jumping to U-Boot @ ");
    uart1_puthex((unsigned int)UBOOT_ENTRY);
    uart1_puts("\n");

    /* 跳到 U-Boot。没放 U-Boot 分区时这里会跑飞/挂死 —— 串口上已经能看到上面几行，
     * 所以"板子起来了"这条结论不受影响。 */
    ((void (*)(void))UBOOT_ENTRY)();

    /* 不会到这里 */
    for (;;) {
    }
}
