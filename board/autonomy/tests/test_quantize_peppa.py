"""Guarded quantization workflow tests; tiny fake files/tools, no real quantization."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("quantize_peppa",ROOT/"board/autonomy/quantize_peppa.py")
workflow=importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
PARAM="""7767517
4 4
Input input0 0 1 data
Convolution first 1 1 data x 0=2 6=4
ConvolutionDepthWise depth 1 1 x y 0=2 6=4 7=2
InnerProduct last 1 1 y z 0=2 6=4
"""
TABLE="""first_param_0 1 inf
depth_param_0 2 3
last_param_0 4 5
first 6
depth 7
last 8
output1 9
"""


class QuantizePeppa(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="vg_quantize_")
        self.root=Path(self.temp.name)
        self.tools=self.root/"tools"
        self.tools.mkdir()
        for name in ("ncnn2table","ncnn2int8"):(self.tools/name).write_bytes(b"fake executable marker")
        self.param,self.weights=self.root/"float.param",self.root/"float.bin"
        self.param.write_text(PARAM)
        self.weights.write_bytes(b"fake weights marker")
        self.calib,self.output=self.root/"calib",self.root/"output"
        self.calib.mkdir()
        samples=[]
        for frame,split in ((0,"calibration"),(3,"holdout")):
            (self.calib/split).mkdir()
            file=self.calib/split/f"frame_{frame:06d}.png"
            file.write_bytes(f"fake PNG marker {frame}".encode())
            samples.append({"frame_id":frame,"split":split,"png":file.relative_to(self.calib).as_posix(),"png_sha256":workflow.sha256(file)})
        self.manifest={"kind":"peppa_local_calibration_preparation","split_frame":3,"samples":samples,
            "source_sha256":"test-only source digest marker","split_independence":"same-video temporal split",
            "ncnn2table_preprocessing_required":{"pixel":"BGR","mean":[127]*3,"norm":[1/127]*3,"shape_whc":[160,160,3]}}
        self.write_manifest()
        (self.calib/"calibration.relative.list").write_text(samples[0]["png"]+"\n")
        self.calls=[]

    def tearDown(self):self.temp.cleanup()
    def write_manifest(self):
        (self.calib/"manifest.json").write_text(json.dumps(self.manifest))

    def fake_run(self,args,**kwargs):
        self.calls.append((args,kwargs))
        self.assertEqual(Path(kwargs["cwd"]),self.calib)
        self.assertFalse(kwargs["check"])
        if Path(args[0]).name=="ncnn2table":
            self.assertEqual(args[3],"calibration.relative.list")
            self.assertIn("norm=[0.007874016,0.007874016,0.007874016]",args)
            self.assertIn("pixel=BGR",args)
            self.assertIn("thread=2",args)
            Path(args[4]).write_text(TABLE)
        else:
            table=Path(args[5]).read_text()
            names={line.split()[0] for line in table.splitlines()}
            lines=PARAM.splitlines()
            for k,line in enumerate(lines):
                fields=line.split()
                if len(fields)>=4 and fields[0] in workflow.QUANTIZABLE and fields[1] in names:
                    lines[k]+= " 8="+("1" if fields[0]=="ConvolutionDepthWise" else "2")
            Path(args[3]).write_text("\n".join(lines)+"\n")
            Path(args[4]).write_bytes(b"fake quantized weights")
        return subprocess.CompletedProcess(args,0,"fake tool success\n","")

    def quantize(self,**kwargs):
        return workflow.quantize(self.tools,self.param,self.weights,self.calib,self.output,**kwargs)

    def test_short_norm_parser_limit_and_float32_equivalence(self):
        self.assertLessEqual(len(workflow.NORM.encode()),19)
        self.assertEqual(struct.pack("<f",float(workflow.NORM)),struct.pack("<f",1/127))
        # Mimic the fixed upstream scanner's 19-byte first token and comma test.
        def channel_count(values):
            text=",".join(values)
            count=0
            while text:
                token=text.split(",",1)[0][:19]
                count+=1
                text=text[len(token):]
                if not text.startswith(","):break
                text=text[1:]
            return count
        self.assertEqual(channel_count([workflow.NORM]*3),3)
        self.assertEqual(channel_count([str(1/127)]*3),1)

    def test_full_int8_workflow_checks_graph_hashes_and_records_infinite_weights(self):
        with patch.object(workflow.subprocess,"run",side_effect=self.fake_run):result=self.quantize()
        self.assertEqual(result["actual_int8_layers"],["depth","first","last"])
        self.assertEqual(result["weight_nonfinite_values"],1)
        self.assertFalse(result["accuracy_verified"])
        self.assertFalse(result["full_pipeline_realtime_verified"])
        self.assertIn("inf",(self.output/"peppa.table").read_text())
        self.assertEqual(result["calibration_validation"]["calibration_samples"],1)
        self.assertEqual(result["calibration_validation"]["holdout_samples"],1)
        for path,digest in result["sha256"].items():self.assertEqual(workflow.sha256(path),digest)
        self.assertEqual(json.loads((self.output/"summary.json").read_text()),result)

    def test_keep_float_removes_both_exact_rows_without_comments(self):
        with patch.object(workflow.subprocess,"run",side_effect=self.fake_run):result=self.quantize(keep_float=["first","last"],method="aciq")
        table=(self.output/"peppa.table").read_text()
        self.assertEqual(result["actual_int8_layers"],["depth"])
        self.assertNotIn("#",table)
        names={line.split()[0] for line in table.splitlines()}
        self.assertTrue({"first","first_param_0","last","last_param_0"}.isdisjoint(names))
        self.assertIn("method=aciq",self.calls[0][0])

    def test_exit_zero_incomplete_quantization_is_rejected(self):
        def broken(args,**kwargs):
            result=self.fake_run(args,**kwargs)
            if Path(args[0]).name=="ncnn2int8":
                Path(args[3]).write_text(PARAM.replace("0=2 6=4 7=2","0=2 6=4 7=2 8=1"))
            return result
        with patch.object(workflow.subprocess,"run",side_effect=broken):
            with self.assertRaises(ValueError):self.quantize()
        self.assertFalse((self.output/"summary.json").exists())

    def test_quantizer_warning_or_failed_stderr_rejected_even_exit_zero(self):
        for text in ("this layer need to be quantized, but no scale param!","allocation FAILED"):
            target=self.root/("warn"+str(len(self.calls)))
            def warning(args,**kwargs):
                result=self.fake_run(args,**kwargs)
                return subprocess.CompletedProcess(args,0,result.stdout,text)
            with patch.object(workflow.subprocess,"run",side_effect=warning):
                with self.assertRaises(RuntimeError):workflow.quantize(self.tools,self.param,self.weights,self.calib,target)
            self.assertTrue((target/"ncnn2table.stderr.log").exists())

    def test_manifest_list_hash_split_and_traversal_guard(self):
        list_path=self.calib/"calibration.relative.list"
        for text in ("holdout/frame_000003.png\n","calibration/frame_000000.png\ncalibration/frame_000000.png\n","../escape.png\n"):
            list_path.write_text(text)
            with self.assertRaises(ValueError):workflow.validate_calibration(self.calib)
        list_path.write_text("calibration/frame_000000.png\n")
        self.manifest["samples"][0]["split"]="holdout"
        self.write_manifest()
        with self.assertRaises(ValueError):workflow.validate_calibration(self.calib)
        self.manifest["samples"][0]["split"]="calibration"
        self.manifest["samples"][0]["png_sha256"]="wrong"
        self.write_manifest()
        with self.assertRaises(ValueError):workflow.validate_calibration(self.calib)

    def test_invalid_activation_scales_and_missing_scale_pair_fail(self):
        source,destination=self.root/"source.table",self.root/"filtered.table"
        for token in ("nan","inf","0","-1","1e100","1e-100"):
            source.write_text(TABLE.replace("first 6","first "+token))
            with self.assertRaises(ValueError):workflow.filter_table(source,destination,workflow.parse_param(self.param),set())
        source.write_text(TABLE.replace("first_param_0 1 inf\n",""))
        with self.assertRaises(ValueError):workflow.filter_table(source,destination,workflow.parse_param(self.param),{"first"})

    def test_empty_output_unknown_float_layer_and_graph_integrity(self):
        self.output.mkdir()
        (self.output/"old").write_text("marker")
        with self.assertRaises(FileExistsError):self.quantize()
        with self.assertRaises(ValueError):workflow.quantize(self.tools,self.param,self.weights,self.calib,self.root/"unknown",keep_float=["input0"])
        malformed=self.root/"malformed.param"
        malformed.write_text(PARAM.replace("4 4","5 4"))
        with self.assertRaises(ValueError):workflow.parse_param(malformed)


if __name__=="__main__":unittest.main()
