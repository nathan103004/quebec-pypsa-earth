# -*- coding: utf-8 -*-
"""
run_pf.py

Run power flow (DC or AC) on the solved main-island network, using its
LOPF-optimal dispatch (generators_t.p, storage_units_t.p, loads_t.p_set)
as the fixed injection pattern -- i.e. verifying that the dispatch LOPF
found is actually deliverable according to Kirchhoff's laws, not just
economically optimal against the simplified LOPF constraints.

DC (--method lpf, default): linearized power flow, n.lpf() -- same
reactance-only approximation as LOPF itself, so this should reproduce
the LOPF line flows almost exactly and mainly serves as a consistency
check (single slack bus per sub-network required -- see
run_lopf_main_island.py's slack assignment).

AC (--method pf): full nonlinear Newton-Raphson power flow, n.pf(). Four
gaps were closed to make this meaningful (harmless no-ops for --method
lpf, which ignores r, Q, and PV/PQ classification entirely):

1. Transformer resistance. All 22 transformers had r=0 (only x was set).
   Backed out from a generic X/R=30 -- a commonly-cited typical value
   for large power transformers (IEEE C57.12.00-class units are usually
   quoted in the 20-40 range) -- not measured, same honest-generic
   treatment as the line/transformer thermal limits elsewhere in this
   project. r = x / 30.

2. Load reactive power. Every load had q_set=0 (unity power factor).
   Assumed a generic 0.95 lagging power factor: q_set = p_set * tan(acos(0.95)).

3. PV buses. Every generator bus with a plain Generator component is
   marked PV (its largest generator there specifically) -- these hold a
   fixed voltage magnitude and let PyPSA solve for Q, instead of every
   non-slack bus being fixed-P-fixed-Q with no voltage support at all.
   Important limitation, same root cause as the Slack-bus one: PyPSA's
   own bus-control logic (find_bus_controls() in pypsa/pf.py) only ever
   inspects Generator components, never StorageUnit -- so the 15
   storage-only reservoir buses (including the two largest generation
   buys in the network, 339 and Churchill Falls) CANNOT be made PV this
   way; they stay PQ regardless of what's set on their StorageUnit's
   control attribute. Flagged explicitly below, not silently accepted.

4. Voltage bounds. v_mag_pu_min/max were 0/inf (no real band) on every
   bus; set to a generic 0.95-1.05 pu, so the AC solution can actually be
   checked against something after solving (n.pf() doesn't enforce these
   as hard constraints, they're purely for post-hoc evaluation).

Usage
-----
    python network/run_pf.py --network networks/elec_main_island_solved.nc --method lpf
    python network/run_pf.py --network networks/elec_main_island_solved.nc --method pf
"""
import argparse
import os

import numpy as np
import pandas as pd
import pypsa

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_main_island_solved.nc")

TRANSFORMER_X_R_RATIO = 30.0
LOAD_POWER_FACTOR = 0.95
V_MAG_PU_MIN = 0.95
V_MAG_PU_MAX = 1.05
PV_MIN_CAPACITY_MW = 100.0
PV_MAX_LOAD_RATIO = 0.10


def prepare_for_ac_pf(n: pypsa.Network) -> None:
    print("\n--- Preparing for AC PF (no-op for DC/lpf) ---")

    n.transformers["r"] = n.transformers["x"] / TRANSFORMER_X_R_RATIO
    print(f"1. Transformer r = x / {TRANSFORMER_X_R_RATIO:.0f} (generic X/R, not measured) "
          f"on {len(n.transformers)} transformers")

    pf_angle = np.arccos(LOAD_POWER_FACTOR)
    n.loads_t.q_set = n.loads_t.p_set * np.tan(pf_angle)
    n.loads["q_set"] = n.loads["p_set"] * np.tan(pf_angle)
    print(f"2. Load q_set = p_set * tan(acos({LOAD_POWER_FACTOR})) on {len(n.loads)} loads "
          f"(generic {LOAD_POWER_FACTOR} lagging power factor assumption)")

    # Marking every generator bus PV (all 38) made n.pf() diverge on every
    # snapshot: PyPSA's PV holds voltage exactly with unlimited reactive
    # power, no Q-capability limit -- fine for one bus, unstable for 38 at
    # once on a network with long, weak radial corridors. Narrowed to buses
    # that are both a) large enough to plausibly be a real voltage-regulating
    # station, not a small plant that in practice runs at fixed power factor,
    # and b) predominantly generation, not a mixed load-and-gen bus (on this
    # reduced network, reduce_voltage_network.py folded local load onto every
    # backbone bus, so *no* bus is purely generation with zero load -- "load
    # under 10% of local generation" is the closest real analogue).
    n.generators["control"] = "PQ"
    gens = n.generators[n.generators.carrier != "load_shedding"]
    cap_by_bus = gens.groupby("bus").p_nom.sum()
    mean_load_by_bus = n.loads_t.p_set.T.groupby(n.loads.bus).sum().mean(axis=1).reindex(cap_by_bus.index, fill_value=0.0)
    load_ratio = mean_load_by_bus / cap_by_bus
    pv_eligible_buses = cap_by_bus[(cap_by_bus >= PV_MIN_CAPACITY_MW) & (load_ratio < PV_MAX_LOAD_RATIO)].index
    pv_buses = gens[gens.bus.isin(pv_eligible_buses)].groupby("bus").p_nom.idxmax()
    n.generators.loc[pv_buses.values, "control"] = "PV"
    su_only_buses = set(n.storage_units.bus) - set(gens.bus)
    su_only_cap = n.storage_units[n.storage_units.bus.isin(su_only_buses)].p_nom.sum()
    print(f"3. Marked {len(pv_buses)} / {len(cap_by_bus)} generator buses PV "
          f"(p_nom >= {PV_MIN_CAPACITY_MW:.0f} MW and load < {PV_MAX_LOAD_RATIO:.0%} of local generation; "
          "largest plain Generator there each). Remaining generator buses stay PQ (too small and/or "
          f"too load-dominated to plausibly regulate voltage in practice). {len(su_only_buses)} storage-only "
          f"buses ({su_only_cap:.0f} MW combined) CANNOT be PV regardless -- PyPSA's find_bus_controls() "
          "only reads Generator components, never StorageUnit.")

    n.buses["v_mag_pu_min"] = V_MAG_PU_MIN
    n.buses["v_mag_pu_max"] = V_MAG_PU_MAX
    print(f"4. Set v_mag_pu bounds to [{V_MAG_PU_MIN}, {V_MAG_PU_MAX}] pu on all {len(n.buses)} buses "
          "(informational only -- n.pf() doesn't enforce these, but they're now there to check against)")

    # n.pf()'s single-bus sub-network handling (sub_network_pf_singlebus) writes
    # its power-balance residual into a slack generator's p, so it needs one to
    # exist even for a trivial 1-bus sub-network -- unlike n.lpf(), which doesn't
    # need this. Bus 65 (a pure Link pass-through terminal: two DC links meet
    # there, nothing else attached) has no Generator at all, and crashes n.pf()
    # with a bare KeyError otherwise. A p_nom=0 placeholder is inert bookkeeping,
    # not a modeling change -- nothing real is or can be attached there.
    for b in list(n.buses.index):
        touches_branch = ((n.lines.bus0 == b) | (n.lines.bus1 == b)).any() or \
                          ((n.transformers.bus0 == b) | (n.transformers.bus1 == b)).any()
        has_gen = (n.generators.bus == b).any()
        touches_link = ((n.links.bus0 == b) | (n.links.bus1 == b)).any()
        if not touches_branch and touches_link and not has_gen:
            n.add("Generator", f"{b} pf-slack-placeholder", bus=b, carrier="AC", p_nom=0.0, control="Slack")
            print(f"  Added a p_nom=0 placeholder Generator at bus {b} (pure Link terminal, no Generator otherwise)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--method", choices=["lpf", "pf"], default="lpf")
    args = parser.parse_args()

    print(f"Loading solved network: {args.network}")
    n = pypsa.Network(args.network)
    print(f"  {len(n.buses)} buses, {len(n.snapshots)} snapshots")

    n.determine_network_topology()
    print("\nSub-networks:")
    print(n.sub_networks)
    for sn in n.sub_networks.index:
        sub = n.sub_networks.at[sn, "obj"]
        slack_gens = n.generators.index[(n.generators.bus.isin(sub.buses_i())) & (n.generators.control == "Slack")]
        print(f"  SubNetwork {sn} ({n.sub_networks.at[sn,'carrier']}, {len(sub.buses_i())} buses): "
              f"slack_bus={n.sub_networks.at[sn,'slack_bus']}, slack generator(s)={slack_gens.tolist()}")

    if args.method == "pf":
        prepare_for_ac_pf(n)

    print(f"\nRunning {'DC (linearized)' if args.method=='lpf' else 'AC (nonlinear Newton-Raphson)'} "
          f"power flow over all {len(n.snapshots)} snapshots, using the LOPF-optimal dispatch as fixed injections...")
    if args.method == "lpf":
        n.lpf(n.snapshots)
    else:
        print("  Seeding from a DC (n.lpf()) solve first -- gives Newton-Raphson a much better "
              "starting point than PyPSA's default flat start (0 deg / 1.0 pu everywhere).")
        n.lpf(n.snapshots)
        results = n.pf(n.snapshots, use_seed=True)
        print("\nConvergence:")
        print(results.converged.all(axis=1).value_counts())
        print(f"  {int(results.converged.all(axis=1).sum())} / {len(n.snapshots)} snapshots fully converged")

    print("\n=== Line loading (from PF results) ===")
    loading = n.lines_t.p0.abs().div(n.lines.s_nom, axis=1)
    print(f"  max loading: {loading.max().max():.1%}, lines >=90%: {(loading.max()>=0.9).sum()} / {len(n.lines)}")

    if args.method == "pf":
        print("\n=== Voltage magnitude vs the 0.95-1.05 pu band just set ===")
        vmag = n.buses_t.v_mag_pu
        below = (vmag < n.buses.v_mag_pu_min).any()
        above = (vmag > n.buses.v_mag_pu_max).any()
        print(f"  buses ever below {V_MAG_PU_MIN} pu: {below.sum()} / {len(n.buses)}")
        print(f"  buses ever above {V_MAG_PU_MAX} pu: {above.sum()} / {len(n.buses)}")
        print(f"  v_mag_pu overall: min={vmag.min().min():.3f}, max={vmag.max().max():.3f}")
    else:
        print("\n=== Bus voltage angles (DC) ===")
        print(n.buses_t.v_ang.describe().T[["mean", "std", "min", "max"]].describe())

    output = os.path.splitext(args.network)[0] + f"_{args.method}.nc"
    n.export_to_netcdf(output)
    print(f"\nSaved -> {output}")


if __name__ == "__main__":
    main()
