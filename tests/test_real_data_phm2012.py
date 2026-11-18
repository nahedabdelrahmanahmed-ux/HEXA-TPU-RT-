"""
Tests on REAL run-to-failure data (IEEE PHM 2012 / FEMTO-ST PRONOSTIA bearings, public GitHub mirror).
Data integrity + a characterization of the Bat forecaster on real degradation (leave-one-bearing-out
failure thresholds, no peeking). These numbers are measured results, locked with tolerances.
"""
import csv, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "real_data"))
from validate_bat_phm2012 import evaluate, load

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "real_data", "phm2012_features.csv")
EXPECTED = {"Bearing1_2": 871, "Bearing2_1": 911, "Bearing2_2": 797, "Bearing3_1": 515}


class TestDataIntegrity(unittest.TestCase):
    def test_complete_and_clean(self):
        data = load()
        self.assertEqual({k: len(v) for k, v in data.items()}, EXPECTED)
        for k, v in data.items():
            self.assertTrue(all(x == x and x > 0 for x in v), f"{k}: NaN or non-positive RMS")
        with open(CSV) as f:
            rows = list(csv.DictReader(f))
        for b, n in EXPECTED.items():
            snaps = [int(r["snapshot"]) for r in rows if r["bearing"] == b]
            self.assertEqual(snaps, list(range(1, n + 1)))

    def test_degradation_is_real_and_late(self):
        """The real curves are flat for most of the life and jump at the end (sudden-onset failure)."""
        for k, v in load().items():
            n = len(v)
            early = sorted(v[int(.1 * n):int(.7 * n)])[len(v[int(.1 * n):int(.7 * n)]) // 2]
            self.assertLess(max(v[:int(.8 * n)]), 4 * early + 1.0, k)      # no early runaway
        v = load()["Bearing1_2"]
        self.assertGreater(v[-1], 3 * sorted(v)[len(v) // 2])               # the end is far above the median


class TestBatOnRealBearings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = evaluate()

    def test_no_false_alerts_in_first_80_percent_of_life(self):
        for b, r in self.res.items():
            self.assertEqual(r["bat_false_alert_pct_first80"], 0.0, b)

    def test_every_bearing_is_alerted_before_failure(self):
        for b, r in self.res.items():
            self.assertIsNotNone(r["bat_first_alert_snapshot"], b)
            self.assertLess(r["bat_first_alert_snapshot"], r["n"], b)
            self.assertGreaterEqual(r["bat_lead_s"], 60, b)                 # measured: 90..410 s

    def test_lead_times_match_measured_values(self):
        measured = {"Bearing1_2": 410, "Bearing2_1": 110, "Bearing2_2": 90, "Bearing3_1": 210}
        for b, lead in measured.items():
            self.assertAlmostEqual(self.res[b]["bat_lead_s"], lead, delta=30, msg=b)

    def test_bat_is_no_worse_than_a_simple_change_detector(self):
        """Fair comparison: bat's lead >= baseline lead - 50 s on every bearing, and baseline misses one."""
        for b, r in self.res.items():
            if r["baseline_lead_s"] is not None:
                self.assertGreaterEqual(r["bat_lead_s"], r["baseline_lead_s"] - 50, b)
        self.assertTrue(any(r["baseline_lead_s"] is None for r in self.res.values()))

    def test_lead_time_is_short_compared_with_life(self):
        """Honest limit: sudden-onset failures give warning of only ~1-5 % of life (not a long-horizon RUL)."""
        for b, r in self.res.items():
            self.assertLess(r["bat_lead_s"] / (r["n"] * 10), 0.08, b)


if __name__ == "__main__":
    unittest.main()
