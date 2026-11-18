"""Validate the Bat forecaster (linear trend RUL) on REAL run-to-failure bearing data
(IEEE PHM 2012 / FEMTO-ST). Leave-one-bearing-out: the failure threshold for a bearing is the
median final RMS of the OTHER bearings (no peeking). One snapshot = 10 s.
Run:  python3 real_data/validate_bat_phm2012.py"""
import csv, collections, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mec.bhs_cognitive_engine import BatForecaster

HERE = os.path.dirname(os.path.abspath(__file__))
HORIZON = 30          # alert when forecast RUL <= 30 snapshots (300 s)
WINDOW = 15


def load():
    d = collections.defaultdict(list)
    with open(os.path.join(HERE, "phm2012_features.csv")) as f:
        for r in csv.DictReader(f):
            d[r["bearing"]].append(float(r["rms"]))
    return dict(d)


def bat_alerts(rms, T):
    bat = BatForecaster(failure_threshold_kpa=T, history_len=WINDOW)
    out = []
    for i in range(WINDOW, len(rms) + 1):
        est = bat.forecast(rms[i - WINDOW:i])
        out.append((i, est.remaining_useful_life_windows))
    return out


def baseline_alert_index(rms, k=3.0, hold=3):
    """Simple change detector: RMS > k x median(first 100 snapshots) for `hold` consecutive snapshots."""
    base = statistics.median(rms[:100]); run = 0
    for i, v in enumerate(rms):
        run = run + 1 if v > k * base else 0
        if run >= hold:
            return i + 1
    return None


def evaluate():
    data = load(); res = {}
    for b, rms in data.items():
        n = len(rms)
        T = statistics.median(v[-1] for k, v in data.items() if k != b)
        series = bat_alerts(rms, T)
        early = [i for i, r in series if r is not None and r <= HORIZON and i <= 0.8 * n]
        first = next((i for i, r in series if r is not None and r <= HORIZON), None)
        base_i = baseline_alert_index(rms)
        res[b] = {
            "n": n, "threshold": T,
            "bat_false_alert_pct_first80": 100 * len(early) / max(1, int(0.8 * n) - WINDOW),
            "bat_first_alert_snapshot": first,
            "bat_lead_s": None if first is None else (n - first) * 10,
            "baseline_first_alert_snapshot": base_i,
            "baseline_lead_s": None if base_i is None else (n - base_i) * 10,
        }
    return res


if __name__ == "__main__":
    for b, r in evaluate().items():
        print(b, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
