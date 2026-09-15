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
the LOPF line flows almost exactly. Requires a single slack bus per
sub-network (see run_lopf_main_island.py's slack assignment).

AC (--method pf): full nonlinear Newton-Raphson power flow, n.pf(). Four
gaps are closed in prepare_for_ac_pf() (harmless no-ops for --method lpf,
which ignores r, Q, and PV/PQ classification entirely):

1. Transformer resistance: r = x / 30 (generic X/R, not measured -- r was
   0 in the source data).
2. Load reactive power: q_set = p_set * tan(acos(0.95)), a generic
   lagging power factor (source data has q_set = 0 everywhere).
3. PV buses: the largest plain Generator at each sufficiently large,
   generation-dominant bus is marked PV so PyPSA solves for Q there
   instead of holding it fixed. Storage-only buses get a zero-dispatch
   placeholder Generator so they're eligible too (PyPSA's bus-control
   logic never reads StorageUnit.control). See network/docs/DEBUGGING_HISTORY.md
   for why the eligibility thresholds are network-specific.
4. Voltage bounds: v_mag_pu_min/max set to a generic 0.95-1.05 pu
   (informational only -- n.pf() doesn't enforce these).

Usage
-----
    python network/run_pf.py --network networks/elec_solved.nc --method lpf
    python network/run_pf.py --network networks/elec_solved.nc --method pf
"""
import argparse
import os

import numpy as np
import pandas as pd
import pypsa

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_solved.nc")

TRANSFORMER_X_R_RATIO = 30.0
LOAD_POWER_FACTOR = 0.95
GENERATOR_POWER_FACTOR = 0.9
V_MAG_PU_MIN = 0.95
V_MAG_PU_MAX = 1.05

# PV-eligibility thresholds: a bus qualifies if local generator capacity
# >= PV_MIN_CAPACITY_MW and local load / local generation < PV_MAX_LOAD_RATIO.
# The right values are topology-dependent (see network/docs/DEBUGGING_HISTORY.md) --
# override via --pv-min-capacity / --pv-max-load-ratio per network. Defaults
# below are tuned for elec_solved.nc; use 0 / 1 for elec_735kv.nc.
PV_MIN_CAPACITY_MW = 100.0
PV_MAX_LOAD_RATIO = 0.10


def prepare_for_ac_pf(n: pypsa.Network, pv_min_capacity: float = PV_MIN_CAPACITY_MW,
                       pv_max_load_ratio: float = PV_MAX_LOAD_RATIO) -> None:
    print("\n--- Preparing for AC PF (no-op for DC/lpf) ---")

    n.transformers["r"] = n.transformers["x"] / TRANSFORMER_X_R_RATIO
    print(f"1. Transformer r = x / {TRANSFORMER_X_R_RATIO:.0f} (generic X/R, not measured) "
          f"on {len(n.transformers)} transformers")

    pf_angle = np.arccos(LOAD_POWER_FACTOR)
    n.loads_t.q_set = n.loads_t.p_set * np.tan(pf_angle)
    n.loads["q_set"] = n.loads["p_set"] * np.tan(pf_angle)
    print(f"2. Load q_set = p_set * tan(acos({LOAD_POWER_FACTOR})) on {len(n.loads)} loads "
          f"(generic {LOAD_POWER_FACTOR} lagging power factor assumption)")

    # Storage-only buses can never become PV: PyPSA's find_bus_controls() only
    # reads Generator.control, never StorageUnit.control. Placeholder carries
    # zero dispatch -- real MW injection stays on the StorageUnit as before;
    # it exists purely so the bus is eligible for the PV logic below.
    su_only_buses = set(n.storage_units.bus) - set(n.generators[n.generators.carrier != "load_shedding"].bus)
    for b in su_only_buses:
        cap = n.storage_units.loc[n.storage_units.bus == b, "p_nom"].sum()
        n.add("Generator", f"{b} storage-pv-placeholder", bus=b, carrier="hydro", p_nom=cap)
    print(f"1b. Added {len(su_only_buses)} zero-dispatch placeholder generator(s) on storage-only "
          f"buses ({n.storage_units.loc[n.storage_units.bus.isin(su_only_buses), 'p_nom'].sum():.0f} MW "
          "combined, sized to match), so they're eligible for PV classification below")

    # Generator reactive capability is based on nameplate capacity, not
    # dispatch -- a synchronous machine can supply close to full Q even at
    # zero real output (synchronous-condenser behavior), so tying it to
    # dispatch would give zero support from idle-but-present generators.
    real_gens = n.generators.index[n.generators.carrier != "load_shedding"]
    gen_pf_angle = np.arccos(GENERATOR_POWER_FACTOR)
    n.generators.loc[real_gens, "q_set"] = n.generators.loc[real_gens, "p_nom"] * np.tan(gen_pf_angle)
    print(f"2b. Generator q_set = p_nom * tan(acos({GENERATOR_POWER_FACTOR})) on {len(real_gens)} real generators "
          f"(generic {GENERATOR_POWER_FACTOR} PF reactive capability, fixed to installed capacity not dispatch)")

    # PV eligibility: large enough to plausibly be a real voltage-regulating
    # station, and generation-dominant rather than a mixed load/gen bus.
    # Preserve the deliberately-chosen slack generator through the reset --
    # resetting every generator to PQ first (needed before PV promotion)
    # would otherwise silently drop the Slack flag with nothing restoring it.
    prior_slack = n.generators.index[n.generators.control == "Slack"]
    n.generators["control"] = "PQ"
    gens = n.generators[n.generators.carrier != "load_shedding"]
    cap_by_bus = gens.groupby("bus").p_nom.sum()
    mean_load_by_bus = n.loads_t.p_set.T.groupby(n.loads.bus).sum().mean(axis=1).reindex(cap_by_bus.index, fill_value=0.0)
    load_ratio = mean_load_by_bus / cap_by_bus
    pv_eligible_buses = cap_by_bus[(cap_by_bus >= pv_min_capacity) & (load_ratio < pv_max_load_ratio)].index
    pv_buses = gens[gens.bus.isin(pv_eligible_buses)].groupby("bus").p_nom.idxmax()
    n.generators.loc[pv_buses.values, "control"] = "PV"
    n.generators.loc[prior_slack, "control"] = "Slack"
    su_only_buses = set(n.storage_units.bus) - set(gens.bus)
    print(f"3. Marked {len(pv_buses)} / {len(cap_by_bus)} generator buses PV "
          f"(p_nom >= {pv_min_capacity:.0f} MW and load < {pv_max_load_ratio:.0%} of local generation; "
          "largest plain Generator there each -- including the storage-pv-placeholders added in step "
          f"1b, now eligible on the same terms as real generators). Remaining generator buses stay PQ "
          f"(too small and/or too load-dominated to plausibly regulate voltage in practice). "
          f"{len(su_only_buses)} storage-only buses remain un-PV-eligible after that.")

    n.buses["v_mag_pu_min"] = V_MAG_PU_MIN
    n.buses["v_mag_pu_max"] = V_MAG_PU_MAX
    print(f"4. Set v_mag_pu bounds to [{V_MAG_PU_MIN}, {V_MAG_PU_MAX}] pu on all {len(n.buses)} buses "
          "(informational only -- n.pf() doesn't enforce these, but they're now there to check against)")

    # n.pf()'s single-bus sub-network handling needs a slack generator to exist
    # even for a trivial 1-bus sub-network. A p_nom=0 placeholder is inert
    # bookkeeping for a pure Link pass-through terminal with no Generator.
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
    parser.add_argument("--pv-min-capacity", type=float, default=PV_MIN_CAPACITY_MW,
                         help="Min local generator p_nom (MW) for a bus to be PV-eligible. "
                              "Use 0 for elec_735kv.nc (see constant definitions above).")
    parser.add_argument("--pv-max-load-ratio", type=float, default=PV_MAX_LOAD_RATIO,
                         help="Max local-load/local-generation ratio for a bus to be PV-eligible. "
                              "Use 1 for elec_735kv.nc.")
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
        prepare_for_ac_pf(n, pv_min_capacity=args.pv_min_capacity, pv_max_load_ratio=args.pv_max_load_ratio)

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
