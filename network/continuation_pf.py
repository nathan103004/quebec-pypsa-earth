# -*- coding: utf-8 -*-
"""
continuation_pf.py

Continuation (homotopy) AC power flow: ramp the dispatch pattern from
0% to 100% of its LOPF-optimal level in small steps, re-solving AC PF
(n.pf()) at each step and seeding from the previous step's converged
voltage solution. If AC PF stops converging before reaching 100%, the
fraction where it last converged is the network's real maximum
loadability for this dispatch pattern and bus -- a direct, physical
answer instead of inferring it from Jacobian behavior.

Usage
-----
    python network/continuation_pf.py --network networks/elec_main_island_solved.nc
"""
import argparse
import logging
import os

import networkx as nx
import numpy as np
import pandas as pd
import pypsa

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_main_island_solved.nc")

TRANSFORMER_X_R_RATIO = 30.0
LOAD_POWER_FACTOR = 0.95
PV_MIN_CAPACITY_MW = 100.0
PV_MAX_LOAD_RATIO = 0.10


def prepare(n: pypsa.Network) -> None:
    n.transformers["r"] = n.transformers["x"] / TRANSFORMER_X_R_RATIO
    pf_angle = np.arccos(LOAD_POWER_FACTOR)
    n.loads_t.q_set = n.loads_t.p_set * np.tan(pf_angle)
    n.loads["q_set"] = n.loads["p_set"] * np.tan(pf_angle)

    n.generators["control"] = "PQ"
    gens = n.generators[n.generators.carrier != "load_shedding"]
    cap_by_bus = gens.groupby("bus").p_nom.sum()
    mean_load_by_bus = n.loads_t.p_set.T.groupby(n.loads.bus).sum().mean(axis=1).reindex(cap_by_bus.index, fill_value=0.0)
    load_ratio = mean_load_by_bus / cap_by_bus
    pv_eligible = cap_by_bus[(cap_by_bus >= PV_MIN_CAPACITY_MW) & (load_ratio < PV_MAX_LOAD_RATIO)].index
    pv_buses = gens[gens.bus.isin(pv_eligible)].groupby("bus").p_nom.idxmax()
    n.generators.loc[pv_buses.values, "control"] = "PV"
    n.generators.loc["129 ror-Beauharnois", "control"] = "Slack"

    # Drop links and the DC-only buses entirely -- links carry their full
    # LOPF-dispatched flow regardless of any generator/load scaling applied
    # below, which would create a fake imbalance at low scale. Isolating to
    # just the main AC network removes that confound.
    G = nx.Graph()
    G.add_nodes_from(n.buses.index)
    for df in (n.lines, n.transformers):
        for _, r in df.iterrows():
            G.add_edge(r.bus0, r.bus1)
    main_ac = max(nx.connected_components(G), key=len)
    drop_buses = n.buses.index.difference(main_ac)
    n.mremove("Link", n.links.index)
    n.mremove("Load", n.loads.index[n.loads.bus.isin(drop_buses)])
    n.mremove("Generator", n.generators.index[n.generators.bus.isin(drop_buses)])
    n.mremove("StorageUnit", n.storage_units.index[n.storage_units.bus.isin(drop_buses)])
    n.mremove("Bus", drop_buses)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--snapshot-index", type=int, default=0)
    parser.add_argument("--step", type=float, default=0.05)
    args = parser.parse_args()

    print(f"Loading: {args.network}")
    n = pypsa.Network(args.network)
    prepare(n)
    snap = n.snapshots[args.snapshot_index:args.snapshot_index + 1]
    print(f"Snapshot: {snap[0]}")

    # Full-dispatch (100%) targets, per-snapshot
    full_gen_p = n.generators_t.p.reindex(columns=n.generators.index, fill_value=0.0).loc[snap]
    full_su_p = n.storage_units_t.p.reindex(columns=n.storage_units.index, fill_value=0.0).loc[snap]
    full_load_p = n.loads_t.p_set.loc[snap]
    full_load_q = n.loads_t.q_set.loc[snap]

    non_slack_gens = n.generators.index[n.generators.control != "Slack"]

    logging.disable(logging.CRITICAL)
    last_good = 0.0
    last_good_ang = None
    last_good_vmag = None
    scales = np.arange(args.step, 1.0 + 1e-9, args.step)
    for s in scales:
        n.generators_t.p.loc[snap, non_slack_gens] = full_gen_p[non_slack_gens].to_numpy() * s
        n.storage_units_t.p.loc[snap, :] = full_su_p.to_numpy() * s
        n.loads_t.p_set.loc[snap, :] = full_load_p.to_numpy() * s
        n.loads_t.q_set.loc[snap, :] = full_load_q.to_numpy() * s

        # pf()/lpf() read p_set, not p -- keep them synced every step (this
        # was silently reverting to stale/zero p_set each iteration otherwise)
        n.generators_t.p_set = n.generators_t.p.copy()
        n.storage_units_t.p_set = n.storage_units_t.p.copy()

        if last_good_ang is not None:
            n.buses_t.v_ang.loc[snap] = last_good_ang
            n.buses_t.v_mag_pu.loc[snap] = last_good_vmag
            res = n.pf(snap, use_seed=True)
        else:
            # true flat start -- use_seed=False does NOT reset pre-existing
            # buses_t.v_ang/v_mag_pu (e.g. stale values loaded from the
            # LOPF-solved .nc file on disk), so reset explicitly
            n.buses_t.v_ang.loc[snap] = 0.0
            n.buses_t.v_mag_pu.loc[snap] = 1.0
            res = n.pf(snap, use_seed=False)

        conv = bool(res["converged"].iloc[0].all())
        err = res["error"].iloc[0].max()
        logging.disable(logging.NOTSET)
        print(f"  scale={s:.2f}: converged={conv}, error={err:.3e}")
        logging.disable(logging.CRITICAL)
        if conv:
            last_good = s
            last_good_ang = n.buses_t.v_ang.loc[snap].copy()
            last_good_vmag = n.buses_t.v_mag_pu.loc[snap].copy()
        else:
            print(f"\nStopped converging beyond scale={last_good:.2f} "
                  f"({last_good*100:.0f}% of the LOPF-dispatched level).")
            break
    else:
        print(f"\nConverged all the way to 100% (last good scale={last_good:.2f}).")
    logging.disable(logging.NOTSET)


if __name__ == "__main__":
    main()
