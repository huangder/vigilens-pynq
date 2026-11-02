"""Independent tests using synthetic colors/mock detections, no real video run."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location("prepare_peppa",ROOT/"board/autonomy/prepare_peppa_calibration.py")
preparation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparation)


class PeppaPreparation(unittest.TestCase):
    def test_author_crop_floors_expands_14_and_clamps(self):
        self.assertEqual(preparation.crop_bounds([20,30,40,50],100,100),(16,26,44,54))
        self.assertEqual(preparation.crop_bounds([0,0,20,20],100,100),(0,0,24,24))
        self.assertEqual(preparation.crop_bounds([80,80,100,100],100,100),(76,76,100,100))
        # 1.4*90 is 125.99999999999999; author's float //2 gives 62, not 63.
        self.assertEqual(preparation.crop_bounds([10,10,100,100],200,200),(0,0,117,117))
        self.assertNotEqual(preparation.crop_bounds([20,30,40,50],100,100),(15,25,45,55))

    def test_degenerate_noninteger_nonfinite_bbox_rejected(self):
        for bbox in ([0,0,0,5],[2,2,1,4],[0,0,1,1],[0,0,2.5,4],[0,0,float("nan"),4],[True,0,3,4],[-30,0,-20,10]):
            with self.assertRaises(ValueError): preparation.crop_bounds(bbox,10,10)

    def test_exact_temporal_split_boundary(self):
        self.assertEqual(preparation.sample_split(299,300),"calibration")
        self.assertEqual(preparation.sample_split(300,300),"holdout")
        self.assertEqual(preparation.sample_split(301,300),"holdout")
        with self.assertRaises(ValueError): preparation.sample_split(-1,300)

    def test_paths_limits_and_nonempty_output_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/"allowed"
            source=Path(temp)/"source.mp4"
            source.write_bytes(b"synthetic test source marker")
            with patch.object(preparation,"ALLOWED_OUTPUT",base):
                preparation.validate_paths(source,base,600,3,300)
                for output in (Path(temp),Path(temp)/"deploy",base/".."/"escape"):
                    with self.assertRaises(ValueError): preparation.validate_paths(source,output,600,3,300)
                for frames,stride,split in ((0,3,300),(600,0,300),(600,3,-1),(True,3,300)):
                    with self.assertRaises(ValueError): preparation.validate_paths(source,base,frames,stride,split)
                base.mkdir()
                (base/"old.png").write_bytes(b"old test marker")
                with self.assertRaises(FileExistsError): preparation.validate_paths(source,base,600,3,300)
                self.assertEqual((base/"old.png").read_bytes(),b"old test marker")

    def test_author_preprocessing_bgr_float64_then_nchw_float32(self):
        import cv2
        import numpy as np
        rng=np.random.default_rng(719)
        image=rng.integers(0,256,(17,23,3),dtype=np.uint8)
        png,tensor,bounds=preparation.preprocess(image,[4,3,18,15])
        x0,y0,x1,y1=bounds
        expected_png=cv2.resize(image[y0:y1,x0:x1,:],(160,160),interpolation=cv2.INTER_LINEAR)
        expected=np.array([np.transpose((expected_png-127.0)/127.0,(2,0,1))]).astype(np.float32)
        self.assertTrue(np.array_equal(png,expected_png))
        self.assertTrue(np.array_equal(tensor,expected))
        self.assertEqual(tensor.shape,(1,3,160,160))
        image[:]=[0,127,255]
        _,tensor,_=preparation.preprocess(image,[4,3,18,15])
        self.assertEqual(float(tensor[0,0,0,0]),-1.)
        self.assertEqual(float(tensor[0,1,0,0]),0.)
        self.assertEqual(tensor[0,2,0,0],np.float32(128/127))
        for invalid in (image.astype(np.float32),image[:,:,0],image[:,:,:2]):
            with self.assertRaises(ValueError): preparation.preprocess(invalid,[4,3,18,15])

    def test_preparation_with_mock_detections_skips_and_never_crosses_split(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            source=folder/"mock_video.mp4"
            source.write_bytes(b"mock capture only; not a real image source")
            author=folder/"author.py"
            author.write_text("# test provenance marker\n")
            output=folder/"allowed"
            cap=MagicMock()
            cap.isOpened.return_value=True
            cap.get.side_effect=lambda prop: {cv2.CAP_PROP_FPS:30.,cv2.CAP_PROP_FRAME_WIDTH:16.,cv2.CAP_PROP_FRAME_HEIGHT:12.,cv2.CAP_PROP_FRAME_COUNT:6.}.get(prop,0.)
            image=np.full((12,16,3),127,dtype=np.uint8)
            cap.read.side_effect=[(True,image.copy()) for _ in range(6)]
            detector=MagicMock()
            detector.detect.side_effect=lambda image,idx: SimpleNamespace(source="mediapipe",visible=0. if idx==1 else 1.,bbox=(0,0,0,0) if idx==4 else (4,3,8,6))
            with patch.object(preparation,"ALLOWED_OUTPUT",output),patch.object(preparation,"AUTHOR_SOURCE",author),patch("cv2.VideoCapture",return_value=cap),patch("backend.face_landmark.MediaPipeLandmarker",return_value=detector):
                result=preparation.prepare(source,output,frames=6,stride=1,split_frame=3)
                self.assertEqual(result["counts"]["calibration_samples"],2)
                self.assertEqual(result["counts"]["holdout_samples"],2)
                self.assertFalse(result["accuracy_verified"])
                self.assertEqual([s["frame_id"] for s in result["samples"] if s["split"]=="calibration"],[0,2])
                self.assertEqual([s["frame_id"] for s in result["samples"] if s["split"]=="holdout"],[3,5])
                self.assertEqual([s["frame_id"] for s in result["skipped"]],[1,4])
                for sample in result["samples"]:
                    for extension in ("png","f32"):
                        path=output/sample[extension]
                        self.assertEqual(preparation.sha256(path),sample[extension+"_sha256"])
                    self.assertEqual(sample["f32_bytes"],1*3*160*160*4)
                listing=(output/"calibration.list").read_text()
                self.assertNotIn("holdout",listing)
                self.assertEqual(len(listing.splitlines()),2)
                self.assertEqual(json.loads((output/"manifest.json").read_text()),result)
                with self.assertRaises(FileExistsError): preparation.prepare(source,output,frames=6,stride=1,split_frame=3)
            detector._mesh.close.assert_called_once()
            cap.release.assert_called_once()


if __name__=="__main__":unittest.main()
