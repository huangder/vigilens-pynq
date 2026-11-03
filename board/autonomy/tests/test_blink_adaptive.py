"""Independent synthetic expectations for the opt-in board EAR policy.

These tests verify event semantics, not human blink accuracy. Legacy Python/C
parity is still exercised by test_behavior.py with the policy disabled.
"""
import ctypes as ct
import unittest

from board.autonomy.tests.test_behavior import BehaviorParity, Config, Observation, Result, native, observation


class BlinkConfig(ct.Structure):
    _fields_=[(k,ct.c_double) for k in (
        'close_ratio','open_ratio','baseline_tau_s','min_close_ms',
        'min_open_ms','max_gap_ms','baseline_reset_ms','close_threshold','open_threshold','long_close_ms')]


class AdaptiveBlink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BehaviorParity.setUpClass.__func__(cls)
        cls.lib.vg_behavior_configure_blink.argtypes=[ct.c_void_p,ct.POINTER(BlinkConfig)]
        cls.lib.vg_behavior_configure_blink.restype=ct.c_int

    @classmethod
    def tearDownClass(cls):
        BehaviorParity.tearDownClass.__func__(cls)

    def make(self, absolute=False, production=False, **overrides):
        config=Config(*(self.cfg[k] for k,_ in Config._fields_))
        handle=self.lib.vg_behavior_create(ct.byref(config))
        self.assertTrue(handle)
        self.addCleanup(self.lib.vg_behavior_destroy,handle)
        b=self.cfg['board_vision']
        values=dict(zip((k for k,_ in BlinkConfig._fields_),
            (b[k] for k in ('ear_close_ratio','ear_open_ratio','ear_baseline_tau_s',
                           'blink_min_close_ms','blink_min_open_ms','blink_max_gap_ms','ear_baseline_reset_ms'))))
        # Preserve the previous adaptive 60 ms fixture and independent assertions.
        values.update(min_close_ms=b['blink_min_close_ms'] if absolute else 60,
                      min_open_ms=b['blink_min_open_ms'] if absolute else 60,
                      long_close_ms=b['blink_long_close_ms'] if absolute else self.cfg['long_close_ms'],
                      close_threshold=(b['ear_close_absolute'] if production else .4) if absolute else 0,
                      open_threshold=(b['ear_open_absolute'] if production else .4) if absolute else 0)
        values.update(overrides)
        policy=BlinkConfig(**values)
        self.assertEqual(self.lib.vg_behavior_configure_blink(handle,ct.byref(policy)),0)
        return handle

    def step(self, h, ts, left, right=None, visible=1):
        obs=observation(left,visible=visible)
        if right is not None:
            obs.eyes['right']=observation(right).eyes['right']
        result=Result()
        self.assertEqual(self.lib.vg_behavior_update(h,ts,ct.byref(native(obs)),ct.byref(result)),0)
        return result

    def test_production_policy_detects_relative_drop_above_old_fixed_threshold(self):
        h=self.make()
        self.step(h,0,.5)
        self.step(h,.032,.5)
        self.step(h,.064,.5)
        self.step(h,.096,.3)
        self.step(h,.128,.3)
        r=self.step(h,.160,.5)
        self.assertEqual(r.blink_count,0)  # reopening must be confirmed
        r=self.step(h,.224,.5)
        self.assertEqual(r.blink_count,1)
        self.assertEqual(r.long_close_count,0)
        self.assertAlmostEqual(r.ear_left,.5)

    def test_reopening_at_exact_minimum_duration_is_counted_once(self):
        h=self.make(min_close_ms=125,max_gap_ms=200)
        self.step(h,-.125,.4)
        self.step(h,0,.1)
        self.step(h,.0625,.1)
        self.step(h,.125,.4)
        r=self.step(h,.25,.4)
        self.assertEqual(r.blink_count,1)
        self.assertEqual(self.step(h,.3125,.4).blink_count,1)

    def test_both_large_and_small_open_eye_baselines(self):
        for opened in (.18,.6):
            h=self.make()
            self.step(h,0,opened)
            self.step(h,.032,opened)
            self.step(h,.064,opened)
            self.step(h,.096,opened/2)
            self.step(h,.128,opened/2)
            self.step(h,.16,opened)
            self.assertEqual(self.step(h,.224,opened).blink_count,1)

    def test_single_eye_wink_and_single_frame_dip_do_not_count(self):
        h=self.make()
        for i in range(16):
            r=self.step(h,i*.032,.1 if 3<=i<=10 else .5,.5)
        self.assertEqual(r.blink_count,0)
        self.step(h,.512,.1)
        self.step(h,.544,.5)
        self.assertEqual(self.step(h,.608,.5).blink_count,0)

    def test_hysteresis_and_short_reopening_spike_do_not_split_event(self):
        h=self.make()
        self.step(h,0,.5)
        self.step(h,.032,.5)
        self.step(h,.064,.2)
        self.step(h,.128,.2)
        self.step(h,.160,.5)  # short reopening
        self.step(h,.192,.2)
        r=self.step(h,.224,.36)  # between close=.325 and open=.4
        self.assertEqual(r.blink_count,0)
        self.step(h,.256,.5)
        self.assertEqual(self.step(h,.320,.5).blink_count,1)

    def test_gaps_and_face_loss_cancel_unfinished_events(self):
        for missing_face in (False,True):
            h=self.make()
            self.step(h,0,.5)
            self.step(h,.032,.2)
            self.step(h,.096,.2)
            if missing_face:
                self.step(h,.128,.2,visible=0)
                end=.192
            else:
                end=.5
            self.step(h,end,.5)
            r=self.step(h,end+.064,.5)
            self.assertEqual((r.blink_count,r.long_close_count),(0,0))

    def test_sustained_closure_freezes_baseline_and_is_long_not_blink(self):
        h=self.make()
        self.step(h,0,.5)
        for i in range(1,25):
            r=self.step(h,i*.032,.2)
        self.assertEqual(r.blink_state,2)
        self.step(h,.8,.5)
        r=self.step(h,.864,.5)
        self.assertEqual((r.blink_count,r.long_close_count),(0,1))

    def test_long_face_loss_relearns_baseline(self):
        h=self.make()
        self.step(h,0,.6)
        self.step(h,.032,.6,visible=0)
        self.step(h,2,.18)
        self.step(h,2.032,.18)
        self.step(h,2.064,.18)
        self.step(h,2.096,.08)
        self.step(h,2.128,.08)
        self.step(h,2.160,.18)
        self.assertEqual(self.step(h,2.224,.18).blink_count,1)

    def test_policy_rejects_invalid_config_and_mid_session_reconfiguration(self):
        h=self.make()
        bad=BlinkConfig(.8,.65,2,60,60,120,1000)
        self.assertEqual(self.lib.vg_behavior_configure_blink(h,ct.byref(bad)),-1)
        self.step(h,0,.5)
        valid=BlinkConfig(.65,.8,2,60,60,120,1000)
        self.assertEqual(self.lib.vg_behavior_configure_blink(h,ct.byref(valid)),-1)

    def test_absolute_point_399_is_counted_once_after_reopening(self):
        h=self.make(absolute=True,min_close_ms=30,min_open_ms=60,long_close_ms=500)
        self.step(h,0,.45)
        self.step(h,.032,.399)
        self.step(h,.064,.45)
        r=self.step(h,.128,.45)
        self.assertEqual(r.blink_count,1)
        self.assertEqual(self.step(h,.192,.45).blink_count,1)

    def test_absolute_400_exactly_is_not_below_threshold(self):
        h=self.make(absolute=True)
        for i in range(20):
            r=self.step(h,i*.032,.4 if 2<=i<=8 else .45)
        self.assertEqual((r.blink_count,r.long_close_count),(0,0))

    def test_absolute_uses_mean_and_not_learned_baseline(self):
        h=self.make(absolute=True,min_close_ms=30,min_open_ms=60,long_close_ms=500)
        self.step(h,0,.45)
        self.step(h,.032,.35,.43)  # mean .39, above old adaptive threshold
        self.step(h,.064,.45)
        self.assertEqual(self.step(h,.128,.45).blink_count,1)

    def test_absolute_sustained_closure_remains_one_long_event(self):
        h=self.make(absolute=True,min_close_ms=30,min_open_ms=60,long_close_ms=500)
        self.step(h,0,.5)
        for i in range(1,25):
            r=self.step(h,i*.032,.3)
        self.assertEqual((r.blink_count,r.long_close_count),(0,0))
        self.step(h,.8,.45)
        r=self.step(h,.864,.45)
        self.assertEqual((r.blink_count,r.long_close_count),(0,1))

    def test_absolute_hysteresis_does_not_rearm_at_point_410(self):
        h=self.make(absolute=True,min_close_ms=30,min_open_ms=60,long_close_ms=500,open_threshold=.43)
        self.step(h,0,.5)
        self.step(h,.032,.39)
        self.step(h,.064,.41)
        self.step(h,.096,.39)
        self.step(h,.128,.45)
        self.assertEqual(self.step(h,.192,.45).blink_count,1)

    def test_fixed_400_reopens_above_400_without_waiting_for_430(self):
        h=self.make(absolute=True)
        self.step(h,0,.45);self.step(h,.04,.39);self.step(h,.12,.39)
        self.step(h,.16,.405)
        self.assertEqual(self.step(h,.27,.405).blink_count,1)
        self.assertEqual(self.step(h,.4,.405).blink_count,1)

    def test_production_rejects_short_dip_and_accepts_125ms_blink(self):
        h=self.make(absolute=True,production=True)
        self.step(h,0,.5)
        self.step(h,.032,.2)
        self.step(h,.064,.5)
        self.assertEqual(self.step(h,.192,.5).blink_count,0)
        self.step(h,.25,.2)
        self.step(h,.3125,.2)
        self.step(h,.375,.5)
        self.step(h,.4375,.5)
        self.assertEqual(self.step(h,.5,.5).blink_count,1)

    def test_production_750ms_closure_is_blink_not_long(self):
        h=self.make(absolute=True,production=True)
        self.step(h,-.0625,.5)
        for i in range(12):self.step(h,i*.0625,.2)
        self.step(h,.75,.5)
        self.step(h,.8125,.5)
        r=self.step(h,.875,.5)
        self.assertEqual((r.blink_count,r.long_close_count),(1,0))

    def test_production_exact_1500ms_is_one_long_closure(self):
        h=self.make(absolute=True,production=True)
        self.step(h,-.0625,.5)
        for i in range(24):self.step(h,i*.0625,.2)
        self.step(h,1.5,.5)
        self.step(h,1.5625,.5)
        r=self.step(h,1.625,.5)
        self.assertEqual((r.blink_count,r.long_close_count),(0,1))

    def test_hysteresis_wait_is_not_added_to_actual_closed_duration(self):
        h=self.make(absolute=True,open_threshold=.43)
        self.step(h,-.0625,.5)
        for i in range(4):self.step(h,i*.0625,.3)
        for i in range(4,40):self.step(h,i*.0625,.415)
        self.step(h,2.5,.5)
        self.step(h,2.5625,.5)
        r=self.step(h,2.625,.5)
        self.assertEqual((r.blink_count,r.long_close_count),(1,0))

    def test_production_300_is_boundary_and_299_counts_once(self):
        h=self.make(absolute=True,production=True)
        self.step(h,0,.4)
        for i in range(1,10):r=self.step(h,i*.03,.3)
        self.assertEqual((r.blink_count,r.long_close_count),(0,0))
        self.step(h,.30,.299);self.step(h,.39,.299)
        self.step(h,.42,.3);r=self.step(h,.53,.3)
        self.assertEqual((r.blink_count,r.long_close_count),(1,0))
        self.assertEqual(self.step(h,.64,.4).blink_count,1)


if __name__=='__main__':unittest.main()
