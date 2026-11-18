"""
Tests for the optional CSE-inspection tooling (Mobius4 TUI Resource Browser integration):
  * deploy/mobius4-browser.config.yaml must stay consistent with deploy/run_acme_cse.sh;
  * deploy/get_mobius4_browser.sh platform mapping and integrity checks (tampered / re-released archives);
  * vm_test/live_feeder.py behaviour (in-memory CSE, error propagation, real CSE when reachable);
  * the real browser executable, when installed (tools/ or MOBIUS4_BROWSER): offline `sym` lookup.
The full visual check against a live CSE is vm_test/tui_smoke.py (needs the binary and `pyte`).
"""
import glob, hashlib, os, re, shutil, subprocess, sys, tempfile, unittest, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "vm_test"))

from middleware.onem2m import MN_CSE, ResponsePrimitive, ResponseStatusCode
from live_feeder import run_feeder

try:
    import yaml
except ImportError:                                  # pragma: no cover
    yaml = None


def _browser():
    c = [os.environ.get("MOBIUS4_BROWSER"), shutil.which("mobius4-browser")]
    c += glob.glob(os.path.join(ROOT, "tools", "mobius4-browser", "*", "mobius4-browser"))
    return next((x for x in c if x and os.path.isfile(x) and os.access(x, os.X_OK)), None)


def _cse_up():
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080/id-in", headers={
            "X-M2M-Origin": "CAdmin", "X-M2M-RI": "1", "X-M2M-RVI": "3"}), timeout=1)
        return True
    except Exception:
        return False


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestConfigTemplate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "deploy", "mobius4-browser.config.yaml")) as f:
            cls.cfg = yaml.safe_load(f)
        with open(os.path.join(ROOT, "deploy", "run_acme_cse.sh")) as f:
            cls.script = f.read()

    def test_structure(self):
        self.assertEqual(self.cfg["default_profile"], "acme")
        p = self.cfg["profiles"]["acme"]
        for k in ("host", "port", "cse_id", "cb_rn"):
            self.assertIn(k, p["cse"])
        self.assertIsInstance(p["cse"]["port"], int)
        self.assertTrue(p["originator"])

    def test_matches_the_acme_deploy_script(self):
        """If someone changes the CSE-ID/name/admin/port in the deploy script, the template must follow."""
        cse = self.cfg["profiles"]["acme"]["cse"]
        get = lambda key: re.search(rf"^{key}=(\S+)", self.script, re.M).group(1)
        self.assertEqual(cse["cse_id"], get("cseID"))
        self.assertEqual(cse["cb_rn"], get("cseName"))
        self.assertEqual(self.cfg["profiles"]["acme"]["originator"], get("adminID"))
        default_port = int(re.search(r'HTTP_PORT="\$\{2:-(\d+)\}"', self.script).group(1))
        self.assertEqual(cse["port"], default_port)

    def test_no_mqtt_broker_configured(self):
        """ACME runs without MQTT, so the profile must not promise a broker (the browser then polls)."""
        self.assertNotIn("mqtt", self.cfg["profiles"]["acme"])


class TestScripts(unittest.TestCase):
    def _url(self, s, m, **env):
        e = dict(os.environ, MOBIUS4_UNAME_S=s, MOBIUS4_UNAME_M=m, **env)
        return subprocess.run(["bash", os.path.join(ROOT, "deploy", "get_mobius4_browser.sh"), "--print-url"],
                              capture_output=True, text=True, env=e)

    def test_shell_syntax(self):
        for f in ("deploy/get_mobius4_browser.sh", "vm_test/inspect_cse.sh", "vm_test/run_vm_tests.sh",
                  "vm_test/committee_run.sh"):
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f}: {r.stderr}")

    def test_platform_mapping(self):
        base = "https://github.com/IoTKETI/mobius4/releases/download/resource_browser/mobius4-browser-0.15.0-"
        for s, m, plat in (("Linux", "x86_64", "linux-x86_64"), ("Linux", "aarch64", "linux-arm64"),
                           ("Linux", "arm64", "linux-arm64"), ("Darwin", "arm64", "macos-arm64"),
                           ("Darwin", "x86_64", "macos-x86_64")):
            r = self._url(s, m)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), base + plat + ".zip")

    def test_unsupported_platform_is_refused(self):
        for s, m in (("FreeBSD", "amd64"), ("Linux", "riscv64"), ("Darwin", "ppc")):
            r = self._url(s, m)
            self.assertEqual(r.returncode, 2, (s, m))
            self.assertIn("unsupported", r.stderr)

    def test_version_override_is_used(self):
        self.assertIn("0.99.1-linux-x86_64.zip", self._url("Linux", "x86_64", MOBIUS4_BROWSER_VERSION="0.99.1").stdout)

    def test_default_download_is_https_and_hash_pinned(self):
        with open(os.path.join(ROOT, "deploy", "get_mobius4_browser.sh")) as f:
            s = f.read()
        self.assertIn("https://github.com/IoTKETI/mobius4/releases/download/", s)
        self.assertNotIn("http://github", s)
        self.assertIn("7846d9190f69409872daa91f8655f7916474cacdd5365878d833a355e6a59361", s)   # tested asset

    # --- integrity checks exercised for real through a local file:// "release" ---------------------------------
    def _fake_release(self, tmp, version, plat, payload, sha=None):
        asset = f"mobius4-browser-{version}-{plat}.zip"
        with zipfile.ZipFile(os.path.join(tmp, asset), "w") as z:
            z.writestr("mobius4-browser-x/mobius4-browser", payload)
        with open(os.path.join(tmp, asset), "rb") as f:
            digest = sha or hashlib.sha256(f.read()).hexdigest()
        with open(os.path.join(tmp, asset + ".sha256"), "w") as f:
            f.write(f"{digest}  {asset}\n")

    def _install(self, tmp, dest, version="0.0.1"):
        env = dict(os.environ, MOBIUS4_UNAME_S="Linux", MOBIUS4_UNAME_M="x86_64", MOBIUS4_BROWSER_VERSION=version,
                   MOBIUS4_BROWSER_BASE_URL="file://" + tmp, MOBIUS4_BROWSER_DEST=dest)
        return subprocess.run(["bash", os.path.join(ROOT, "deploy", "get_mobius4_browser.sh")], capture_output=True, text=True, env=env)

    def test_installer_installs_a_valid_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_release(tmp, "0.0.1", "linux-x86_64", "#!/bin/sh\necho hi\n")
            dest = os.path.join(tmp, "out")
            r = self._install(tmp, dest)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.access(os.path.join(dest, "mobius4-browser-x", "mobius4-browser"), os.X_OK))
            self.assertIn("published SHA-256 matches", r.stdout)

    def test_installer_rejects_a_tampered_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_release(tmp, "0.0.1", "linux-x86_64", "payload", sha="0" * 64)     # published hash does not match
            dest = os.path.join(tmp, "out")
            r = self._install(tmp, dest)
            self.assertEqual(r.returncode, 1)
            self.assertIn("mismatch", r.stderr)
            self.assertFalse(os.path.exists(dest))                                         # nothing installed

    def test_installer_rejects_a_re_release_that_differs_from_the_pinned_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_release(tmp, "0.15.0", "linux-x86_64", "not the real binary")        # self-consistent but not the pinned one
            dest = os.path.join(tmp, "out")
            r = self._install(tmp, dest, version="0.15.0")
            self.assertEqual(r.returncode, 1)
            self.assertIn("pinned", r.stderr)
            self.assertFalse(os.path.exists(dest))

    def test_installer_fails_cleanly_when_the_asset_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._install(tmp, os.path.join(tmp, "out"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("download failed", r.stderr)

    def test_inspect_script_never_overwrites_an_existing_config(self):
        with open(os.path.join(ROOT, "vm_test", "inspect_cse.sh")) as f:
            s = f.read()
        self.assertIn('if [ ! -f "$CFG" ]', s)       # copy only when absent
        self.assertNotIn('cp -f', s)

    def test_binary_is_not_bundled_in_the_repository_root(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "mobius4-browser")))


class TestLiveFeeder(unittest.TestCase):
    def test_water_feeder_pushes_heartbeats_and_triggers(self):
        ae, pushed, trig = run_feeder(MN_CSE(), "water", seconds=10, rate_hz=20, sleep=lambda s: None)   # 200 samples
        self.assertGreaterEqual(trig, 1)                         # the burst is in the scenario
        self.assertGreaterEqual(pushed, 200 // 25 + trig - 1)
        self.assertLess(pushed, 200)                             # not one push per sample

    def test_aviation_feeder_triggers(self):
        _, pushed, trig = run_feeder(MN_CSE(), "aviation", seconds=10, rate_hz=20, sleep=lambda s: None)
        self.assertGreater(trig, 50)                             # dense fog: about half the samples fire

    def test_feeder_respects_rate(self):
        slept = []
        run_feeder(MN_CSE(), "water", seconds=2, rate_hz=10, sleep=slept.append)
        self.assertEqual(len(slept), 20)
        self.assertTrue(all(abs(x - 0.1) < 1e-9 for x in slept))

    def test_feeder_fails_loudly_on_error_responses(self):
        class Broken:
            def handle_primitive(self, req):
                return ResponsePrimitive("x", ResponseStatusCode.INTERNAL_SERVER_ERROR, {"error": "down"})
        with self.assertRaises(RuntimeError) as cm:
            run_feeder(Broken(), "water", seconds=1, sleep=lambda s: None)
        self.assertIn("registration", str(cm.exception))

    def test_feeder_fails_loudly_when_the_cse_dies_after_registration(self):
        """Registration works, then every push fails (CSE went away): must raise, not silently continue."""
        inner = MN_CSE()

        class DiesAfterRegistration:
            calls = 0
            def handle_primitive(self, req):
                DiesAfterRegistration.calls += 1
                if DiesAfterRegistration.calls > 3:          # AE + two containers are created first
                    return ResponsePrimitive("x", ResponseStatusCode.INTERNAL_SERVER_ERROR, {"error": "down"})
                return inner.handle_primitive(req)
        with self.assertRaises(RuntimeError) as cm:
            run_feeder(DiesAfterRegistration(), "water", seconds=2, sleep=lambda s: None)
        self.assertIn("push failed", str(cm.exception))

    @unittest.skipUnless(_cse_up(), "needs the ACME CSE on :8080 (deploy/run_acme_cse.sh)")
    def test_events_really_arrive_in_a_live_cse(self):
        import json
        from middleware.onem2m_http import HttpCSEClient
        name = f"feeder-test-{os.getpid()}"
        ae, pushed, trig = run_feeder(HttpCSEClient(base_url="http://127.0.0.1:8080", cse_id="id-in"),
                                      "water", seconds=1, rate_hz=30, sleep=lambda s: None, name=name)
        self.assertGreaterEqual(pushed, 1)
        req = urllib.request.Request(f"http://127.0.0.1:8080/cse-in/{name}/reflexEvents",
                                     headers={"X-M2M-Origin": "CAdmin", "X-M2M-RI": "2", "X-M2M-RVI": "3", "Accept": "application/json"})
        body = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertEqual(body["m2m:cnt"]["cni"], pushed)


@unittest.skipUnless(_browser(), "mobius4-browser not installed (./deploy/get_mobius4_browser.sh)")
class TestRealBrowserBinary(unittest.TestCase):
    def test_sym_lookup_works_offline(self):
        r = subprocess.run([_browser(), "sym", "rsc"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Response Status Code", r.stdout)

    def test_sym_resolves_names_used_by_this_project(self):
        for short, long_ in (("mni", "maxNrOfInstances"), ("cni", "currentNrOfInstances"), ("rn", "resourceName")):
            r = subprocess.run([_browser(), "sym", short], capture_output=True, text=True, timeout=60)
            self.assertIn(long_, r.stdout, short)


if __name__ == "__main__":
    unittest.main()
