# -*- coding: utf-8 -*-
"""
apply_series_compensation.py

Reduces series reactance on specific long 735kV lines via a capacitor bank
in series with the conductor (x_new = x * (1 - k)) -- the standard real-world
fix for a line whose length alone makes its reactance the limiting factor on
both power transfer and voltage drop. Hydro-Quebec's own 735kV network is
known for exactly this kind of compensation on its longest corridors.

Targets the 10 lines identified as feeding the three buses (308, 1291, 312)
found to be the sole points of AC PF voltage collapse on the
line-reactance-corrected 735kV network (network/docs/POWER_FLOW.md) -- all
are 250-500km lines with either no local generation or, for 312, real but
non-voltage-controlling generation. The short (95.8km) 312-310 line is
deliberately excluded: it isn't part of the long-line problem this is
fixing.

Applied on top of apply_hq_line_characteristics.py (after it has already
set the line's real x -- this just scales it down further), and before
reduce_voltage_network.py, since these are
original OSM line ids that pass through every later reduction stage
unchanged.

COMPENSATION_FRACTION (0.50) is a generic, undocumented-in-real-data
assumption -- typical real-world EHV series compensation runs roughly
30-70% on the longest corridors; 50% is a reasonable mid-range planning
value, not a measured Hydro-Quebec figure for these specific lines. Real
series compensation also risks sub-synchronous resonance at high
compensation degrees -- not modeled here (steady-state power flow only),
one reason compensation isn't pushed higher.

Usage
-----
    python network/apply_series_compensation.py --network networks/elec_full.nc --output networks/elec_full_seriescomp.nc
"""
import argparse

import pypsa

COMPENSATION_FRACTION = 0.50

TARGET_LINES = {
    "2408": "114-1291 (500.6km, sole supply path to bus 1291)",
    "880": "312-308 circuit 1 (433.4km)",
    "1935": "312-308 circuit 2 (409.5km)",
    "2410": "312-308 circuit 3 (409.6km)",
    "1536": "308-2944 (256.2km)",
    "1538": "308-1081 (266.1km)",
    "1936": "308-136 (348.0km)",
    "163": "457-312 circuit 1 (344.7km)",
    "1655": "457-312 circuit 2 (344.6km)",
    "2386": "457-312 circuit 3 (378.2km)",
}


def apply_compensation(n: pypsa.Network, lines=None, fraction: float = COMPENSATION_FRACTION) -> None:
    lines = lines or list(TARGET_LINES)
    n.calculate_dependent_values()
    missing = [l for l in lines if l not in n.lines.index]
    if missing:
        print(f"  [warn] {len(missing)} target line(s) not present in this network, skipping: {missing}")
    present = [l for l in lines if l in n.lines.index]
    old_x = n.lines.loc[present, "x"].copy()
    n.lines.loc[present, "x"] = old_x * (1 - fraction)
    n.calculate_dependent_values()
    print(f"Applied {fraction:.0%} series compensation to {len(present)} lines:")
    for l in present:
        print(f"  {l} ({TARGET_LINES.get(l, '?')}): x {old_x[l]:.2f} -> {n.lines.at[l, 'x']:.2f} ohm")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--fraction", type=float, default=COMPENSATION_FRACTION)
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    apply_compensation(n, fraction=args.fraction)

    output = args.network if args.in_place else (args.output or args.network)
    n.export_to_netcdf(output)
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
