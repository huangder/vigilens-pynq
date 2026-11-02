"""Synthetic arithmetic and split checks; no invented measured video results."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("compare_peppa",ROOT/"board/autonomy/compare_peppa_quantization.py")
comparison=importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class QuantizationComparison(unittest.TestCase):
    def test_output_size_and_finite_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"tensor.f32"
            path.write_bytes(struct.pack("<143f",*range(143)))
            self.assertEqual(comparison.read_output(path)[-1],142.)
            for raw in (b"",b"0"*571,struct.pack("<143f",float("nan"),*range(142))):
                path.write_bytes(raw)
                with self.assertRaises(ValueError): comparison.read_output(path)

    def test_actual_crop_scaling_and_auxiliary_exclusion(self):
        reference=[0.]*143
        target=[0.]*143
        target[0],target[1],target[-1]=.03,.04,1000.
        coordinates,points=comparison.point_differences(reference,target,[10,20,110,220])
        self.assertEqual(len(coordinates),136)
        self.assertAlmostEqual(points[0],(3**2+8**2)**.5)
        self.assertEqual(points[1:],[0.]*67)
        with self.assertRaises(ValueError): comparison.point_differences(reference,target,[0,0,0,1])

    def test_disjoint_source_frame_split_and_duplicates(self):
        manifest={"split_frame":300,"samples":[{"frame_id":299,"split":"calibration"},{"frame_id":300,"split":"holdout"}]}
        self.assertEqual(comparison.selected_holdout(manifest)[0]["frame_id"],300)
        manifest["samples"][0]["split"]="holdout"
        with self.assertRaises(ValueError): comparison.selected_holdout(manifest)
        manifest["samples"]=[{"frame_id":300,"split":"holdout"}]*2
        with self.assertRaises(ValueError): comparison.selected_holdout(manifest)
        manifest["samples"]=[]
        with self.assertRaises(ValueError): comparison.selected_holdout(manifest)

    def test_interpolated_quantiles_and_empty_distribution(self):
        result=comparison.distribution([1.,2.,3.,4.])
        self.assertEqual(result["p50"],2.5)
        self.assertAlmostEqual(result["p95"],3.85)
        with self.assertRaises(ValueError): comparison.distribution([])


if __name__=="__main__":unittest.main()
