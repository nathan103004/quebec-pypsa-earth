# -*- coding: utf-8 -*-
"""
summarize_735kv_acpf.py

AC power flow summary of the 735kV network in three reactive-support cases:

- No reactive support: the 15 compensated lines restored to their
  uncompensated HQ per-km reactance (circuit-count correction kept).
- Series compensation: elec_735kv.nc as built.
- Series compensation + shunts: elec_735kv_shunt.nc.

For each case: convergence, voltage range, buses/hours outside the
0.95-1.05 pu band (with time of day and system demand when it happens),
largest line angle, and max line loading (P and S against s_nom). Results
cover converged snapshots only.

Usage
-----
    python network/summarize_735kv_acpf.py --output network/networks_current/summary_735kv_acpf.csv
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, NETWORK_DIR)
import run_pf  # noqa: E402
from apply_hq_line_characteristics import VOLTAGE_TIER_MAP, load_hq_params  # noqa: E402
from apply_series_compensation import TARGET_LINES  # noqa: E402

CURRENT = os.path.join(NETWORK_DIR, "networks_current")
V_MIN, V_MAX = 0.95, 1.05


def remove_series_compensation(n: pypsa.Network) -> None:
    ref = load_hq_params()
    for l in [l for l in TARGET_LINES if l in n.lines.index]:
        x_per_km = ref.loc[VOLTAGE_TIER_MAP[n.lines.at[l, "v_nom"]], "xl_ohm_per_km"]
        n.lines.at[l, "x"] = x_per_km * n.lines.at[l, "length"] / n.lines.at[l, "num_parallel"]


def hours(series: pd.Series) -> str:
    return ",".join(f"{h:02d}" for h in series[series > 0].sort_values(ascending=False).head(4).index) or "-"


def summarize(n: pypsa.Network) -> dict:
    n.calculate_dependent_values()
    n.determine_network_topology()
    run_pf.prepare_for_ac_pf(n, pv_min_capacity=0.0, pv_max_load_ratio=1.0)
    n.lpf(n.snapshots)
    c = n.pf(n.snapshots, use_seed=True).converged.all(axis=1)
    v = n.buses_t.v_mag_pu.loc[c]
    demand = n.loads_t.p_set.sum(axis=1)[c]
    p0, q0 = n.lines_t.p0.loc[c], n.lines_t.q0.loc[c]
    ang = np.degrees(n.buses_t.v_ang.loc[c])
    dang = np.abs(ang[n.lines.bus0.values].values - ang[n.lines.bus1.values].values)
    sh = n.shunt_impedances
    q_sh = sh.b * n.buses.v_nom.reindex(sh.bus).values ** 2
    r = {
        "Converged": f"{int(c.sum())}/{len(c)}",
        "Voltage range (pu)": f"{v.min().min():.3f}-{v.max().max():.3f}",
        "Shunt capacitors (MVAr)": f"{int((q_sh > 0).sum())} ({q_sh[q_sh > 0].sum():+,.0f})" if len(sh) else "-",
        "Shunt reactors (MVAr)": f"{int((q_sh < 0).sum())} ({q_sh[q_sh < 0].sum():+,.0f})" if len(sh) else "-",
    }
    for label, mask in [("Under 0.95", v < V_MIN), ("Over 1.05", v > V_MAX)]:
        per_hour = mask.sum(axis=1)
        out = per_hour > 0
        r[f"{label}: buses"] = int(mask.any().sum())
        r[f"{label}: hours"] = int(out.sum())
        r[f"{label}: worst hours of day"] = hours(per_hour.groupby(per_hour.index.hour).sum())
        r[f"{label}: demand (GW)"] = f"{demand[out].min()/1e3:.1f}-{demand[out].max()/1e3:.1f}" if out.any() else "-"
    r["Largest line angle (deg)"] = round(float(dang.max()), 1)
    r["Lines > 30 deg"] = int((dang.max(axis=0) > 30).sum())
    r["Max loading P (%)"] = round(float((p0.abs() / n.lines.s_nom).max().max() * 100), 1)
    r["Max loading S (%)"] = round(float((np.sqrt(p0**2 + q0**2) / n.lines.s_nom).max().max() * 100), 1)
    return r


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=os.path.join(CURRENT, "elec_735kv.nc"))
    parser.add_argument("--shunt-network", default=os.path.join(CURRENT, "elec_735kv_shunt.nc"))
    parser.add_argument("--output", default=os.path.join(CURRENT, "summary_735kv_acpf.csv"))
    args = parser.parse_args()

    none = pypsa.Network(args.network)
    remove_series_compensation(none)
    cases = {
        "No reactive support": none,
        "Series compensation": pypsa.Network(args.network),
        "Series compensation + shunts": pypsa.Network(args.shunt_network),
    }
    df = pd.DataFrame({label: summarize(n) for label, n in cases.items()})
    df.to_csv(args.output)
    pd.set_option("display.width", 200)
    print(df.to_string())
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
