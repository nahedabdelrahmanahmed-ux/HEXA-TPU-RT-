"""Regenerate twin/reference/tsnet_reference.json with TSNet (independent MOC solver, validated by its
authors against the commercial Bentley HAMMER) on its own example network Tnet0:
reservoir (750 m) - pipe 1200 m D=600 mm - pipe 2400 m D=1200 mm - end valve (Q0 = 50 L/s), a = 1200 m/s.
   python3 -m venv v && v/bin/pip install "numpy<2" "scipy<1.14" "matplotlib<3.10" "wntr<1.3" "setuptools<81" tsnet
   v/bin/python twin/crosscheck_tsnet.py
TSNet valve rule [tc, ts, se, m]: opening = 1 - (1-se)*clip((t-ts)/tc,0,1)^m  (se = final open fraction)."""
import json, os, warnings
warnings.filterwarnings("ignore")
import numpy as np
import tsnet, wntr

HERE = os.path.dirname(os.path.abspath(__file__))
INP = os.path.join(HERE, "reference", "tnet0.inp")
DT = 0.01
CASES = {"close_0p05s": [0.05, 0.5, 0.0, 1], "close_2s": [2.0, 0.5, 0.0, 1],
         "close_10s": [10.0, 0.5, 0.0, 1], "partial_2s_to_50pct": [2.0, 0.5, 0.5, 1]}
out = {"meta": {"tsnet": getattr(tsnet, "__version__", "?"), "wntr": wntr.__version__, "dt_s": DT,
                "wave_speed": 1200.0, "network": "Tnet0 (TSNet example)", "t_end_s": 20.0, "stride": 10}, "cases": {}}
for name, rule in CASES.items():
    tm = tsnet.network.TransientModel(INP)
    tm.set_wavespeed(1200.0)
    tm.set_time(20.0, DT)
    tm.valve_closure("3", rule)
    tm = tsnet.simulation.Initializer(tm, 0, "DD")
    tm = tsnet.simulation.MOCSimulator(tm, "tmp_" + name, "steady")
    h = np.array(tm.get_node("3").head, dtype=float)
    t = np.array(tm.simulation_timestamps, dtype=float)
    n = min(len(h), len(t))
    out["cases"][name] = {"rule": rule, "h0": float(h[0]), "peak": float(h[:n].max()),
                          "t_peak": float(t[:n][h[:n].argmax()]), "n": int(n),
                          "t": t[:n:10].tolist(), "h": h[:n:10].tolist()}
    print(f"{name:22s} h0={h[0]:.3f} peak={h.max():.3f}  dH={h.max()-h[0]:.3f} m at t={t[:n][h[:n].argmax()]:.3f}  (len h={len(h)}, t={len(t)})")
json.dump(out, open(os.path.join(HERE, "reference", "tsnet_reference.json"), "w"))
