# -*- coding: utf-8 -*-
"""
export_to_matpower.py

Export the solved main-island network to a MATPOWER case (.m) file, for
running AC OPF / AC PF in MATLAB/MATPOWER directly, as a cross-check
against PyPSA's own n.pf() (which hit an unresolved convergence issue --
see the conversation this script came out of).

Scope: the main AC network only (209 buses, lines+transformers) -- the 5
Link-only DC "islands" (buses 65/161/1015/2206/3549) are dropped, same as
every AC PF test earlier in this project, since MATPOWER's base case
format has no native DC-link representation and those buses are a small,
separate part of the network anyway.

One snapshot only, since a MATPOWER case is a static single-operating-point
model, not a time series. Defaults to the network's worst-loaded hour
(most interesting for a congestion study); pick any other with
--snapshot-index.

Per-unit conversion, since MATPOWER expects everything on one common
system base (--base-mva, default 100 MVA) and PyPSA stores r_pu/x_pu on
two DIFFERENT implicit bases depending on component type (confirmed
directly from PyPSA's own source, pypsa/pf.py):
  - Lines:        x_pu = x / v_nom**2       -- implicit 1 MVA base
  - Transformers: x_pu = x / s_nom          -- each transformer's OWN s_nom as base
Both get rescaled to the system base via the standard Z_pu_new =
Z_pu_old * (S_new / S_old) relation.

Generator reactive capability (Qmax/Qmin) isn't in our data (no real
generator Q-capability curves), needed for MATPOWER's mandatory columns.
Uses a generic +-tan(acos(0.85)) * Pmax assumption -- wider than the
0.95 power-factor assumption used for load Q elsewhere in this project,
since generator reactive capability is normally larger than a typical
load's power factor. Flagged here as generic, not measured, same
honesty standard as every other placeholder assumption in this project.

Usage
-----
    python network/export_to_matpower.py --network networks/elec_main_island_solved.nc
"""
import argparse
import os
import sys

import networkx as nx
import numpy as np
import pandas as pd
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(NETWORK_DIR)

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_main_island_solved.nc")
# Writes directly to MATPOWER's data folder -- where it's actually run
# from -- not this project's own network/ folder, after losing time
# earlier to two different copies silently going out of sync.
DEFAULT_OUTPUT = r"C:\Users\hjgua\Documents\MATLAB\matpower8.1\data\quebec_main_island.m"

BASE_MVA = 100.0
GEN_PF_ASSUMED = 0.85  # generic reactive-capability assumption, not measured
V_MAG_MIN, V_MAG_MAX = 0.95, 1.05

# Many generators (hydro/ror) share an identical $0/MWh marginal cost with
# zero curvature -- a flat, degenerate cost function is a known cause of a
# singular KKT matrix in interior-point OPF solvers (MATPOWER's MIPS
# reported this directly: "Matrix is close to singular", RCOND ~1e-16,
# even after fixing the separate transformer-r=0 issue below). A tiny
# quadratic term breaks the degeneracy without materially changing the
# economics -- at 1000 MW dispatch it adds about 2*eps*1000 = 0.2 $/MWh
# to the effective marginal cost, negligible next to real cost spreads.
GENCOST_QUADRATIC_EPS = 1e-4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-index", type=int, default=None,
                         help="Index into n.snapshots to export. Default: the worst-loaded hour.")
    parser.add_argument("--base-mva", type=float, default=BASE_MVA)
    parser.add_argument("--case-name", default="quebec_main_island")
    args = parser.parse_args()

    print(f"Loading: {args.network}")
    n = pypsa.Network(args.network)

    # Transformer resistance isn't persisted in the saved network (only x
    # is) -- same generic X/R=30 fix used for AC PF work earlier. Without
    # this every transformer exports with r=0 exactly, which is a classic
    # cause of "badly scaled" / near-singular failures in AC solvers
    # (confirmed directly: MATPOWER's MIPS interior-point OPF failed with
    # RCOND ~2e-19 on an export that had this bug).
    n.transformers["r"] = n.transformers["x"] / 30.0
    n.calculate_dependent_values()

    # Real dispatch (p_set is what n.pf() reads; use p directly here since
    # we're deriving Pg/Pd ourselves, not calling n.pf()).
    if args.snapshot_index is None:
        loading = n.lines_t.p0.abs().div(n.lines.s_nom, axis=1).max(axis=1)
        snap = loading.idxmax()
        print(f"No --snapshot-index given: using the worst-loaded hour, {snap}")
    else:
        snap = n.snapshots[args.snapshot_index]
        print(f"Using snapshot {snap}")

    # Isolate the main AC network (lines+transformers only), matching
    # every AC PF test earlier in this project.
    G = nx.Graph()
    G.add_nodes_from(n.buses.index)
    for df in (n.lines, n.transformers):
        for _, r in df.iterrows():
            G.add_edge(r.bus0, r.bus1)
    main_ac = max(nx.connected_components(G), key=len)
    print(f"Main AC network: {len(main_ac)} / {len(n.buses)} buses")

    buses = n.buses.loc[list(main_ac)].copy()
    bus_id = {b: i + 1 for i, b in enumerate(buses.index)}  # MATPOWER bus IDs are 1-based integers

    lines = n.lines[n.lines.bus0.isin(main_ac) & n.lines.bus1.isin(main_ac)]
    trafos = n.transformers[n.transformers.bus0.isin(main_ac) & n.transformers.bus1.isin(main_ac)]
    gens = n.generators[(n.generators.bus.isin(main_ac)) & (n.generators.carrier != "load_shedding")]
    sus = n.storage_units[n.storage_units.bus.isin(main_ac)]
    loads = n.loads[n.loads.bus.isin(main_ac)]

    # --- bus data ---
    load_p_by_bus = n.loads_t.p_set.loc[snap, loads.index].groupby(loads.bus).sum().reindex(buses.index, fill_value=0.0)
    pf_angle = np.arccos(0.95)
    load_q_by_bus = load_p_by_bus * np.tan(pf_angle)

    slack_gen = n.generators.index[n.generators.control == "Slack"]
    slack_bus = n.generators.at[slack_gen[0], "bus"] if len(slack_gen) else buses.index[0]
    pv_buses = set(gens[gens.control == "PV"].bus)

    bus_rows = []
    for b, row in buses.iterrows():
        if b == slack_bus:
            btype = 3
        elif b in pv_buses:
            btype = 2
        else:
            btype = 1
        bus_rows.append([
            bus_id[b], btype, load_p_by_bus[b], load_q_by_bus[b],
            0.0, 0.0, 1, 1.0, 0.0, row.v_nom, 1, V_MAG_MAX, V_MAG_MIN,
        ])

    # --- generator data (plain Generators + StorageUnit dispatch, both as MATPOWER gens) ---
    gen_rows = []
    gencost_rows = []
    for g, row in gens.iterrows():
        p = float(n.generators_t.p.at[snap, g]) if g in n.generators_t.p.columns else 0.0
        qmax = row.p_nom * np.tan(np.arccos(GEN_PF_ASSUMED))
        gen_rows.append([
            bus_id[row.bus], p, 0.0, qmax, -qmax, 1.0, row.p_nom if row.p_nom > 0 else args.base_mva,
            1, row.p_nom, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        ])
        mc = float(row.marginal_cost) if pd.notna(row.marginal_cost) else 0.0
        gencost_rows.append([2, 0, 0, 3, GENCOST_QUADRATIC_EPS, mc, 0.0])

    for s, row in sus.iterrows():
        p = float(n.storage_units_t.p.at[snap, s]) if s in n.storage_units_t.p.columns else 0.0
        qmax = row.p_nom * np.tan(np.arccos(GEN_PF_ASSUMED))
        gen_rows.append([
            bus_id[row.bus], max(p, 0.0), 0.0, qmax, -qmax, 1.0, row.p_nom,
            1, row.p_nom, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        ])
        mc = float(row.marginal_cost) if pd.notna(row.marginal_cost) else 0.0
        gencost_rows.append([2, 0, 0, 3, GENCOST_QUADRATIC_EPS, mc, 0.0])

    print(f"  {len(gen_rows)} generators (incl. storage dispatch), slack bus = {slack_bus} (MATPOWER id {bus_id[slack_bus]})")

    # --- branch data (lines + transformers combined) ---
    branch_rows = []
    for l, row in lines.iterrows():
        r_pu = row.r / row.v_nom**2 * args.base_mva
        x_pu = row.x / row.v_nom**2 * args.base_mva
        b_pu = row.b * row.v_nom**2 / args.base_mva
        branch_rows.append([
            bus_id[row.bus0], bus_id[row.bus1], r_pu, x_pu, b_pu,
            row.s_nom, row.s_nom, row.s_nom, 0.0, 0.0, 1, -360, 360,
        ])
    for t, row in trafos.iterrows():
        r_pu = row.r / row.s_nom * args.base_mva
        x_pu = row.x / row.s_nom * args.base_mva
        ratio = row.tap_ratio if row.tap_ratio else 1.0
        branch_rows.append([
            bus_id[row.bus0], bus_id[row.bus1], r_pu, x_pu, 0.0,
            row.s_nom, row.s_nom, row.s_nom, ratio, 0.0, 1, -360, 360,
        ])
    print(f"  {len(branch_rows)} branches ({len(lines)} lines + {len(trafos)} transformers)")

    # --- write the .m file ---
    def fmt_matrix(rows):
        return ";\n".join("\t" + "\t".join(f"{v:.6g}" for v in r) for r in rows)

    total_load = load_p_by_bus.sum()
    total_gen = sum(r[1] for r in gen_rows)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(f"function mpc = {args.case_name}\n")
        f.write(f"%% Exported from PyPSA network {os.path.basename(args.network)}, snapshot {snap}\n")
        f.write(f"%% Total load: {total_load:.1f} MW, total generation (incl. storage): {total_gen:.1f} MW\n")
        f.write("%% Generator Qmax/Qmin are a generic assumption (not measured) -- see script docstring.\n\n")
        f.write("mpc.version = '2';\n\n")
        f.write(f"mpc.baseMVA = {args.base_mva:.1f};\n\n")

        f.write("%% bus data\n%\tbus_i\ttype\tPd\tQd\tGs\tBs\tarea\tVm\tVa\tbaseKV\tzone\tVmax\tVmin\n")
        f.write("mpc.bus = [\n" + fmt_matrix(bus_rows) + ";\n];\n\n")

        f.write("%% generator data\n%\tbus\tPg\tQg\tQmax\tQmin\tVg\tmBase\tstatus\tPmax\tPmin\tPc1\tPc2\tQc1min\tQc1max\tQc2min\tQc2max\tramp_agc\tramp_10\tramp_30\tramp_q\tapf\n")
        f.write("mpc.gen = [\n" + fmt_matrix(gen_rows) + ";\n];\n\n")

        f.write("%% branch data\n%\tfbus\ttbus\tr\tx\tb\trateA\trateB\trateC\tratio\tangle\tstatus\tangmin\tangmax\n")
        f.write("mpc.branch = [\n" + fmt_matrix(branch_rows) + ";\n];\n\n")

        f.write("%% generator cost data\n%\t2\tstartup\tshutdown\tn\tc(n-1)\t...\tc0\n")
        f.write("mpc.gencost = [\n" + fmt_matrix(gencost_rows) + ";\n];\n")

    print(f"\nWritten -> {args.output}")
    print(f"  buses: {len(bus_rows)}, generators: {len(gen_rows)}, branches: {len(branch_rows)}")
    print(f"  Run in MATLAB with: mpc = quebec_main_island; results = runpf(mpc); / runopf(mpc);")


if __name__ == "__main__":
    main()
