# -*- coding: utf-8 -*-
"""
reduce_to_735kv.py

Reduce the solved main-island network (networks/elec_solved.nc) down to only
its 735kV backbone -- for use as a PF/stability-study input (not meant to be
re-optimized via LOPF; it carries the already-solved dispatch forward as
fixed data).

735kV and 765kV are treated as a single "735kV" tier: they have essentially
identical reactance-per-km (within 1%), and s_nom ranges overlap -- no real
engineering distinction between them in this data.

Method
------
1. Backbone = every bus at 735kV or 765kV, relabeled uniformly to 735kV.
   Backbone lines = lines with both ends in that set. The one transformer
   bridging 735-765 (transf_55_3, buses 132-133) is converted to an
   equivalent Line (see convert_transformer_to_line()) instead of being
   dropped, to avoid stranding buses 133/3554. Every other Transformer and
   every Link is dropped.
2. Every load/generator/storage unit NOT already on a backbone bus is
   reassigned to its nearest backbone bus via graph shortest-path
   (cumulative real line length, multi-source Dijkstra over the full
   original line+transformer graph) -- not straight-line geography.
3. Loads landing on the same backbone bus: p_set time series summed into
   one aggregate Load.
4. Generators/storage units landing on the same (backbone bus, carrier):
   merged into one aggregate unit. p_nom = sum of the originals' p_nom.
   Dispatch is the sum of the already-solved actual dispatch from
   elec_solved.nc (not re-derived), written into p_set.

See network/docs/DEBUGGING_HISTORY.md for the issues found and fixed in this
script's development (near-singular bridge-line admittance, straight-line
vs. graph-distance reassignment, load-shedding placeholders in slack
candidacy).

Usage
-----
    python network/reduce_to_735kv.py --network networks/elec_solved.nc
"""
import argparse
import os

import networkx as nx
import pandas as pd
import pypsa

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_solved.nc")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "networks", "elec_735kv.nc")

BACKBONE_VOLTAGES = [735.0, 765.0]
UNIFIED_VOLTAGE = 735.0
BRIDGE_TRANSFORMER = "transf_55_3"  # 132 (735kV) -- 133 (765kV)
BRIDGE_NOMINAL_LENGTH_KM = 6.0  # matches the 735kV fleet's own shortest real line


def build_full_graph(n: pypsa.Network) -> nx.Graph:
    """Full connectivity graph (lines + transformers + links) used only to find
    each bus's nearest backbone bus by real path length. Links are included here
    for connectivity even though they're dropped as components in the output --
    some buses are reachable only through a link."""
    G = nx.Graph()
    G.add_nodes_from(n.buses.index)
    for _, r in n.lines.iterrows():
        G.add_edge(r.bus0, r.bus1, length=r.length)
    for _, r in n.transformers.iterrows():
        G.add_edge(r.bus0, r.bus1, length=0.0)  # same-station, no transmission distance
    for _, r in n.links.iterrows():
        G.add_edge(r.bus0, r.bus1, length=0.0)
    return G


def convert_transformer_to_line(n: pypsa.Network) -> dict:
    """Equivalent Line for the 735-765 bridge transformer, sized from the 735kV
    fleet's own average reactance-per-km over a short nominal length -- not from
    the transformer's own per-unit reactance, which converts to a near-zero,
    numerically pathological value (see network/docs/DEBUGGING_HISTORY.md)."""
    t = n.transformers.loc[BRIDGE_TRANSFORMER]
    fleet_735 = n.lines[(n.lines.bus0.map(n.buses.v_nom) == UNIFIED_VOLTAGE) &
                         (n.lines.bus1.map(n.buses.v_nom) == UNIFIED_VOLTAGE)]
    x_per_km = (fleet_735.x / fleet_735.length).mean()
    r_per_km = (fleet_735.r / fleet_735.length).mean()
    x_ohms = x_per_km * BRIDGE_NOMINAL_LENGTH_KM
    r_ohms = r_per_km * BRIDGE_NOMINAL_LENGTH_KM
    return dict(
        bus0=t.bus0, bus1=t.bus1, x=x_ohms, r=r_ohms,
        s_nom=t.s_nom, length=BRIDGE_NOMINAL_LENGTH_KM, num_parallel=1.0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print(f"Loading solved network: {args.network}")
    n = pypsa.Network(args.network)
    print(f"  {len(n.buses)} buses, {len(n.lines)} lines, {len(n.transformers)} transformers, "
          f"{len(n.links)} links, {len(n.generators)} generators, {len(n.storage_units)} storage units, "
          f"{len(n.loads)} loads")

    # Snapshot the actual solved dispatch and full connectivity graph BEFORE
    # any mutation -- both are needed as inputs to steps below.
    gen_dispatch = n.generators_t.p.copy()
    su_dispatch = n.storage_units_t.p.copy()
    load_pset = n.loads_t.p_set.copy()
    full_graph = build_full_graph(n)

    backbone_buses = set(n.buses.index[n.buses.v_nom.isin(BACKBONE_VOLTAGES)])
    print(f"\nBackbone: {len(backbone_buses)} buses at {BACKBONE_VOLTAGES} -> relabeling all to {UNIFIED_VOLTAGE:.0f}kV")
    n.buses.loc[list(backbone_buses), "v_nom"] = UNIFIED_VOLTAGE

    print(f"Converting bridge transformer '{BRIDGE_TRANSFORMER}' (132-133) into an equivalent Line...")
    bridge_line = convert_transformer_to_line(n)
    n.add("Line", "bridge_132_133", **bridge_line)

    print("\nAssigning every non-backbone bus to its nearest backbone bus via graph shortest-path "
          "(cumulative real line length, multi-source Dijkstra)...")
    lengths, paths = nx.multi_source_dijkstra(full_graph, sources=backbone_buses, weight="length")
    nearest_backbone = {bus: paths[bus][0] for bus in n.buses.index if bus in paths}
    unreachable = set(n.buses.index) - set(nearest_backbone)
    if unreachable:
        print(f"  WARNING: {len(unreachable)} buses have no path to the backbone, dropping their "
              f"components without reassignment: {sorted(unreachable)}")

    print("\nAggregating loads onto their nearest backbone bus...")
    load_target = n.loads.bus.map(nearest_backbone)
    load_groups = n.loads.groupby(load_target).groups
    new_loads = {}
    for target, members in load_groups.items():
        if pd.isna(target):
            continue
        new_loads[f"{target} load"] = load_pset[members].sum(axis=1)
    print(f"  {len(n.loads)} original loads -> {len(new_loads)} aggregate loads on backbone buses")

    print("\nAggregating generators onto (nearest backbone bus, carrier), using actual solved "
          "dispatch (not a re-derived capacity-factor ceiling)...")
    gen_target = n.generators.bus.map(nearest_backbone)
    gen_key = pd.DataFrame({"target": gen_target, "carrier": n.generators.carrier})
    new_gens = {}
    for (target, carrier), members in gen_key.groupby(["target", "carrier"]).groups.items():
        if pd.isna(target):
            continue
        name = f"{target} {carrier}"
        new_gens[name] = dict(
            bus=target, carrier=carrier,
            p_nom=n.generators.loc[members, "p_nom"].sum(),
            marginal_cost=n.generators.loc[members, "marginal_cost"].iloc[0],
            p_set=gen_dispatch[members].sum(axis=1),
        )
    print(f"  {len(n.generators)} original generators -> {len(new_gens)} aggregate generators")

    print("\nAggregating storage units onto (nearest backbone bus, carrier), same treatment...")
    su_target = n.storage_units.bus.map(nearest_backbone)
    su_key = pd.DataFrame({"target": su_target, "carrier": n.storage_units.carrier})
    new_sus = {}
    for (target, carrier), members in su_key.groupby(["target", "carrier"]).groups.items():
        if pd.isna(target):
            continue
        name = f"{target} {carrier}"
        new_sus[name] = dict(
            bus=target, carrier=carrier,
            p_nom=n.storage_units.loc[members, "p_nom"].sum(),
            marginal_cost=n.storage_units.loc[members, "marginal_cost"].iloc[0],
            p_set=su_dispatch[members].sum(axis=1),
        )
    print(f"  {len(n.storage_units)} original storage units -> {len(new_sus)} aggregate storage units")

    print(f"\nDropping all {len(n.links)} links and all {len(n.transformers)} transformers "
          "(bridge one already replaced by an equivalent Line above)...")
    n.mremove("Link", n.links.index)
    n.mremove("Transformer", n.transformers.index)

    print("Removing all original loads, generators, storage units...")
    n.mremove("Load", n.loads.index)
    n.mremove("Generator", n.generators.index)
    n.mremove("StorageUnit", n.storage_units.index)

    print("Dropping every non-backbone bus and any line touching one...")
    drop_buses = n.buses.index.difference(backbone_buses)
    n.mremove("Bus", drop_buses)
    off_backbone_lines = n.lines.index[~(n.lines.bus0.isin(backbone_buses) & n.lines.bus1.isin(backbone_buses))]
    n.mremove("Line", off_backbone_lines)

    print("\nAdding aggregated loads/generators/storage units to the reduced network...")
    for name, p_set in new_loads.items():
        bus = name.rsplit(" ", 1)[0]
        n.add("Load", name, bus=bus, p_set=p_set)
    for name, attrs in new_gens.items():
        p_set = attrs.pop("p_set")
        n.add("Generator", name, **attrs)
        n.generators_t.p_set[name] = p_set
    for name, attrs in new_sus.items():
        p_set = attrs.pop("p_set")
        n.add("StorageUnit", name, **attrs)
        n.storage_units_t.p_set[name] = p_set

    # Slack: largest-capacity plain Generator, excluding load_shedding.
    # PyPSA's find_slack_bus()/find_bus_controls() only ever look at Generator
    # components, never StorageUnit -- see network/docs/DEBUGGING_HISTORY.md.
    print("\nAssigning slack (largest-capacity plain Generator, excluding load_shedding)...")
    real_gens = n.generators[n.generators.carrier != "load_shedding"]
    slack_gen = real_gens.p_nom.idxmax()
    n.generators.loc[slack_gen, "control"] = "Slack"
    print(f"  Slack generator: '{slack_gen}' at bus {n.generators.at[slack_gen,'bus']} "
          f"({n.generators.at[slack_gen,'p_nom']:.0f} MW)")

    print(f"\nFinal reduced network: {len(n.buses)} buses, {len(n.lines)} lines, "
          f"{len(n.generators)} generators, {len(n.storage_units)} storage units, {len(n.loads)} loads")
    print(f"Total load (mean): {n.loads_t.p_set.sum(axis=1).mean():.0f} MW")
    print(f"Total generator + storage dispatch (mean): "
          f"{(n.generators_t.p_set.sum(axis=1) + n.storage_units_t.p_set.sum(axis=1)).mean():.0f} MW")

    n.export_to_netcdf(args.output)
    print(f"\nSaved reduced network -> {args.output}")


if __name__ == "__main__":
    main()
