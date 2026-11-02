"""Old labels cannot silently survive video transcoding/trimming."""
from metrics.scripts.check_p4_readiness import annotation_problems


def test_old_45fps_labels_and_out_of_range_frames_are_rejected():
    problems = annotation_problems({"video": "blink.mp4", "fps": "45", "duration_s": "27.711"},
        [{"start_frame": "1145", "end_frame": "1152", "event": "blink"}],
        {"fps": 30, "duration": 656 / 30, "frames": 656}, "blink.mp4")
    assert len(problems) == 3
    assert any("超过视频末帧" in p for p in problems)
    assert any("fps=45" in p for p in problems)


def test_valid_labels_allow_duration_rounding_but_not_bad_ranges():
    video = {"fps": 30, "duration": 656 / 30, "frames": 656}
    assert annotation_problems({"video": "blink.mp4", "fps": "30", "duration_s": "21.867"},
        [{"start_frame": "10", "end_frame": "14"}], video, "blink.mp4") == []
    problems = annotation_problems({"video": "other.mp4", "fps": "nan"},
        [{"start_frame": "-1", "end_frame": "2"}, {"start_frame": "4", "end_frame": "3"},
         {"start_frame": "1.5", "end_frame": "2"}], video, "blink.mp4")
    assert len(problems) == 5
