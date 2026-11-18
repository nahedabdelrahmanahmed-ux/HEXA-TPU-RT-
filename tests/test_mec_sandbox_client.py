"""
Strict tests of mec/mec_sandbox_client.py against the strict MEC 011 test double.
NOTE: they prove contract-conformance and robustness offline; they do NOT prove interoperability with the
live ETSI MEC Sandbox (run `python3 vm_test/mec_sandbox_smoke.py <url>` against it from a machine that can
reach it).
"""
import os
import re
import sys
import threading
import time
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mec.mec_mock_platform import MockMecPlatform, PREFIX
from mec.mec_sandbox_client import (MecAppSession, MecSandboxClient, MecSandboxError, MecSandboxUnavailable,
                                    ServiceInfo, hexa_services)

APP = "app-instance-0001"


def svc(name="hexa-water-cognitive", **kw):
    return ServiceInfo(name, "1.0.0", "http://10.0.0.5:9000/water/v1", **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.p = MockMecPlatform().start()
        self.c = MecSandboxClient(self.p.url, retries=2, backoff_s=0.01, timeout=(1.0, 1.0))

    def tearDown(self):
        self.p.stop()


class TestContract(Base):
    def test_confirm_ready_exact_request(self):
        self.c.confirm_ready(APP)
        r = self.p.requests("POST")[0]
        self.assertEqual(r["path"], f"{PREFIX}/mec_app_support/v1/applications/{APP}/confirm_ready")
        self.assertEqual(r["body"], {"indication": "READY"})
        self.assertIn("application/json", r["headers"]["content-type"])
        self.assertIn("application/json", r["headers"]["accept"])

    def test_confirm_termination_exact_request(self):
        self.c.confirm_termination(APP, "STOPPING")
        r = self.p.requests("POST")[0]
        self.assertTrue(r["path"].endswith(f"/applications/{APP}/confirm_termination"))
        self.assertEqual(r["body"], {"operationAction": "STOPPING"})

    def test_register_payload_and_result(self):
        self.c.confirm_ready(APP)
        out = self.c.register_service(APP, svc())
        r = self.p.requests("POST", "/services")[0]
        b = r["body"]
        for k in ("serName", "version", "state", "transportInfo", "serializer"):
            self.assertIn(k, b)
        self.assertEqual(b["state"], "ACTIVE")
        self.assertEqual(b["transportInfo"]["endpoint"]["uri"], "http://10.0.0.5:9000/water/v1")
        self.assertRegex(out.ser_instance_id, r"^[0-9a-f]{32}$")
        self.assertEqual(out.ser_name, "hexa-water-cognitive")

    def test_discover_filters_and_encoding(self):
        self.c.confirm_ready(APP)
        self.c.register_service(APP, svc("a"))
        self.c.register_service(APP, svc("b"))
        self.assertEqual([s.ser_name for s in self.c.discover_services(ser_name="a")], ["a"])
        self.assertEqual(len(self.c.discover_services()), 2)
        self.assertEqual(self.c.discover_services(ser_name="nope"), [])
        weird = "a b&c=d"
        self.c.discover_services(ser_name=weird)
        self.assertEqual(self.p.requests("GET", "/services")[-1]["query"]["ser_name"], [weird])

    def test_get_update_deregister_roundtrip(self):
        self.c.confirm_ready(APP)
        s = self.c.register_service(APP, svc())
        self.assertEqual(self.c.get_service(APP, s.ser_instance_id).ser_name, s.ser_name)
        s.state = "INACTIVE"
        self.assertEqual(self.c.update_service(APP, s).state, "INACTIVE")
        self.assertEqual(self.p.requests("PUT")[0]["body"]["serInstanceId"], s.ser_instance_id)
        self.c.deregister_service(APP, s.ser_instance_id)
        with self.assertRaises(MecSandboxError) as cm:
            self.c.get_service(APP, s.ser_instance_id)
        self.assertEqual(cm.exception.status, 404)

    def test_deregister_missing_is_error_unless_ignored(self):
        with self.assertRaises(MecSandboxError):
            self.c.deregister_service(APP, "deadbeef")
        self.c.deregister_service(APP, "deadbeef", ignore_missing=True)

    def test_timing_and_transports(self):
        self.assertIn("timeStamp", self.c.timing_caps())
        self.assertIn("seconds", self.c.current_time())
        self.assertEqual(self.c.transports()[0]["type"], "REST_HTTP")

    def test_service_not_visible_to_another_application(self):
        self.c.confirm_ready(APP); self.c.confirm_ready("other-app")
        s = self.c.register_service(APP, svc())
        with self.assertRaises(MecSandboxError) as cm:
            self.c.get_service("other-app", s.ser_instance_id)
        self.assertEqual(cm.exception.status, 404)


class TestValidationBeforeIO(Base):
    def test_bad_ids_rejected_without_any_request(self):
        for bad in ("", "../x", "a/b", "a b", "x" * 129, "é", None, 123, "a?b", "a#b", "%2e%2e"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.c.confirm_ready(bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.c.get_service(APP, bad)
        self.assertEqual(self.p.log, [])

    def test_bad_service_definitions_rejected_without_any_request(self):
        for kw in ({"state": "RUNNING"}, {"serializer": "YAML"}, {"scope_of_locality": "GALAXY"},
                   {"transport_type": "CARRIER_PIGEON"}):
            with self.assertRaises(ValueError, msg=str(kw)):
                self.c.register_service(APP, svc(**kw))
        for uri in ("ftp://x/y", "http://", "javascript:alert(1)", "", "/relative"):
            with self.assertRaises(ValueError, msg=uri):
                self.c.register_service(APP, ServiceInfo("n", "1", uri))
        with self.assertRaises(ValueError):
            self.c.register_service(APP, ServiceInfo("", "1", "http://h/p"))
        self.assertEqual(self.p.log, [])

    def test_bad_client_configuration(self):
        for url in ("", "ftp://h", "http://", "http://h/?x=1", "http://h/#f", "no-scheme.example"):
            with self.assertRaises(ValueError, msg=url):
                MecSandboxClient(url)
        with self.assertRaises(ValueError):
            MecSandboxClient("http://h", retries=11)
        with self.assertRaises(ValueError):
            self.c.confirm_termination(APP, "EXPLODING")


class TestErrorHandling(Base):
    def test_platform_error_is_mapped_with_problem_details(self):
        self.p.inject("status", status=400)
        with self.assertRaises(MecSandboxError) as cm:
            self.c.confirm_ready(APP)
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(cm.exception.problem.get("status"), 400)

    def test_500_is_not_retried(self):
        self.p.inject("status", status=500)
        with self.assertRaises(MecSandboxError):
            self.c.confirm_ready(APP)
        self.assertEqual(len(self.p.log), 1)

    def test_503_retried_then_succeeds(self):
        self.p.inject("status", times=2, status=503)
        self.c.confirm_ready(APP)
        self.assertEqual(len(self.p.log), 3)

    def test_503_gives_up_after_retries(self):
        self.p.inject("status", times=10, status=503)
        with self.assertRaises(MecSandboxError) as cm:
            self.c.confirm_ready(APP)
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(len(self.p.log), 3)                      # 1 + 2 retries

    def test_dropped_connection_retried(self):
        self.p.inject("drop", times=2)
        self.c.confirm_ready(APP)
        self.assertEqual(len(self.p.log), 3)

    def test_persistent_drop_is_unavailable(self):
        self.p.inject("drop", times=10)
        with self.assertRaises(MecSandboxUnavailable):
            self.c.confirm_ready(APP)

    def test_connection_refused_fails_fast(self):
        self.p.stop()
        t0 = time.time()
        with self.assertRaises(MecSandboxUnavailable):
            self.c.confirm_ready(APP)
        self.assertLess(time.time() - t0, 3.0)

    def test_read_timeout_is_bounded(self):
        c = MecSandboxClient(self.p.url, retries=0, timeout=(0.5, 0.2))
        self.p.inject("delay", delay_s=1.0)
        t0 = time.time()
        with self.assertRaises(MecSandboxUnavailable):
            c.confirm_ready(APP)
        self.assertLess(time.time() - t0, 0.9)

    def test_non_json_success_body_is_an_error(self):
        self.p.inject("garbage")
        with self.assertRaises(MecSandboxError):
            self.c.discover_services()

    def test_redirect_never_followed(self):
        c = MecSandboxClient(self.p.url, auth_token="s3cret-token", retries=0, timeout=(1, 1))
        self.p.inject("redirect")
        with self.assertRaises(MecSandboxError) as cm:
            c.confirm_ready(APP)
        self.assertEqual(cm.exception.status, 302)
        self.assertEqual(len(self.p.log), 1)

    def test_token_sent_only_when_configured_and_never_leaked(self):
        self.c.confirm_ready(APP)
        self.assertNotIn("authorization", self.p.log[-1]["headers"])
        c = MecSandboxClient(self.p.url, auth_token="s3cret-token", retries=0, timeout=(1, 1))
        c.confirm_ready(APP)
        self.assertEqual(self.p.log[-1]["headers"]["authorization"], "Bearer s3cret-token")
        self.p.inject("status", status=400)
        with self.assertRaises(MecSandboxError) as cm:
            c.confirm_ready(APP)
        for text in (repr(c), str(cm.exception), repr(cm.exception)):
            self.assertNotIn("s3cret-token", text)

    def test_register_post_is_not_blindly_retried(self):
        self.c.confirm_ready(APP)
        self.p.inject("status", status=503)
        with self.assertRaises(MecSandboxError):
            self.c.register_service(APP, svc())
        self.assertEqual(len(self.p.requests("POST", "/services")), 1)

    def test_register_before_ready_is_rejected_by_platform(self):
        with self.assertRaises(MecSandboxError) as cm:
            self.c.register_service(APP, svc())
        self.assertEqual(cm.exception.status, 403)


class TestIdempotentRegistration(Base):
    def test_ambiguous_timeout_does_not_create_duplicates(self):
        self.c.confirm_ready(APP)
        slow = MecSandboxClient(self.p.url, retries=0, timeout=(1.0, 0.2))
        self.p.inject("delay", delay_s=0.5, process_after_delay=True)     # server will still register it
        with self.assertRaises(MecSandboxUnavailable):
            slow.register_service(APP, svc())
        time.sleep(0.8)                                                    # let the server finish
        self.assertEqual(len(self.p.services), 1)
        again = self.c.ensure_service(APP, svc())                          # outcome unknown -> reuse
        self.assertEqual(len(self.p.services), 1)
        self.assertEqual(again.ser_name, "hexa-water-cognitive")

    def test_ensure_service_registers_once_and_reuses(self):
        self.c.confirm_ready(APP)
        a = self.c.ensure_service(APP, svc())
        b = self.c.ensure_service(APP, svc())
        self.assertEqual(a.ser_instance_id, b.ser_instance_id)
        self.assertEqual(len(self.p.services), 1)


class TestSessionLifecycle(Base):
    def _order(self):
        out = []
        for r in self.p.log:
            if r["path"].endswith("confirm_ready"): out.append("ready")
            elif r["method"] == "POST" and r["path"].endswith("/services"): out.append("register")
            elif r["method"] == "DELETE": out.append("deregister")
            elif r["path"].endswith("confirm_termination"): out.append("terminate")
        return out

    def test_normal_lifecycle_order_and_state(self):
        with MecAppSession(self.c, APP, hexa_services()) as s:
            self.assertEqual(len(s.registered), 2)
            self.assertEqual(len(self.c.discover_services()), 2)
        self.assertEqual(self._order(), ["ready", "register", "register", "deregister", "deregister", "terminate"])
        self.assertEqual(self.p.services, {})
        self.assertIn(APP, self.p.terminated)
        self.assertEqual(s.cleanup_errors, [])

    def test_exception_in_body_still_cleans_up(self):
        with self.assertRaises(RuntimeError):
            with MecAppSession(self.c, APP, hexa_services()):
                raise RuntimeError("boom")
        self.assertEqual(self.p.services, {})
        self.assertIn(APP, self.p.terminated)

    def test_registration_failure_rolls_back(self):
        # confirm_ready ok, first POST ok, second POST rejected -> first must be removed, app terminated
        self.p.inject("status", times=0)
        sv = hexa_services()
        orig = self.c.ensure_service
        calls = {"n": 0}

        def flaky(app, info):
            calls["n"] += 1
            if calls["n"] == 2:
                raise MecSandboxError("boom", 400)
            return orig(app, info)
        self.c.ensure_service = flaky
        with self.assertRaises(MecSandboxError):
            with MecAppSession(self.c, APP, sv):
                self.fail("must not enter")
        self.assertEqual(self.p.services, {})
        self.assertIn(APP, self.p.terminated)

    def test_cleanup_errors_are_recorded_not_raised(self):
        s = MecAppSession(self.c, APP, hexa_services())
        s.__enter__()
        self.p.inject("drop", times=100)                # platform vanishes while we clean up
        s.__exit__(None, None, None)                    # must not raise
        self.assertGreaterEqual(len(s.cleanup_errors), 3)   # 2 deregisters + termination

    def test_close_is_idempotent(self):
        s = MecAppSession(self.c, APP, hexa_services())
        s.__enter__(); s.close(); s.close()
        self.assertEqual(len([r for r in self.p.log if r["path"].endswith("confirm_termination")]), 1)

    def test_concurrent_applications_do_not_interfere(self):
        errors, ids = [], {}

        def worker(name):
            try:
                c = MecSandboxClient(self.p.url, retries=1, backoff_s=0.01, timeout=(1, 2))
                with MecAppSession(c, name, [svc(f"svc-{name}")]) as s:
                    time.sleep(0.05)
                    ids[name] = s.registered[0].ser_instance_id
                    c.get_service(name, ids[name])
            except Exception as e:                       # pragma: no cover
                errors.append(repr(e))
        ts = [threading.Thread(target=worker, args=(f"app-{i}",)) for i in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids.values())), 8)
        self.assertEqual(self.p.services, {})


class TestMockPlatformIsStrict(Base):
    """Meta-tests: the test double must reject what the spec rejects, otherwise passing tests mean nothing."""

    def _post(self, path, **kw):
        return requests.post(f"{self.p.url}/{path}", timeout=2, **kw)

    def test_rejects_bad_payloads_and_media_type(self):
        self.c.confirm_ready(APP)
        base = f"mec_service_mgmt/v1/applications/{APP}/services"
        good = svc().to_json()
        self.assertEqual(self._post(base, json={k: v for k, v in good.items() if k != "serName"}).status_code, 400)
        self.assertEqual(self._post(base, json=dict(good, state="WEIRD")).status_code, 400)
        self.assertEqual(self._post(base, json=dict(good, serializer="YAML")).status_code, 400)
        self.assertEqual(self._post(base, data="{not json", headers={"Content-Type": "application/json"}).status_code, 400)
        self.assertEqual(self._post(base, data="x=1", headers={"Content-Type": "text/plain"}).status_code, 415)
        self.assertEqual(self._post("mec_app_support/v1/applications/a1/confirm_ready", json={"indication": "NOPE"}).status_code, 400)
        self.assertEqual(requests.get(f"{self.p.url}/nope", timeout=2).status_code, 404)

    def test_put_requires_matching_instance_id(self):
        self.c.confirm_ready(APP)
        s = self.c.register_service(APP, svc())
        body = dict(s.to_json(), serInstanceId="other")
        r = requests.put(f"{self.p.url}/mec_service_mgmt/v1/applications/{APP}/services/{s.ser_instance_id}", json=body, timeout=2)
        self.assertEqual(r.status_code, 400)


class TestProjectServices(Base):
    def test_hexa_services_are_valid_and_discoverable(self):
        sv = hexa_services("http://192.0.2.10:9000")
        for s in sv:
            s.validate()
        with MecAppSession(self.c, "hexa-edge-01", sv):
            names = sorted(x.ser_name for x in self.c.discover_services())
            self.assertEqual(names, ["hexa-aviation-cognitive", "hexa-water-cognitive"])


if __name__ == "__main__":
    unittest.main()
