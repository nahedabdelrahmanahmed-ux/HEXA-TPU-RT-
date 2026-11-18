#!/usr/bin/env bash
# vm_test/inspect_cse.sh -- look at the oneM2M resources this project creates, in the Mobius4 TUI Resource Browser.
#   ./vm_test/inspect_cse.sh             # start ACME, run the committee demo into it, open the browser
#   ./vm_test/inspect_cse.sh --live      # ...and keep a live event feeder running so you can watch (key: w)
#   ./vm_test/inspect_cse.sh --no-browser   # just leave the populated CSE running (Ctrl-C to stop)
# The browser is an optional third-party tool: install with ./deploy/get_mobius4_browser.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PY="${PYTHON:-python3}"; [ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
LIVE=0; BROWSER=1
for a in "$@"; do case "$a" in --live) LIVE=1;; --no-browser) BROWSER=0;; esac; done
CSE_PID=""; FEED_PID=""
cleanup() { [ -n "$FEED_PID" ] && kill "$FEED_PID" 2>/dev/null; [ -n "$CSE_PID" ] && pkill -P "$CSE_PID" 2>/dev/null; [ -n "$CSE_PID" ] && kill "$CSE_PID" 2>/dev/null; pkill -f acmecse 2>/dev/null; true; }
trap cleanup EXIT

BIN="${MOBIUS4_BROWSER:-}"
[ -z "$BIN" ] && BIN="$(command -v mobius4-browser 2>/dev/null || true)"
[ -z "$BIN" ] && BIN="$(find "$ROOT/tools/mobius4-browser" -maxdepth 2 -type f -name 'mobius4-browser*' ! -name '*.zip' ! -name '*.so*' 2>/dev/null | head -1)"
if [ $BROWSER -eq 1 ] && [ -z "$BIN" ]; then
  echo "mobius4-browser not found. Install it with: ./deploy/get_mobius4_browser.sh   (or use --no-browser)"; exit 1
fi

mkdir -p "$ROOT/vm_test/results"
echo "[cse] starting ACME on :8080 ..."
bash "$ROOT/deploy/run_acme_cse.sh" "$HOME/acme_cse_runtime" 8080 >"$ROOT/vm_test/results/acme_server.log" 2>&1 &
CSE_PID=$!
UP=0
for _ in $(seq 1 90); do
  curl -s -m 2 -H "X-M2M-Origin: CAdmin" -H "X-M2M-RI: 1" -H "X-M2M-RVI: 3" http://127.0.0.1:8080/id-in >/dev/null 2>&1 && { UP=1; break; }; sleep 1
done
[ $UP -eq 1 ] || { echo "ACME did not start (see vm_test/results/acme_server.log)"; exit 1; }
echo "[cse] up. Populating with the committee demo ..."
"$PY" committee_demo.py --quick --cse-http-url http://127.0.0.1:8080 >/dev/null 2>&1 || echo "(demo reported an error; the CSE may be partly empty)"
if [ $LIVE -eq 1 ]; then
  "$PY" vm_test/live_feeder.py --cse-http-url http://127.0.0.1:8080 --seconds 600 >/dev/null 2>&1 &
  FEED_PID=$!; echo "[live] feeder running (AE water-live-*): select its reflexEvents container and press w"
fi

if [ $BROWSER -eq 0 ]; then
  echo "CSE running at http://127.0.0.1:8080  (CSE-ID id-in, CSEBase cse-in, originator CAdmin). Ctrl-C to stop."; wait "$CSE_PID"; exit 0
fi
# the browser insists on a config file; install ours only if none exists
case "$(uname -s)" in
  Darwin) CFG="$HOME/Library/Application Support/mobius4-browser/config.yaml";;
  *)      CFG="${XDG_CONFIG_HOME:-$HOME/.config}/mobius4-browser/config.yaml";;
esac
if [ ! -f "$CFG" ]; then mkdir -p "$(dirname "$CFG")"; cp "$ROOT/deploy/mobius4-browser.config.yaml" "$CFG"; echo "[cfg] created $CFG"; fi
if ! grep -q "^  acme:" "$CFG"; then echo "[cfg] your config has no 'acme' profile; append the profile from deploy/mobius4-browser.config.yaml to $CFG"; exit 1; fi
echo "[browser] starting (q quits, ? help, R registrations, 3 expands to depth 3, s symbols)"
"$BIN" --profile acme
