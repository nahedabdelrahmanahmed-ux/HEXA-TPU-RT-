#!/usr/bin/env python3
"""
vm_test/live_feeder.py -- keeps ONE edge node pushing events into a oneM2M CSE so they can be watched live
in the Mobius4 TUI Resource Browser (select the container, press `w`; counters such as cni change as events arrive).

  python3 vm_test/live_feeder.py --cse-http-url http://127.0.0.1:8080 --seconds 120 [--domain water|aviation]

It replays a scenario through the real ReflexKernel in (accelerated) real time and pushes a reflex event
for every triggered sample plus a heartbeat every 25th sample, exactly like committee_demo.py.
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from middleware.onem2m import ADN_AE, MN_CSE
from middleware.onem2m_http import HttpCSEClient
from reflex.reflex_kernel import ReflexKernel
from reflex.domains.water import WaterPressureTransientTrigger, generate_water_scenario, sample_to_dict as wdict
from reflex.domains.aviation import AviationDAATrigger, generate_aviation_scenario, sample_to_dict as adict


def run_feeder(cse, domain="water", seconds=60.0, rate_hz=20.0, heartbeat_every=25, name=None, seed=1, sleep=time.sleep):
    """Returns (ae, pushed_events, triggered). `cse` is any object with handle_primitive (in-memory or HTTP)."""
    if domain == "water":
        kernel, to_dict, app = ReflexKernel(WaterPressureTransientTrigger()), wdict, "NWaterEdge"
        scen = lambda n: generate_water_scenario("sudden_burst", n, seed=seed)
    else:
        kernel, to_dict, app = ReflexKernel(AviationDAATrigger()), adict, "NAviationEdge"
        scen = lambda n: generate_aviation_scenario("dense_fog_approach", n, seed=seed)
    ae = ADN_AE(name or f"{domain}-live-{int(time.time() * 1000)}", cse, app_type=app)
    r = ae.register()
    if int(getattr(r.response_status_code, "value", r.response_status_code)) >= 4000:
        raise RuntimeError(f"AE registration failed: {r.content}")
    n_total = max(1, int(seconds * rate_hz)); pushed = trig = i = 0
    samples = scen(200)
    while i < n_total:
        s = samples[i % len(samples)]
        ev = kernel.evaluate(to_dict(s), sample_index=i)
        if ev.triggered or i % heartbeat_every == 0:
            resp = ae.push_reflex_event(window_index=i, channels_processed=kernel.trigger.channels_checked,
                                        triggered=ev.triggered,
                                        detail={"action": ev.decision.action if ev.decision else None})
            if int(getattr(resp.response_status_code, "value", resp.response_status_code)) >= 4000:
                raise RuntimeError(f"push failed: {resp.content}")
            pushed += 1; trig += int(ev.triggered)
        i += 1
        sleep(1.0 / rate_hz)
    return ae, pushed, trig


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cse-http-url", default=None, help="real CSE; omit for the in-memory one (nothing to watch)")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--domain", choices=("water", "aviation"), default="water")
    a = ap.parse_args()
    cse = HttpCSEClient(base_url=a.cse_http_url, cse_id="id-in") if a.cse_http_url else MN_CSE()
    if a.cse_http_url and not cse.ping():
        sys.exit(f"No oneM2M CSE reachable at {a.cse_http_url}")
    print(f"feeding {a.domain} events for {a.seconds:.0f}s ... (Ctrl-C to stop)", flush=True)
    ae, pushed, trig = run_feeder(cse, a.domain, a.seconds)
    print(f"done: {pushed} events pushed ({trig} triggered)")
