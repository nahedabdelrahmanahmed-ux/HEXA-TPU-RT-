"""
vm_test/chaos_tests.py -- failure-injection tests (closes the "no chaos testing"
limitation of Technical_Report.docx section 7). Needs no hardware.

Each test prints PASS / FAIL / INFO. FAIL = a safety/robustness property was
violated. INFO = observed behaviour that you must review (not auto-judged).
Exit code 1 if any FAIL.
"""
import math, os, statistics, subprocess, sys, threading, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from integration.edge_to_mec_pipeline import EdgeToMecPipeline
from network.urllc_link import URLLCLink
from reflex.reflex_kernel import ReflexKernel
from reflex.domains.aviation import AviationDAATrigger
from reflex.domains.water import WaterPressureTransientTrigger
from mec.domains.water_cognitive_engine import WaterBHSCognitiveEngine
from models.sensor_events import generate_scenario

RESULTS = []
def rec(status, name, detail=""):
    RESULTS.append((status, name, detail))
    print(f"[{status:4}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)

# ---------------------------------------------------------------- C1: CSE outage
def start_cse(port=8081):
    d = f"/tmp/acme_chaos_{port}"
    os.makedirs(d, exist_ok=True)
    ini = os.path.join(d, "acme.ini")
    if not os.path.exists(ini):
        import acmecse
        src = os.path.join(os.path.dirname(acmecse.__file__), "init", "acme.ini.default")
        c = open(src).read()
        basic = (f"\n[basic.config]\nadminID=CAdmin\ncseHost=127.0.0.1\ncseID=id-in\ncseName=cse-in\n"
                 f"cseType=IN\ndatabaseType=memory\nhttpPort={port}\n\n")
        open(ini, "w").write(c.replace("[cse]", basic + "[cse]", 1))
    p = subprocess.Popen([sys.executable, "-m", "acmecse", "--headless", "--no-coap", "--no-mqtt",
                          "--no-ws", "--no-remote-cse", "--db-type", "memory", "--http-port", str(port),
                          "--log-level", "warn", "-dir", d],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(40):
        try:
            urllib.request.urlopen(urllib.request.Request(url + "/id-in", headers={
                "X-M2M-Origin": "CAdmin", "X-M2M-RI": "1", "X-M2M-RVI": "3"}), timeout=2)
            return p, url
        except Exception:
            time.sleep(1)
    p.kill(); raise RuntimeError("ACME did not start")

def test_cse_outage():
    try:
        proc, url = start_cse()
    except Exception as e:
        rec("INFO", "C1 CSE outage mid-run", f"skipped, ACME unavailable: {e}"); return
    try:
        pl = EdgeToMecPipeline(cfg=Config(NUM_WORKERS=4), cse_http_url=url,
                               edge_node_name=f"chaos-{int(time.time())}")
        wins = generate_scenario("normal", 6, seed=1)
        for w in wins[:3]:
            pl._run_window_through_standards_stack(w)
        proc.kill(); proc.wait()
        t0 = time.time(); err = None; resp = None
        try:
            resp = pl.ae.push_sensor_window(wins[3])      # raw transport call
            pl._run_window_through_standards_stack(wins[4])  # what the pipeline does
        except Exception as e:
            err = e
        dt = time.time() - t0
        rsc = getattr(resp, "response_status_code", None)
        rsc_v = getattr(rsc, "value", rsc)
        failed_flag = rsc_v is not None and int(rsc_v) >= 4000
        if dt > 30:
            rec("FAIL", "C1 CSE outage mid-run", f"hung {dt:.0f}s")
        elif err is not None:
            rec("PASS", "C1 CSE outage mid-run", f"pipeline raised {type(err).__name__} in {dt:.2f}s")
        elif failed_flag:
            rec("FAIL", "C1 CSE outage mid-run",
                f"transport returned error rsc={rsc_v} but the pipeline did NOT raise or flag it "
                f"-> windows silently lost (pipeline ignores ResponsePrimitive.rsc)")
        else:
            rec("FAIL", "C1 CSE outage mid-run", f"unexpected: no error, rsc={rsc_v}")
    finally:
        proc.kill()

# ---------------------------------------------------------------- C2: malformed sensor input
def test_malformed_reflex():
    bad_av = [{}, {"range_m": 100}, {"range_m": float("nan"), "closing_speed_mps": 50, "visibility_m": 1000},
              {"range_m": -5, "closing_speed_mps": 50, "visibility_m": 1000},
              {"range_m": 100, "closing_speed_mps": float("inf"), "visibility_m": 0},
              {"range_m": None, "closing_speed_mps": 10, "visibility_m": 500}]
    k = ReflexKernel(AviationDAATrigger())
    ok_av = [k.evaluate(x).sensor_fault for x in bad_av]
    rec("PASS" if all(ok_av) else "FAIL", "C2a aviation reflex: malformed samples -> fail-safe",
        f"{sum(ok_av)}/{len(bad_av)} bad samples fired fail_safe_hold")
    t0 = time.time(); k.evaluate(bad_av[2])
    rec("PASS" if time.time() - t0 < 1 else "FAIL", "C2b NaN sample returns promptly")

    kw = ReflexKernel(WaterPressureTransientTrigger())
    ok = {"prior_pressure_kpa": 500.0, "dt_s": 0.1}
    bad_w = [{}, {**ok, "pressure_kpa": float("nan")}, {**ok, "pressure_kpa": None}, {"pressure_kpa": 1.0}]
    ok_w = [kw.evaluate(x).sensor_fault for x in bad_w]
    rec("PASS" if all(ok_w) else "FAIL", "C2c water reflex: malformed samples -> fail-safe",
        f"{sum(ok_w)}/{len(bad_w)} bad samples fired fail_safe_hold")

def test_malformed_mec():
    eng = WaterBHSCognitiveEngine()
    out = []
    for hist in ([], [500.0], [float("nan")] * 5, [500, 400, 300, 200, 100, 0, -100]):
        try:
            r = eng.evaluate(hist, 0.5); out.append(f"ok:{r.control.chosen.name if hasattr(r.control,'chosen') else 'decided'}")
        except Exception as e:
            out.append(type(e).__name__)
    rec("INFO", "C2d water MEC on degenerate pressure history", str(out))

# ---------------------------------------------------------------- C3: degraded network
def test_degraded_urllc():
    link = URLLCLink(mean_latency_ms=9.0, jitter_sigma_ms=3.0, radio_link_failure_prob=0.3, seed=7)
    pl = EdgeToMecPipeline(cfg=Config(NUM_WORKERS=4), urllc_link=link)
    try:
        rep = pl.run(scenario="critical_event", num_windows=60, seed=3, num_workers=4)
    except Exception as e:
        rec("FAIL", "C3 degraded URLLC", f"pipeline crashed: {type(e).__name__}: {e}"); return
    viol = len(rep.urllc_sla_violations())
    rec("PASS", "C3 degraded URLLC (30% HARQ failures, 9ms mean)",
        f"pipeline completed, {viol}/60 windows violated the 10ms SLA (expected > 0)")
    if viol == 0:
        rec("FAIL", "C3b SLA monitor sensitivity", "no violations under heavy degradation -- monitor may be blind")

# ---------------------------------------------------------------- C4: CPU starvation
def test_cpu_starvation():
    k = ReflexKernel(AviationDAATrigger())
    s = {"range_m": 400.0, "closing_speed_mps": 60.0, "visibility_m": 300.0}
    def measure(n=20000):
        v = []
        for _ in range(n):
            t = time.perf_counter(); k.evaluate(s); v.append((time.perf_counter() - t) * 1e6)
        v.sort(); return statistics.mean(v), v[int(.99 * n)], v[-1]
    base = measure()
    stop = False
    def burn():
        while not stop: math.sqrt(12345.678) ** 3
    ts = [threading.Thread(target=burn, daemon=True) for _ in range((os.cpu_count() or 2) * 2)]
    [t.start() for t in ts]; loaded = measure(); stop = True
    rec("INFO", "C4 reflex WALL-CLOCK latency (python, us) idle vs CPU-starved",
        f"idle mean/p99/max={base[0]:.1f}/{base[1]:.1f}/{base[2]:.0f}  loaded={loaded[0]:.1f}/{loaded[1]:.1f}/{loaded[2]:.0f}  "
        f"(1000us deadline; this is NOT the modelled 0.04us -- see README_VM)")
    rec("PASS" if loaded[1] < 1000 else "FAIL", "C4b p99 wall-clock under load < 1ms deadline")

if __name__ == "__main__":
    for t in (test_cse_outage, test_malformed_reflex, test_malformed_mec, test_degraded_urllc, test_cpu_starvation):
        try: t()
        except Exception as e: rec("FAIL", t.__name__, f"harness error {type(e).__name__}: {e}")
    f = sum(1 for r in RESULTS if r[0] == "FAIL")
    print(f"\nchaos summary: PASS={sum(r[0]=='PASS' for r in RESULTS)} FAIL={f} INFO={sum(r[0]=='INFO' for r in RESULTS)}")
    sys.exit(1 if f else 0)
