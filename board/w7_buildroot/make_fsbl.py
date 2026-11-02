r"""make_fsbl.py —— 用 Vitis 2026.1 的 Python CLI 生成 fsbl.elf（Windows）

为什么要有这个文件：FSBL 是 `boot.bif` 的第一段，没有它 bootgen 出不了 BOOT.BIN。
Vitis 的 GUI 能点出来，但我们要可复现，所以把同样的动作写成脚本。

【已验证】的边界（2026-10-25，本机）
  - `vitis -s make_fsbl.py` 这条路本身能跑（Python 3.13，`vitis` 模块暴露 create_client）。
  - 它**曾经失败**，报 `Error in generating Processor List.`；追到根因是 XSA 里
    `system.hwh` 把 PS7 标成 `IPTYPE="PERIPHERAL"`（应为 `PROCESSOR`）见 docs/41 §18.5。
  - 另有一条**纯环境**问题：`%APPDATA%`/`%LOCALAPPDATA%`/`%HOME%` 不可写时，Vivado/Vitis
    会抛 `Could not open 'C' for writing` 与 `tclapp::load_apps` 失败。**普通终端没有这个问题**。

用法（普通 PowerShell，不要用受限沙箱）

    # 0) 先有 XSA（正斜杠路径；导出见 export_xsa.tcl）
    # 1) 一条命令：
    D:/Xilinx/2026.1/Vitis/bin/vitis.bat -s board/w7_buildroot/make_fsbl.py

可选环境变量：
    VIGILENS_XSA   XSA 绝对路径（默认找 %TEMP%\xsa_out\system.xsa）
    VIGILENS_WS    Vitis workspace（默认 %TEMP%\w7_fsbl_ws，**不写进仓库**）

产物：<ws>\fsbl\Debug\fsbl.elf   （把路径填进 boot.bif 的第一个 file 字段）
"""

import os
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pick_xsa() -> str:
    cand = [os.environ.get("VIGILENS_XSA", "")]
    cand += [
        os.path.join(os.environ.get("TEMP", ""), "xsa_out", "system.xsa"),
        os.path.join(REPO, "board", "bitstream", "system.xsa"),
        os.path.join(REPO, "board", "bitstream", "system_emb.xsa"),
    ]
    for c in cand:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise SystemExit(
        "找不到 XSA。先跑 board/w7_buildroot/export_xsa.tcl，"
        "或用 VIGILENS_XSA=<绝对路径> 指一个。"
    )


def force_utf8_console() -> None:
    """把控制台代码页切到 65001（UTF-8），必须在 create_client() 之前调用。

    为什么需要：Vitis CLI 的 _server.py:136-137 是这样读服务端启动日志的 ——

        for line in iter(self.cp.stdout.readline, ''):
            line = str(line, 'utf-8')

    服务端（`Vitis/bin/vitis-server.bat`，Java 启动器）把启动信息按**控制台代码页**写出来，
    中文 Windows 上就是 GBK（936）。只要它吐一个非 ASCII 字节（`0xd5` 常是 GBK 的引导字节），
    上面那行 `str(line, 'utf-8')` 就抛
        `'utf-8' codec can't decode byte 0xd5 in position 4: invalid continuation byte`
    —— 而且被 `vitis/__init__.py:46-48` 包成 `Vitis Create Client failed` 抛给我们，
    看起来像"建 client 失败"，实际是**编解码问题**。

    子进程继承控制台代码页，所以这里先切 65001，服务端就用 UTF-8 输出，str(..., 'utf-8') 才对得上。
    【推测】依据是 Windows 控制台继承规则 + 报错位置；本机沙箱跑不出这个错（沙箱 APPDATA 被重定向），
    所以这条只能在你机器上验证。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
        print("[make_fsbl] console code page -> 65001 (UTF-8)", flush=True)
    except Exception as e:  # 切不动就算了，后面会给出人工修法
        print("[make_fsbl] WARN: 切代码页失败：", e, flush=True)


def main() -> int:
    force_utf8_console()
    import vitis

    xsa = pick_xsa()
    # 工作区默认放到 TEMP：既不会污染仓库，也不受"仓库目录只读"这类策略影响。
    # 想留证据就自己设 VIGILENS_WS 指到某处。
    ws = os.environ.get("VIGILENS_WS") or os.path.join(
        os.environ.get("TEMP", tempfile.gettempdir()), "w7_fsbl_ws"
    )
    os.makedirs(ws, exist_ok=True)
    print("[make_fsbl] XSA  =", xsa, os.path.getsize(xsa), "B", flush=True)
    print("[make_fsbl] WS   =", ws, flush=True)

    try:
        port = os.environ.get("VIGILENS_VITIS_PORT", "").strip()
        if port:
            # 【绕过通病用的后门】先手动起一个 Vitis Server，再让 client 直连它的端口。
            # 这样 create_client() 走 verify()==True 的分支，**不会**去执行
            # _server.py:136-137 那行 str(line,'utf-8')，也就不会撞代码页解码错。
            print(f"[make_fsbl] 直连已运行的 Vitis Server：port={port}", flush=True)
            client = vitis.create_client(port=int(port))
        else:
            client = vitis.create_client()
    except Exception as e:
        msg = str(e)
        print("[make_fsbl] create_client 失败：", msg, flush=True)
        if "codec" in msg or "decode" in msg:
            print(
                "[make_fsbl] 这是控制台代码页问题，不是 XSA 问题（见 Vitis/cli/vitis/_server.py:136-137）。\n"
                "            已尝试自动切 65001；若仍失败，任选一条在**运行 vitis 的那个窗口**里先执行：\n"
                "              chcp 65001\n"
                "              set JAVA_TOOL_OPTIONS=-Duser.language=en -Duser.country=US\n"
                "            再重跑本脚本；都无效就改用 Vitis GUI 建 FSBL（10 分钟，见 docs/41 §18.4）。",
                flush=True,
            )
            return 7
        traceback.print_exc()
        return 3
    client.set_workspace(path=ws)

    # 1) 先问 Vitis：它认不认这块板子的处理器？认不出来就别往下走。
    try:
        os_list = client.get_processor_os_list(xsa=xsa)
        print("[make_fsbl] OS list:", os_list, flush=True)
    except Exception:
        print("[make_fsbl] 致命：XSA 里认不出处理器（docs/41 §18.5 的 IPTYPE 问题）", flush=True)
        traceback.print_exc()
        vitis.dispose()
        return 2

    plat_name = "mizar_ps7"
    domain = "zynq_fsbl_bsp"  # FSBL 模板必须建在平台自带的 Boot domain 上
    try:
        plat = client.create_platform_component(
            name=plat_name,
            hw_design=xsa,
            os="standalone",
            cpu="ps7_cortexa9_0",
            domain_name=domain,
        )
        print("[make_fsbl] platform:", plat, flush=True)
        # zynq_fsbl 模板要求 BSP 带 xilffs(读 SD 分区) 与 xilrsa(镜像校验)；CLI 默认不加。
        try:
            dom_obj = plat.get_domain(name=domain)
            for lib in ("xilffs", "xilrsa"):
                dom_obj.set_lib(lib)
                print(f"[make_fsbl] BSP lib added: {lib}", flush=True)
            plat.build()
            print("[make_fsbl] platform rebuilt with libs", flush=True)
        except Exception:
            print("[make_fsbl] 加 BSP 库失败（继续试建应用）", flush=True)
            traceback.print_exc()
    except Exception:
        print("[make_fsbl] 建 platform 失败（把全文贴出来，别只看最后一行）", flush=True)
        traceback.print_exc()
        vitis.dispose()
        return 3

    # Vitis 2026.1 的 create_app_component(platform=...) 按**当前目录**解析平台名，
    # 不跟随 set_workspace；不切目录就会报 "Platform <cwd>\mizar_ps7 does not exist"。
    os.chdir(ws)

    # 2) 找 FSBL 模板名（不同版本用 'Zynq FSBL' 或 'fsbl'）
    tpl = "Zynq FSBL"
    try:
        names = client.get_templates(type="EMBD_APP") or []
        for n in names:
            s = str(n)
            if "fsbl" in s.lower():
                tpl = s
                break
        print("[make_fsbl] templates:", names, flush=True)
    except Exception:
        print("[make_fsbl] 取模板列表失败，按默认 'Zynq FSBL' 试", flush=True)

    app_name = "fsbl"
    # 2026.1 的 server 对平台名解析不稳定（报 not in repository）；直接给导出的 .xpfm 路径。
    xpfm = os.path.join(ws, plat_name, "export", plat_name, plat_name + ".xpfm")
    platform_arg = xpfm if os.path.isfile(xpfm) else plat_name
    try:
        app = client.create_app_component(
            name=app_name, platform=platform_arg, domain=domain, template=tpl
        )
        print("[make_fsbl] app:", app, flush=True)
    except Exception:
        print("[make_fsbl] 建 FSBL 应用失败", flush=True)
        traceback.print_exc()
        vitis.dispose()
        return 4

    # 3) 编译
    try:
        app.build()
    except Exception:
        print("[make_fsbl] 编译失败", flush=True)
        traceback.print_exc()
        vitis.dispose()
        return 5

    elf_candidates = []
    for root, _dirs, files in os.walk(os.path.join(ws, app_name)):
        for f in files:
            if f.lower() == "fsbl.elf":
                elf_candidates.append(os.path.join(root, f))
    if not elf_candidates:
        print("[make_fsbl] 编译没报错，但没找到 fsbl.elf —— 不要当成成功", flush=True)
        vitis.dispose()
        return 6

    for p in elf_candidates:
        print("[make_fsbl] fsbl.elf =", p, os.path.getsize(p), "B", flush=True)
    print("[make_fsbl] 下一步：把这个路径填进 boot.bif 的第一个 file 字段，然后", flush=True)
    print("             bootgen -image boot.bif -arch zynq -o BOOT.BIN -w on", flush=True)
    vitis.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
