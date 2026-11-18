#!/usr/bin/env python3
"""
vm_test/tui_smoke.py -- automated check of the Mobius4 TUI Resource Browser against a live ACME CSE filled by
this project. Drives the real executable inside a pseudo-terminal, renders its screen with `pyte` and asserts on text.

  pip install pyte            # test-only dependency
  ./deploy/get_mobius4_browser.sh && python3 vm_test/tui_smoke.py

Uses a throw-away HOME so the user's browser configuration is never touched. Linux/macOS only.
Exit code 0 = all checks passed, 2 = prerequisites missing (browser binary / pyte), 1 = a check failed.
"""
import fcntl, glob, os, pty, select, shutil, signal, struct, subprocess, sys, tempfile, termios, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLS, ROWS = 150, 45


def find_browser():
    cands = [os.environ.get("MOBIUS4_BROWSER"), shutil.which("mobius4-browser")]
    cands += glob.glob(os.path.join(ROOT, "tools", "mobius4-browser", "*", "mobius4-browser"))
    cands += glob.glob(os.path.join(ROOT, "tools", "mobius4-browser", "mobius4-browser"))
    return next((c for c in cands if c and os.path.isfile(c) and os.access(c, os.X_OK)), None)


def main():
    try:
        import pyte
    except ImportError:
        print("pyte not installed (pip install pyte)"); return 2
    browser = find_browser()
    if not browser:
        print("mobius4-browser not found (run ./deploy/get_mobius4_browser.sh)"); return 2

    home = tempfile.mkdtemp(prefix="m4b_home_")
    cfg_dir = os.path.join(home, "Library", "Application Support", "mobius4-browser") if sys.platform == "darwin" \
        else os.path.join(home, ".config", "mobius4-browser")
    os.makedirs(cfg_dir); shutil.copy(os.path.join(ROOT, "deploy", "mobius4-browser.config.yaml"), os.path.join(cfg_dir, "config.yaml"))

    srv = subprocess.Popen(["bash", "deploy/run_acme_cse.sh", os.path.join(home, "acme"), "8080"], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results, pid, saved = [], None, {}
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080/id-in", headers={
                    "X-M2M-Origin": "CAdmin", "X-M2M-RI": "1", "X-M2M-RVI": "3"}), timeout=2); break
            except Exception:
                time.sleep(1)
        else:
            print("ACME did not start"); return 1
        r = subprocess.run([sys.executable, "committee_demo.py", "--quick", "--cse-http-url", "http://127.0.0.1:8080",
                            "--out", os.path.join(home, "r.html")], cwd=ROOT, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print("demo failed:", r.stderr[-300:]); return 1

        screen = pyte.Screen(COLS, ROWS); stream = pyte.ByteStream(screen)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update({"TERM": "xterm-256color", "HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config")})
            os.execv(browser, ["mobius4-browser", "--profile", "acme"])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))

        def pump(sec):
            end = time.time() + sec
            while time.time() < end:
                if select.select([fd], [], [], 0.2)[0]:
                    try:
                        d = os.read(fd, 65536)
                    except OSError:
                        return
                    if not d:
                        return
                    stream.feed(d)

        def text():
            return "\n".join(screen.display)

        def check(name, cond):
            results.append((name, bool(cond))); print(f"[{'PASS' if cond else 'FAIL'}] {name}")

        pump(7); t = text(); saved["start"] = [l.rstrip() for l in screen.display if l.strip()]
        check("connects to ACME (header shows profile acme and the CSEBase URL)", "profile acme" in t and "localhost:8080/cse-in" in t)
        check("no first-run wizard", "No profile found" not in t)
        check("tree root is the CSEBase cse-in", "cse-in" in t and " cb " in t)
        check("aviation AEs from the demo are visible", "aviation-" in t)
        check("water AEs from the demo are visible", "water-" in t)
        os.write(fd, b"3"); pump(5); t = text(); saved["depth 3"] = [l.rstrip() for l in screen.display if l.strip()]
        check("depth 3 shows the reflexEvents container", "reflexEvents" in t)
        check("depth 3 shows contentInstances (cin)", " cin" in t)
        check("virtual latest/oldest resources are shown", "latest" in t and "oldest" in t)
        os.write(fd, b"R"); pump(4); t = text(); saved["registrations (R)"] = [l.rstrip() for l in screen.display if l.strip()]
        check("registrations dialog lists our AEs", "AEs" in t and "what CAdmin may discover" in t)
        check("registrations show our application types", "NWaterEdge" in t or "NAviationEdge" in t or "NAviation" in t)
        os.write(fd, b"\x1b"); pump(1)
        os.write(fd, b"s"); pump(2); os.write(fd, b"mni"); pump(2); t = text()
        check("offline symbol dictionary works inside the TUI (mni -> maxNrOfInstances)", "maxNrOfInstances" in t)
        if os.environ.get("TUI_SMOKE_SAVE"):
            with open(os.environ["TUI_SMOKE_SAVE"], "w") as f:
                f.write(f"# Real screens of mobius4-browser against the ACME CSE filled by committee_demo.py (captured with pyte, {COLS}x{ROWS})\n\n")
                for k, v in saved.items():
                    f.write(f"## {k}\n```\n" + "\n".join(v[:36]) + "\n```\n\n")
    finally:
        if pid:
            try: os.kill(pid, signal.SIGTERM)
            except Exception: pass
        srv.terminate(); subprocess.run(["pkill", "-f", "acmecse"], capture_output=True); shutil.rmtree(home, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
