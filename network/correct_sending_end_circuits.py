# -*- coding: utf-8 -*-
"""
correct_sending_end_circuits.py

Corrects the circuit count on the 735kV lines leaving Quebec's major
generating stations. Hydro-Quebec builds these corridors as 3 parallel
circuits; the OSM-derived topology this project starts from under-counts
them on five corridors (114-1291, 114-148, 340-457, 66-457, 651-25), each
captured as only 1 or 2 separate Line objects instead of 3 circuits' worth
of capacity. Left uncorrected, this overstates the effective series
reactance on exactly the lines that are the tightest constraint on AC power
flow convergence.

Where OSM already split a corridor into two Line objects (114-148: lines
1049/1448; 66-457: lines 156/1938), num_parallel on each is raised to 1.5 so
the pair together represents 3 circuits. Where only one Line object exists
(114-1291: 2408; 340-457: 162; 651-25: 3756), num_parallel is set to 3.0
directly on that line.

r, x and b are then recomputed from Hydro-Quebec's per-km 735kV rates (the
same table and formula as apply_hq_line_characteristics.py) using the
corrected num_parallel, so the effect is consistent with how every other
735/765kV line's parameters are derived. s_nom is scaled by the same
circuit-count ratio, since it was computed per Line object upstream.

Runs on the 735kV network (after reduce_to_735kv.py) and before
apply_series_compensation.py -- recomputing x here resets any compensation
already applied to these lines.

Usage
-----
    python network/correct_sending_end_circuits.py --network networks_current/elec_735kv.nc --in-place
"""
import argparse

import pandas as pd
import pypsa

from apply_hq_line_characteristics import VOLTAGE_TIER_MAP, load_hq_params

# Corrected total circuit count per corridor, split evenly across however
# many Line objects OSM already represents that corridor with.
NEW_NUM_PARALLEL = {
    "2408": 3.0,   # 114-1291, single Line object -> 3 circuits
    "162": 3.0,    # 340-457, single Line object -> 3 circuits
    "3756": 3.0,   # 651-25, single Line object -> 3 circuits
    "1049": 1.5,   # 114-148 circuit 1 of 2 Line objects -> 3 circuits total
    "1448": 1.5,   # 114-148 circuit 2 of 2 Line objects -> 3 circuits total
    "156": 1.5,    # 66-457 circuit 1 of 2 Line objects -> 3 circuits total
    "1938": 1.5,   # 66-457 circuit 2 of 2 Line objects -> 3 circuits total
}


def correct_circuits(n: pypsa.Network, corrections: dict = NEW_NUM_PARALLEL) -> pd.DataFrame:
    ref = load_hq_params()
    present = {l: npar for l, npar in corrections.items() if l in n.lines.index}
    missing = set(corrections) - set(present)
    if missing:
        print(f"  [warn] {len(missing)} target line(s) not present in this network, skipping: {sorted(missing)}")

    old = n.lines.loc[list(present), ["num_parallel", "r", "x", "b", "s_nom"]].copy()
    for l, npar in present.items():
        tier = VOLTAGE_TIER_MAP[n.lines.at[l, "v_nom"]]
        row = ref.loc[tier]
        length = n.lines.at[l, "length"]
        n.lines.at[l, "s_nom"] *= npar / max(n.lines.at[l, "num_parallel"], 1e-9)
        n.lines.at[l, "num_parallel"] = npar
        n.lines.at[l, "r"] = row["r_ohm_per_km"] * length / npar
        n.lines.at[l, "x"] = row["xl_ohm_per_km"] * length / npar
        n.lines.at[l, "b"] = row["bc_uS_per_km"] * 1e-6 * length * npar
    n.calculate_dependent_values()

    new = n.lines.loc[list(present), ["num_parallel", "r", "x", "b", "s_nom"]]
    diff = old.join(new, lsuffix="_old", rsuffix="_new")
    print(f"Corrected circuit count on {len(present)} sending-end lines:")
    print(diff.round(2).to_string())
    return diff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)
    correct_circuits(n)

    out = args.network if args.in_place else args.output
    if out is None:
        raise SystemExit("Provide --output or pass --in-place.")
    n.export_to_netcdf(out)
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
