# Inspecting the oneM2M resources with the Mobius4 TUI Resource Browser (optional)

`mobius4-browser` (IoTKETI / Mobius4 project, tutorial by S. Jeong) is a terminal oneM2M client: it browses the
resource tree, decodes attributes (short name, long name, value; enums, timestamps, ACP bits), watches resources
change, looks up oneM2M short names offline, and can create/edit/delete resources. It uses only standard oneM2M
RETRIEVE, discovery and subscriptions over the HTTP binding, so **it works with any CSE, including the ACME CSE
this project uses** (nothing in it is specific to Mobius4).

It is an **optional third-party tool**: it is not bundled here, not required by any test of the platform, and
only the Linux x86_64 build (version 0.15.0) has been tested with this project.

## What it adds to this project
The platform already writes real oneM2M resources (AE, containers, contentInstances) into the CSE. The browser lets
the committee *see* them instead of reading logs: which edge node registered, what the reflex pushed, what the
counters do while a scenario runs.

## Install and run
```
./deploy/get_mobius4_browser.sh          # downloads, verifies SHA-256 (published + pinned), extracts to tools/
./vm_test/inspect_cse.sh                 # start ACME, run the committee demo into it, open the browser
./vm_test/inspect_cse.sh --live          # additionally keep an event feeder running: select its reflexEvents, press w
./vm_test/inspect_cse.sh --no-browser    # only leave the populated CSE running
```
* The browser refuses to start without a config file; `inspect_cse.sh` installs `deploy/mobius4-browser.config.yaml`
  **only if you have no config** and never overwrites one. If you already have a config, append the `acme:` profile.
* Profile values (checked by a test against `deploy/run_acme_cse.sh`): host `localhost`, port `8080`,
  CSE-ID `id-in`, CSEBase `cse-in`, originator `CAdmin` (the ACME admin), no MQTT broker.
* Windows: download the zip manually from the Mobius4 releases page and use the same profile; macOS: the script clears the quarantine flag.

## What you will see (verified against the real tool)
Real screens are in `docs/tui_example.md`. In short:
* `cse-in` (CSEBase) with one `ae` per demo scenario: `aviation-clear-sky-…`, `aviation-dense-fog-approach-…`, `aviation-skin-…`,
  `water-sudden-burst-…`, … plus ACME's own `CAdmin`, ACPs and `defaultNTP`.
* Under each AE two containers: **`sensorData`** (sensor-window summaries) and **`reflexEvents`** (one contentInstance per triggered
  sample plus heartbeats); each container also shows the virtual `la` (latest) and `ol` (oldest).
* `R` lists the registered AEs with their App-IDs (`NAviationEdge`, `NWaterEdge`, `NBDO-SKIN-edge`).
* `mni` in the symbol dictionary (`s`) shows `maxNrOfInstances`, with the defining clause, offline.

## Keys worth knowing (from the tutorial)
`1`-`9` expand to that depth, `0` collapse all, `/` filter (client-side, direct children of expanded nodes only), `v` essential/full attributes,
`w` watch the resource under the cursor (`x` stop, `X` stop all), `R` registrations, `o` switch originator (this run only), `s` symbols and enum
search (`Enter` on a symbol with an enumeration searches its values), `?` help (prints the config path in use), `q` quit.
Writes: `n` create, `e` edit (UPDATE), `D` delete (capital on purpose; typing the name is required when it has children).

## Honest caveats
* **"What this originator may discover."** The tool shows what your originator may see and says so in the header. An empty branch can mean
  empty or not permitted. Use the admin originator (`CAdmin`, as in the profile) to look at everything.
* **Live watching uses polling** (about every 10 s) because the project's ACME runs without an MQTT broker (`live off: no broker` in the header).
* **Do not write to the CSE while a demo or benchmark is running.** Edits and deletes are real UPDATE/DELETE requests with no undo.
  The tool's watch subscriptions carry an expiry lease and a label and are removed on exit; leftovers from a crashed run are offered for cleanup.
* The tool does not make the platform more correct; it makes it **inspectable**. Interoperability with Mobius and tinyIoT is still untested.

## Automated checks
* `python3 -m unittest tests.test_cse_inspection` (22 tests: config template vs deploy script, installer integrity incl. tampered and re-released
  archives, live feeder, offline `sym` lookups with the real binary when installed).
* `python3 vm_test/tui_smoke.py` (needs the binary and `pip install pyte`): starts ACME, runs the demo, drives the real browser in a pseudo-terminal and
  asserts 11 things on the rendered screen (connection, no wizard, the AEs, `reflexEvents`, contentInstances, registrations, symbol dictionary).
* `./vm_test/run_vm_tests.sh inspect` and `tui`.

## Troubleshooting
| Symptom | Meaning / fix |
|---|---|
| First-run wizard appears | No config file: let `inspect_cse.sh` create it, or copy `deploy/mobius4-browser.config.yaml` |
| Only `cse-in` visible, nothing under it | The originator cannot discover the children: press `o` and use `CAdmin` |
| `limited mode` in the status bar | The CSE ignored `rcn=5`; child types are unknown, everything else works |
| Nothing happens on macOS | Quarantine: `xattr -r -d com.apple.quarantine tools/mobius4-browser` |
| Columns truncated | Terminal narrower than 100 columns |
