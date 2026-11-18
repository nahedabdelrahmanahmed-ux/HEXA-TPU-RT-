# Real-data evidence: what was used, what was found, what is still missing

Honest inventory (searched October 2026). The development sandbox could only reach GitHub and PyPI, so some
real datasets were identified but could not be downloaded there. Download them on a machine with open internet.

## Used (in the repository, with tests)
| Data | What it is | Used for | Where |
|---|---|---|---|
| IEEE PHM 2012 / FEMTO-ST PRONOSTIA bearings (public GitHub mirror `wkzs111/phm-ieee-2012-data-challenge-dataset`) | REAL accelerated run-to-failure vibration, 4 bearings, 3,094 snapshots (kept: RMS/peak/kurtosis per snapshot, 132 kB) | Characterising the Bat forecaster on real degradation | `real_data/phm2012_features.csv`, `validate_bat_phm2012.py`, `tests/test_real_data_phm2012.py` |
| TSNet 0.2.x (PyPI) + its Tnet0 example network | Independent Method-of-Characteristics solver, validated by its authors against the commercial Bentley HAMMER (reference *simulation*, not measurement) | Cross-checking the digital twin | `twin/reference/`, `twin/crosscheck_tsnet.py`, `tests/test_water_twin.py` |

Measured result on the real bearings (leave-one-bearing-out failure threshold, no peeking): Bat raised **no
false alerts in the first 80 % of life** and warned **before failure on all 4 bearings** (lead 90-410 s, i.e.
1-5 % of life); a simple change detector missed one bearing. The limit is physical: real bearing failures are
sudden-onset, so Bat gives a short warning, not a long-horizon RUL.

## Found but NOT downloadable from the sandbox (do these next)
| Data | Why it matters | Access |
|---|---|---|
| Zenodo 10.5281/zenodo.6984669 (Pan Bin, HK PolyU; CC-BY 4.0), file `experimentalhead.zip` (811 kB) | **Experimental water-hammer / burst pressure records from the University of Perugia** (+ numerical burst and leak cases `burstatpipe5/10.zip`, `leakatpipe5/10.zip`). The best real data for validating the digital twin and the reflex thresholds | Download, read the paper "Efficient pipe burst detection in tree-shape water distribution networks using forward-backward transient analysis" for the rig geometry, then compare the twin's pressure traces |
| 4TU.ResearchData `b07b9b7a-2065-4607-9d72-8885414c2854` (Halifax Water, pipe-burst warning with Bayesian DLMs) | Real DMA pressure/flow time series (hourly): tests slow-leak behaviour of the Bat forecaster, not transients | Download from data.4tu.nl |
| NASA PCoE Prognostics Data Repository / PHM Society mirror (`data.phmsociety.org/nasa`) | More run-to-failure data (batteries, turbofans, bearings) | Download from the mirror |

## Gaps: no suitable public data was found in reach
* **Aviation detect-and-avoid / fog**: no downloadable encounter or visibility dataset was reachable. Candidates to try:
  OpenSky Network ADS-B state vectors, the MIT-LL airspace encounter models, METAR visibility archives.
  Until then the aviation thresholds (2 s margin, 1500 m reference visibility) remain *named assumptions*.
* **5G URLLC latency**: the Irish 5G dataset (`uccmisl/5Gdataset`, real operator traces) contains throughput and
  radio KPIs but **no latency**, so it cannot validate the 2.5 ms log-normal model. Measure RTT on a real/testbed
  link (e.g. 5G testbed with `ping`/`irtt`) and fit with `math_model/run_math_model.py` (M4).

Citation for the bearing data: P. Nectoux et al., "PRONOSTIA: An experimental platform for bearings accelerated
degradation tests", IEEE PHM 2012, Denver. Check the licence of every dataset before redistributing it.
