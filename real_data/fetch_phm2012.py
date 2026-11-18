"""Download real run-to-failure bearing data (IEEE PHM 2012 / FEMTO-ST PRONOSTIA, public
GitHub mirror) and keep only per-snapshot health indicators (horizontal accel RMS, peak,
kurtosis). Raw files (about 80 KB each) are streamed and discarded.
Citation: Nectoux et al., PRONOSTIA: an experimental platform for bearings accelerated
degradation tests, IEEE PHM 2012, Denver."""
import csv, io, math, sys, time, os
from concurrent.futures import ThreadPoolExecutor
import requests

BASE = "https://raw.githubusercontent.com/wkzs111/phm-ieee-2012-data-challenge-dataset/master/Learning_set"
BEARINGS = {"Bearing3_1": 515, "Bearing2_2": 797, "Bearing1_2": 871, "Bearing2_1": 911}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phm2012_features.csv")
sess = requests.Session()

def one(args):
    b, i = args
    for attempt in range(4):
        try:
            r = sess.get(f"{BASE}/{b}/acc_{i:05d}.csv", timeout=30)
            if r.status_code != 200:
                raise RuntimeError(r.status_code)
            x = [float(row[4]) for row in csv.reader(io.StringIO(r.text))]
            n = len(x); m = sum(x) / n
            v = sum((a - m) ** 2 for a in x) / n
            rms = math.sqrt(sum(a * a for a in x) / n)
            kurt = (sum((a - m) ** 4 for a in x) / n) / (v ** 2) if v > 0 else 0.0
            return b, i, rms, max(abs(a) for a in x), kurt
        except Exception as e:
            time.sleep(1 + attempt)
    return b, i, float("nan"), float("nan"), float("nan")

t0 = time.time()
jobs = [(b, i) for b, n in BEARINGS.items() for i in range(1, n + 1)]
with ThreadPoolExecutor(max_workers=40) as ex:
    res = list(ex.map(one, jobs))
bad = sum(1 for r in res if r[2] != r[2])
with open(OUT, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["bearing", "snapshot", "t_s", "rms", "peak", "kurtosis"])
    for b, i, rms, pk, ku in sorted(res):
        w.writerow([b, i, i * 10, f"{rms:.5f}", f"{pk:.4f}", f"{ku:.4f}"])
print(f"{len(res)} snapshots, {bad} failed, {time.time()-t0:.0f}s -> {OUT}")
