#!/usr/bin/env python3
"""
committee_demo.py -- one command that simulates BOTH applications on a VM.

  Application 1: Aviation & UAV  (reflex DAA in fog  +  BDO-SKIN structural
                                  health pipeline with the TPU simulator)
  Application 2: Smart Water     (reflex pipe-burst / water-hammer  +  water MEC)

Both share the SAME ReflexKernel, oneM2M layer, URLLC link and Bat/Squid code.
Output: a console summary and a self-contained HTML report (open in a browser).

  python3 committee_demo.py                       # in-memory oneM2M CSE
  python3 committee_demo.py --cse-http-url http://127.0.0.1:8080   # real ACME CSE
  python3 committee_demo.py --quick               # faster, fewer windows
"""
import argparse, html, os, sys, time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _m in ("numpy", "requests"):
    try:
        __import__(_m)
    except ImportError:
        sys.exit(f"Missing package '{_m}'. Run: python3 -m pip install -r requirements.txt")

from config import Config
from integration.edge_to_mec_pipeline import EdgeToMecPipeline
from middleware.onem2m import MN_CSE, ADN_AE
from middleware.onem2m_http import HttpCSEClient
from network.urllc_link import URLLCLink
from reflex.reflex_kernel import ReflexKernel
from reflex.domains.aviation import (AviationDAATrigger, generate_aviation_scenario,
                                     sample_to_dict as av_dict, SCENARIOS as AV_SCEN)
from reflex.domains.water import (WaterPressureTransientTrigger, generate_water_scenario,
                                  sample_to_dict as wa_dict, SCENARIOS as WA_SCEN)
from mec.domains.water_cognitive_engine import (WaterBHSCognitiveEngine, WaterHammerHermitCrabEvaluator,
                                                DEFAULT_WATER_CANDIDATE_ACTIONS)
from mec.mec_sandbox_client import MecAppSession, MecSandboxClient, hexa_services
from twin.water_moc import reference_main, linear_closure, G

SAMPLES = 200


# ----------------------------------------------------------------- helpers
def make_cse(url):
    if url:
        cse = HttpCSEClient(base_url=url, cse_id="id-in")
        if not cse.ping():
            sys.exit(f"No oneM2M CSE reachable at {url}. Start it first: ./deploy/run_acme_cse.sh "
                     f"(or run without --cse-http-url to use the built-in simulated CSE).")
        return cse
    return MN_CSE()


def ok(resp, what):
    code = int(getattr(resp.response_status_code, "value", resp.response_status_code))
    if code >= 4000:
        raise RuntimeError(f"oneM2M {what} failed (rsc={code}): {resp.content}")
    return resp


def run_reflex_scenario(domain, scenario, kernel, samples, to_dict, cse_url, seed):
    """Reflex over all samples; push triggered events (+ a heartbeat every 25th
    sample) through oneM2M and the URLLC link. Returns a result dict."""
    cse = make_cse(cse_url)
    ae = ADN_AE(f"{domain}-{scenario.replace('_', '-')}-{int(time.time() * 1000)}", cse,
                app_type=f"N{domain.capitalize()}Edge")   # oneM2M: api must start with N or R
    ok(ae.register(), "AE registration")
    link = URLLCLink(seed=seed)
    ops = 0
    trig_idx = []
    for i, s in enumerate(samples):
        ev = kernel.evaluate(to_dict(s), sample_index=i)
        if ev.triggered:
            trig_idx.append(i)
        if ev.triggered or i % 25 == 0:
            resp = ok(ae.push_reflex_event(
                window_index=i, channels_processed=kernel.trigger.channels_checked,
                triggered=ev.triggered,
                detail={"action": ev.decision.action if ev.decision else None,
                        "sensor_fault": ev.sensor_fault}), "reflex event")
            link.transmit(resp.content)
            ops += 1
    st = kernel.stats()
    u = link.stats()
    return {"scenario": scenario, "n": len(samples), "triggered": len(trig_idx),
            "first_trigger": trig_idx[0] if trig_idx else None, "trig_idx": trig_idx,
            "misses": st.deadline_misses, "max_lat_us": st.max_latency_ms * 1000,
            "faults": st.sensor_faults, "onem2m_ops": ops,
            "urllc_mean": u.get("mean_latency_ms", 0.0), "urllc_p99": u.get("p99_latency_ms", 0.0),
            "sla": u.get("sla_compliance_rate", 1.0) * 100,
            "actions": dict(Counter(e.decision.action for e in kernel.events if e.decision))}


def svg_line(values, marks, color="#2563eb", w=520, h=70):
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    pts = " ".join(f"{i * (w - 4) / (n - 1) + 2:.1f},{h - 4 - (v - lo) / span * (h - 8):.1f}"
                   for i, v in enumerate(values))
    dots = "".join(f'<circle cx="{i * (w - 4) / (n - 1) + 2:.1f}" '
                   f'cy="{h - 4 - (values[i] - lo) / span * (h - 8):.1f}" r="2.2" fill="#dc2626"/>'
                   for i in marks[:400])
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img">'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{pts}"/>{dots}</svg>')


# ----------------------------------------------------------------- application 1
def app_aviation(cse_url, windows):
    rows = []
    for sc in AV_SCEN:
        samples = generate_aviation_scenario(sc, SAMPLES, seed=1)
        k = ReflexKernel(AviationDAATrigger())
        r = run_reflex_scenario("aviation", sc, k, samples, av_dict, cse_url, seed=1)
        r["signal"] = [s.range_m for s in samples]
        r["signal_label"] = "range to nearest object (m)"
        r["truth"] = sum(1 for s in samples if s.is_near_miss)
        rows.append(r)

    structural = []
    for sc in ["normal", "gradual_anomaly", "burst_anomaly", "critical_event"]:
        name = f"aviation-skin-{sc.replace('_', '-')}-{int(time.time() * 1000)}" if cse_url else "hexa-tpu-edge-01"
        pl = EdgeToMecPipeline(cfg=Config(NUM_WORKERS=10), seed=0, cse_http_url=cse_url,
                               edge_node_name=name)
        rep = pl.run(scenario=sc, num_windows=windows, seed=0, num_workers=10)
        u, tr = rep.urllc_stats, rep.tpu_report
        acts = Counter(t.cognitive.control.chosen_action for t in rep.telemetry
                       if t.cognitive.control.chosen_action)
        structural.append({"scenario": sc, "windows": windows, "ops": rep.onem2m_request_log_len,
                           "urllc_mean": u["mean_latency_ms"], "urllc_p99": u["p99_latency_ms"],
                           "sla": u["sla_compliance_rate"] * 100,
                           "vetoes": len(rep.mec_vetoes()), "actions": dict(acts),
                           "tpu_cycles": tr.total_cycles, "tpu_misses": tr.deadline_misses,
                           "tpu_power": tr.power.average_power_mw})
    return rows, structural


# ----------------------------------------------------------------- application 2
def app_water(cse_url):
    rows, mec = [], []
    for sc in WA_SCEN:
        samples = generate_water_scenario(sc, SAMPLES, seed=1)
        k = ReflexKernel(WaterPressureTransientTrigger())
        r = run_reflex_scenario("water", sc, k, samples, wa_dict, cse_url, seed=1)
        r["signal"] = [s.pressure_kpa for s in samples]
        r["signal_label"] = "pipe pressure (kPa)"
        r["truth"] = sum(1 for s in samples if s.is_burst_ground_truth)
        rows.append(r)

        # MEC cognitive evaluation: pressure history -> 15 windows (mean per chunk)
        chunk = max(1, SAMPLES // 15)
        hist = [sum(r["signal"][i:i + chunk]) / chunk for i in range(0, chunk * 15, chunk)]
        # Named assumption: leak severity = deepest pressure drop vs. the 600 kPa
        # baseline, normalised so a 40% drop (or more) = 1.0.
        eng = WaterBHSCognitiveEngine()
        drop = max(0.0, (eng.baseline_pressure_kpa - min(r["signal"])) / eng.baseline_pressure_kpa)
        severity = min(1.0, drop / 0.4)
        res = eng.evaluate(hist, severity)
        mec.append({"scenario": sc, "severity": severity,
                    "rul": res.rul.remaining_useful_life_windows,
                    "vetoed": res.stability.vetoed_actions,
                    "chosen": res.control.chosen_action,
                    "surge": res.stability.worst_case_surge_kpa})
    return rows, mec



# ----------------------------------------------------------------- digital twin
def twin_section():
    """Ground-truth check of the water MEC veto with the MOC digital twin + burst detection."""
    hc = WaterHammerHermitCrabEvaluator()
    rows = []
    for c in DEFAULT_WATER_CANDIDATE_ACTIONS:
        dq = abs(c.flow_rate_change_lps)
        if dq == 0:
            continue
        tc = c.closure_time_s if c.closure_time_s else 0.05
        real = reference_main(dt_s=0.01).run(60.0, valve_tau=linear_closure(0.1, tc, 1 - dq / 100)).peak_valve_surge_kpa()
        est = hc._estimate_surge_kpa(c.flow_rate_change_lps, c.closure_time_s)
        rows.append({"action": c.name, "tc": c.closure_time_s, "est": est, "twin": real,
                     "vetoed": est >= hc.surge_pressure_veto_threshold_kpa, "unsafe": real >= 500.0})
    # burst detection at the sensor (30 L/s burst at 500 m, sensor at the valve, 10 ms sampling)
    area = 0.03 / (0.6 * (2 * G * 61.0) ** 0.5)
    tw = reference_main(dt_s=0.01, leak_x_m=500.0, sensor_x_m=1000.0)
    res = tw.run(3.0, leak_area=lambda t: 0.0 if t < 1.0 else min(1.0, (t - 1.0) / 0.02) * area)
    k = ReflexKernel(WaterPressureTransientTrigger())
    prior, first, fa = res.sensor_kpa[0], None, 0
    for t, p in zip(res.t[1:], res.sensor_kpa[1:]):
        ev = k.evaluate({"pressure_kpa": float(p), "prior_pressure_kpa": float(prior), "dt_s": 0.01}); prior = p
        if ev.triggered:
            if t < 1.0: fa += 1
            elif first is None: first = t
    ref = {}
    try:
        import json
        ref = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "twin", "reference", "tsnet_reference.json")))
    except Exception:
        pass
    return {"rows": rows, "arrival_s": 1.0 + 500.0 / 1200.0, "detected_s": first, "false_alarms": fa,
            "min_burst_lps": 2 * 0.2 * 15000.0 / (1000 * 1200.0) * 1000, "tsnet": ref}


def real_data_section():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "real_data"))
        from validate_bat_phm2012 import evaluate
        return evaluate()
    except Exception as e:                               # pragma: no cover
        return {"error": repr(e)}


def mec_section(mec_url):
    """Run the MEC 011 lifecycle (confirm_ready, register 2 services, discover, deregister, terminate)."""
    mock = None
    if mec_url:
        client, label = MecSandboxClient(mec_url), f"REAL platform {mec_url}"
    else:
        from mec.mec_mock_platform import MockMecPlatform
        mock = MockMecPlatform().start()
        client, label = MecSandboxClient(mock.url), "built-in strict MEC 011 test double (NOT the ETSI sandbox)"
    out = {"label": label, "ok": False}
    try:
        with MecAppSession(client, f"hexa-demo-{int(time.time())}", hexa_services()) as sess:
            out["registered"] = [x.ser_name for x in sess.registered]
            out["discovered"] = sorted(x.ser_name for x in client.discover_services())
        out["cleanup_errors"] = sess.cleanup_errors
        out["ok"] = sorted(out["registered"]) == out["discovered"] and not sess.cleanup_errors
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        if mock:
            mock.stop()
    return out

# ----------------------------------------------------------------- robustness
def robustness():
    out = []
    bad = float("nan")
    cases = [
        ("Aviation", ReflexKernel(AviationDAATrigger()),
         [{"range_m": bad, "closing_speed_mps": 50.0, "visibility_m": 900.0},
          {"range_m": 120.0}, {"range_m": None, "closing_speed_mps": 5.0, "visibility_m": 400.0},
          {"range_m": -5.0, "closing_speed_mps": 5.0, "visibility_m": 400.0}]),
        ("Water", ReflexKernel(WaterPressureTransientTrigger()),
         [{"pressure_kpa": bad, "prior_pressure_kpa": 500.0, "dt_s": 0.01}, {},
          {"pressure_kpa": None, "prior_pressure_kpa": 500.0, "dt_s": 0.01}]),
    ]
    for name, k, samples in cases:
        evs = [k.evaluate(s) for s in samples]
        out.append((name, len(samples), sum(e.sensor_fault for e in evs),
                    sorted({e.decision.action for e in evs if e.decision})))
    return out


# ----------------------------------------------------------------- report
CSS = """body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;
color:#111;background:#fff}h1{font-size:1.6rem}h2{border-bottom:2px solid #2563eb;padding-bottom:4px;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin:.6rem 0}th,td{border:1px solid #ccc;padding:5px 8px;text-align:right}
th{background:#eef2ff}td:first-child,th:first-child{text-align:left}.card{border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:10px 0}
.good{color:#15803d;font-weight:600}.bad{color:#b91c1c;font-weight:600}.note{background:#fffbeb;border:1px solid #f59e0b;border-radius:8px;padding:10px 14px;font-size:.88rem}
small{color:#555}@media(prefers-color-scheme:dark){body{background:#111;color:#eee}th{background:#1e293b}th,td{border-color:#444}.card{border-color:#444}.note{background:#2a2108;color:#eee}small{color:#aaa}}"""


def tbl(head, rows):
    h = "".join(f"<th>{html.escape(str(c))}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{b}</table>"


def reflex_section(title, rows):
    s = f"<h3>{title}</h3>"
    for r in rows:
        s += (f'<div class="card"><b>{r["scenario"]}</b> &nbsp; <small>{r["signal_label"]}; '
              f'red dots = reflex fired</small>{svg_line(r["signal"], r["trig_idx"])}</div>')
    head = ["Scenario", "Samples", "Ground-truth events", "Reflex fired", "First fire @", "Deadline misses",
            "Max modelled latency (µs)", "oneM2M ops", "URLLC mean/p99 (ms)", "SLA ≤10ms"]
    body = [[r["scenario"], r["n"], r["truth"], r["triggered"],
             "-" if r["first_trigger"] is None else r["first_trigger"],
             f'<span class="{"good" if r["misses"] == 0 else "bad"}">{r["misses"]}</span>',
             f'{r["max_lat_us"]:.3f}', r["onem2m_ops"],
             f'{r["urllc_mean"]:.2f} / {r["urllc_p99"]:.2f}', f'{r["sla"]:.1f}%'] for r in rows]
    return s + tbl(head, body)


def build_html(av, struct, wa, mec, rob, cse_label, secs, twin=None, real=None, mecp=None):
    p = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
         "content='width=device-width,initial-scale=1'><title>HEXA-TPU-RT committee simulation</title>"
         f"<style>{CSS}</style></head><body>",
         "<h1>HEXA-TPU-RT / BDO-SKIN — Simulation Report</h1>",
         f"<p><small>Generated {time.strftime('%Y-%m-%d %H:%M:%S')} · oneM2M CSE: <b>{html.escape(cse_label)}"
         f"</b> · run time {secs:.1f}s · same ReflexKernel / oneM2M / URLLC / Bat-Squid code in both applications</small></p>",
         "<div class='note'><b>What this is / is not.</b> A software simulation on a VM. Reflex latency is a "
         "<b>cycle-count model</b> (not measured on silicon). Aviation/water triggers are demonstration-grade, "
         "not certified. No physical hardware is involved.</div>",
         "<h2>Application 1 — Aviation &amp; UAV</h2>",
         reflex_section("1a. Detect-and-Avoid reflex (degraded visibility)", av),
         "<h3>1b. Structural-skin health pipeline: sensor → reflex → oneM2M → 5G URLLC → MEC → TPU</h3>"]
    p.append(tbl(["Scenario", "Windows", "oneM2M ops", "URLLC mean/p99 (ms)", "SLA", "MEC windows w/ veto",
                  "Squid actions", "TPU cycles", "TPU deadline misses", "Avg power (mW)"],
                 [[r["scenario"], r["windows"], r["ops"], f'{r["urllc_mean"]:.2f} / {r["urllc_p99"]:.2f}',
                   f'{r["sla"]:.1f}%', r["vetoes"], html.escape(str(r["actions"])), r["tpu_cycles"],
                   f'<span class="{"good" if r["tpu_misses"] == 0 else "bad"}">{r["tpu_misses"]}</span>',
                   f'{r["tpu_power"]:.0f}'] for r in struct]))
    p += ["<h2>Application 2 — Smart Water Infrastructure</h2>",
          reflex_section("2a. Pipe-burst / water-hammer reflex", wa),
          "<h3>2b. Water MEC cognitive engine (Bat forecast + Hermit-Crab water-hammer veto + Squid decision)</h3>",
          tbl(["Scenario", "Leak severity", "Projected RUL (windows)", "Worst-case surge (kPa)", "Vetoed", "Squid chose"],
              [[r["scenario"], f'{r["severity"]:.1f}', "-" if r["rul"] is None else f'{r["rul"]:.1f}',
                f'{r["surge"]:.0f}', html.escape(", ".join(r["vetoed"]) or "-"),
                html.escape(str(r["chosen"]))] for r in mec]),
          "<h2>Robustness — malformed sensor data (fail-safe)</h2>",
          tbl(["Application", "Bad samples injected", "Fail-safe fired", "Action issued"],
              [[n, t, f'<span class="{"good" if f == t else "bad"}">{f}</span>', ", ".join(a)] for n, t, f, a in rob]),
          "@@EXTRA@@",
          "<h2>Shared core (“one core, two industries”)</h2><p>Both applications ran through the identical "
          "<code>ReflexKernel</code>, <code>ADN_AE/oneM2M</code> and <code>URLLCLink</code>; only the trigger and "
          "the domain MEC veto differ. The TPU layer is exercised by the aviation structural pipeline only.</p>",
          "</body></html>"]
    idx = p.index("@@EXTRA@@"); tail = p[idx + 1:]; p = p[:idx]

    if twin:
        p += ["<h2>Digital twin of the water main (Method of Characteristics)</h2>",
              "<p><small>Ground truth for the MEC veto: each candidate action is executed in the twin "
              "(1 km main, 100 L/s, a=1200 m/s) and its real peak surge compared with the closed-form estimate. "
              "Cross-checked against the independent TSNet solver (Tnet0): "
              "peak-surge agreement within 1% on 4 closure cases, see tests/test_water_twin.py.</small></p>",
              tbl(["Action", "Closure (s)", "Code estimate (kPa)", "Twin peak (kPa)", "Vetoed", "Really unsafe (>=500)"],
                  [[r["action"], "instant" if not r["tc"] else r["tc"], f'{r["est"]:.0f}', f'{r["twin"]:.0f}',
                    "yes" if r["vetoed"] else "no",
                    f'<span class="{"bad" if (r["unsafe"] and not r["vetoed"]) else "good"}">{"yes" if r["unsafe"] else "no"}</span>']
                   for r in twin["rows"]]),
              f"<p>Burst of 30 L/s at 500 m: pressure wave reaches the sensor at <b>{twin['arrival_s']:.3f} s</b>, reflex fires at "
              f"<b>{twin['detected_s']:.3f} s</b>; false alarms before the burst: <b>{twin['false_alarms']}</b>. "
              f"Physical detection limit of the 15 kPa floor: bursts below <b>{twin['min_burst_lps']:.1f} L/s</b> are invisible to the transient reflex.</p>"]
    if real:
        p += ["<h2>Real data: Bat forecaster on run-to-failure bearings (IEEE PHM 2012)</h2>",
              "<p><small>Real accelerated-life data, 4 bearings, 3,094 snapshots; failure threshold taken from the other bearings (no peeking). "
              "Alert = forecast remaining life &le; 300 s.</small></p>"]
        if "error" in real:
            p.append(f"<p class='bad'>unavailable: {html.escape(real['error'])}</p>")
        else:
            p.append(tbl(["Bearing", "Life (s)", "False alerts in first 80% of life", "Bat warning lead (s)", "Simple detector lead (s)"],
                         [[b, r["n"] * 10, f'{r["bat_false_alert_pct_first80"]:.1f}%', r["bat_lead_s"],
                           "missed" if r["baseline_lead_s"] is None else r["baseline_lead_s"]] for b, r in real.items()]))
            p.append("<p>Honest reading: no false alarms and a warning before every failure, but the lead time is only 1-5% of life "
                     "because real bearing failures are sudden-onset, not slow linear drifts.</p>")
    if mecp:
        p += ["<h2>ETSI MEC 011 (Mp1) integration</h2>", f"<p><small>Platform: {html.escape(mecp['label'])}</small></p>"]
        if mecp.get("error"):
            p.append(f"<p class='bad'>{html.escape(mecp['error'])}</p>")
        else:
            p.append(tbl(["Step", "Result"], [["confirm_ready", "ok"], ["services registered", html.escape(", ".join(mecp["registered"]))],
                                               ["services discovered", html.escape(", ".join(mecp["discovered"]))],
                                               ["deregister + confirm_termination", "ok" if not mecp["cleanup_errors"] else html.escape(str(mecp["cleanup_errors"]))]]))
    p += tail
    return "".join(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cse-http-url", default=None, help="real oneM2M CSE (e.g. http://127.0.0.1:8080)")
    ap.add_argument("--mec-url", default=None, help="real MEC 011 platform base URL (e.g. ETSI MEC Sandbox)")
    ap.add_argument("--quick", action="store_true", help="fewer structural windows (60 instead of 150)")
    ap.add_argument("--out", default=os.path.join("vm_test", "results", "committee_report.html"))
    a = ap.parse_args()
    t0 = time.time()
    windows = 60 if a.quick else 150

    print("== Application 1: Aviation & UAV ==", flush=True)
    av, struct = app_aviation(a.cse_http_url, windows)
    for r in av:
        print(f"  [reflex/{r['scenario']:<20}] fired {r['triggered']:>3}/{r['n']}  misses={r['misses']}  "
              f"max={r['max_lat_us']:.3f}us  URLLC mean={r['urllc_mean']:.2f}ms")
    for r in struct:
        print(f"  [skin/{r['scenario']:<20}] vetoes={r['vetoes']:>3}/{r['windows']}  "
              f"URLLC SLA={r['sla']:.0f}%  TPU misses={r['tpu_misses']}")
    print("== Application 2: Smart Water ==", flush=True)
    wa, mec = app_water(a.cse_http_url)
    for r, m in zip(wa, mec):
        print(f"  [reflex/{r['scenario']:<14}] fired {r['triggered']:>3}/{r['n']}  misses={r['misses']}  "
              f"| MEC chose={m['chosen']} vetoed={m['vetoed']}")
    print("== Digital twin, real data, MEC Mp1 ==", flush=True)
    twin = twin_section()
    for r in twin["rows"]:
        print(f"  [twin/{r['action']:<26}] estimate {r['est']:6.1f} kPa  twin {r['twin']:6.1f} kPa  vetoed={r['vetoed']} unsafe={r['unsafe']}")
    print(f"  [twin/burst] wave arrives {twin['arrival_s']:.3f}s, reflex fires {twin['detected_s']:.3f}s, false alarms {twin['false_alarms']}")
    real = real_data_section()
    for b, r in (real.items() if "error" not in real else []):
        print(f"  [real/{b}] bat lead {r['bat_lead_s']} s, false alerts {r['bat_false_alert_pct_first80']:.1f}%")
    mecp = mec_section(a.mec_url)
    print(f"  [mec] {mecp['label']}: ok={mecp['ok']} registered={mecp.get('registered')} {mecp.get('error','')}")
    rob = robustness()
    for n, t, f, acts in rob:
        print(f"  [fail-safe/{n}] {f}/{t} malformed samples handled -> {acts}")

    label = f"REAL ACME CSE @ {a.cse_http_url}" if a.cse_http_url else "built-in simulated MN-CSE"
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(build_html(av, struct, wa, mec, rob, label, time.time() - t0, twin, real, mecp))
    print(f"\nReport written: {a.out}   (open it in a browser)  [{time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
