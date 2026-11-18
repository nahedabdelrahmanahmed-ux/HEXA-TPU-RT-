#!/usr/bin/env bash
# One command for the committee, inside the VM:
#   ./vm_test/committee_run.sh            # simulate BOTH applications, real ACME oneM2M CSE
#   ./vm_test/committee_run.sh --serve    # ...then serve the HTML report on port 8000
#   ./vm_test/committee_run.sh --no-cse   # use the built-in simulated CSE (no ACME needed)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
SERVE=0; USE_CSE=1
for a in "$@"; do case "$a" in --serve) SERVE=1;; --no-cse) USE_CSE=0;; esac; done

[ -x "$ROOT/.venv/bin/python" ] || { echo "[setup] first run: installing dependencies..."; bash "$ROOT/vm_test/provision.sh" || exit 1; }
PY="$ROOT/.venv/bin/python"
OUT="$ROOT/vm_test/results"; mkdir -p "$OUT"
CSE_PID=""; trap '[ -n "$CSE_PID" ] && pkill -P "$CSE_PID" 2>/dev/null; pkill -f acmecse 2>/dev/null; true' EXIT

ARGS=()
if [ $USE_CSE -eq 1 ]; then
  echo "[cse] starting ACME oneM2M CSE on :8080 ..."
  bash "$ROOT/deploy/run_acme_cse.sh" "$HOME/acme_cse_runtime" 8080 >"$OUT/acme_server.log" 2>&1 &
  CSE_PID=$!
  UP=0
  for _ in $(seq 1 90); do
    curl -s -m 2 -H "X-M2M-Origin: CAdmin" -H "X-M2M-RI: 1" -H "X-M2M-RVI: 3" http://127.0.0.1:8080/id-in >/dev/null 2>&1 && { UP=1; break; }
    sleep 1
  done
  if [ $UP -eq 1 ]; then echo "[cse] up."; ARGS+=(--cse-http-url http://127.0.0.1:8080)
  else echo "[cse] ACME did not start (see $OUT/acme_server.log) -> falling back to the built-in simulated CSE."; fi
fi

"$PY" committee_demo.py "${ARGS[@]}" || exit 1

if [ $SERVE -eq 1 ]; then
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo; echo "Serving report:  http://localhost:8000/committee_report.html   (VM address: http://${IP:-<vm-ip>}:8000/committee_report.html)"
  echo "Press Ctrl+C to stop."
  cd "$OUT" && "$PY" -m http.server 8000 --bind 0.0.0.0
fi
