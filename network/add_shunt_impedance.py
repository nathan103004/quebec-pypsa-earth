# -*- coding: utf-8 -*-
"""
add_shunt_impedance.py

Adds fixed PyPSA `ShuntImpedance` components to the 735kV network: shunt
reactors (b < 0) at buses that exceed 1.05pu and shunt capacitors (b > 0) at
buses that fall below 0.95pu. A ShuntImpedance's reactive output is
q = v_mag_pu**2 * b_pu (positive = generation, see pypsa/pf.py), so a
capacitor's support and a reactor's absorption both scale with the square
of bus voltage. `b` is a single float, not a time series: each device
delivers the same susceptance every hour.

Sizing
------
1. Probe: a p_nom=0, control=PV generator is added at every out-of-band bus
   at once, held at the band edge it violates (1.05pu for overvoltage
   buses, 0.95pu for undervoltage buses), and a full AC PF is run over all
   snapshots. Its q at each hour is the reactive power needed to just reach
   the band at that bus. Probing all buses together captures how
   neighbouring devices affect each other.
2. Buses that leave the band in both directions over the week are skipped:
   a fixed device can only correct one direction (they need a switched bank
   or SVC). A reactor bus whose probe never absorbs, or a capacitor bus
   whose probe never supplies, needs no device and is skipped too.
3. Each device is sized at its bus's worst hour (most absorption for a
   reactor, most supply for a capacitor), converted to susceptance via
   b = Q_MVAr / v_nom_kV**2 (b_pu = b * v_nom**2 = Q at 1.0pu).
4. Worst-hour sizing over-compensates at every other hour, so all reactors
   are scaled by one factor and all capacitors by another. Defaults:
   reactors 1.0, capacitors 0.7 (--reactor-scale / --capacitor-scale). A
   sweep over a grid of factors is always run and printed for reference;
   --grid-best keeps the pair that converges every snapshot and leaves the
   fewest buses outside the band (ties broken by fewest bus-hours).

Usage
-----
    python network/add_shunt_impedance.py --network network/networks_current/elec_735kv.nc --output network/networks_current/elec_735kv_shunt.nc
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, NETWORK_DIR)

import run_pf  # noqa: E402

V_MIN = 0.95
V_MAX = 1.05
SCALE_GRID = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]


def solve_ac(n: pypsa.Network) -> pd.Series:
    """DC-seeded AC PF over all snapshots; returns per-snapshot convergence."""
    n.lpf(n.snapshots)
    results = n.pf(n.snapshots, use_seed=True)
    return results.converged.all(axis=1)


def prepared(n: pypsa.Network, pv_min_capacity: float, pv_max_load_ratio: float) -> pypsa.Network:
    m = n.copy()
    m.calculate_dependent_values()
    m.determine_network_topology()
    run_pf.prepare_for_ac_pf(m, pv_min_capacity=pv_min_capacity, pv_max_load_ratio=pv_max_load_ratio)
    return m


def probe_reactive_requirement(n: pypsa.Network) -> pd.DataFrame:
    """Returns, per out-of-band bus, its violation direction and the probe's
    required reactive power (MVAr, positive = supply) over the week."""
    base = n.copy()
    solve_ac(base)
    v = base.buses_t.v_mag_pu
    over = v.columns[v.max() > V_MAX]
    under = v.columns[v.min() < V_MIN]
    both = over.intersection(under)

    probe = n.copy()
    targets = pd.concat([pd.Series(V_MAX, index=over.difference(both)),
                         pd.Series(V_MIN, index=under.difference(both))])
    for bus, v_set in targets.items():
        probe.add("Generator", f"{bus} probe", bus=bus, carrier="probe", p_nom=0.0, control="PV")
        probe.buses.loc[bus, "v_mag_pu_set"] = v_set
    converged = solve_ac(probe)
    print(f"Probe run: {int(converged.sum())}/{len(converged)} snapshots converged, "
          f"{len(targets)} buses probed, {len(both)} skipped (out of band in both directions: "
          f"{', '.join(both) or 'none'})")

    q = probe.generators_t.q.loc[converged, [f"{b} probe" for b in targets.index]]
    q.columns = targets.index
    return pd.DataFrame({
        "type": np.where(targets == V_MAX, "reactor", "capacitor"),
        "min_Q": q.min(), "max_Q": q.max(),
    }, index=targets.index)


def worst_hour_sizing(probe: pd.DataFrame) -> pd.Series:
    """Worst-hour Q per device (MVAr, negative = absorbing); buses whose probe
    never needs the device's direction are dropped."""
    reactors = probe[(probe.type == "reactor") & (probe.min_Q < 0)].min_Q
    capacitors = probe[(probe.type == "capacitor") & (probe.max_Q > 0)].max_Q
    return pd.concat([reactors, capacitors])


def apply_shunt_impedance(n: pypsa.Network, sizing_q: pd.Series) -> None:
    v_nom = n.buses.loc[sizing_q.index, "v_nom"]
    b = sizing_q / v_nom**2  # Siemens; b_pu = b*v_nom**2 = Q(MVAr) at 1.0pu
    for bus, b_val in b.items():
        kind = "shunt-reactor" if b_val < 0 else "shunt-capacitor"
        n.add("ShuntImpedance", f"{bus} {kind}", bus=bus, g=0.0, b=b_val)


def evaluate(n: pypsa.Network) -> dict:
    converged = solve_ac(n)
    v = n.buses_t.v_mag_pu.loc[converged]
    out = (v < V_MIN) | (v > V_MAX)
    return {"converged": int(converged.sum()), "under": int((v.min() < V_MIN).sum()),
            "over": int((v.max() > V_MAX).sum()), "out_buses": int(out.any().sum()),
            "out_bus_hours": int(out.values.sum()),
            "v_min": float(v.min().min()), "v_max": float(v.max().max())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pv-min-capacity", type=float, default=0.0)
    parser.add_argument("--pv-max-load-ratio", type=float, default=1.0)
    parser.add_argument("--reactor-scale", type=float, default=1.0,
                        help="Fraction of each reactor bus's worst-hour Q to install.")
    parser.add_argument("--capacitor-scale", type=float, default=0.7,
                        help="Fraction of each capacitor bus's worst-hour Q to install.")
    parser.add_argument("--grid-best", action="store_true",
                        help="Use the scale pair with the fewest out-of-band buses from the sweep instead.")
    args = parser.parse_args()

    n = pypsa.Network(args.network)
    base = prepared(n, args.pv_min_capacity, args.pv_max_load_ratio)
    probe = probe_reactive_requirement(base)
    sizing_q = worst_hour_sizing(probe)
    reactor = sizing_q < 0
    print(f"Worst-hour sizing: {reactor.sum()} reactors ({sizing_q[reactor].sum():.0f} MVAr), "
          f"{(~reactor).sum()} capacitors ({sizing_q[~reactor].sum():.0f} MVAr)")

    rows = []
    for k_r in SCALE_GRID:
        for k_c in SCALE_GRID:
            m = base.copy()
            apply_shunt_impedance(m, sizing_q.where(~reactor, sizing_q * k_r).where(reactor, sizing_q * k_c))
            rows.append({"k_reactor": k_r, "k_capacitor": k_c, **evaluate(m)})
    grid = pd.DataFrame(rows)
    full = len(n.snapshots)
    best = grid[grid.converged == full].sort_values(["out_buses", "out_bus_hours"]).iloc[0]
    print(grid.sort_values(["converged", "out_buses", "out_bus_hours"],
                           ascending=[False, True, True]).head(10).to_string(index=False))
    print(f"Grid best: reactors x{best.k_reactor}, capacitors x{best.k_capacitor}")
    k_r, k_c = (best.k_reactor, best.k_capacitor) if args.grid_best else (args.reactor_scale, args.capacitor_scale)
    print(f"Kept: reactors x{k_r}, capacitors x{k_c}")

    final_q = sizing_q.where(~reactor, sizing_q * k_r).where(reactor, sizing_q * k_c)
    apply_shunt_impedance(n, final_q)
    print(final_q.rename("Q_MVAr at 1.0pu (negative = reactor)").round(1).to_string())
    n.export_to_netcdf(args.output)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
