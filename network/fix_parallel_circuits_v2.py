# -*- coding: utf-8 -*-
"""
fix_parallel_circuits_v2.py

Correct undercounted parallel circuits across Quebec's whole >=315 kV
backbone (735, 315, 765 kV), superseding fix_parallel_circuits.py (which
only covered 735 kV, via the 67 corridors captured by one "collection"
relation).

Source: every individual Hydro-Quebec-operated line circuit tagged as
its own OSM relation within Quebec's bounding box (99 relations at
735/315/765 kV -- see network/hq_all_circuit_relations.json for the
inventory query and network/hq_backbone_circuits_full.json for the full
fetch). Each relation is one circuit; its two route endpoints are found
as the two "terminal" points appearing exactly once among all its member
ways' endpoints (i.e. not shared with another way in the same relation --
robust to multi-segment ways, unlike just taking first/last coordinates).
84/99 relations resolved to exactly 2 terminals (15 skipped -- branched
or incomplete geometry, not usable this way).

Corridors are formed by clustering terminal points within ~1 km (not by
matching to named substations -- the available substation list is
735kV-focused and incomplete for 315 kV) -- 84 circuits collapse to 55
real corridors: 36 single-circuit, 11 double, 6 triple, 2 quadruple.

Method: same as fix_parallel_circuits.py -- match our network's own
line endpoints to the nearest real corridor cluster, compare modeled
line-row count per corridor against the real circuit count, and ADD
(never remove) lines to close any shortfall, using the shortfall
corridor's own existing line as a template for type/length and
St Clair (network/st_clair.py) for s_nom.

Usage
-----
    python network/fix_parallel_circuits_v2.py --network networks/elec_real_generators_hydro2022_cftie_reduced.nc
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(NETWORK_DIR)
sys.path.insert(0, NETWORK_DIR)
from st_clair import load_line_params, st_clair_s_nom_mva  # noqa: E402

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_real_generators_hydro2022_cftie_reduced.nc")
PARSED_CORRIDORS = os.path.join(NETWORK_DIR, "hq_backbone_corridors_parsed.json")
CLUSTER_TOL_DEG = 0.01  # matches the clustering tolerance used to build the corridors (~1 km)
BUS_MATCH_TOL_DEG = 0.15  # ~15 km, how far a bus can be from a cluster and still count as "there"


def build_corridors(parsed_path: str):
    with open(parsed_path, encoding="utf-8") as f:
        results = json.load(f)
    valid = [r for r in results if r["ok"]]

    clusters = []

    def cluster_id(pt):
        for i, c in enumerate(clusters):
            if abs(c[0] - pt[0]) < CLUSTER_TOL_DEG and abs(c[1] - pt[1]) < CLUSTER_TOL_DEG:
                return i
        clusters.append(list(pt))
        return len(clusters) - 1

    corridor_circuits = defaultdict(list)
    for r in valid:
        a, b = r["terminals"]
        ca, cb = cluster_id(a), cluster_id(b)
        key = tuple(sorted([ca, cb]))
        corridor_circuits[key].append((r["name"], float(r["voltage"]) / 1000.0))

    return clusters, corridor_circuits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--corridors", default=PARSED_CORRIDORS)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    print(f"Building real corridors from {args.corridors}...")
    clusters, corridor_circuits = build_corridors(args.corridors)
    print(f"  {len(clusters)} clustered terminal points, {len(corridor_circuits)} real corridors")

    lines = n.lines[n.lines.v_nom.isin([315.0, 345.0, 500.0, 735.0, 765.0])].copy()
    print(f"{len(lines)} backbone-voltage lines in our network to check")

    def nearest_cluster(x, y):
        best_i, best_d = None, None
        for i, c in enumerate(clusters):
            d = (c[0] - y) ** 2 + (c[1] - x) ** 2
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        return best_i, best_d**0.5

    bus_to_cluster = {}
    for b in set(lines.bus0) | set(lines.bus1):
        row = n.buses.loc[b]
        ci, dist = nearest_cluster(row.x, row.y)
        if dist <= BUS_MATCH_TOL_DEG:
            bus_to_cluster[b] = ci
    print(f"  Matched {len(bus_to_cluster)} / {len(set(lines.bus0) | set(lines.bus1))} bus endpoints to a real corridor cluster")

    lines["c0"] = lines.bus0.map(bus_to_cluster)
    lines["c1"] = lines.bus1.map(bus_to_cluster)
    matched = lines.dropna(subset=["c0", "c1"]).copy()
    matched["corridor"] = matched.apply(lambda r: tuple(sorted([int(r.c0), int(r.c1)])), axis=1)

    ref = load_line_params()
    n_fixed = 0
    total_added = 0
    for corridor, circuits in corridor_circuits.items():
        real_count = len(circuits)
        if real_count < 2:
            continue  # nothing to undercounted-check on a single-circuit corridor
        our_rows = matched[matched.corridor == corridor]
        our_count = len(our_rows)
        if our_count == 0 or our_count >= real_count:
            continue

        shortfall = real_count - our_count
        template = our_rows.iloc[0]
        v_nom = template.v_nom
        length_km = template.length
        line_type = template.type if template.type else None
        s_nom = st_clair_s_nom_mva(length_km, v_nom, ref)

        for i in range(shortfall):
            new_name = f"{template.bus0}-{template.bus1} extra-circuit-{i+1}"
            kwargs = dict(
                bus0=template.bus0, bus1=template.bus1,
                length=length_km, num_parallel=1.0,
                s_nom=s_nom, s_nom_extendable=False,
            )
            if line_type:
                kwargs["type"] = line_type
            n.add("Line", new_name, **kwargs)
            n.lines.loc[new_name, "v_nom"] = v_nom
            total_added += 1

        names = [c[0] for c in circuits]
        print(f"  {v_nom:.0f}kV corridor {corridor}: real={real_count} ({names}), modeled={our_count} -> added {shortfall}")
        n_fixed += 1

    print(f"\nFixed {n_fixed} corridors, added {total_added} new lines total")

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_parfix2{ext}"
    n.export_to_netcdf(output)
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
