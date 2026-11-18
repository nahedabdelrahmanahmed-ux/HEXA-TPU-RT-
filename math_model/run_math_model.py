#!/usr/bin/env python3
"""
math_model/run_math_model.py
============================
Closed-form mathematical model of every layer of the platform. Each model is
(1) derived from the source code's formulas, (2) evaluated analytically, and
(3) VALIDATED against the real implementation (Monte-Carlo / exhaustive grid).

  M1 Reflex latency            L = (c0 + n*c1) / f
  M2 Aviation DAA trigger      fire  <=>  R < v * m(V)
  M3 Water pressure trigger    fire  <=>  drop >= max(d_min, r_th * dt)
  M4 URLLC link                log-normal + HARQ mixture: mean, quantile, SLA
  M5 Reflex-vs-network gap     distance travelled "blind"
  M6 Joukowsky veto            S = rho*a*Q/(A*1000) = 6*Q kPa
  M7 Bat forecaster (OLS)      Var(slope)=sigma^2/Sxx ; RUL=(T-y)/slope
  M8 Squid controller          decision regions as a function of severity
  M9 TPU roofline              T = T_ideal * max(1, N*b/W)

Run:  python3 math_model/run_math_model.py       (about 30 s)
Output: math_model/results/results.json and math_model_figures.png
"""
import json, math, os, sys, random
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "math_model", "results")
os.makedirs(OUT, exist_ok=True)
R = {}                                   # all results end up here


def Phi(x):                              # standard normal CDF
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# =====================================================================
# M1  Reflex latency
# =====================================================================
from reflex.reflex_kernel import ReflexKernel
from reflex.domains.aviation import AviationDAATrigger
from reflex.domains.water import WaterPressureTransientTrigger

c0, c1, f_mhz, D_ms = 12, 12, 1200.0, 1.0


def reflex_latency_us(n):                # L = (c0 + n c1) / f
    return (c0 + n * c1) / f_mhz         # cycles / MHz = microseconds


m1 = {}
for name, trig in (("aviation", AviationDAATrigger()), ("water", WaterPressureTransientTrigger())):
    k = ReflexKernel(trig)
    ev = k.evaluate({"range_m": 100., "closing_speed_mps": 10., "visibility_m": 1000.}
                    if name == "aviation" else
                    {"pressure_kpa": 500., "prior_pressure_kpa": 500., "dt_s": 0.01})
    m1[name] = {"n_channels": trig.channels_checked, "model_us": reflex_latency_us(trig.channels_checked),
                "code_us": ev.latency_ms * 1000, "deadline_margin_x": D_ms * 1000 / reflex_latency_us(trig.channels_checked)}
m1["max_channels_within_1ms"] = int((D_ms * 1e3 * f_mhz - c0) // c1)
R["M1_reflex_latency"] = m1

# =====================================================================
# M2  Aviation DAA trigger
# =====================================================================
m0, s_cap, Vref = 2.0, 3.0, 1500.0


def margin_s(V):                         # m(V) = m0 * min(max(1, Vref/V), s_cap)
    return m0 * min(max(1.0, Vref / max(V, 1.0)), s_cap)


def fires_daa(Rm, v, V):                 # R/v < m(V)  (v>0)
    return v > 0 and Rm / v < margin_s(V)


rng = random.Random(1)
trig = AviationDAATrigger()
agree = 0
N = 100000
for _ in range(N):
    Rm, v, V = rng.uniform(0, 3000), rng.uniform(-20, 120), rng.uniform(20, 10000)
    code = trig.evaluate({"range_m": Rm, "closing_speed_mps": v, "visibility_m": V}) is not None
    agree += (code == fires_daa(Rm, v, V))
tab = {}
for v in (30, 60, 100):
    tab[f"v={v}m/s"] = {f"V={V}m": round(v * margin_s(V), 1) for V in (3000, 1500, 1000, 500, 200)}
R["M2_daa"] = {"agreement_with_code_pct": 100.0 * agree / N, "samples": N,
               "V_saturation_m": Vref / s_cap, "critical_range_m_table": tab,
               "max_margin_s": m0 * s_cap}

# =====================================================================
# M3  Water pressure trigger
# =====================================================================
d_min, r_th = 15.0, 400.0
wt = WaterPressureTransientTrigger()


def fires_water(p, prior, dt):
    drop = prior - p
    return drop >= d_min and drop / max(dt, 1e-3) >= r_th


rng = random.Random(2)
agree = 0
for _ in range(100000):
    prior = rng.uniform(300, 700)
    p = prior - rng.uniform(-10, 120)
    dt = rng.uniform(1e-4, 0.2)
    code = wt.evaluate({"pressure_kpa": p, "prior_pressure_kpa": prior, "dt_s": dt}) is not None
    agree += (code == fires_water(p, prior, dt))
dt_star = d_min / r_th
eff_ok = all(abs(wt.effective_drop_threshold_kpa(dt) - max(d_min, r_th * max(dt, 1e-3))) < 1e-9
             for dt in np.linspace(1e-4, 0.2, 400))
R["M3_water_trigger"] = {"agreement_with_code_pct": agree / 1000.0, "crossover_dt_ms": dt_star * 1000,
                         "effective_threshold_method_matches_formula": bool(eff_ok),
                         "code_crossover_dt_ms": wt.crossover_dt_s * 1000,
                         "min_drop_at_dt_10ms_kpa": max(d_min, r_th * 0.01),
                         "effective_rate_at_dt_10ms_kpa_s": max(d_min, r_th * 0.01) / 0.01}

# =====================================================================
# M4  URLLC link: log-normal + HARQ retransmission mixture
# =====================================================================
from network.urllc_link import URLLCLink


class UrllcModel:
    def __init__(self, mean_ms, jitter_ms, p, pen=4.0, sla=10.0):
        self.m, self.s, self.p, self.pen, self.sla = mean_ms, jitter_ms / mean_ms, p, pen, sla
        hi = self.m * math.exp(self.s * 8)
        self.x = np.linspace(1e-4, hi, 40001)
        z = np.log(self.x / self.m) / self.s
        self.pdf = np.exp(-0.5 * z * z) / (self.x * self.s * math.sqrt(2 * math.pi))
        self.dx = self.x[1] - self.x[0]
        self.Fx = np.array([Phi(v) for v in z])

    def F1(self, t):
        return Phi(math.log(t / self.m) / self.s) if t > 0 else 0.0

    def G(self, t):                      # P(X1 + X2 <= t)  (convolution)
        if t <= 0:
            return 0.0
        mask = self.x < t
        Ft = np.array([self.F1(t - xi) for xi in self.x[mask][::20]])
        return float(np.sum(self.pdf[mask][::20] * Ft) * self.dx * 20)

    def cdf(self, t):                    # P(L <= t)
        return (1 - self.p) * self.F1(t) + self.p * self.G(t - self.pen)

    def mean(self):
        return self.m * math.exp(self.s ** 2 / 2) * (1 + self.p) + self.p * self.pen

    def quantile(self, q):
        lo, hi = 0.01, 200.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if self.cdf(mid) < q else (lo, mid)
        return 0.5 * (lo + hi)


def mc_urllc(mean_ms, jitter_ms, p, n=200000):
    link = URLLCLink(mean_latency_ms=mean_ms, jitter_sigma_ms=jitter_ms,
                     radio_link_failure_prob=p, seed=11)
    lats = np.array([link.transmit(None).latency_ms for _ in range(n)])
    return {"mean": float(lats.mean()), "p99": float(np.quantile(lats, 0.99)),
            "sla": float((lats <= 10.0).mean() * 100), "lats": lats}


m4, cdf_curves = {}, {}
for label, (mm, jj, pp) in {"nominal": (2.5, 0.6, 0.001), "degraded": (9.0, 3.0, 0.30)}.items():
    mod = UrllcModel(mm, jj, pp)
    mc = mc_urllc(mm, jj, pp)
    m4[label] = {"params": {"mean_ms": mm, "jitter_ms": jj, "p_retx": pp, "sigma_ln": jj / mm},
                 "model": {"mean_ms": mod.mean(), "p99_ms": mod.quantile(0.99),
                           "sla_pct": 100 * mod.cdf(10.0), "violation_prob": 1 - mod.cdf(10.0)},
                 "monte_carlo": {k: v for k, v in mc.items() if k != "lats"}}
    ts = np.linspace(0.5, 25, 120)
    cdf_curves[label] = (ts, [mod.cdf(t) for t in ts], np.sort(mc["lats"])[::2000], mod)
R["M4_urllc"] = m4

# =====================================================================
# M5  Reflex vs network: distance travelled "blind"
# =====================================================================
net = m4["nominal"]["model"]["mean_ms"]
m5 = {"network_mean_ms": net, "reflex_av_us": m1["aviation"]["model_us"],
      "ratio_net_over_reflex": net * 1000 / m1["aviation"]["model_us"], "blind_distance_m": {}}
for v in (30, 60, 100):
    m5["blind_distance_m"][f"v={v}"] = {"reflex_m": v * m1["aviation"]["model_us"] * 1e-6,
                                         "network_m": v * net * 1e-3}
m5["water_wave_blind_m"] = {"reflex_m": 1200 * m1["water"]["model_us"] * 1e-6, "network_m": 1200 * net * 1e-3}
R["M5_reflex_vs_network"] = m5

# =====================================================================
# M6  Joukowsky veto
# =====================================================================
from mec.domains.water_cognitive_engine import (WaterHammerHermitCrabEvaluator, WaterCandidateAction,
                                                DEFAULT_WATER_CANDIDATE_ACTIONS, WaterBHSCognitiveEngine)
rho, a, A, S_veto = 1000.0, 1200.0, 0.2, 500.0
k_surge = rho * a / (A * 1000.0 * 1000.0) * 1000.0 / 1000.0   # kPa per L/s  (= 6)


def surge(Q, tc=None, L=1000.0):         # instantaneous unless a closure time is given
    base = rho * a * (abs(Q) / 1000.0 / A) / 1000.0
    if tc is not None and tc > 0:
        base *= min(1.0, (2 * L / a) / tc)
    return base


hc = WaterHammerHermitCrabEvaluator()
mism = 0
for Q in np.arange(0, 150.01, 0.5):
    veto_code = bool(hc.assess(0.5, [WaterCandidateAction("x", -Q, 1, 1, 1)]).vetoed_actions)
    mism += (veto_code != (surge(Q) >= S_veto))
mism_tc = 0                                # closure-time grid
for Q in np.arange(10, 150.01, 10.0):
    for tc in (0.5, 1.0, 1.67, 3.0, 10.0, 30.0, 60.0):
        veto_code = bool(hc.assess(0.5, [WaterCandidateAction("x", -Q, 1, 1, 1, closure_time_s=tc)]).vetoed_actions)
        mism_tc += (veto_code != (surge(Q, tc) >= S_veto))
Qstar = S_veto / surge(1.0)
t_crit = 2 * 1000.0 / a
tc_min_100 = 600.0 * t_crit / S_veto       # shortest closure time that lets 100 L/s through
R["M6_joukowsky"] = {"kpa_per_lps": surge(1.0), "Q_star_lps": Qstar, "grid_mismatches": int(mism),
                     "closure_grid_mismatches": int(mism_tc),
                     "surge_by_action_kpa": {c.name: surge(c.flow_rate_change_lps, c.closure_time_s)
                                              for c in DEFAULT_WATER_CANDIDATE_ACTIONS},
                     "critical_closure_time_s": t_crit,
                     "min_safe_closure_time_for_100lps_s": tc_min_100,
                     "surge_100lps_30s_closure_kpa": surge(100, 30.0)}

# =====================================================================
# M7  Bat forecaster (OLS)
# =====================================================================
from mec.bhs_cognitive_engine import BatForecaster
n, T_fail, b_true, sig = 15, 150.0, 3.0, 2.0
Sxx = n * (n * n - 1) / 12.0
bat = BatForecaster(failure_threshold_kpa=T_fail, history_len=n)
rng = np.random.default_rng(5)
slopes, ruls, y_last_true = [], [], 40.0 + b_true * (n - 1)
for _ in range(20000):
    y = 40.0 + b_true * np.arange(n) + rng.normal(0, sig, n)
    est = bat.forecast(list(y))
    slopes.append(est.trend_slope_kpa_per_window)
    ruls.append(est.remaining_useful_life_windows if est.remaining_useful_life_windows is not None else np.nan)
slopes, ruls = np.array(slopes), np.array(ruls)
rul_true = (T_fail - y_last_true) / b_true
R["M7_bat"] = {"n": n, "Sxx": Sxx, "slope_std_theory": sig / math.sqrt(Sxx), "slope_std_mc": float(slopes.std()),
               "slope_mean_mc": float(slopes.mean()), "slope_true": b_true,
               "rul_true_windows": rul_true, "rul_mean_mc": float(np.nanmean(ruls)),
               "rul_std_mc": float(np.nanstd(ruls)),
               "rul_cv_delta_method": (sig / math.sqrt(Sxx)) / b_true,
               "rul_cv_full_delta_method": math.sqrt(
                   (sig * math.sqrt(1 / n + ((n - 1) / 2) ** 2 / Sxx) / (T_fail - y_last_true)) ** 2
                   + (sig / math.sqrt(Sxx) / b_true) ** 2
                   + 2 * (sig ** 2 * ((n - 1) / 2) / Sxx) / ((T_fail - y_last_true) * b_true)),
               "rul_cv_before_fix_raw_last_value": math.sqrt(
                   (sig / (T_fail - y_last_true)) ** 2 + (sig / math.sqrt(Sxx) / b_true) ** 2
                   + 2 * (sig ** 2 * ((n - 1) / 2) / Sxx) / ((T_fail - y_last_true) * b_true)),
               "rul_cv_mc": float(np.nanstd(ruls) / np.nanmean(ruls)),
               "min_detectable_slope_3sigma": 3 * sig / math.sqrt(Sxx),
               "min_detectable_slope_in_sigma_units": 3 / math.sqrt(Sxx)}

# =====================================================================
# M8  Squid controller: decision regions vs severity
# =====================================================================
from mec.bhs_cognitive_engine import SquidController
w0 = np.array([0.35, 0.45, 0.20]); w0 = w0 / w0.sum()


def weights(s):
    ws = w0[0] + 0.9 * s
    wp = max(0.02, w0[1] - 0.9 * s * 0.7)
    ww = max(0.02, w0[2] - 0.9 * s * 0.5)
    t = ws + wp + ww
    return ws / t, wp / t, ww / t


def choose(s, veto=True, gate=True):
    if gate and s < 0.1:                  # idle gate: no leak -> hold flow
        return "hold_flow"
    ws, wp, ww = weights(s)
    best, bs = None, -1e9
    for c in DEFAULT_WATER_CANDIDATE_ACTIONS:
        if veto and surge(c.flow_rate_change_lps, c.closure_time_s) >= S_veto:
            continue
        sc = ws * c.safety_benefit - wp * c.productivity_cost - ww * min(1.0, c.power_cost_w / 50.0)
        if sc > bs:
            best, bs = c.name, sc
    return best


eng = WaterBHSCognitiveEngine()
mism = 0
grid = np.arange(0, 1.0001, 0.001)
for s in grid:
    res = eng.evaluate([600.0] * 15, s)          # full engine decision (incl. idle gate)
    mism += (res.control.chosen_action != choose(s))


def regions(veto, gate=True):
    out, prev, start = [], None, 0.0
    for s in grid:
        c = choose(s, veto, gate)
        if c != prev:
            if prev is not None:
                out.append((prev, round(start, 3), round(s - 0.001, 3)))
            prev, start = c, s
    out.append((prev, round(start, 3), 1.0))
    return out


R["M8_squid"] = {"grid_points": len(grid), "mismatches_vs_code": int(mism),
                 "regions_with_veto": regions(True), "regions_without_veto": regions(False),
                 "regions_before_fix_no_gate_instant_closure_only": None}

# =====================================================================
# M9  TPU roofline
# =====================================================================
from benchmark import build_tiny_cnn
from config import Config
from simulator import HexaTPUSimulator

b_bytes = 64
m9 = {"peak_tops_model": 2 * 8 * 8 * 10 * 800e6 / 1e12, "peak_tops_code": Config(NUM_WORKERS=10).peak_tops(), "runs": {}}
for Nw in (10, 4):
    rows = {}
    for W in (64, 128, 256, 512, 1024):
        cfg = Config(NUM_WORKERS=Nw, AXI_WIDTH_BYTES=W, CACHE_ENABLED=False, DEADLINE_MEMORY_AWARE=True)
        lay = build_tiny_cnn(Nw, sparsity=0.30, tiles_per_worker=20, tiling_strategy="spatial")
        rep = HexaTPUSimulator(cfg).run(lay)
        rows[W] = rep.total_cycles
    T_ideal = rows[1024]
    for W in rows:
        pred = T_ideal * max(1.0, Nw * b_bytes / W)
        rows[W] = {"sim_cycles": rows[W], "model_cycles": pred, "error_pct": 100 * (rows[W] - pred) / pred}
    m9["runs"][f"N={Nw}"] = {"W_saturation_B_per_cycle": Nw * b_bytes, "T_ideal": T_ideal, "by_width": rows}
# below the cliff (W < 64 B/cycle): default model livelocks; credit accumulation fixes it
T10 = m9["runs"]["N=10"]["T_ideal"]
m9["below_cliff_W32"] = {}
for credit in (False, True):
    cfg = Config(NUM_WORKERS=10, AXI_WIDTH_BYTES=32, CACHE_ENABLED=False, DEADLINE_MEMORY_AWARE=True,
                 AXI_CREDIT_ACCUMULATION=credit)
    rep = HexaTPUSimulator(cfg).run(build_tiny_cnn(10, sparsity=0.30, tiles_per_worker=20, tiling_strategy="spatial"))
    pred = T10 * 20
    m9["below_cliff_W32"]["credit_on" if credit else "credit_off"] = {
        "sim_cycles": rep.total_cycles, "model_cycles": pred,
        "error_pct": 100 * (rep.total_cycles - pred) / pred,
        "tasks_completed": rep.tasks_completed, "deadline_misses": rep.deadline_misses}
R["M9_tpu_roofline"] = m9

json.dump(R, open(os.path.join(OUT, "results.json"), "w"), indent=2, default=float)

# =====================================================================
# Figures
# =====================================================================
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(2, 3, figsize=(16, 9))
# (1) DAA critical range
Vs = np.linspace(100, 3000, 300)
for v in (30, 60, 100):
    ax[0, 0].plot(Vs, [v * margin_s(V) for V in Vs], label=f"closing speed {v} m/s")
ax[0, 0].axvline(Vref / s_cap, ls=":", c="gray")
ax[0, 0].set(title="M2  DAA critical range R* = v·m(V)", xlabel="visibility V (m)", ylabel="R* (m)")
ax[0, 0].legend()
# (2) URLLC CDF
for label, c in (("nominal", "tab:blue"), ("degraded", "tab:red")):
    ts, cdf, emp, mod = cdf_curves[label]
    ax[0, 1].plot(ts, cdf, c=c, label=f"{label} (model)")
    ax[0, 1].plot(emp, np.linspace(0, 1, len(emp)), c=c, ls="--", label=f"{label} (Monte-Carlo)")
ax[0, 1].axvline(10, c="k", ls=":")
ax[0, 1].set(title="M4  URLLC latency CDF; SLA = 10 ms", xlabel="latency (ms)", ylabel="P(L ≤ t)", xlim=(0, 25))
ax[0, 1].legend(fontsize=8)
# (3) Joukowsky
Q = np.linspace(0, 150, 200)
ax[0, 2].plot(Q, [surge(q) for q in Q])
ax[0, 2].axhline(S_veto, c="r", ls="--"); ax[0, 2].axvline(Qstar, c="gray", ls=":")
for c in DEFAULT_WATER_CANDIDATE_ACTIONS:
    ax[0, 2].plot(abs(c.flow_rate_change_lps), surge(c.flow_rate_change_lps), "o")
    ax[0, 2].annotate(c.name.replace("_", " "), (abs(c.flow_rate_change_lps), surge(c.flow_rate_change_lps)),
                      fontsize=7, xytext=(3, 4), textcoords="offset points")
ax[0, 2].set(title="M6  Joukowsky surge S = 6·Q (kPa)", xlabel="|flow change| (L/s)", ylabel="surge (kPa)")
# (4) Bat
ax[1, 0].hist(slopes, bins=60, density=True, alpha=.6, label="Monte-Carlo (real BatForecaster)")
xs = np.linspace(slopes.min(), slopes.max(), 200)
sd = sig / math.sqrt(Sxx)
ax[1, 0].plot(xs, np.exp(-0.5 * ((xs - b_true) / sd) ** 2) / (sd * math.sqrt(2 * math.pi)), "r", label=f"theory N(3, {sd:.3f}²)")
ax[1, 0].set(title="M7  OLS slope distribution", xlabel="estimated slope (kPa/window)"); ax[1, 0].legend(fontsize=8)
# (5) Squid
cols = {}
for name, lo, hi in R["M8_squid"]["regions_with_veto"]:
    cols.setdefault(name, len(cols))
    ax[1, 1].barh(1, hi - lo, left=lo, color=f"C{cols[name]}", label=name)
for name, lo, hi in R["M8_squid"]["regions_without_veto"]:
    cols.setdefault(name, len(cols))
    ax[1, 1].barh(0, hi - lo, left=lo, color=f"C{cols[name]}", label=name if name not in [h.get_label() for h in ax[1, 1].patches] else None)
ax[1, 1].set(title="M8  Squid choice vs severity", xlabel="severity s", yticks=[0, 1], yticklabels=["no veto", "with veto"])
h, l = ax[1, 1].get_legend_handles_labels(); u = dict(zip(l, h)); ax[1, 1].legend(u.values(), u.keys(), fontsize=7, loc="lower right")
# (6) TPU
for Nw, c in (("N=10", "tab:blue"), ("N=4", "tab:green")):
    d = m9["runs"][Nw]["by_width"]
    Ws = sorted(d)
    ax[1, 2].loglog(Ws, [d[w]["model_cycles"] for w in Ws], c=c, label=f"{Nw} model")
    ax[1, 2].loglog(Ws, [d[w]["sim_cycles"] for w in Ws], "o", c=c, label=f"{Nw} simulator")
ax[1, 2].set(title="M9  TPU cycles vs AXI width", xlabel="AXI width (B/cycle)", ylabel="total cycles"); ax[1, 2].legend(fontsize=8)
plt.tight_layout()
plt.savefig(os.path.join(OUT, "math_model_figures.png"), dpi=130)
print(json.dumps(R, indent=1, default=float)[:6000])
print("\nWritten:", OUT)
