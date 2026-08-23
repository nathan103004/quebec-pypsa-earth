# -*- coding: utf-8 -*-
"""
fix_parallel_circuits.py

Correct undercounted parallel circuits on Quebec's 735 kV backbone.

Investigation (see conversation): our network's 735 kV lines show 77
distinct bus-pair corridors, 54 of them with only 1 modeled circuit.
Cross-referencing against the real OSM data (network/hq_735kv_osm_relation.json,
relation 4504531, "Hydro-Quebec High-voltage transport network - 735 KV"
-- the same source used for the Churchill Falls tie) shows the real
network has 67 corridors, only 38 of which are genuinely single-circuit;
29 have 2-4 real parallel circuits (13 with 2, 15 with 3, 1 with 4).
So part of our "1 circuit" corridors are correct, but roughly 16 are
real multi-circuit routes our OSM-derived base network only captured
one circuit of.

Method
------
1. Parse the real OSM relation into corridors: group way segments by
   their two nearest named substations, count distinct circuit refs
   per corridor (e.g. Poste Arnaud <-> Poste des Montagnais has 3:
   "HQ 7031/7032/7033").
2. Match our network's buses (that touch a line at the target voltage)
   to their nearest real substation by coordinates.
3. Group our own lines by matched corridor (substation pair), and
   compare our modeled circuit count (number of Line rows) against the
   real one.
4. Where we're short, add more Line rows on that same bus pair --
   cloning an existing line's `type`/length in that corridor, with
   `s_nom` from the St Clair curve (network/st_clair.py) for that
   length/voltage, same approach as the Churchill Falls tie.

This only ever ADDS lines to close a shortfall -- it never removes or
reduces anything, even where our count exceeds the real one (could be a
real difference in circuit segmentation/matching precision, not
necessarily an error, and removing capacity is a much higher-risk
change than adding known-missing capacity).

Usage
-----
    python network/fix_parallel_circuits.py --network networks/elec_real_generators_hydro2022_cftie.nc --voltage 735 --osm-relation network/hq_735kv_osm_relation.json
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(NETWORK_DIR)
sys.path.insert(0, NETWORK_DIR)
from st_clair import load_line_params, st_clair_s_nom_mva  # noqa: E402

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_real_generators_hydro2022_cftie.nc")
DEFAULT_RELATION = os.path.join(NETWORK_DIR, "hq_735kv_osm_relation.json")
MATCH_TOLERANCE_DEG = 0.15  # ~15 km, a bus must be within this of a substation to match it


def load_real_corridors(relation_path: str):
    with open(relation_path, encoding="utf-8") as f:
        data = json.load(f)
    ways = [e for e in data["elements"] if e["type"] == "way"]
    subs = [w for w in ways if w.get("tags", {}).get("power") == "substation"]
    lines = [w for w in ways if w.get("tags", {}).get("power") == "line"]

    sub_centers = []
    for w in subs:
        g = w["geometry"]
        clat = sum(p["lat"] for p in g) / len(g)
        clon = sum(p["lon"] for p in g) / len(g)
        sub_centers.append((w["tags"].get("name", f"way{w['id']}"), clat, clon))

    def nearest_sub(lat, lon):
        return min(sub_centers, key=lambda s: (s[1] - lat) ** 2 + (s[2] - lon) ** 2)

    corridor_refs = defaultdict(set)
    for w in lines:
        g = w["geometry"]
        a = nearest_sub(g[0]["lat"], g[0]["lon"])[0]
        b = nearest_sub(g[-1]["lat"], g[-1]["lon"])[0]
        key = tuple(sorted([a, b]))
        ref = w["tags"].get("ref", w["tags"].get("name", str(w["id"])))
        corridor_refs[key].add(ref)

    return sub_centers, corridor_refs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--osm-relation", default=DEFAULT_RELATION)
    parser.add_argument("--voltage", type=float, default=735.0)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    print(f"Parsing real corridors from {args.osm_relation}...")
    sub_centers, real_corridors = load_real_corridors(args.osm_relation)
    print(f"  {len(sub_centers)} substations, {len(real_corridors)} real corridors")

    lines_v = n.lines[n.lines.v_nom == args.voltage].copy()
    buses_v = set(lines_v.bus0) | set(lines_v.bus1)
    print(f"{len(lines_v)} lines / {len(buses_v)} buses at {args.voltage:.0f} kV in our network")

    # Match our buses to nearest real substation, within tolerance.
    bus_to_sub = {}
    unmatched_buses = []
    for b in buses_v:
        row = n.buses.loc[b]
        name, slat, slon = min(sub_centers, key=lambda s: (s[1] - row.y) ** 2 + (s[2] - row.x) ** 2)
        dist = ((slat - row.y) ** 2 + (slon - row.x) ** 2) ** 0.5
        if dist <= MATCH_TOLERANCE_DEG:
            bus_to_sub[b] = name
        else:
            unmatched_buses.append(b)
    print(f"  Matched {len(bus_to_sub)} / {len(buses_v)} buses to a real substation (tolerance {MATCH_TOLERANCE_DEG} deg)")
    if unmatched_buses:
        print(f"  [note] {len(unmatched_buses)} buses too far from any known substation, skipped from this check")

    lines_v["sub0"] = lines_v.bus0.map(bus_to_sub)
    lines_v["sub1"] = lines_v.bus1.map(bus_to_sub)
    matched = lines_v.dropna(subset=["sub0", "sub1"]).copy()
    matched["corridor"] = matched.apply(lambda r: tuple(sorted([r.sub0, r.sub1])), axis=1)

    ref = load_line_params()
    n_fixed = 0
    total_added = 0
    for corridor, real_refs in real_corridors.items():
        real_count = len(real_refs)
        our_rows = matched[matched.corridor == corridor]
        our_count = len(our_rows)
        if our_count == 0 or our_count >= real_count:
            continue  # not modeled at all here (different voltage/route), or already matches/exceeds

        shortfall = real_count - our_count
        template = our_rows.iloc[0]
        length_km = template.length
        line_type = template.type if template.type else None
        s_nom = st_clair_s_nom_mva(length_km, args.voltage, ref)

        for i in range(shortfall):
            new_name = f"{template.bus0}-{template.bus1} extra-circuit-{i+1}"
            kwargs = dict(
                bus0=template.bus0,
                bus1=template.bus1,
                length=length_km,
                num_parallel=1.0,
                s_nom=s_nom,
                s_nom_extendable=False,
            )
            if line_type:
                kwargs["type"] = line_type
            n.add("Line", new_name, **kwargs)
            n.lines.loc[new_name, "v_nom"] = args.voltage
            total_added += 1

        print(
            f"  {corridor[0]} <-> {corridor[1]}: real={real_count} circuits "
            f"({sorted(real_refs)}), modeled={our_count} -> added {shortfall}"
        )
        n_fixed += 1

    print(f"\nFixed {n_fixed} corridors, added {total_added} new lines total")

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_parfix{ext}"
    n.export_to_netcdf(output)
    print(f"Saved -> {output}")


if __name__ == "__main__":
    main()
