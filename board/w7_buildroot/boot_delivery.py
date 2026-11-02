"""Prepare an isolated SD boot candidate; never open devices or flash storage.

The PS sequences are translated from the exact TCL used by network_jtag_boot.
Build with build_boot_delivery.sh, then inspect manifest.json. Hardware acceptance
is deliberately false until a human performs an independent SD cold boot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct

HERE = Path(__file__).resolve().parent
STAGES = ("mio_init_data", "pll_init_data", "clock_init_data", "ddr_init_data", "peripherals_init_data", "post_config")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_zynq_header(boot: bytes) -> None:
    # tools/zynqimage.c stores the identifier 0x584c4e58 as LITTLE endian.
    if len(boot)<0x8c0:
        raise ValueError("short Zynq BootROM header")
    words=struct.unpack_from('<11I',boot,0x20)
    if words[0:2]!=(0xaa995566,0x584c4e58) or (sum(words)&0xffffffff)!=0xffffffff:
        raise ValueError("invalid Zynq BootROM header/checksum")
    offset,size,stored=words[4],words[5],words[8]
    if offset<0x8c0 or size==0 or stored<size or offset+stored>len(boot):
        raise ValueError("invalid Zynq BootROM image bounds")


def parse_sequences(text: str) -> dict[str, list[tuple[str, list[int]]]]:
    """Accept only the generated straight-line PS procedures; reject new syntax."""
    result = {}
    for version in ("1_0", "2_0", "3_0"):
        for stage in STAGES:
            name = f"ps7_{stage}_{version}"
            match = re.search(r"proc " + name + r" \{\} \{\s*\n(.*?)\n\}", text, re.S)
            if not match:
                raise ValueError(f"missing procedure: {name}")
            ops = []
            for line in match[1].splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens = line.split()
                op = tokens.pop(0)
                if op == "mwr" and tokens.pop(0) == "-force":
                    op = "write"
                count = {"write": 2, "mask_write": 3, "mask_poll": 2, "mask_delay": 2}.get(op)
                if count is None or len(tokens) != count or not all(re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", t) for t in tokens):
                    raise ValueError(f"unsupported command in {name}: {line}")
                ops.append((op, [int(t, 0) for t in tokens]))
            result[name] = ops
    if not re.search(r"set APU_FREQ\s+667000000\b", text):
        raise ValueError("review timer frequency: expected current validated APU_FREQ")
    return result


def generate_c(text: str) -> str:
    sequences = parse_sequences(text)
    code = ["/* Generated from validated JTAG TCL; do not edit. */", '#include <asm/io.h>', '#include <hang.h>', '#include <asm/arch/ps7_init_gpl.h>',
            "static void write(unsigned long a, unsigned long v) { writel(v, (void *)a); }",
            "static unsigned long rd(unsigned long a) { return readl((void *)a); }",
            "static void mask_write(unsigned long a, unsigned long m, unsigned long v) { write(a, (rd(a) & ~m) | (v & m)); }",
            "static void mask_poll(unsigned long a, unsigned long m) { unsigned long i; for(i=0;i<100000000;i++) if(rd(a)&m) return; hang(); }",
            "static void mask_delay(unsigned long a, unsigned long ms) { unsigned long ticks=ms*(667000000UL/2000); write(0xf8f00208,0); write(0xf8f00200,0); write(0xf8f00204,0); write(0xf8f00208,9); while(rd(a)<ticks) {} write(0xf8f00208,0); write(0xf8f00200,0); write(0xf8f00204,0); }",
            "static void checked(unsigned long a,unsigned long m,unsigned long v) { unsigned long n=(rd(a)&~m)|(v&m); write(a,n); if(rd(a)!=n) hang(); }"]
    for name, ops in sequences.items():
        code.append(f"static void {name}(void) {{")
        code.extend(f"  {op}({', '.join(hex(n)+'UL' for n in args)});" for op, args in ops)
        code.append("}")
    code.append((HERE / "boot_peripherals.c.inc").read_text(encoding="utf-8"))
    for fn, stages in (("ps7_init", STAGES[:-1]), ("ps7_post_config", STAGES[-1:])):
        code.append(f"int {fn}(void) {{ unsigned long v=rd(0xf8007080)>>28;")
        for i, version in enumerate(("1_0", "2_0", "3_0")):
            code.append((f"if(v=={i}) {{" if i < 2 else "{") if i == 0 else (f"else if(v=={i}) {{" if i < 2 else "else {"))
            code.extend(f"ps7_{s}_{version}();" for s in stages)
            code.append("}")
        if fn == "ps7_init":
            code.append("prepare_peripherals();")
        code.append("return PS7_INIT_SUCCESS; }")
    return "\n".join(code) + "\n"


def prepare(images: Path, bitstream: Path, out: Path) -> None:
    if out.exists():
        raise ValueError(f"refusing to overwrite existing directory: {out}")
    required = [images / f for f in ("zImage", "uInitrd", "system.dtb")]
    sources = required + [bitstream, HERE / "dt/ps7_init.tcl", HERE / "gem0_init.tcl", HERE / "usb_phy.tcl", HERE / "boot_peripherals.c.inc", HERE / "boot_sd_network.cmd", HERE / "boot_delivery.py", HERE / "build_boot_delivery.sh", HERE / "uboot_mizar_candidate.dts"]
    for path in sources:
        if not path.is_file():
            raise ValueError(f"missing input: {path}")
    generated = generate_c((HERE / "dt/ps7_init.tcl").read_text())
    out.mkdir(parents=True)
    (out / "fat").mkdir()
    for path in required:
        shutil.copyfile(path, out / "fat" / path.name)
    shutil.copyfile(bitstream, out / "fat/system.bit")
    shutil.copyfile(HERE / "boot_sd_network.cmd", out / "fat/boot.cmd")
    (out / "ps7_validated.c").write_text(generated, encoding="utf-8")
    manifest = {"hardware_sd_boot_verified": False, "status": "PREPARED_NOT_BUILT", "inputs": {str(p.resolve()): digest(p) for p in sources}, "artifacts": {}, "limitations": ["SD/MMC cold boot has not been exercised", "Current U-Boot control DT must be reviewed for SD0 and UART1", "PL/USB/GEM boot order must be verified without JTAG", "Candidate is not an accepted release"]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def verify(out: Path, seal: bool = False) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    for source, expected in manifest["inputs"].items():
        if digest(Path(source)) != expected:
            raise ValueError(f"input changed since prepare: {source}")
    artifacts = ["fat/BOOT.BIN", "fat/u-boot.img", "fat/boot.scr", "fat/system.bit", "fat/zImage", "fat/uInitrd", "fat/system.dtb", "ps7_validated.c"]
    for rel in artifacts:
        path = out / rel
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"missing or empty artifact: {rel}")
        if not seal and digest(path) != manifest["artifacts"].get(rel):
            raise ValueError(f"artifact hash mismatch: {rel}")
    boot = (out / "fat/BOOT.BIN").read_bytes()
    validate_zynq_header(boot)
    if (out / "fat/system.dtb").read_bytes()[:4] != bytes.fromhex("d00dfeed"):
        raise ValueError("invalid DTB magic")
    if seal:
        manifest["artifacts"] = {rel: digest(out / rel) for rel in artifacts}
        manifest["status"] = "OFFLINE_BUILT_SD_BOOT_UNVERIFIED"
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("OFFLINE_CANDIDATE_PASS; SD hardware boot remains UNVERIFIED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "seal", "verify"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--bitstream", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.images or not args.bitstream:
            parser.error("prepare requires --images and --bitstream")
        prepare(args.images.resolve(), args.bitstream.resolve(), args.out.resolve())
    else:
        verify(args.out.resolve(), args.action == "seal")


if __name__ == "__main__":
    main()
