# -*- coding: utf-8 -*-
"""
fix_line_lengths_from_geometry.py

10 of 287 geometry-bearing lines in the base network have a `length`
attribute wildly inconsistent with their own stored geometry (ratio to the
geometry's real cumulative distance as low as 0.02 -- i.e. length claims 2%
of the real path). Worst case: line 1358 (buses 1638-2913, the Fermont
corridor) claims 9.5 km against a geometry that traces 149.8 km. Three
others (194-1715) claim under 1 km for paths that are really ~11.7 km. This
is a pre-existing data issue in the OSM-derived base topology, not
something introduced by any script in this project.

Since r/x/b (via apply_line_types) and the St-Clair-derived s_nom both
scale with `length`, an understated length produces an understated
impedance -- tolerated by LOPF/DC-PF's linear solve, but enough to make AC
PF's Newton-Raphson diverge (x_pu down to 2.3e-7 on the worst line, an
8,071x spread in the Y-matrix diagonal magnitude).

Fix: recompute `length` for every line from its own geometry (haversine
cumulative distance across all MultiLineString segments) wherever that
disagrees with the stored value by more than 3x either way -- the
geometry itself is intact, only `length` is wrong.

Usage
-----
    python network/fix_line_lengths_from_geometry.py --network networks/elec.nc --in-place
"""
import argparse
import os

import numpy as np
import pandas as pd
import pypsa
from shapely import wkt

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec.nc")
RATIO_LOW, RATIO_HIGH = 0.3, 3.0


def haversine_km(lon1, lat1, lon2, lat2):
    R = 6371.0
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def geom_length_km(geom):
    if isinstance(geom, str):
        geom = wkt.loads(geom)
    parts = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
    total = 0.0
    for part in parts:
        coords = list(part.coords)
        for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
            total += haversine_km(lon1, lat1, lon2, lat2)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    n_fixed = 0
    for l, row in n.lines.iterrows():
        if row.geometry is None or (isinstance(row.geometry, float) and pd.isna(row.geometry)):
            continue
        try:
            glen = geom_length_km(row.geometry)
        except Exception:
            continue
        if glen <= 0:
            continue
        ratio = row.length / glen
        if ratio < RATIO_LOW or ratio > RATIO_HIGH:
            print(f"  line {l} ({row.bus0}-{row.bus1}): length {row.length:.2f} km -> {glen:.2f} km "
                  f"(was {ratio:.1%} of its own geometry's real distance)")
            n.lines.at[l, "length"] = glen
            n_fixed += 1

    print(f"\nFixed length on {n_fixed} lines")

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_lengthfixed{ext}"
    n.export_to_netcdf(output)
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
