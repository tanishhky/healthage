"""Extractor tests on response shapes seen from the live Google Health API."""

import unittest

from healthage import google_health as gh


class Extractors(unittest.TestCase):
    def test_azm_credits_are_halved_for_minutes(self):
        # Fat-burn is minutes; cardio and peak arrive as doubled AZM credits.
        a = gh.azm_of({"activeZoneMinutes": {"sumInFatBurnHeartZone": "40", "sumInCardioHeartZone": "6",
                                             "sumInPeakHeartZone": "0"}})
        self.assertEqual(a["total"], 46)
        self.assertEqual(a["cardio_min"], 3)
        self.assertEqual(a["fat_burn_min"], 40)

    def test_steps_and_weight(self):
        self.assertEqual(gh.steps_of({"steps": {"countSum": "4321"}}), 4321)
        self.assertEqual(gh.weight_of({"weight": {"weightGramsAvg": 72350}}), 72.35)

    def _sleep(self, start, end, minutes, main=True, nap=False, off="-18000s"):
        md = {"mainSleep": main} if main else {}
        if nap:
            md["nap"] = True
        return {"sleep": {"interval": {"startTime": start, "endTime": end, "startUtcOffset": off, "endUtcOffset": off},
                          "metadata": md, "summary": {"minutesAsleep": str(minutes)}}}

    def test_sleep_main_episode_not_sum(self):
        pts = [self._sleep("2030-01-10T04:00:00Z", "2030-01-10T11:30:00Z", 420),
               self._sleep("2030-01-10T13:00:00Z", "2030-01-10T15:00:00Z", 110, main=False),
               self._sleep("2030-01-10T20:00:00Z", "2030-01-10T20:40:00Z", 35, main=False, nap=True)]
        s = gh.sleep_by_date(pts)["2030-01-10"]
        self.assertAlmostEqual(s["hours"], 420 / 60, places=2)
        self.assertAlmostEqual(s["other_hours"], 110 / 60, places=2)
        self.assertAlmostEqual(s["nap_hours"], 35 / 60, places=2)

    def test_sleep_grouped_by_local_wake_date(self):
        # ends 01:30Z on the 11th = 20:30 on the 10th in UTC-5
        pts = [self._sleep("2030-01-10T19:00:00Z", "2030-01-11T01:30:00Z", 360)]
        self.assertIn("2030-01-10", gh.sleep_by_date(pts))

    def test_duplicate_sources_take_longest_main(self):
        pts = [self._sleep("2030-02-03T04:00:00Z", "2030-02-03T11:00:00Z", 400),
               self._sleep("2030-02-03T04:05:00Z", "2030-02-03T10:55:00Z", 390)]
        self.assertAlmostEqual(gh.sleep_by_date(pts)["2030-02-03"]["hours"], 400 / 60, places=2)

    def test_rhr_prefers_dominant_source(self):
        def p(day, v, dev):
            return {"dataSource": {"device": {"displayName": dev}},
                    "dailyRestingHeartRate": {"date": {"year": 2030, "month": 3, "day": day}, "beatsPerMinute": str(v)}}
        pts = [p(d, 62, "Band") for d in range(1, 8)] + [p(5, 58, "Phone")]
        self.assertEqual(gh.rhr_by_date(pts)["2030-03-05"], (62.0, "Band"))


if __name__ == "__main__":
    unittest.main()
