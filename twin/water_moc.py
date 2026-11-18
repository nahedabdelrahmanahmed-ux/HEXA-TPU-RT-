"""
twin/water_moc.py -- digital twin of a water main: 1-D water-hammer solver using the
Method of Characteristics (Wylie & Streeter). Series of pipes, upstream reservoir,
downstream orifice-type valve, optional leak/burst orifice at an interior node.

Why it exists: the MEC water engine vetoes actions with a closed-form Joukowsky estimate
(S = rho*a*dV * min(1, (2L/a)/t_c)). The twin is the physical ground truth used to test
whether that estimate is SAFE (never allows an action that really exceeds the limit).

Units: SI. Heads in metres of water; pressures reported in kPa (rho = 1000, g = 9.81).
Notation: B = a/(gA);  R = f*dx/(2 g D A^2);  C+ : Hp = Cp - B*Qp ;  C- : Hp = Cm + B*Qp.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence
import numpy as np

G = 9.81
RHO = 1000.0


def head_to_kpa(h_m):
    return RHO * G * np.asarray(h_m) / 1000.0


def kpa_to_head(p_kpa):
    return np.asarray(p_kpa) * 1000.0 / (RHO * G)


@dataclass
class Pipe:
    length_m: float
    diameter_m: float
    wave_speed_m_s: float = 1200.0
    friction_f: float = 0.02          # Darcy-Weisbach


@dataclass
class TwinResult:
    t: np.ndarray                     # s
    head_valve_m: np.ndarray
    head_sensor_m: np.ndarray
    q_valve_m3s: np.ndarray
    q_leak_m3s: np.ndarray
    q_inlet_m3s: np.ndarray
    sensor_index: int
    dt_s: float
    h_valve0_m: float
    max_wave_speed_adjustment_pct: float

    @property
    def valve_kpa(self):
        return head_to_kpa(self.head_valve_m)

    @property
    def sensor_kpa(self):
        return head_to_kpa(self.head_sensor_m)

    def peak_valve_surge_kpa(self) -> float:
        """Max valve pressure minus its initial (steady) pressure."""
        return float(self.valve_kpa.max() - head_to_kpa(self.h_valve0_m))


class WaterMainTwin:
    def __init__(self, pipes: Sequence[Pipe], q0_m3s: float, h_valve0_m: float,
                 dt_s: float = 0.005, sensor_x_m: Optional[float] = None,
                 leak_x_m: Optional[float] = None, leak_cd: float = 0.6):
        if not pipes:
            raise ValueError("at least one pipe is required")
        for p in pipes:
            if not (p.length_m > 0 and p.diameter_m > 0 and p.wave_speed_m_s > 0 and p.friction_f >= 0):
                raise ValueError(f"invalid pipe {p}")
        if not (q0_m3s > 0 and h_valve0_m > 0 and dt_s > 0):
            raise ValueError("q0, h_valve0 and dt must be positive")
        if not math.isfinite(q0_m3s + h_valve0_m + dt_s):
            raise ValueError("non-finite parameter")
        self.pipes = list(pipes)
        self.q0 = float(q0_m3s)
        self.h_valve0 = float(h_valve0_m)
        self.dt = float(dt_s)
        self.leak_cd = leak_cd
        # --- discretisation: integer reaches per pipe, wave speed adjusted so dx = a*dt (standard)
        self.n_reach, self.a_adj, self.dx = [], [], []
        max_adj = 0.0
        for p in self.pipes:
            n = max(1, round(p.length_m / (p.wave_speed_m_s * self.dt)))
            a_adj = p.length_m / (n * self.dt)
            max_adj = max(max_adj, abs(a_adj - p.wave_speed_m_s) / p.wave_speed_m_s * 100)
            self.n_reach.append(n); self.a_adj.append(a_adj); self.dx.append(p.length_m / n)
        self.max_adj_pct = max_adj
        # per-reach coefficients
        B, Rf = [], []
        for p, n, a, dx in zip(self.pipes, self.n_reach, self.a_adj, self.dx):
            A = math.pi * p.diameter_m ** 2 / 4.0
            B += [a / (G * A)] * n
            Rf += [p.friction_f * dx / (2 * G * p.diameter_m * A ** 2)] * n
        self.B, self.R = np.array(B), np.array(Rf)
        self.n_nodes = len(self.B) + 1
        # node x-positions
        xs = [0.0]
        for n, dx in zip(self.n_reach, self.dx):
            xs += [xs[-1] + dx * (k + 1) for k in range(n)]
        self.x = np.array(xs)
        self.total_length = self.x[-1]
        for name, pos in (("sensor_x_m", sensor_x_m), ("leak_x_m", leak_x_m)):
            if pos is not None and not (0.0 <= pos <= self.total_length):
                raise ValueError(f"{name}={pos} outside the pipeline [0, {self.total_length}] m")
        if leak_x_m is not None and not (0.0 < leak_x_m < self.total_length):
            raise ValueError("leak must be at an interior node")
        self.sensor_index = self._nearest(sensor_x_m if sensor_x_m is not None else self.total_length)
        self.leak_index = None if leak_x_m is None else self._nearest(leak_x_m)
        # steady state: heads rise going upstream by friction loss
        self.H0 = np.empty(self.n_nodes)
        self.H0[-1] = self.h_valve0
        for j in range(self.n_nodes - 2, -1, -1):
            self.H0[j] = self.H0[j + 1] + self.R[j] * self.q0 * abs(self.q0)
        self.h_res = self.H0[0]

    def _nearest(self, x):
        return int(np.argmin(np.abs(self.x - x)))

    # ------------------------------------------------------------------
    def run(self, t_end_s: float,
            valve_tau: Callable[[float], float] = lambda t: 1.0,
            leak_area: Callable[[float], float] = lambda t: 0.0,
            observer: Optional[Callable[[float, float, float], None]] = None) -> TwinResult:
        """valve_tau(t): valve opening 0..1 (1 = steady). leak_area(t): orifice area m^2 at leak node.
        observer(t, head_sensor_m, head_valve_m) is called after every step (closed-loop hook)."""
        n_steps = int(round(t_end_s / self.dt))
        H = self.H0.copy()
        Q = np.full(self.n_nodes, self.q0)       # flow seen by the reach on the node's LEFT
        Qr = Q.copy()                            # flow seen by the reach on the node's RIGHT (differs at a leak)
        B, R = self.B, self.R
        hv, hs, qv, ql, qi, ts = [H[-1]], [H[self.sensor_index]], [Q[-1]], [0.0], [Q[0]], [0.0]
        k = self.leak_index
        for s in range(1, n_steps + 1):
            t = s * self.dt
            Hn, Qn = np.empty_like(H), np.empty_like(Q)
            Qlk = 0.0
            # interior nodes
            Cp = H[:-2] + B[:-1] * Qr[:-2] - R[:-1] * Qr[:-2] * np.abs(Qr[:-2])
            Cm = H[2:] - B[1:] * Q[2:] + R[1:] * Q[2:] * np.abs(Q[2:])
            Qn[1:-1] = (Cp - Cm) / (B[:-1] + B[1:])
            Hn[1:-1] = Cp - B[:-1] * Qn[1:-1]
            Qrn = Qn.copy()
            # leak at interior node k: continuity Qup = Qdn + Cd*A*sqrt(2gH)
            if k is not None and 0 < k < self.n_nodes - 1:
                AL = leak_area(t)
                if AL > 0.0:
                    K = self.leak_cd * AL * math.sqrt(2 * G)
                    cp, cm = Cp[k - 1], Cm[k - 1]
                    bl, br = B[k - 1], B[k]
                    # Hp = cp - bl*Qu ; Hp = cm + br*Qd ; Qu = Qd + K*sqrt(Hp)
                    # -> (cp-Hp)/bl = (Hp-cm)/br + K sqrt(Hp) ; solve for s = sqrt(Hp):
                    # s^2 (1/bl+1/br) + K s - (cp/bl + cm/br) = 0
                    a2, b1, c0 = (1 / bl + 1 / br), K, -(cp / bl + cm / br)
                    sq = (-b1 + math.sqrt(b1 * b1 - 4 * a2 * c0)) / (2 * a2)
                    Hp = max(sq, 0.0) ** 2
                    Qn[k] = (cp - Hp) / bl                 # flow arriving from upstream
                    Qrn[k] = (Hp - cm) / br                # flow leaving downstream (= Qn[k] - Qleak)
                    Hn[k] = Hp
                    Qlk = K * math.sqrt(Hp)
            # upstream reservoir
            Cm0 = H[1] - B[0] * Q[1] + R[0] * Q[1] * abs(Q[1])
            Hn[0] = self.h_res
            Qn[0] = (self.h_res - Cm0) / B[0]
            # downstream valve: Q = tau*Q0*sqrt(H/Hv0)
            Cpv = H[-2] + B[-1] * Qr[-2] - R[-1] * Qr[-2] * abs(Qr[-2])
            tau = min(1.0, max(0.0, valve_tau(t)))
            Cv = (tau * self.q0) ** 2 / self.h_valve0
            if Cv <= 0.0:
                Qn[-1] = 0.0
                Hn[-1] = Cpv
            else:
                Qn[-1] = (-B[-1] * Cv + math.sqrt((B[-1] * Cv) ** 2 + 4 * Cv * Cpv)) / 2
                Hn[-1] = Cpv - B[-1] * Qn[-1]
            Qrn[0], Qrn[-1] = Qn[0], Qn[-1]
            H, Q, Qr = Hn, Qn, Qrn
            hv.append(H[-1]); hs.append(H[self.sensor_index]); qv.append(Q[-1]); ql.append(Qlk); qi.append(Q[0]); ts.append(t)
            if observer is not None:
                observer(t, H[self.sensor_index], H[-1])
        return TwinResult(np.array(ts), np.array(hv), np.array(hs), np.array(qv), np.array(ql), np.array(qi),
                          self.sensor_index, self.dt, self.h_valve0, self.max_adj_pct)


def linear_closure(t_start: float, t_close: float, tau_final: float = 0.0):
    """Valve opening: 1 until t_start, then linear to tau_final over t_close seconds."""
    def tau(t):
        if t <= t_start:
            return 1.0
        if t >= t_start + t_close:
            return tau_final
        return 1.0 - (1.0 - tau_final) * (t - t_start) / t_close
    return tau


def reference_main(dt_s: float = 0.005, **kw) -> WaterMainTwin:
    """The water main the MEC engine's constants describe: L = 1000 m, A = 0.2 m^2,
    a = 1200 m/s, Q0 = 100 L/s, valve pressure 600 kPa, f = 0.02."""
    D = math.sqrt(4 * 0.2 / math.pi)
    return WaterMainTwin([Pipe(1000.0, D, 1200.0, 0.02)], q0_m3s=0.1,
                         h_valve0_m=float(kpa_to_head(600.0)), dt_s=dt_s, **kw)
