# -*- coding: utf-8 -*-
"""
fix_line_reactance_hypersim.py

Replaces r/x/b on every AC line at v_nom >= MIN_VOLTAGE_KV with values
derived from the real, 60Hz, Quebec-sourced parameters in
network/overhead_line_parameters_by_voltage.csv (Menard 2023, Hypersim/EMTP
knowledge base), instead of PyPSA-Earth's generic default line type.

Why this was needed: every line's `type` in this project's networks is
"Al/St 560/50 4-bundle 750.0" (config.default.yaml's ac_types lookup,
unmodified from PyPSA-Earth's upstream default). That type comes from a
German textbook (Oeding & Oswald 2011) at 50Hz -- PyPSA's
apply_line_types() applies its r_per_length/x_per_length directly with no
frequency correction, and its susceptance formula uses the type's own
f_nom (50, not Quebec's 60) -- so every AC line's x/b in this project's
networks up to this fix were 50Hz-German-sourced, not the 60Hz Quebec data
already sitting unused in overhead_line_parameters_by_voltage.csv, which
was only ever wired into st_clair.py's thermal-capacity (s_nom) calc, not
the actual electrical model. Below 230kV the Hypersim table has no data,
so lines under MIN_VOLTAGE_KV are left on the PyPSA-Earth default
(a real, documented limitation, not silently ignored).

Method
------
Same log-log interpolation over the 5 tabulated voltage levels
(230/345/500/765/1100 kV) as st_clair.py's interpolate_params(), applied
to r_ohm_per_km/xl_ohm_per_km/bc_uS_per_km instead of zc/L/C. For each
affected line: r = r_per_km * length / num_parallel, x likewise, and
b = bc_per_km * length * num_parallel (susceptance combines in parallel by
adding, same convention as PyPSA's own apply_line_types()). `type` is
cleared so PyPSA's apply_line_types() (called by
n.calculate_dependent_values(), which every downstream script re-runs)
leaves these lines alone instead of recomputing them from the old type.

Usage
-----
    python network/fix_line_reactance_hypersim.py --network networks/elec_full.nc --in-place
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, NETWORK_DIR)
from st_clair import load_line_params  # noqa: E402

MIN_VOLTAGE_KV = 220.0  # below the tabulated range floor (230kV); nothing lower is touched


def interpolate_rxb(voltage_kv: float, ref: pd.DataFrame) -> tuple[float, float, float]:
    """Log-log interpolate (r_ohm_per_km, xl_ohm_per_km, bc_uS_per_km) at
    voltage_kv from the reference table -- same method as st_clair.py's
    interpolate_params(), applied to the series r/x/b columns instead."""
    v_ref = ref.index.to_numpy(dtype=float)
    log_v = np.log(v_ref)
    log_v_query = np.log(voltage_kv)

    def interp(col):
        return float(np.exp(np.interp(log_v_query, log_v, np.log(ref[col].to_numpy(dtype=float)))))

    return interp("r_ohm_per_km"), interp("xl_ohm_per_km"), interp("bc_uS_per_km")


def fix_reactance(n: pypsa.Network, min_voltage_kv: float = MIN_VOLTAGE_KV) -> pd.DataFrame:
    ref = load_line_params()

    # Baseline for the before/after comparison: x/r/b as originally computed
    # from the line's old `type` + length (the raw .nc file stores x=0 --
    # calculate_dependent_values() is only ever run at load time by whichever
    # script uses the network, never persisted -- so comparing against the
    # stored value would be comparing against an always-zero placeholder).
    n.calculate_dependent_values()

    lines = n.lines
    affected = lines.index[lines.v_nom >= min_voltage_kv]
    skipped = lines.index[lines.v_nom < min_voltage_kv]

    num_parallel = lines["num_parallel"].where(lines["num_parallel"] > 0, 1.0)

    old = lines.loc[affected, ["r", "x", "b", "x_pu"]].copy()

    for l in affected:
        v_nom = lines.at[l, "v_nom"]
        length = lines.at[l, "length"]
        np_l = num_parallel[l]
        r_per_km, x_per_km, b_per_km = interpolate_rxb(v_nom, ref)
        n.lines.at[l, "r"] = r_per_km * length / np_l
        n.lines.at[l, "x"] = x_per_km * length / np_l
        n.lines.at[l, "b"] = b_per_km * 1e-6 * length * np_l
        n.lines.at[l, "type"] = ""

    n.calculate_dependent_values()

    new = n.lines.loc[affected, ["r", "x", "b", "x_pu"]]
    diff = pd.DataFrame({
        "old_x_pu": old["x_pu"], "new_x_pu": new["x_pu"],
        "pct_change": 100 * (new["x_pu"] - old["x_pu"]) / old["x_pu"],
    })
    print(f"Fixed r/x/b on {len(affected)} lines (v_nom >= {min_voltage_kv:.0f} kV); "
          f"left {len(skipped)} lines below that untouched.")
    print(diff["pct_change"].describe().round(1))
    return diff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--min-voltage-kv", type=float, default=MIN_VOLTAGE_KV)
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    fix_reactance(n, min_voltage_kv=args.min_voltage_kv)

    output = args.network if args.in_place else (args.output or args.network)
    n.export_to_netcdf(output)
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
