# -*- coding: utf-8 -*-
"""
apply_hq_line_characteristics.py

Overrides r/x/b on 315kV and 735kV-tier AC lines with Hydro-Quebec's own
real line-characteristics table (network/hq_line_characteristics_by_voltage.csv,
transcribed from an HQ planning document -- Z1 positive-sequence impedance
and Y1 susceptance, exact per-voltage values, not interpolated) for exactly
the two tiers this project's reduced networks actually keep as topology:
315kV (the main working network's backbone floor) and 735kV (the dedicated
AC-PF-tractable reduction). 345kV lines are treated as 315kV-tier and 765kV
lines as 735kV-tier (electrically near-identical, same as
reduce_to_735kv.py's existing 735/765 unification). No other voltage level
is touched -- lines there keep PyPSA-Earth's generic default type.

Each affected line's `type` is cleared after r/x/b are written, so
PyPSA's apply_line_types() (run inside n.calculate_dependent_values(), which
every downstream script re-runs) skips it instead of recomputing r/x/b from
the old generic type; calculate_dependent_values() then only derives
x_pu/r_pu/b_pu from the r/x/b set here.

Usage
-----
    python network/apply_hq_line_characteristics.py --network networks/elec_full.nc --output networks/elec_full_hq.nc
"""
import argparse
import os
import sys

import pandas as pd
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARAMS_CSV = os.path.join(NETWORK_DIR, "hq_line_characteristics_by_voltage.csv")

# line v_nom -> which HQ table row to use
VOLTAGE_TIER_MAP = {315.0: 315.0, 345.0: 315.0, 735.0: 735.0, 765.0: 735.0}


def load_hq_params(csv_path: str = DEFAULT_PARAMS_CSV) -> pd.DataFrame:
    return pd.read_csv(csv_path, comment="#").set_index("voltage_kv")


def apply_hq_characteristics(n: pypsa.Network) -> pd.DataFrame:
    ref = load_hq_params()

    # Baseline for the before/after comparison -- x/r/b as currently computed
    # (from whatever type or prior fix is already in place).
    n.calculate_dependent_values()

    lines = n.lines
    tier = lines["v_nom"].map(VOLTAGE_TIER_MAP)
    affected = lines.index[tier.notna()]

    num_parallel = lines["num_parallel"].where(lines["num_parallel"] > 0, 1.0)
    old = lines.loc[affected, ["r", "x", "b", "x_pu"]].copy()

    for l in affected:
        row = ref.loc[tier[l]]
        length = lines.at[l, "length"]
        np_l = num_parallel[l]
        n.lines.at[l, "r"] = row["r_ohm_per_km"] * length / np_l
        n.lines.at[l, "x"] = row["xl_ohm_per_km"] * length / np_l
        n.lines.at[l, "b"] = row["bc_uS_per_km"] * 1e-6 * length * np_l
        n.lines.at[l, "type"] = ""

    n.calculate_dependent_values()

    new = n.lines.loc[affected, ["r", "x", "b", "x_pu"]]
    diff = pd.DataFrame({
        "v_nom": lines.loc[affected, "v_nom"],
        "old_x_pu": old["x_pu"], "new_x_pu": new["x_pu"],
        "pct_change": 100 * (new["x_pu"] - old["x_pu"]) / old["x_pu"],
    })
    print(f"Applied HQ line characteristics to {len(affected)} lines "
          f"(315/345 -> 315kV row, 735/765 -> 735kV row).")
    print(diff.groupby("v_nom")["pct_change"].describe()[["count", "mean", "min", "max"]].round(1))
    return diff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    apply_hq_characteristics(n)

    output = args.network if args.in_place else (args.output or args.network)
    n.export_to_netcdf(output)
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
