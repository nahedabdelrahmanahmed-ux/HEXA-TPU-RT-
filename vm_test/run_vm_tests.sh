#!/usr/bin/env bash
# vm_test/run_vm_tests.sh -- per-application test runner for a hardware-less VM.
# Usage: ./vm_test/run_vm_tests.sh [tpu|aviation|water|structural|onem2m|chaos|all]
# Logs: vm_test/results/<app>.log ; summary: vm_test/results/SUMMARY.txt
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PY="${PYTHON:-python3}"; [ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
OUT="$ROOT/vm_test/results"; mkdir -p "$OUT"; : > "$OUT/SUMMARY.txt"
CSE_PID=""; CSE_URL="http://127.0.0.1:8080"
declare -A STATUS

run() {  # run <app> <cmd...>
  local app="$1"; shift
  echo ">>> [$app] $*" | tee -a "$OUT/$app.log"
  "$@" >>"$OUT/$app.log" 2>&1; local rc=$?
  [ $rc -eq 0 ] && s=PASS || s="FAIL(rc=$rc)"
  STATUS[$app]="${STATUS[$app]:-PASS}"; [ $rc -ne 0 ] && STATUS[$app]="$s"
  echo "    -> $s"
}
start_cse() {
  bash "$ROOT/deploy/run_acme_cse.sh" "$HOME/acme_cse_runtime" 8080 >"$OUT/acme_server.log" 2>&1 &
  CSE_PID=$!
  for _ in $(seq 1 60); do
    curl -s -m 2 -H "X-M2M-Origin: CAdmin" -H "X-M2M-RI: 1" -H "X-M2M-RVI: 3" "$CSE_URL/id-in" >/dev/null 2>&1 && return 0
    sleep 1
  done; return 1
}
stop_cse() { [ -n "$CSE_PID" ] && pkill -P "$CSE_PID" 2>/dev/null; [ -n "$CSE_PID" ] && kill "$CSE_PID" 2>/dev/null; pkill -f "acmecse" 2>/dev/null; true; }
trap stop_cse EXIT

t_tpu()       { : > "$OUT/tpu.log"
  run tpu $PY -m unittest tests.test_core -v
  run tpu $PY benchmark.py single --workers 10 --tiles-per-worker 20 --axi-width 128 --tiling output_channel --memory-aware-deadline
  run tpu $PY benchmark.py power --workers 10; }
t_aviation()  { : > "$OUT/aviation.log"
  run aviation $PY -m unittest tests.test_reflex_kernel.TestReflexKernelCore tests.test_reflex_kernel.TestAviationDomain tests.test_reflex_kernel.TestDomainNeutrality -v
  run aviation $PY -m unittest tests.test_middleware_mec.TestBatForecaster tests.test_middleware_mec.TestHermitCrabEvaluator tests.test_middleware_mec.TestSquidController -v
  run aviation $PY benchmarks/reflex_domain_benchmark.py; }
t_water()     { : > "$OUT/water.log"
  run water $PY -m unittest tests.test_reflex_kernel.TestWaterDomain tests.test_water_cognitive_engine -v; }
t_structural(){ : > "$OUT/structural.log"
  run structural $PY -m unittest tests.test_core.TestBDOSkinIntegration tests.test_middleware_mec.TestBHSCognitiveEngineAndMecPlatform tests.test_middleware_mec.TestEdgeToMecPipelineIntegration -v
  run structural $PY benchmarks/bdo_skin_benchmark.py --workers 10 --windows 150 --compare
  run structural $PY benchmarks/onem2m_mec_benchmark.py --num-windows 150; }
t_onem2m()    { : > "$OUT/onem2m.log"
  run onem2m $PY -m unittest tests.test_middleware_mec.TestOneM2MMiddleware tests.test_middleware_mec.TestURLLCLink -v
  if start_cse; then
    run onem2m $PY -m unittest tests.test_middleware_mec.TestRealCSEHttpTransport -v
    run onem2m $PY benchmarks/onem2m_mec_benchmark.py --cse-http-url "$CSE_URL" --num-windows 150
  else STATUS[onem2m]="FAIL(ACME did not start, see acme_server.log)"; fi
  stop_cse; }
t_demo()      { : > "$OUT/demo.log"; run demo $PY committee_demo.py --quick; }
t_math()      { : > "$OUT/math.log"; run math $PY math_model/run_math_model.py; }
t_twin()      { : > "$OUT/twin.log"; run twin $PY -m unittest tests.test_water_twin -v; }
t_mec()       { : > "$OUT/mec.log"; run mec $PY -m unittest tests.test_mec_sandbox_client -v; run mec $PY vm_test/mec_sandbox_smoke.py --mock; }
t_real()      { : > "$OUT/real.log"; run real $PY -m unittest tests.test_real_data_phm2012 -v; run real $PY real_data/validate_bat_phm2012.py; }
t_inspect()   { : > "$OUT/inspect.log"; run inspect $PY -m unittest tests.test_cse_inspection -v; }
t_tui()       { : > "$OUT/tui.log"; echo ">>> [tui] vm_test/tui_smoke.py" | tee -a "$OUT/tui.log"
  $PY vm_test/tui_smoke.py >>"$OUT/tui.log" 2>&1; rc=$?
  if [ $rc -eq 0 ]; then STATUS[tui]=PASS; echo "    -> PASS"; elif [ $rc -eq 2 ]; then STATUS[tui]="SKIP(browser or pyte missing)"; echo "    -> SKIP"; else STATUS[tui]="FAIL(rc=$rc)"; echo "    -> FAIL"; fi; }
t_chaos()     { : > "$OUT/chaos.log"; run chaos $PY vm_test/chaos_tests.py; }

APP="${1:-all}"
case "$APP" in
  tpu|aviation|water|structural|onem2m|chaos|demo|math|twin|mec|real|inspect|tui) "t_$APP";;
  all) for a in tpu aviation water structural onem2m chaos demo math twin mec real inspect tui; do "t_$a"; done;;
  *) echo "unknown app '$APP'"; exit 2;;
esac
{ echo "=== per-application summary ($(date -Is)) ==="
  for a in "${!STATUS[@]}"; do printf "%-12s %s\n" "$a" "${STATUS[$a]}"; done | sort; } | tee "$OUT/SUMMARY.txt"
grep -q FAIL "$OUT/SUMMARY.txt" && exit 1 || exit 0
