"""Compile probe against an explicit fake NCNN API to test CLI/packing/I/O.

This does NOT validate compilation or inference against official NCNN.
The main agent separately builds the official runtime and executes real models.
"""
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
STUB = r'''
#ifndef FAKE_NCNN_H
#define FAKE_NCNN_H
#include <string>
#include <vector>
#include <stdexcept>
#include <limits>
#ifndef VG_PROBE_INT8_ENABLED
#define VG_PROBE_INT8_ENABLED 0
#endif
namespace ncnn {
struct Option {
 int num_threads=0;
 bool use_vulkan_compute=true,use_fp16_packed=true,use_fp16_storage=true,use_fp16_arithmetic=true;
 bool use_bf16_storage=true,use_int8_inference=true,use_int8_packed=true,use_int8_storage=true,use_int8_arithmetic=true;
 bool use_packing_layout=false;
 bool use_winograd_convolution=true,use_sgemm_convolution=true;
};
struct Mat {
 int dims=0,w=0,elempack=1,plane=0;
 std::vector<float> data;
 Mat()=default;
 Mat(int x,int y,int c):dims(3),w(x),plane(x*y+5),data((x*y+5)*c){}
 bool empty() const {return data.empty();}
 int elembits() const {return 32;}
 void fill(float v) {for(float &x:data)x=v;}
 float* channel(int c) {return data.data()+c*plane;}
 operator const float*() const {return data.data();}
};
struct Extractor {
 std::string mode; bool input_set=false,extracted=false;float base=0;
 Extractor(std::string s):mode(s){}
 void set_light_mode(bool){}
 int input(const char* name,const Mat& in){
  if(std::string(name)!="input")return -1;
  if(mode=="file"){
   if(in.data.front()!=1.25f||in.data[in.plane]!=3.25f||in.data[2*in.plane+112*112-1]!=-2.5f)return -1;
   base=in.data.front();
  }else for(float x:in.data)if(x!=0.f)return -1;
  input_set=true;return 0;
 }
 int extract(const char* name,Mat& out,int type){
  if(!input_set||extracted||type!=1||std::string(name)!="output")return -1;
  static int count=0; ++count; extracted=true;
  out.dims=(mode=="dims2"?2:1);out.elempack=(mode=="packed"?4:mode=="badpack"?2:1);
  out.w=196/out.elempack;out.data.resize(196);
  for(int k=0;k<196;++k)out.data[k]=float(count+k)+base;
  if(mode=="nonfinite")out.data[0]=std::numeric_limits<float>::quiet_NaN();
  return 0;
 }
};
struct Net {
 Option opt;std::string mode;
 int load_param(const char* path){
  if(opt.num_threads<1||opt.num_threads>2||opt.use_vulkan_compute||opt.use_fp16_packed||opt.use_fp16_storage||
     opt.use_fp16_arithmetic||opt.use_bf16_storage||opt.use_int8_inference!=bool(VG_PROBE_INT8_ENABLED)||
     opt.use_int8_packed!=bool(VG_PROBE_INT8_ENABLED)||opt.use_int8_storage!=bool(VG_PROBE_INT8_ENABLED)||
     opt.use_int8_arithmetic!=bool(VG_PROBE_INT8_ENABLED))return -1;
  mode=path;return mode=="badmodel"?-1:0;
 }
 int load_model(const char*){return 0;}
 Extractor create_extractor(){return Extractor(mode);}
};
inline void convert_packing(const Mat& src,Mat& dst,int pack,const Option&){
 dst=src;dst.w=src.w*src.elempack;dst.elempack=pack;
}
}
#endif
'''


class NcnnProbeCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="vg_ncnn_cli_")
        cls.folder=Path(cls.temp.name)
        (cls.folder/"net.h").write_text(STUB)
        cls.binary=cls.folder/"probe"
        args=shlex.split(os.environ.get("CXX","g++"))
        args += ["-std=c++11","-Wall","-Wextra","-Werror","-I",str(cls.folder),
                 str(ROOT/"board/autonomy/ncnn_probe/model_probe.cc"),"-o",str(cls.binary)]
        subprocess.run(args,check=True,capture_output=True,text=True)
        cls.int8_binary=cls.folder/"probe_int8"
        args[-1]=str(cls.int8_binary)
        args.insert(1,"-DVG_PROBE_INT8_ENABLED=1")
        subprocess.run(args,check=True,capture_output=True,text=True)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def command(self,mode="normal",expected="196",threads="2",iterations="3"):
        return [str(self.binary),mode,"fake.bin","112","112","input","output",
                str(self.folder/"result"),iterations,threads,expected]

    def test_invalid_cli_and_argument_bounds(self):
        self.assertEqual(subprocess.run([str(self.binary)],capture_output=True).returncode,2)
        for idx,value in ((3,"0"),(3,"4097"),(4,"NaN"),(8,"-1"),(8,"10001"),(9,"0"),(9,"3"),(10,"0"),(10,"196junk")):
            command=self.command()
            command[idx]=value
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_fp32_zero_input_fresh_extractors_and_little_endian_output(self):
        for mode in ("normal","packed"):
            result=subprocess.run(self.command(mode),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            metadata=json.loads((self.folder/"result.json").read_text())
            self.assertTrue(metadata["synthetic_input"])
            self.assertFalse(metadata["landmark_pipeline_verified"])
            self.assertTrue(metadata["fresh_extractor_each_iteration"])
            self.assertEqual(metadata["warmup_iterations"],1)
            self.assertFalse(metadata["int8"])
            self.assertEqual(metadata["threads_requested"],2)
            self.assertEqual(metadata["outputs"][0]["elements"],196)
            self.assertEqual(metadata["outputs"][0]["source_elempack"],4 if mode=="packed" else 1)
            values=struct.unpack("<196f",(self.folder/"result.output0.f32").read_bytes())
            self.assertEqual(values[0],4.) # warmup + 3 new Extractors, no cached extraction
            self.assertEqual(values[-1],199.)

    def test_bad_models_shapes_counts_nonfinite_and_blob_names_fail(self):
        for mode in ("badmodel","badpack","dims2","nonfinite"):
            result=subprocess.run(self.command(mode),capture_output=True)
            self.assertNotEqual(result.returncode,0)
        self.assertNotEqual(subprocess.run(self.command(expected="195"),capture_output=True).returncode,0)
        command=self.command()
        command[5]="wrong_input"
        self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_file_input_exact_length_finite_little_endian_and_metadata(self):
        file=self.folder/"input.f32"
        values=[0.]*(112*112*3)
        values[0],values[112*112],values[-1]=1.25,3.25,-2.5
        raw=struct.pack("<%df"%len(values),*values)
        file.write_bytes(raw)
        result=subprocess.run(self.command(mode="file")+[str(file)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        metadata=json.loads((self.folder/"result.json").read_text())
        self.assertEqual(metadata["input_kind"],"file")
        self.assertFalse(metadata["synthetic_input"])
        self.assertFalse(metadata["input_provenance_verified"])
        self.assertEqual(struct.unpack("<196f",(self.folder/"result.output0.f32").read_bytes())[0],5.25)
        for invalid in (raw[:-4],raw+b"x",struct.pack("<f",float("nan"))+raw[4:]):
            file.write_bytes(invalid)
            self.assertNotEqual(subprocess.run(self.command(mode="file")+[str(file)],capture_output=True).returncode,0)
        self.assertEqual(subprocess.run(self.command()+["--zeros"],capture_output=True).returncode,0)

    def test_kernel_options_recorded_and_invalid_values_rejected(self):
        for policy,winograd,sgemm in (("default",True,True),("winograd_only",True,False),
                                     ("sgemm_only",False,True),("direct",False,False)):
            env=dict(os.environ,VG_NCNN_KERNELS=policy,VG_NCNN_PACKING="0")
            result=subprocess.run(self.command(),env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            metadata=json.loads((self.folder/"result.json").read_text())
            self.assertEqual(metadata["kernel_policy"],policy)
            self.assertEqual(metadata["winograd"],winograd)
            self.assertEqual(metadata["sgemm"],sgemm)
            self.assertFalse(metadata["packing"])
        for env in (dict(os.environ,VG_NCNN_KERNELS="typo"),dict(os.environ,VG_NCNN_PACKING="yes")):
            self.assertNotEqual(subprocess.run(self.command(),env=env,capture_output=True).returncode,0)

    def test_int8_build_reports_permission_without_certifying_model(self):
        command=self.command()
        command[0]=str(self.int8_binary)
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        metadata=json.loads((self.folder/"result.json").read_text())
        self.assertTrue(metadata["int8"])
        self.assertFalse(metadata["quantized_model_verified"])


if __name__=="__main__":unittest.main()
