"""
Strict tests for twin/water_moc.py (digital twin of a water main, Method of Characteristics)
and for the MEC water engine's veto, using the twin as physical ground truth.

Layers of evidence:
  A. Solver correctness vs textbook analytic results (Joukowsky, wave period, no numerical
     amplification, friction decay, grid convergence, mass balance, steady state).
  B. Independent cross-check vs TSNet (separate MOC code validated by its authors against the
     commercial Bentley HAMMER) on TSNet's own Tnet0 network -- stored reference, runs offline.
  C. Physical soundness of the code's closed-form Joukowsky/Allievi veto estimate.
  D. Closed loop: burst -> reflex -> MEC engine -> valve closure -> measured surge.
"""
import json
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from twin.water_moc import (Pipe, WaterMainTwin, linear_closure, reference_main,
                            head_to_kpa, kpa_to_head, G, RHO)
from mec.domains.water_cognitive_engine import (WaterBHSCognitiveEngine, WaterHammerHermitCrabEvaluator,
                                                DEFAULT_WATER_CANDIDATE_ACTIONS)
from reflex.reflex_kernel import ReflexKernel
from reflex.domains.water import WaterPressureTransientTrigger

L, A_PIPE, A_WAVE, Q0 = 1000.0, 0.2, 1200.0, 0.1
D = math.sqrt(4 * A_PIPE / math.pi)
T_RT = 2 * L / A_WAVE                                   # wave round trip, 1.667 s
SURGE_LIMIT_KPA = 500.0
REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "twin", "reference",
                   "tsnet_reference.json")


def frictionless(dt=1 / 120, **kw):
    """dt = 1/120 s makes L/(a*dt) = 100 reaches exactly: no wave-speed adjustment."""
    return WaterMainTwin([Pipe(L, D, A_WAVE, 0.0)], q0_m3s=Q0, h_valve0_m=float(kpa_to_head(600.0)), dt_s=dt, **kw)


class TestSolverCorrectness(unittest.TestCase):
    def test_steady_state_is_stationary(self):
        r = reference_main().run(5.0)
        self.assertLess(np.abs(r.valve_kpa - r.valve_kpa[0]).max(), 1e-6)
        self.assertLess(np.abs(r.q_valve_m3s - Q0).max(), 1e-9)

    def test_joukowsky_exact_for_instantaneous_closure(self):
        tw = frictionless()
        self.assertAlmostEqual(tw.max_adj_pct, 0.0, places=9)
        r = tw.run(1.5, valve_tau=linear_closure(0.1, tw.dt, 0.0))
        expected = RHO * A_WAVE * (Q0 / A_PIPE) / 1000.0           # rho*a*V in kPa = 600
        self.assertAlmostEqual(r.peak_valve_surge_kpa(), expected, delta=0.005 * expected)

    def test_wave_period_is_4L_over_a(self):
        tw = frictionless()
        t0 = 0.1
        r = tw.run(8.0, valve_tau=linear_closure(t0, tw.dt, 0.0))
        dH = r.valve_kpa - r.valve_kpa[0]
        sign = np.sign(dH)
        flips = r.t[1:][np.diff(sign) != 0]
        flips = flips[flips > t0 + 0.5]
        for k, t_flip in enumerate(flips[:3], start=1):                  # sign changes every 2L/a
            self.assertAlmostEqual(t_flip, t0 + k * T_RT, delta=2 * tw.dt)

    def test_no_numerical_amplification_without_friction(self):
        tw = frictionless()
        r = tw.run(5 * 4 * L / A_WAVE, valve_tau=linear_closure(0.1, tw.dt, 0.0))
        self.assertLess(np.abs(r.valve_kpa - r.valve_kpa[0]).max(), 1.01 * 600.0)

    def test_friction_damps_the_oscillation(self):
        tw = WaterMainTwin([Pipe(L, D, A_WAVE, 0.05)], q0_m3s=Q0, h_valve0_m=float(kpa_to_head(600.0)), dt_s=1 / 120)
        r = tw.run(40.0, valve_tau=linear_closure(0.1, tw.dt, 0.0))
        env = np.abs(r.valve_kpa - r.valve_kpa[-1])
        period = int(round(4 * L / A_WAVE / tw.dt))
        peaks = [env[i:i + period].max() for i in range(0, len(env) - period, period)]
        self.assertTrue(all(b < a for a, b in zip(peaks, peaks[1:])), peaks)

    def test_grid_convergence(self):
        peaks = []
        for dt in (0.01, 0.005, 0.0025):
            r = reference_main(dt_s=dt).run(10.0, valve_tau=linear_closure(0.1, 3.0, 0.0))
            peaks.append(r.peak_valve_surge_kpa())
        self.assertLess(abs(peaks[0] - peaks[2]) / peaks[2], 0.01)
        self.assertLess(abs(peaks[1] - peaks[2]), abs(peaks[0] - peaks[2]) + 0.5)

    def test_mass_balance_with_steady_leak(self):
        tw = reference_main(dt_s=0.01, leak_x_m=500.0)
        area = 0.03 / (0.6 * math.sqrt(2 * G * 61.0))
        r = tw.run(60.0, leak_area=lambda t: area if t > 1.0 else 0.0)
        end = slice(-200, None)
        imbalance = r.q_inlet_m3s[end] - r.q_valve_m3s[end] - r.q_leak_m3s[end]
        self.assertLess(np.abs(imbalance).max(), 0.01 * Q0)               # within 1% of nominal flow
        self.assertGreater(r.q_leak_m3s[end].mean(), 0.5 * 0.03)          # leak really flows

    def test_input_validation(self):
        good = dict(q0_m3s=0.1, h_valve0_m=61.0, dt_s=0.01)
        with self.assertRaises(ValueError):
            WaterMainTwin([], **good)
        with self.assertRaises(ValueError):
            WaterMainTwin([Pipe(-5, 0.5)], **good)
        with self.assertRaises(ValueError):
            WaterMainTwin([Pipe(100, 0.5)], q0_m3s=0.0, h_valve0_m=61.0, dt_s=0.01)
        with self.assertRaises(ValueError):
            WaterMainTwin([Pipe(100, 0.5)], q0_m3s=0.1, h_valve0_m=61.0, dt_s=float("nan"))
        with self.assertRaises(ValueError):
            WaterMainTwin([Pipe(100, 0.5)], leak_x_m=500.0, **good)       # outside the pipe


class TestAgainstTSNet(unittest.TestCase):
    """TSNet = independent MOC implementation (validated vs Bentley HAMMER); reference stored offline."""

    @classmethod
    def setUpClass(cls):
        with open(REF) as fh:
            cls.ref = json.load(fh)

    def _twin_history(self, rule, h0):
        tc, ts, se, _ = rule
        tw = WaterMainTwin([Pipe(1200, 0.6, 1200, 0.0), Pipe(2400, 1.2, 1200, 0.0)],
                           q0_m3s=0.05, h_valve0_m=h0, dt_s=0.01)
        return tw.run(20.0, valve_tau=linear_closure(ts, tc, se)).head_valve_m

    def test_peak_surge_and_waveform_match_tsnet(self):
        for name, c in self.ref["cases"].items():
            h = self._twin_history(c["rule"], c["h0"])
            surge_ref = c["peak"] - c["h0"]
            surge_twin = h.max() - h[0]
            self.assertAlmostEqual(surge_twin / surge_ref, 1.0, delta=0.02, msg=name)
            theirs = np.array(c["h"])
            mine = h[::10][:len(theirs)]
            rms = float(np.sqrt(np.mean((mine - theirs[:len(mine)]) ** 2)))
            self.assertLess(rms / surge_ref, 0.03, msg=f"{name}: rms {rms:.3f} m")


class TestMecVetoPhysicalSoundness(unittest.TestCase):
    """Using the twin as ground truth for the code's closed-form surge estimate."""

    GRID_DQ = (10, 25, 50, 75, 100)
    GRID_TC = (0.05, 0.5, 1.0, 1.67, 2.0, 3.0, 5.0, 10.0, 30.0)

    @classmethod
    def setUpClass(cls):
        cls.hc = WaterHammerHermitCrabEvaluator()
        cls.rows = []
        for dq in cls.GRID_DQ:
            for tc in cls.GRID_TC:
                r = reference_main(dt_s=0.01).run(40.0, valve_tau=linear_closure(0.1, tc, 1 - dq / 100))
                est = cls.hc._estimate_surge_kpa(-dq, tc if tc > 0.05 else None)
                cls.rows.append((dq, tc, est, r.peak_valve_surge_kpa()))

    def test_no_action_allowed_by_the_veto_exceeds_the_limit(self):
        """SAFETY: if the engine allows it, the real surge must stay below the 500 kPa limit."""
        for dq, tc, est, real in self.rows:
            if est < SURGE_LIMIT_KPA:
                self.assertLess(real, SURGE_LIMIT_KPA, f"dQ={dq} tc={tc}: est {est:.0f} real {real:.0f}")

    def test_estimate_is_conservative_except_full_fast_closure(self):
        for dq, tc, est, real in self.rows:
            if not (dq == 100 and tc < T_RT + 0.01):
                self.assertLessEqual(real, est * 1.005, f"dQ={dq} tc={tc}")

    def test_worst_underestimate_is_small_and_known(self):
        worst = max(real / est for _, _, est, real in self.rows)
        self.assertLess(worst, 1.02)         # measured 1.012: full instantaneous closure, already vetoed

    def test_default_candidates_classified_correctly_by_physics(self):
        a = self.hc.assess(0.9, DEFAULT_WATER_CANDIDATE_ACTIONS)
        for c in DEFAULT_WATER_CANDIDATE_ACTIONS:
            if c.flow_rate_change_lps == 0:
                continue
            dq = abs(c.flow_rate_change_lps)
            tc = c.closure_time_s if c.closure_time_s else 0.05
            real = reference_main(dt_s=0.01).run(60.0, valve_tau=linear_closure(0.1, tc, 1 - dq / 100)
                                                 ).peak_valve_surge_kpa()
            self.assertEqual(c.name in a.vetoed_actions, real >= SURGE_LIMIT_KPA, f"{c.name}: real {real:.0f} kPa")

    def test_controlled_shutdown_really_is_gentle(self):
        r = reference_main(dt_s=0.01).run(60.0, valve_tau=linear_closure(0.1, 30.0, 0.0))
        self.assertLess(r.peak_valve_surge_kpa(), 0.1 * 600.0)             # < 60 kPa vs 600 for the slam


class TestClosedLoop(unittest.TestCase):
    DT = 0.01
    BURST_X = 500.0
    BURST_T = 1.0

    @staticmethod
    def _area(flow_lps):
        return (flow_lps / 1000.0) / (0.6 * math.sqrt(2 * G * 61.0))

    def _detect(self, flow_lps, open_time=0.02):
        """Return (first_trigger_time or None, number of triggers before the burst, twin result)."""
        tw = reference_main(dt_s=self.DT, leak_x_m=self.BURST_X, sensor_x_m=1000.0)
        area = self._area(flow_lps)
        res = tw.run(3.0, leak_area=lambda t: 0.0 if t < self.BURST_T else min(1.0, (t - self.BURST_T) / open_time) * area)
        kernel = ReflexKernel(WaterPressureTransientTrigger())
        prior = res.sensor_kpa[0]
        first, false_alarms = None, 0
        for t, p in zip(res.t[1:], res.sensor_kpa[1:]):
            ev = kernel.evaluate({"pressure_kpa": float(p), "prior_pressure_kpa": float(prior), "dt_s": self.DT})
            prior = p
            if ev.triggered:
                if t < self.BURST_T:
                    false_alarms += 1
                elif first is None:
                    first = t
        return first, false_alarms, res

    def test_no_false_alarms_before_the_burst(self):
        _, false_alarms, _ = self._detect(30.0)
        self.assertEqual(false_alarms, 0)

    def test_burst_detected_when_the_pressure_wave_arrives(self):
        first, _, _ = self._detect(30.0)
        arrival = self.BURST_T + (1000.0 - self.BURST_X) / A_WAVE
        self.assertIsNotNone(first)
        self.assertGreaterEqual(first, arrival - 2 * self.DT)             # causality: not before the wave
        self.assertLessEqual(first, arrival + 4 * self.DT)                # and promptly after

    def test_detection_threshold_matches_physics(self):
        """Fast burst of flow Q -> wave of rho*a*Q/(2A) kPa; 15 kPa floor => Qmin = 2A*15kPa/(rho*a) = 5 L/s."""
        q_min_lps = 2 * A_PIPE * 15_000.0 / (RHO * A_WAVE) * 1000.0
        self.assertAlmostEqual(q_min_lps, 5.0, places=6)
        self.assertIsNotNone(self._detect(8.0, open_time=0.005)[0])        # clearly above the limit
        self.assertIsNone(self._detect(2.5, open_time=0.005)[0])           # below: invisible to the reflex

    def test_engine_choice_is_always_physically_safe(self):
        """For every leak severity: execute the engine's chosen action in the twin, surge must stay < limit."""
        eng = WaterBHSCognitiveEngine()
        for sev in np.linspace(0.0, 1.0, 21):
            res = eng.evaluate([600.0 - 3 * i for i in range(15)], float(sev))
            act = next(c for c in eng.candidate_actions if c.name == res.control.chosen_action)
            dq = abs(act.flow_rate_change_lps)
            if dq == 0:
                continue
            tc = act.closure_time_s if act.closure_time_s else 0.05
            r = reference_main(dt_s=0.01).run(60.0, valve_tau=linear_closure(0.1, tc, 1 - dq / 100))
            self.assertLess(r.peak_valve_surge_kpa(), SURGE_LIMIT_KPA, f"severity {sev:.2f}: {act.name}")

    def test_without_the_veto_the_slam_would_be_unsafe(self):
        """The veto is necessary: the instantaneous full shutdown really exceeds the limit."""
        r = reference_main(dt_s=0.01).run(10.0, valve_tau=linear_closure(0.1, 0.05, 0.0))
        self.assertGreater(r.peak_valve_surge_kpa(), SURGE_LIMIT_KPA)

    def test_burst_then_mitigation_stays_below_limit(self):
        """Full chain: burst -> detected -> engine decides -> valve closes: peak valve pressure bounded."""
        first, _, _ = self._detect(30.0)
        self.assertIsNotNone(first)
        sev = 0.8
        res = WaterBHSCognitiveEngine().evaluate([600.0 - 3 * i for i in range(15)], sev)
        act = next(c for c in WaterBHSCognitiveEngine().candidate_actions if c.name == res.control.chosen_action)
        dq = abs(act.flow_rate_change_lps)
        tc = act.closure_time_s if act.closure_time_s else 0.05
        tw = reference_main(dt_s=self.DT, leak_x_m=self.BURST_X)
        area = self._area(30.0)
        out = tw.run(60.0, valve_tau=linear_closure(first + 0.003, tc, 1 - dq / 100),
                     leak_area=lambda t: 0.0 if t < self.BURST_T else min(1.0, (t - self.BURST_T) / 0.02) * area)
        self.assertLess(out.peak_valve_surge_kpa(), SURGE_LIMIT_KPA)


if __name__ == "__main__":
    unittest.main()
