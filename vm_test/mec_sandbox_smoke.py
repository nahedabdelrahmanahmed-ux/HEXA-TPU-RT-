#!/usr/bin/env python3
"""
Smoke test of the MEC 011 client against a REAL platform (e.g. the ETSI MEC Sandbox) or the local mock.

  python3 vm_test/mec_sandbox_smoke.py https://<sandbox-host>/<sandbox-name> [--token T] [--app-id ID]
  python3 vm_test/mec_sandbox_smoke.py --mock          # self-test against the built-in strict test double

Walk through (each step prints PASS / FAIL / SKIP):  transports -> timing -> confirm_ready -> register 2
services -> discover -> update -> deregister -> confirm_termination.
Exit code 0 only if no step FAILED. Attach the output to the competition report as interoperability evidence.
In the ETSI MEC Sandbox the app-instance id is one you obtain/assign for your external application.
"""
import argparse, os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mec.mec_sandbox_client import MecSandboxClient, MecSandboxError, hexa_services

RESULTS = []


def step(name, fn, optional=False):
    try:
        out = fn()
        RESULTS.append(("PASS", name)); print(f"[PASS] {name}")
        return out
    except MecSandboxError as e:
        status = "SKIP" if (optional and e.status in (404, 405, 501)) else "FAIL"
        RESULTS.append((status, name)); print(f"[{status}] {name}: {e}")
    except Exception as e:
        RESULTS.append(("FAIL", name)); print(f"[FAIL] {name}: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?")
    ap.add_argument("--token"); ap.add_argument("--app-id", default=None)
    ap.add_argument("--service-url", default="http://127.0.0.1:9000", help="where our REST service would listen")
    ap.add_argument("--mock", action="store_true"); ap.add_argument("--insecure", action="store_true")
    a = ap.parse_args()
    mock = None
    if a.mock:
        from mec.mec_mock_platform import MockMecPlatform
        mock = MockMecPlatform().start(); a.base_url = mock.url
    if not a.base_url:
        ap.error("base_url required (or --mock)")
    app = a.app_id or f"hexa-{uuid.uuid4().hex[:12]}"
    c = MecSandboxClient(a.base_url, auth_token=a.token, verify_tls=not a.insecure)
    print(f"target: {c!r}  app-instance-id: {app}")
    step("GET /mec_service_mgmt/v1/transports", c.transports, optional=True)
    step("GET /mec_app_support/v1/timing/current_time", c.current_time, optional=True)
    step("POST confirm_ready", lambda: c.confirm_ready(app))
    regs = []
    for s in hexa_services(a.service_url):
        r = step(f"POST register service {s.ser_name}", lambda s=s: c.register_service(app, s))
        if r: regs.append(r)
    found = step("GET /services (discover)", lambda: [x.ser_name for x in c.discover_services()])
    if found is not None:
        ok = all(r.ser_name in found for r in regs)
        RESULTS.append(("PASS" if ok else "FAIL", "discovery returns our services")); print(f"[{'PASS' if ok else 'FAIL'}] discovery returns our services")
    if regs:
        r0 = regs[0]; r0.state = "INACTIVE"
        step("PUT update service state", lambda: c.update_service(app, r0))
    for r in regs:
        step(f"DELETE service {r.ser_name}", lambda r=r: c.deregister_service(app, r.ser_instance_id))
    step("POST confirm_termination", lambda: c.confirm_termination(app))
    if mock:
        mock.stop()
    bad = [n for s, n in RESULTS if s == "FAIL"]
    print(f"\n{len(RESULTS)-len(bad)}/{len(RESULTS)} steps ok" + (f"; FAILED: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
