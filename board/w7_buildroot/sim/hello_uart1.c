/*
 * hello_uart1.c —— QEMU 上的 UART1 寄存器级冒烟测试（不依赖 Linux、不依赖 FSBL）
 *
 * 目的（它到底证明了什么）：
 *   1. 我们**知道 UART1 在 0xE0001000**，且知道 Cadence UART 的初始化顺序
 *      （CR 置 TXRST|RXRST → 等自清 → 设 MR → 设波特率分频 → 使能 TX|RX）；
 *   2. QEMU 的 xilinx-zynq-a9 模型能跑起我们的 ARM 代码，串口输出能到 stdio。
 *
 * 它**证明不了**：板上 CH340 到底接在 MIO48/49 还是 MIO14/15（QEMU 不建模 MIO 路由）、
 * DDR 时序、USB ULPI —— 那些只有上板能验。别把这里的 PASS 当成"串口一定通"。
 *
 * 寄存器定义来源（不是猜的）：Linux v6.6 `drivers/tty/serial/xilinx_uartps.c`
 *   CDNS_UART_CR 0x00 / MR 0x04 / BAUDGEN 0x18 / SR 0x2C / FIFO 0x30 / BAUDDIV 0x34
 *   CDNS_UART_CR_TX_EN 0x10 / RX_EN 0x04 / TXRST 0x02 / RXRST 0x01 / TX_DIS 0x20 / RX_DIS 0x08
 *   CDNS_UART_MR_PARITY_NONE 0x20 / CHARLEN_8 0x00 / STOPMODE_1 0x00
 *   CDNS_UART_SR_TXFULL 0x10 / TXEMPTY 0x08
 *
 * 波特率：baud = clk / (CD * (BDIV + 1))，Zynq 的 uart_clk 取 100 MHz 时
 *   115200 ≈ 100e6 / (124 * 7) = 115207（误差 0.006%，远小于 UART 允许的 2~3%）
 *   即 BAUDGEN(CD) = 124，BAUDDIV = 6。QEMU 不校验波特率，真板才需要它准。
 */

#include <stdint.h>

#define UART1_BASE 0xE0001000u

#define UART_CR      0x00u
#define UART_MR      0x04u
#define UART_BAUDGEN 0x18u
#define UART_SR      0x2Cu
#define UART_FIFO    0x30u
#define UART_BAUDDIV 0x34u

#define UART_CR_TX_EN 0x10u
#define UART_CR_RX_EN 0x04u
#define UART_CR_TXRST 0x02u
#define UART_CR_RXRST 0x01u

#define UART_MR_PARITY_NONE 0x20u
#define UART_MR_CHARLEN_8   0x00u
#define UART_MR_STOPMODE_1  0x00u

#define UART_SR_TXFULL 0x10u
#define UART_SR_TXEMPTY 0x08u

static inline void wr(uint32_t off, uint32_t v) { *(volatile uint32_t *)(UART1_BASE + off) = v; }
static inline uint32_t rd(uint32_t off)        { return *(volatile uint32_t *)(UART1_BASE + off); }

static void uart1_init(void)
{
    /*
     * 1) 复位收发逻辑。**等待上界是有意的**：
     *    QEMU 的 cadence-uart 模型不一定实现 TXRST/RXRST 自清；
     *    真板会自清（几拍）。无界等待会在这里挂死，而现象是"串口一个字都没有"、
     *    极难判断是程序没跑还是 UART 没初始化 —— 所以只等有限次，超时就往下走。
     */
    unsigned int spin = 0;
    wr(UART_CR, UART_CR_TXRST | UART_CR_RXRST);
    while ((rd(UART_CR) & (UART_CR_TXRST | UART_CR_RXRST)) && spin++ < 1000000u) {
        /* 有限等待 */
    }

    /* 2) 8N1、正常模式 */
    wr(UART_MR, UART_MR_CHARLEN_8 | UART_MR_STOPMODE_1 | UART_MR_PARITY_NONE);

    /* 3) 115200 @ 100 MHz：CD=124, BDIV=6 */
    wr(UART_BAUDGEN, 124u);
    wr(UART_BAUDDIV, 6u);

    /* 4) 使能收发 */
    wr(UART_CR, UART_CR_TX_EN | UART_CR_RX_EN);
}

static void uart1_putc(char c)
{
    while (rd(UART_SR) & UART_SR_TXFULL) {
        /* 等 FIFO 有空位 */
    }
    wr(UART_FIFO, (uint32_t)(unsigned char)c);
}

static void uart1_puts(const char *s)
{
    while (*s) { uart1_putc(*s++); }
}

static void uart1_flush(void)
{
    while (!(rd(UART_SR) & UART_SR_TXEMPTY)) {
        /* 等最后一字节真的发出去 */
    }
}

int main(void)
{
    uart1_init();

    uart1_puts("\r\n[sim] VigiLens W7 UART1 smoke test\r\n");
    uart1_puts("[sim] base=0xE0001000 (Cadence UART, 8N1, 115200 @ 100MHz)\r\n");

    /* 读回刚写进去的分频值：这一步在真板上也能用，能区分"寄存器能读"与"时钟没开" */
    uart1_puts("[sim] BAUDGEN=");
    {
        uint32_t cd = rd(UART_BAUDGEN);
        char b[4];
        int i = 0;
        if (cd == 0) { b[i++] = '0'; }
        while (cd) { b[i++] = (char)('0' + (cd % 10)); cd /= 10; }
        while (i) { uart1_putc(b[--i]); }
    }
    uart1_puts(" BAUDDIV=");
    uart1_putc((char)('0' + (rd(UART_BAUDDIV) % 10)));
    uart1_puts("\r\n");

    uart1_puts("[sim] UART1_SMOKE_OK\r\n");
    uart1_flush();
    return 0;
}
