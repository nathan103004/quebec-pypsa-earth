# -*- coding: utf-8 -*-
"""
export_to_matpower.py

Export the solved main-island network to a MATPOWER case (.m) file, for
running AC OPF / AC PF in MATLAB/MATPOWER directly, as an independent
cross-check against PyPSA's own n.pf().

Scope: the main AC network only (lines+transformers) -- Link-only DC
"islands" are dropped, since MATPOWER's base case format has no native
DC-link representation.

One snapshot only, since a MATPOWER case is a static single-operating-point
model, not a time series. Defaults to the network's worst-loaded hour
(most interesting for a congestion study); pick any other with
--snapshot-index.

Per-unit conversion: MATPOWER expects everything on one common system base
(--base-mva, default 100 MVA), but PyPSA stores r_pu/x_pu on two different
implicit bases depending on component type (pypsa/pf.py):
  - Lines:        x_pu = x / v_nom**2       -- implicit 1 MVA base
  - Transformers: x_pu = x / s_nom          -- each transformer's OWN s_nom as base
Both are rescaled to the system base via the standard Z_pu_new =
Z_pu_old * (S_new / S_old) relation.

Generator reactive capability (Qmax/Qmin) isn't in the source data (no real
generator Q-capability curves), but is a mandatory MATPOWER column. Uses a
generic +-tan(acos(0.85)) * Pmax assumption -- not measured. Qg itself is
always exported as 0 (a fixed injection for any generator not on a PV/slack
bus, in MATPOWER's convention) -- real/storage generators keep their real Pg
but lose their solved Q; PyPSA's zero-real-power reactive-compensation
generators are excluded entirely rather than exported as dead (0, 0) rows.

Usage
-----
    python network/export_to_matpower.py --network networks/elec_solved.nc
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

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_solved.nc")
# Writes directly to MATPOWER's data folder, where it's actually run from.
DEFAULT_OUTPUT = r"C:\Users\hjgua\Documents\MATLAB\matpower8.1\data\quebec_main_island.m"

BASE_MVA = 100.0
GEN_PF_ASSUMED = 0.85  # generic reactive-capability assumption, not measured
V_MAG_MIN, V_MAG_MAX = 0.95, 1.05

# Many generators share an identical $0/MWh marginal cost with zero
# curvature -- a flat, degenerate cost function causes a singular KKT
# matrix in interior-point OPF solvers. A tiny quadratic term breaks the
# degeneracy without materially changing the economics (negligible next to
# real cost spreads at typical dispatch levels).
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

    # Transformer resistance isn't persisted in the saved network (only x is) --
    # generic X/R=30 assumption, same as run_pf.py's AC PF preparation.
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

    # Isolate the main AC network (lines+transformers only) -- MATPOWER's
    # base case format has no native DC-link representation.
    G = nx.Graph()
    G.add_nodes_from(n.buses.index)
    for df in (n.lines, n.transformers):
        for _, r in df.iterrows():
            G.add_edge(r.bus0, r.bus1)
    main_ac = max(nx.connected_components(G), key=len)
    print(f"Main AC network: {len(main_ac)} / {len(n.buses)} buses")

    buses = n.buses.loc[list(main_ac)].copy()
    bus_id = {b: i + 1 for i, b in enumerate(buses.index)}  # MATPOWER bus IDs are 1-based integers

    # Seed bus voltage angles from a fresh DC PF solve at this snapshot, on a throwaway
    # copy of the network (so it can't disturb the generator dispatch read below) --
    # matches run_pf.py, which always seeds n.pf() from n.lpf() rather than a flat start.
    n_seed = n.copy()
    n_seed.set_snapshots([snap])
    n_seed.lpf(n_seed.snapshots)
    va_deg_by_bus = np.degrees(n_seed.buses_t.v_ang.loc[snap]).reindex(buses.index, fill_value=0.0)
    print(f"  DC-seeded starting angles: {va_deg_by_bus.min():.2f} to {va_deg_by_bus.max():.2f} deg "
          "(exported as bus.Va, not a flat 0 start)")

    lines = n.lines[n.lines.bus0.isin(main_ac) & n.lines.bus1.isin(main_ac)]
    trafos = n.transformers[n.transformers.bus0.isin(main_ac) & n.transformers.bus1.isin(main_ac)]
    # Exclude load-shedding placeholders (not real capacity) and reactive-
    # compensation placeholders (p=0 always, and Qg is exported as 0 below --
    # see docstring -- so they'd inject nothing anyway; excluding them makes
    # that explicit instead of leaving dead rows in the case).
    gens = n.generators[
        (n.generators.bus.isin(main_ac))
        & (~n.generators.carrier.isin(["load_shedding", "reactive_compensation"]))
    ]
    sus = n.storage_units[n.storage_units.bus.isin(main_ac)]
    loads = n.loads[n.loads.bus.isin(main_ac)]

    # --- bus data ---
    load_p_by_bus = n.loads_t.p_set.loc[snap, loads.index].groupby(loads.bus).sum().reindex(buses.index, fill_value=0.0)
    pf_angle = np.arccos(0.95)
    load_q_by_bus = load_p_by_bus * np.tan(pf_angle)

    # Shunt capacitors: MATPOWER's Gs/Bs are real MW/MVAr demanded/injected at
    # V=1.0pu, not a per-unit value; PyPSA's b (Siemens) -> b_pu = b * v_nom**2
    # (implicit 1 MVA base) numerically equals that same MVAr figure at V=1pu,
    # so no further base rescaling is needed here (unlike branch r/x below).
    shunts = n.shunt_impedances[n.shunt_impedances.bus.isin(main_ac)] if len(n.shunt_impedances) else n.shunt_impedances
    bs_by_bus = pd.Series(0.0, index=buses.index)
    if len(shunts):
        b_mvar = shunts.b * shunts.bus.map(n.buses.v_nom) ** 2
        bs_by_bus = b_mvar.groupby(shunts.bus).sum().reindex(buses.index, fill_value=0.0)
        print(f"  {len(shunts)} shunt capacitors carried over ({bs_by_bus.sum():.0f} MVAr total at V=1.0pu)")

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
            0.0, bs_by_bus[b], 1, 1.0, va_deg_by_bus[b], row.v_nom, 1, V_MAG_MAX, V_MAG_MIN,
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
    print(f"  Run in MATLAB with: mpc = {args.case_name}; results = runpf(mpc); / runopf(mpc);")


if __name__ == "__main__":
    main()
