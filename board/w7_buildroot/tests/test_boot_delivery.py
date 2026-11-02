"""Offline safety and sequence-preservation tests; no board or storage device."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
import struct

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("boot_delivery", HERE / "boot_delivery.py")
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)
TCL = Path("D:/Xilinx/2026.1/tps/win64/git-2.53.0/mingw64/bin/tclsh.exe")


class BootDeliveryTests(unittest.TestCase):
    def test_bootrom_little_endian_identity_checksum_and_bounds(self):
        header=bytearray(0x8c0+8)
        words=[0xaa995566,0x584c4e58,0,0,0x8c0,8,0,0,8,0]
        words.append((~sum(words))&0xffffffff)
        struct.pack_into('<11I',header,0x20,*words)
        delivery.validate_zynq_header(header)
        header[0x24:0x28]=bytes.fromhex('584c4e58')
        with self.assertRaises(ValueError):delivery.validate_zynq_header(header)
        struct.pack_into('<11I',header,0x20,*words)
        header[0x48]^=1
        with self.assertRaises(ValueError):delivery.validate_zynq_header(header)
        struct.pack_into('<11I',header,0x20,*words)
        with self.assertRaises(ValueError):delivery.validate_zynq_header(header[:-1])

    def test_all_ps_commands_match_independent_tcl_execution(self):
        source = HERE / "dt/ps7_init.tcl"
        seq = delivery.parse_sequences(source.read_text())
        script = f"source {{{source.as_posix()}}}\n"
        script += """
proc mwr {flag a v} { puts "write [expr {$a+0}] [expr {$v+0}]" }
proc mask_write {a m v} { puts "mask_write [expr {$a+0}] [expr {$m+0}] [expr {$v+0}]" }
proc mask_poll {a m} { puts "mask_poll [expr {$a+0}] [expr {$m+0}]" }
proc mask_delay {a m} { puts "mask_delay [expr {$a+0}] [expr {$m+0}]" }
"""
        for name in seq:
            script += f'puts "PROC {name}"\n{name}\n'
        run = subprocess.run([str(TCL)], input=script, text=True, capture_output=True, check=True)
        actual = {}
        for line in run.stdout.splitlines():
            tokens = line.split()
            if tokens[0] == "PROC":
                current = tokens[1]
                actual[current] = []
            else:
                actual[current].append((tokens[0], [int(n) for n in tokens[1:]]))
        self.assertEqual(actual, seq)
        generated = delivery.generate_c(source.read_text())
        for name, ops in actual.items():
            block = generated.split(f"static void {name}(void) {{\n")[1].split("\n}")[0]
            expected = "\n".join(f"  {op}({', '.join(hex(n)+'UL' for n in args)});" for op, args in ops)
            self.assertEqual(block, expected)

    def test_changed_source_syntax_fails_closed(self):
        text = (HERE / "dt/ps7_init.tcl").read_text()
        with self.assertRaises(ValueError):
            delivery.parse_sequences(text.replace("mask_write 0XF8000110", "unreviewed 0XF8000110", 1))
        with self.assertRaises(ValueError):
            delivery.parse_sequences(text.replace("set APU_FREQ  667000000", "set APU_FREQ  666666666"))

    def test_prepare_rejects_existing_output(self):
        with self.assertRaises(ValueError):
            delivery.prepare(HERE, HERE, HERE)

    def test_sd_script_loads_pl_and_exact_ram_profile(self):
        script = (HERE / "boot_sd_network.cmd").read_text()
        current = (HERE / "boot_direct_loop.cmd").read_text()
        bootargs = next(line for line in current.splitlines() if line.startswith("setenv bootargs"))
        self.assertIn(bootargs, script)
        self.assertIn("fpga loadb 0 0x01000000 ${filesize}", script)
        self.assertNotIn("saveenv", script)
        self.assertNotIn("mmc write", script)


if __name__ == "__main__":
    unittest.main()
