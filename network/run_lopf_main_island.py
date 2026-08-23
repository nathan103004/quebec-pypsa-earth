# -*- coding: utf-8 -*-
"""
run_lopf_main_island.py

Extract the largest connected component (island) from the voltage-reduced
network and run LOPF on it. See the connectivity check earlier in this
project: the reduced network has 4 islands -- one main 214-bus island
holding all real generation/storage, plus 3 small orphaned islands (25
loads with zero generation; 105 MW of wind stranded with 21 loads; 310 MW
of Gaspe wind fully isolated with 23 loads) that reduce_voltage_network.py
left disconnected. This script works on the main island only, so those
orphaned islands (and their loads) are excluded from this run entirely --
not shed, just not part of this network.

Usage
-----
    python network/run_lopf_main_island.py --network networks/elec_real_generators_hydro2022_cftie_reduced_parfix_combined.nc
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

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_real_generators_hydro2022_cftie_reduced_parfix_combined.nc")

# St Clair (network/st_clair.py) is a planning-level analytical envelope
# derived from real Hypersim-sourced line impedance data (network/
# overhead_line_parameters_by_voltage.csv), not an empirical/measured
# thermal rating -- it should not be treated as an exact ceiling. Applied
# here, at LOPF time only, as a uniform margin across every line's
# St-Clair-derived s_nom (not written back into the pipeline's saved .nc
# files, so the underlying network data stays exactly as built).
ST_CLAIR_MARGIN_FACTOR = 3.0

# Flat, deliberately-not-derived transformer capacity (MVA) -- PyPSA-Earth's own
# flat 2000 MVA default was found binding at 6 major 315/735kV junctions with
# 38,000-155,000 MVA of real line capacity converging on them, a fix in the same
# spirit as the line margin above (not empirically precise, just large enough
# that transformers stop being an artificial pinch point).
TRANSFORMER_S_NOM_MVA = 100_000.0


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
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)
    print(f"  {len(n.buses)} buses total")

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

    print("Assigning marginal costs by carrier (missing since attach_real_generators.py never carried these "
          "over when it replaced the synthetic generators) from resources/costs_2030_elec.csv...")
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

    print("Computing r/x/b from line `type` + length (not persisted in the saved file)...")
    n.calculate_dependent_values()
    print(f"  sample line x after calc: {n.lines.x.iloc[0]:.4f} (was 0 before)")

    print(f"Recomputing s_nom for all {len(n.lines)} lines from the St Clair curve "
          f"(real Hypersim-sourced impedance data), x{ST_CLAIR_MARGIN_FACTOR:.0f} margin -- "
          "most of this network's lines currently carry PyPSA-Earth's default "
          "conductor-ampacity s_nom, not a St-Clair-derived one; St Clair itself is a "
          "planning-level analytical envelope (stability margin + thermal plateau), not "
          "an exact empirical thermal limit, so it shouldn't be applied at face value "
          "either. In-memory only, at LOPF time -- the saved pipeline networks are left "
          "untouched.")
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

    print(f"Raising all {len(n.transformers)} transformers to a flat {TRANSFORMER_S_NOM_MVA:.0f} MVA -- "
          "PyPSA-Earth's default (a flat 2000 MVA per 315/735kV junction regardless of location) was "
          "binding at 6 major James Bay/Montreal junctions where 38,000-155,000 MVA of real lines "
          "converge, an obvious real-substation design mismatch (no utility bottlenecks that much line "
          "capacity behind one small bank). No per-substation real transformer MVA data was available "
          "to size this precisely (commercially confidential in the one regulatory filing checked) -- "
          "same treatment as the line thermal limits above, which are themselves a planning-level "
          "estimate, not an exact empirical rating. In-memory only, at LOPF time. Scaling x "
          "proportionally with s_nom (keeping x_pu = x/s_nom fixed) -- PyPSA per-units transformer "
          "reactance against its own s_nom, so raising s_nom alone without also raising x silently "
          "shrinks x_pu toward a numerical short-circuit (found: 5e-5 -> 1e-6, comparable to real "
          "lines' own x_pu down to near-zero), which LOPF/DC-PF's linear solve tolerates but which "
          "made AC PF's Newton-Raphson diverge on every snapshot.")
    old_s_nom_t = n.transformers["s_nom"].copy()
    n.transformers["x"] = n.transformers["x"] * (TRANSFORMER_S_NOM_MVA / old_s_nom_t)
    n.transformers["s_nom"] = TRANSFORMER_S_NOM_MVA

    print("Relaxing ror from forced-exact dispatch to ceiling-only "
          "(attach_hydro_dispatch_2022.py sets p_min_pu = p_max_pu = uniform_cf for ror, "
          "which pins dispatch to an exact historical value with zero curtailment "
          "freedom -- infeasible whenever that exact value isn't deliverable through "
          "the network at some hour. p_max_pu should be a ceiling, not a fixed point. "
          "Hydro storage units no longer need this: attach_real_generators.py now gives "
          "them a real reservoir size/initial state of charge and a plain p_min_pu=0 "
          "instead of a forced time-varying override, so there's nothing to relax there "
          "-- only defensively drop leftover _t columns if an older network file has them.)...")
    ror = n.generators.index[n.generators.carrier == "ror"]
    ror_t_cols = [g for g in ror if g in n.generators_t.p_min_pu.columns]
    n.generators_t.p_min_pu.drop(columns=ror_t_cols, inplace=True)
    n.generators.loc[ror, "p_min_pu"] = 0.0
    su_t_cols = [s for s in n.storage_units.index if s in n.storage_units_t.p_min_pu.columns]
    if su_t_cols:
        n.storage_units_t.p_min_pu.drop(columns=su_t_cols, inplace=True)
    print(f"  Cleared forced-minimum on {len(ror_t_cols)} ror generators and {len(su_t_cols)} hydro storage units (stale leftovers only)")

    print("Relaxing lines from the default n-1 security margin (s_max_pu=0.7, i.e. "
          "70% of nameplate s_nom) to 1.0 -- per-request sensitivity check on how much "
          "of the remaining load shed is driven by that margin specifically...")
    n_derated = (n.lines.s_max_pu < 1.0).sum()
    n.lines["s_max_pu"] = 1.0
    print(f"  Relaxed s_max_pu to 1.0 on {n_derated} / {len(n.lines)} lines (were 0.7)")

    print(f"Adding VOLL load-shedding generators ({args.voll:.0f} $/MWh) at every load bus "
          "-- this network has no slack mechanism otherwise, so any shortfall snapshot makes "
          "the whole horizon infeasible instead of reporting a quantified shed %...")
    load_buses = n.loads.bus.unique()
    peak_by_bus = n.loads_t.p_set.T.groupby(n.loads.bus).sum().max(axis=1)
    for b in load_buses:
        n.add(
            "Generator", f"{b} load_shedding", bus=b, carrier="load_shedding",
            p_nom=float(peak_by_bus.get(b, 0.0)), marginal_cost=args.voll,
        )
    print(f"  Added {len(load_buses)} load-shedding generators")

    print("Assigning slack: exactly one plain Generator, at the largest-Generator-capacity "
          "bus within the largest AC-connected component (lines+transformers only -- links "
          "form separate back-to-back DC sub-networks that don't need/use this AC slack). "
          "Clearing every other generator/storage control flag first -- previously stale "
          "'Slack' flags left over from earlier pipeline stages were never reset, so more "
          "than one component ended up marked Slack at once. Restricted to buses with a "
          "plain Generator on purpose: PyPSA's own SubNetwork.generators() (used by "
          "find_slack_bus() for n.pf()/n.lpf()) only ever looks at Generator components, "
          "never StorageUnit -- so a StorageUnit-only bus (like 339, La Grande-2-A + "
          "Robert-Bourassa, 7,727 MW combined and the true largest-generation bus overall) "
          "can never actually host a PyPSA-recognized slack, no matter what its `control` "
          "attribute says. PyPSA would silently auto-pick a fallback generator elsewhere "
          "instead -- better to choose that fallback explicitly and correctly than rely on "
          "its undocumented auto-selection.")
    n.generators["control"] = "PQ"
    n.storage_units["control"] = "PQ"

    ac_graph = nx.Graph()
    ac_graph.add_nodes_from(n.buses.index)
    for df in (n.lines, n.transformers):
        for _, r in df.iterrows():
            ac_graph.add_edge(r.bus0, r.bus1)
    main_ac_buses = max(nx.connected_components(ac_graph), key=len)

    gens_only = n.generators[(n.generators.carrier != "load_shedding") & (n.generators.bus.isin(main_ac_buses))]
    cap_by_bus = gens_only.groupby("bus").p_nom.sum()
    slack_bus = cap_by_bus.idxmax()
    slack_gen = gens_only[gens_only.bus == slack_bus].p_nom.idxmax()
    n.generators.loc[slack_gen, "control"] = "Slack"
    print(f"Slack generator: '{slack_gen}' at bus {slack_bus} "
          f"({cap_by_bus.max():.0f} MW of plain-generator capacity there, "
          f"largest such bus in the main AC network)")

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

    output = args.output or os.path.join(BASE_DIR, "networks", "elec_main_island_solved.nc")
    n.export_to_netcdf(output)
    print(f"\nSaved solved network -> {output}")


if __name__ == "__main__":
    main()
