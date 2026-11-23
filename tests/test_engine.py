"""Engine tests: the paper's published numbers, plus end-to-end runs on the
synthetic demo person. Standard library only: python -m unittest -v"""

import unittest
from datetime import date

from healthage import demo, engine as E


class PaperNumbers(unittest.TestCase):
    """Values printed in Lee, Esmaeilpour et al. 2026 (arXiv:2611.08568)."""

    def test_figure_2a_worked_example(self):
        # age 29, 9,000 steps, 420 AZM, RHR 60, 7 h sleep -> Health Age 26.0
        c = E.cha(29, rhr=60, steps=9000, sleep_h=7, azm_week=420)
        self.assertAlmostEqual(c["health_age"], 26.0, delta=0.05)
        self.assertAlmostEqual(c["parts"]["steps"], -0.61, delta=0.01)
        self.assertAlmostEqual(c["parts"]["azm"], -2.39, delta=0.01)
        self.assertAlmostEqual(c["parts"]["rhr"], 0.0, delta=1e-9)
        self.assertAlmostEqual(c["parts"]["sleep"], 0.0, delta=1e-9)

    def test_section_4_1_5_caps(self):
        best = E.cha(40, rhr=55, steps=20000, sleep_h=7.5, azm_week=1000)
        self.assertAlmostEqual(best["delta"], -3.292, delta=0.002)
        self.assertAlmostEqual(best["parts"]["azm"], -2.68, delta=0.005)
        self.assertAlmostEqual(best["parts"]["steps"], -0.61, delta=0.005)
        self.assertAlmostEqual(E.loghr_regularity(150) / E.G, 7.47, delta=0.01)
        self.assertAlmostEqual(E.loghr_ir(True) / E.G, 6.93, delta=0.01)

    def test_vo2_one_met_above_expected(self):
        exp = E.expected_vo2(30, False, 80)
        self.assertAlmostEqual(E.loghr_vo2(exp + 3.5, 30, False, 80) / E.G, -1.28, delta=0.01)

    def test_floor_at_18(self):
        self.assertEqual(E.cha(18.5, rhr=55, steps=12000, sleep_h=8, azm_week=600)["health_age"], 18.0)

    def test_piecewise_continuity(self):
        for fn, knots in ((E.loghr_rhr, (60, 70)), (E.loghr_azm, (115, 245, 418, 500)),
                          (E.loghr_regularity, (30, 60, 90)), (E.loghr_sleep, (6, 9))):
            for k in knots:
                self.assertAlmostEqual(fn(k - 1e-9), fn(k + 1e-9), delta=1e-6, msg=f"{fn.__name__} at {k}")


class Helpers(unittest.TestCase):
    def test_circular_sd_wraps(self):
        self.assertAlmostEqual(E.circular_sd_minutes([1430, 10]), 10.0, delta=1e-6)    # across midnight
        self.assertAlmostEqual(E.circular_sd_minutes([710, 730]), 10.0, delta=1e-6)

    def test_local_midpoint_uses_session_offset(self):
        s = {"start": "2026-11-20T05:00:00Z", "end": "2026-11-20T13:00:00Z", "offset_s": -18000}
        self.assertAlmostEqual(E.local_midpoint_minutes(s), 4 * 60, delta=1e-6)       # 04:00 local

    def test_insulin_resistance_surrogate(self):
        self.assertEqual(E.insulin_resistance({"labs": {"triglycerides_mg_dl": 160, "hdl_mg_dl": 40}})[0], True)
        self.assertEqual(E.insulin_resistance({"labs": {"triglycerides_mg_dl": 90, "hdl_mg_dl": 60}})[0], False)
        self.assertIsNone(E.insulin_resistance({})[0])
        self.assertEqual(E.insulin_resistance({"insulin_resistant": False,
                                               "labs": {"triglycerides_mg_dl": 160, "hdl_mg_dl": 40}})[0], False)


class DemoEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.today = date(2026, 11, 22)
        cls.data, cls.prof = demo.data(cls.today), demo.profile(cls.today)
        cls.r = E.report(cls.data, cls.prof, today=cls.today)

    def test_report_shape(self):
        r = self.r
        self.assertTrue(r["ok"])
        self.assertEqual(r["as_of"], "2026-11-21")
        self.assertEqual({d["key"] for d in r["domains"]} >= {"rhr", "steps", "sleep", "azm"}, True)
        self.assertEqual(len(r["futures"]["status_quo"]), E.HORIZON_WEEKS + 1)
        self.assertGreaterEqual(len(r["futures"]["options"]), 1)

    def test_options_beat_threshold_and_are_distinct(self):
        opts = self.r["futures"]["options"]
        self.assertEqual(len({o["pathway"] for o in opts}), len(opts))
        for o in opts:
            self.assertGreater(o["benefit"], E.MIN_BENEFIT_YEARS)
            self.assertLessEqual(o["benefit_low"], o["benefit"] + 1e-9)

    def test_status_quo_is_flat(self):
        sq = [p["health_age"] for p in self.r["futures"]["status_quo"]]
        self.assertAlmostEqual(max(sq), min(sq), delta=1e-9)

    def test_no_lookahead(self):
        """A report as of an earlier date must not change when later days exist."""
        cut = date(2026, 10, 31)
        trimmed = {"days": {k: v for k, v in self.data["days"].items() if k <= cut.isoformat()}}
        a = E.report(trimmed, self.prof, today=self.today, as_of=cut)
        b = E.report(self.data, self.prof, today=self.today, as_of=cut)
        self.assertEqual(a["core"], b["core"])
        self.assertEqual(a["futures"]["options"], b["futures"]["options"])

    def test_simulate_matches_option(self):
        o = self.r["futures"]["options"][0]
        s = E.simulate(self.data, self.prof, o["targets"], today=self.today)
        self.assertAlmostEqual(s["benefit"], o["benefit"], delta=0.02)

    def test_profile_required(self):
        self.assertEqual(E.report(self.data, {}, today=self.today)["reason"], "profile")


if __name__ == "__main__":
    unittest.main()
