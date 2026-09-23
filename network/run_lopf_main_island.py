# -*- coding: utf-8 -*-
"""
run_lopf_main_island.py

Extract the largest connected component (island) from the voltage-reduced
network and run LOPF on it. The reduced network can have small orphaned
islands (buses with load but no path to the main network) that this script
excludes from the run entirely -- not shed, just not part of this network.

Usage
-----
    python network/run_lopf_main_island.py --network networks/elec_reduced.nc
"""
import argparse
import os
import sys

import networkx as nx
import pandas as pd
import pypsa

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, NETWORK_DIR)
from st_clair import load_line_params, st_clair_s_nom_mva  # noqa: E402
from attach_hydro_dispatch_2022 import align_to_snapshots  # noqa: E402

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_reduced.nc")
SOURCES_CSV = os.path.join(NETWORK_DIR, "2022-sources-electricite-quebec.csv")

# St Clair (network/st_clair.py) is a planning-level analytical envelope
# derived from real Hypersim-sourced line impedance data, not an empirical
# thermal rating. Applied at LOPF time only as a uniform margin across every
# line's St-Clair-derived s_nom (in-memory only; saved network files are
# unaffected).
ST_CLAIR_MARGIN_FACTOR = 3.0

# Flat transformer thermal capacity (MVA). PyPSA-Earth's own flat 2000 MVA
# default binds at several major junctions where real line capacity
# converging on them is far larger -- not an empirically precise value,
# just large enough that transformers aren't an artificial pinch point.
TRANSFORMER_S_NOM_MVA = 100_000.0

# Headroom margin on real-data-derived dispatch-ratio ceilings (OCGT's
# Thermique-based ceiling; ror's and hydro storage's uniform_cf-based
# ceiling) -- a hard ceiling at exactly the historical value leaves no room
# to exceed history even when the model legitimately should. Not applied to
# onwind: its p_max_pu is a weather-derived capacity factor, not a
# dispatch-history ratio.
CEILING_MARGIN = 1.10

# Flat demand scale-up: rescale_demand_regional.py matches real HQ regional
# totals only for buses matched to one of Quebec's 17 real administrative
# regions -- buses outside that scope keep smaller synthetic values and are
# never rescaled. This closes that gap against real system-wide demand.
# Calibrated against the real whole-January-2022 mean system demand (32,421
# MW, historique-demande-electricite-quebec.csv) rather than the single
# solved week alone.
DEMAND_SCALE_FACTOR = 1.1142


def largest_island_buses(n: pypsa.Network) -> set:
    G = nx.Graph()
    G.add_nodes_from(n.buses.index)
    for df in (n.lines, n.transformers, n.links):
        for _, r in df.iterrows():
            G.add_edge(r.bus0, r.bus1)
    comps = sorted(nx.connected_components(G), key=len, reverse=True)
    return comps[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--solver", default="highs")
    parser.add_argument("--voll", type=float, default=10000.0, help="Value of lost load, $/MWh, for the load-shedding slack generators.")
    parser.add_argument("--output", default=None)
    parser.add_argument("--snapshots", type=int, default=168, help="Limit to the first N snapshots (default 168 = one week of hourly data; pass 0 for the network's full range).")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)
    print(f"  {len(n.buses)} buses total")

    if args.snapshots:
        n.set_snapshots(n.snapshots[:args.snapshots])
        print(f"  Limited to first {len(n.snapshots)} snapshots ({n.snapshots[0]} to {n.snapshots[-1]})")

    main_buses = largest_island_buses(n)
    print(f"Largest connected island: {len(main_buses)} / {len(n.buses)} buses")

    drop_buses = n.buses.index.difference(main_buses)
    n.mremove("Bus", drop_buses)
    for comp, df_attr in [("Line", "lines"), ("Transformer", "transformers"), ("Link", "links")]:
        df = getattr(n, df_attr)
        to_drop = df.index[~(df.bus0.isin(main_buses) & df.bus1.isin(main_buses))]
        n.mremove(comp, to_drop)
    for comp, df_attr in [("Load", "loads"), ("Generator", "generators"), ("StorageUnit", "storage_units")]:
        df = getattr(n, df_attr)
        to_drop = df.index[~df.bus.isin(main_buses)]
        n.mremove(comp, to_drop)

    print(f"After extraction: {len(n.buses)} buses, {len(n.lines)} lines, {len(n.generators)} generators, "
          f"{len(n.storage_units)} storage units, {len(n.loads)} loads")
    print(f"Total demand (mean): {n.loads_t.p_set.sum(axis=1).mean():.0f} MW")
    print(f"Total generation capacity: {n.generators.p_nom.sum() + n.storage_units.p_nom.sum():.0f} MW")

    print(f"Scaling demand up by {DEMAND_SCALE_FACTOR:.3f}x globally...")
    n.loads_t.p_set = n.loads_t.p_set * DEMAND_SCALE_FACTOR
    print(f"  New total demand (mean): {n.loads_t.p_set.sum(axis=1).mean():.0f} MW")

    print("Assigning marginal costs by carrier from resources/costs_2030_elec.csv...")
    costs = pd.read_csv(os.path.join(BASE_DIR, "resources", "costs_2030_elec.csv")).set_index("technology")
    for carrier in n.generators.carrier.unique():
        if carrier in costs.index:
            mc = costs.at[carrier, "marginal_cost"]
            n.generators.loc[n.generators.carrier == carrier, "marginal_cost"] = mc
            print(f"  {carrier}: marginal_cost = {mc:.4f} $/MWh")
    for carrier in n.storage_units.carrier.unique():
        if carrier in costs.index:
            mc = costs.at[carrier, "marginal_cost"]
            n.storage_units.loc[n.storage_units.carrier == carrier, "marginal_cost"] = mc
            print(f"  {carrier} (storage): marginal_cost = {mc:.4f} $/MWh")

    print("Re-enabling OCGT with a real, time-varying ceiling from Thermique data...")
    ocgt = n.generators.index[n.generators.carrier == "OCGT"]
    ocgt_pmax_t_cols = [g for g in ocgt if g in n.generators_t.p_max_pu.columns]
    if ocgt_pmax_t_cols:
        n.generators_t.p_max_pu.drop(columns=ocgt_pmax_t_cols, inplace=True)
    ocgt_pmin_t_cols = [g for g in ocgt if g in n.generators_t.p_min_pu.columns]
    if ocgt_pmin_t_cols:
        n.generators_t.p_min_pu.drop(columns=ocgt_pmin_t_cols, inplace=True)
    n.generators.loc[ocgt, "p_min_pu"] = 0.0

    ocgt_cap = n.generators.loc[ocgt, "p_nom"].sum()
    src = pd.read_csv(SOURCES_CSV)
    src.columns = [c.strip() for c in src.columns]
    aligned = align_to_snapshots(src[["Date", "Thermique"]], "Date", n.snapshots, shift=pd.Timedelta(minutes=30))
    ocgt_cf = (aligned["Thermique"] / ocgt_cap).clip(upper=1.0) * CEILING_MARGIN
    for g in ocgt:
        n.generators_t.p_max_pu[g] = ocgt_cf
    print(f"  {len(ocgt)} OCGT generator(s): p_max_pu = real Thermique(t)/capacity * "
          f"{CEILING_MARGIN:.2f} (mean={ocgt_cf.mean():.4f}, max={ocgt_cf.max():.3f})")

    n.generators.loc[ocgt, "marginal_cost"] = -0.3
    print("  OCGT marginal_cost -> -0.30 $/MWh (prefers dispatch up to its real ceiling over shedding)")

    onwind = n.generators.index[n.generators.carrier == "onwind"]
    n.generators.loc[onwind, "marginal_cost"] = 0.0
    print(f"  {len(onwind)} onwind generator(s) set to marginal_cost = 0.0 $/MWh")

    print("Computing r/x/b from line `type` + length...")
    n.calculate_dependent_values()

    print(f"Recomputing s_nom for all {len(n.lines)} lines from the St Clair curve, x{ST_CLAIR_MARGIN_FACTOR:.0f} margin...")
    ref = load_line_params()
    old_s_nom = n.lines["s_nom"].copy()
    num_parallel = n.lines["num_parallel"].where(n.lines["num_parallel"] > 0, 1.0)
    n.lines["s_nom"] = [
        ST_CLAIR_MARGIN_FACTOR * st_clair_s_nom_mva(row.length, row.v_nom, ref, num_parallel=num_parallel[l])
        for l, row in n.lines.iterrows()
    ]
    n.lines["s_nom_opt"] = n.lines["s_nom"]
    print(f"  s_nom (MVA): old mean={old_s_nom.mean():.0f}, new mean={n.lines['s_nom'].mean():.0f} "
          f"(ratio {n.lines['s_nom'].mean()/old_s_nom.mean():.2f}x)")

    # Scaling x proportionally with s_nom (keeping x_pu = x/s_nom fixed) --
    # PyPSA per-units transformer reactance against its own s_nom, so raising
    # s_nom alone without also raising x would shrink x_pu toward a numerical
    # short-circuit.
    print(f"Raising all {len(n.transformers)} transformers to a flat {TRANSFORMER_S_NOM_MVA:.0f} MVA...")
    old_s_nom_t = n.transformers["s_nom"].copy()
    n.transformers["x"] = n.transformers["x"] * (TRANSFORMER_S_NOM_MVA / old_s_nom_t)
    n.transformers["s_nom"] = TRANSFORMER_S_NOM_MVA

    print("Relaxing ror from forced-exact dispatch to ceiling-only...")
    ror = n.generators.index[n.generators.carrier == "ror"]
    ror_t_cols = [g for g in ror if g in n.generators_t.p_min_pu.columns]
    n.generators_t.p_min_pu.drop(columns=ror_t_cols, inplace=True)
    n.generators.loc[ror, "p_min_pu"] = 0.0
    su_t_cols = [s for s in n.storage_units.index if s in n.storage_units_t.p_min_pu.columns]
    if su_t_cols:
        n.storage_units_t.p_min_pu.drop(columns=su_t_cols, inplace=True)
    print(f"  Cleared forced-minimum on {len(ror_t_cols)} ror generators and {len(su_t_cols)} hydro storage units")

    print(f"Applying the same {CEILING_MARGIN:.2f}x headroom margin to ror's real ceiling...")
    ror_pmax_t_cols = [g for g in ror if g in n.generators_t.p_max_pu.columns]
    if ror_pmax_t_cols:
        n.generators_t.p_max_pu[ror_pmax_t_cols] = (n.generators_t.p_max_pu[ror_pmax_t_cols] * CEILING_MARGIN).clip(upper=1.0)
        print(f"  Rescaled p_max_pu on {len(ror_pmax_t_cols)} ror generators "
              f"(mean={n.generators_t.p_max_pu[ror_pmax_t_cols].mean().mean():.3f})")

    # Storage reservoirs start full (state_of_charge_initial = 100% of
    # capacity), so within a short window they can draw down faster than real
    # inflow alone would justify. Bounding storage dispatch by the same real
    # fleet-wide ratio (uniform_cf, recovered from the already-set inflow)
    # that already caps ror keeps both resources on the same real per-hour
    # ratio.
    print("Bounding hydro storage dispatch by the same real fleet-wide ratio that caps ror...")
    hydro_su = n.storage_units.index[n.storage_units.carrier == "hydro"]
    uniform_cf_recovered = n.storage_units_t.inflow[hydro_su].div(n.storage_units.loc[hydro_su, "p_nom"], axis=1)
    uniform_cf_recovered = (uniform_cf_recovered * CEILING_MARGIN).clip(upper=1.0)
    su_pmax_t_cols = [s for s in hydro_su if s in n.storage_units_t.p_max_pu.columns]
    if su_pmax_t_cols:
        n.storage_units_t.p_max_pu.drop(columns=su_pmax_t_cols, inplace=True)
    n.storage_units_t.p_max_pu = pd.concat([n.storage_units_t.p_max_pu, uniform_cf_recovered], axis=1)
    print(f"  Set p_max_pu = uniform_cf(t) * {CEILING_MARGIN:.2f} (clipped at 1.0) on {len(hydro_su)} "
          f"hydro storage units (mean={uniform_cf_recovered.mean().mean():.3f})")

    # Looked up by bus pair, not the literal id "4349" -- reduce_voltage_network.py's
    # nearest-bus reassignment isn't fully deterministic run-to-run (ties can break
    # differently), which can occasionally leave this DC tie's buses outside this
    # run's largest connected island, changing which ids survive extraction.
    tie_329_3975 = n.links.index[(n.links.bus0.isin(["329", "3975"])) & (n.links.bus1.isin(["329", "3975"]))]
    if len(tie_329_3975):
        tie_id = tie_329_3975[0]
        old_p_nom = n.links.at[tie_id, "p_nom"]
        n.links.at[tie_id, "p_nom"] = old_p_nom * 3
        print(f"Tripling the 329-3975 DC tie's capacity (link '{tie_id}'): "
              f"{old_p_nom:.1f} -> {n.links.at[tie_id, 'p_nom']:.1f} MW")
    else:
        print("329-3975 DC tie not present in this run's largest island -- skipping its capacity fix.")

    print("Relaxing lines from the default n-1 security margin (s_max_pu=0.7) to 1.0...")
    n_derated = (n.lines.s_max_pu < 1.0).sum()
    n.lines["s_max_pu"] = 1.0
    print(f"  Relaxed s_max_pu to 1.0 on {n_derated} / {len(n.lines)} lines (were 0.7)")

    print(f"Adding VOLL load-shedding generators ({args.voll:.0f} $/MWh) at every load bus...")
    load_buses = n.loads.bus.unique()
    peak_by_bus = n.loads_t.p_set.T.groupby(n.loads.bus).sum().max(axis=1)
    for b in load_buses:
        n.add(
            "Generator", f"{b} load_shedding", bus=b, carrier="load_shedding",
            p_nom=float(peak_by_bus.get(b, 0.0)), marginal_cost=args.voll,
        )
    print(f"  Added {len(load_buses)} load-shedding generators")

    # Slack: largest TOTAL generation capacity (Generator + StorageUnit
    # combined) within the largest AC-connected component. PyPSA's own
    # bus-control logic only reads Generator.control, never StorageUnit.
    # A zero-dispatch placeholder
    # Generator is added when the largest-capacity bus is storage-only, so
    # slack candidacy isn't silently restricted to plain-Generator buses.
    print("Assigning slack (largest total generation capacity bus)...")
    n.generators["control"] = "PQ"
    n.storage_units["control"] = "PQ"

    ac_graph = nx.Graph()
    ac_graph.add_nodes_from(n.buses.index)
    for df in (n.lines, n.transformers):
        for _, r in df.iterrows():
            ac_graph.add_edge(r.bus0, r.bus1)
    main_ac_buses = max(nx.connected_components(ac_graph), key=len)

    gens_only = n.generators[(n.generators.carrier != "load_shedding") & (n.generators.bus.isin(main_ac_buses))]
    su_only = n.storage_units[n.storage_units.bus.isin(main_ac_buses)]
    total_cap_by_bus = gens_only.groupby("bus").p_nom.sum().add(
        su_only.groupby("bus").p_nom.sum(), fill_value=0.0
    )
    slack_bus = total_cap_by_bus.idxmax()

    bus_gens = gens_only[gens_only.bus == slack_bus]
    if len(bus_gens):
        slack_gen = bus_gens.p_nom.idxmax()
        n.generators.loc[slack_gen, "control"] = "Slack"
    else:
        su_cap = su_only.loc[su_only.bus == slack_bus, "p_nom"].sum()
        slack_gen = f"{slack_bus} slack-placeholder"
        n.add("Generator", slack_gen, bus=slack_bus, carrier="AC", p_nom=su_cap, control="Slack")
        print(f"  '{slack_bus}' is storage-only ({su_cap:.0f} MW) -- added zero-dispatch "
              f"placeholder Generator '{slack_gen}' there so it can be recognized as slack.")
    print(f"Slack generator: '{slack_gen}' at bus {slack_bus} "
          f"({total_cap_by_bus.max():.0f} MW total generation capacity there)")

    print(f"\nRunning LOPF (solver={args.solver})...")
    status, condition = n.optimize(solver_name=args.solver)
    print(f"Status: {status}, condition: {condition}")

    if status != "ok":
        print("LOPF did not solve successfully -- stopping before reporting results.")
        return

    weights = n.snapshot_weightings.generators
    total_demand = (n.loads_t.p_set.sum(axis=1) * weights).sum()
    shed_cols = [c for c in n.generators_t.p.columns if n.generators.at[c, "carrier"] == "load_shedding"]
    total_shed = (n.generators_t.p[shed_cols].sum(axis=1) * weights).sum()
    print(f"\nObjective: {n.objective:,.0f}")
    print(f"Total demand: {total_demand:,.0f} MWh")
    print(f"Total shed: {total_shed:,.0f} MWh ({total_shed / total_demand:.2%})")

    print("\nDispatch by carrier (mean MW):")
    real_gens = n.generators.index[n.generators.carrier != "load_shedding"]
    disp = pd.concat([
        n.generators_t.p[real_gens].groupby(n.generators.loc[real_gens, "carrier"], axis=1).sum().mean(),
        n.storage_units_t.p.groupby(n.storage_units.carrier, axis=1).sum().mean().rename(lambda c: c + " (storage)"),
        pd.Series({"load_shedding": n.generators_t.p[shed_cols].sum(axis=1).mean()}),
    ])
    print(disp)

    shed_by_bus = n.generators_t.p[shed_cols].mul(weights, axis=0).sum()
    shed_by_bus = shed_by_bus[shed_by_bus > 0].sort_values(ascending=False)
    print(f"\n{len(shed_by_bus)} / {len(shed_cols)} buses have any shedding. Top 10:")
    print(shed_by_bus.head(10))

    loading = (n.lines_t.p0.abs() / n.lines.s_nom).max()
    print(f"\nLine loading: max={loading.max():.1%}, lines >=90% loaded: {(loading>=0.9).sum()}/{len(n.lines)}")

    output = args.output or os.path.join(BASE_DIR, "networks", "elec_solved.nc")
    n.export_to_netcdf(output)
    print(f"\nSaved solved network -> {output}")


if __name__ == "__main__":
    main()
