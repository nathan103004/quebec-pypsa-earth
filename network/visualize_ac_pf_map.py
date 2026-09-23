# -*- coding: utf-8 -*-
"""
visualize_ac_pf_map.py

Map of the AC-PF-solved 735kV backbone network (networks/elec_735kv_pf.nc,
see run_pf.py --method pf), showing what only a full nonlinear solve can
show -- LOPF/DC PF never touch voltage magnitude or reactive power at all:

- Buses, colored by worst-case voltage magnitude deviation from 1.0pu over
  all snapshots (a diverging scale -- under-voltage and over-voltage are
  both "bad", 1.0pu is "good", so this is a polarity encoding, not a plain
  magnitude one). Sized by local load. Slack/PV buses labeled directly
  (voltage there is a held setpoint, not a free result -- worth knowing at
  a glance which buses those are).
- Lines, colored by max AC PF loading over all snapshots (same congestion
  bucket scheme used elsewhere in this project, for a consistent read).
- Shunt capacitors, as a small overlay sized by their real MVAr at V=1pu.
- Hover text carries the full picture per bus: mean/worst v_mag_pu, mean
  v_ang, bus type, local load, local shunt MVAr.

HTML output only.

Usage
-----
    python network/visualize_ac_pf_map.py
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(NETWORK_DIR)
sys.path.insert(0, NETWORK_DIR)
from visualize_real_network_map import (  # noqa: E402
    exact_map_bounds,
    geo_layout_from_bounds,
    marker_size,
)

DEFAULT_NETWORK = os.path.join(NETWORK_DIR, "networks_current", "elec_735kv_scaled62_pf.nc")
DEFAULT_OUTPUT = os.path.join(NETWORK_DIR, "quebec_735kv_ac_pf_map.html")

CONGESTION_BUCKETS = [
    (0.0, 0.5, "#3fa34d", "< 50% loaded"),
    (0.5, 0.8, "#c9b32c", "50-80% loaded"),
    (0.8, 0.95, "#e08a2b", "80-95% loaded"),
    (0.95, 1.0001, "#c1272d", ">= 95% loaded"),
]

V_BAND_MIN, V_BAND_MAX = 0.95, 1.05


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--min-load-mw", type=float, default=2.0)
    args = parser.parse_args()

    print(f"Loading AC-PF-solved network: {args.network}")
    n = pypsa.Network(args.network)
    print(f"  {len(n.buses)} buses, {len(n.lines)} lines, {len(n.snapshots)} snapshots")

    if n.buses_t.v_mag_pu.empty:
        raise SystemExit("This network has no AC PF results (buses_t.v_mag_pu is empty) -- "
                          "run network/run_pf.py --method pf on it first.")

    fig = go.Figure()

    # --- Lines, colored by max AC PF loading over all snapshots ---
    loading = n.lines_t.p0.abs().div(n.lines.s_nom, axis=1).max()
    print(f"  AC PF max line loading: {loading.max():.1%}, lines >=90%: {(loading >= 0.9).sum()} / {len(n.lines)}")
    for lo, hi, color, label in CONGESTION_BUCKETS:
        grp_lines = n.lines.index[(loading >= lo) & (loading < hi)]
        lats, lons = [], []
        for l in grp_lines:
            row = n.lines.loc[l]
            b0, b1 = n.buses.loc[row.bus0], n.buses.loc[row.bus1]
            lons.extend([b0.x, b1.x, None])
            lats.extend([b0.y, b1.y, None])
        fig.add_trace(
            go.Scattergeo(
                lat=lats, lon=lons, mode="lines",
                line=dict(width=2.0 if hi <= 0.95 else 3.2, color=color),
                opacity=0.85,
                name=f"{label} ({len(grp_lines)} lines)",
                hoverinfo="skip",
            )
        )

    # --- Buses: voltage magnitude deviation from 1.0pu (diverging), worst-case over the week ---
    vmag = n.buses_t.v_mag_pu
    vang_deg = np.degrees(n.buses_t.v_ang)
    dev = vmag - 1.0
    worst_idx = dev.abs().idxmax()  # snapshot index of worst deviation, per bus
    worst_dev = pd.Series({b: dev.loc[worst_idx[b], b] for b in n.buses.index})
    mean_vmag = vmag.mean()
    mean_vang = vang_deg.mean()

    # bus type: Slack > PV > PQ, from generators.control (only source PyPSA itself reads)
    control_by_bus = {}
    for g, row in n.generators.iterrows():
        cur = control_by_bus.get(row.bus, "PQ")
        rank = {"Slack": 2, "PV": 1, "PQ": 0}
        if rank[row.control] > rank.get(cur, 0):
            control_by_bus[row.bus] = row.control
    bus_type = pd.Series({b: control_by_bus.get(b, "PQ") for b in n.buses.index})

    mean_load_by_bus = n.loads_t.p_set.T.groupby(n.loads.bus).sum().mean(axis=1).reindex(n.buses.index, fill_value=0.0)

    max_abs_dev = max(worst_dev.abs().max(), 0.01)
    fig.add_trace(
        go.Scattergeo(
            lat=n.buses.y, lon=n.buses.x,
            mode="markers",
            marker=dict(
                size=marker_size(mean_load_by_bus, 6, 26),
                color=worst_dev,
                colorscale="RdBu_r",
                cmin=-max_abs_dev, cmax=max_abs_dev,
                colorbar=dict(
                    title=dict(text="Worst v_mag<br>deviation (pu)", side="top"),
                    x=1.02, len=0.5, y=0.3,
                ),
                opacity=0.9,
            ),
            name="Buses (color = worst voltage deviation)",
            text=[
                f"Bus {b} ({bus_type[b]})<br>"
                f"Mean v_mag: {mean_vmag[b]:.3f} pu (worst: {vmag.loc[worst_idx[b], b]:.3f} pu)<br>"
                f"Mean v_ang: {mean_vang[b]:.1f} deg<br>"
                f"Mean load: {mean_load_by_bus[b]:.0f} MW"
                for b in n.buses.index
            ],
            hoverinfo="text",
        )
    )

    # --- Slack/PV buses: labeled directly -- voltage there is a held setpoint, not a free result ---
    labeled = bus_type[bus_type != "PQ"]
    fig.add_trace(
        go.Scattergeo(
            lat=labeled.index.map(n.buses.y), lon=labeled.index.map(n.buses.x),
            mode="text",
            text=[("SLACK" if t == "Slack" else "PV") for t in labeled],
            textfont=dict(size=9, color="#111"),
            textposition="top center",
            name=f"Slack/PV buses ({len(labeled)})",
            hoverinfo="skip",
        )
    )

    # --- Shunt capacitors, sized by real MVAr at V=1.0pu ---
    if len(n.shunt_impedances):
        b_mvar = n.shunt_impedances.b * n.shunt_impedances.bus.map(n.buses.v_nom) ** 2
        b_mvar = b_mvar[b_mvar > 0.01]
        if len(b_mvar):
            sbus = n.shunt_impedances.loc[b_mvar.index, "bus"]
            fig.add_trace(
                go.Scattergeo(
                    lat=sbus.map(n.buses.y), lon=sbus.map(n.buses.x),
                    mode="markers",
                    marker=dict(size=marker_size(b_mvar, 4, 16), color="#17becf", symbol="circle", opacity=0.7),
                    name=f"Shunt capacitors ({len(b_mvar)}, {b_mvar.sum():.0f} MVAr total)",
                    text=[f"Bus {b}<br>Shunt: {m:.0f} MVAr at V=1.0pu" for b, m in zip(sbus, b_mvar)],
                    hoverinfo="text",
                )
            )

    bounds = exact_map_bounds(n.buses.x, n.buses.y, margin_pct=0.05)
    n_out_of_band = ((vmag < V_BAND_MIN) | (vmag > V_BAND_MAX)).any().sum()
    converged_frac = 1.0  # this file only exists if n.pf() was run; convergence is asserted upstream

    fig.update_layout(
        geo=geo_layout_from_bounds(bounds),
        margin=dict(l=0, r=0, t=90, b=0),
        title=(
            "Quebec 735kV backbone -- AC power flow results (Newton-Raphson)"
            f"<br><sub>{n_out_of_band}/{len(n.buses)} buses ever outside [{V_BAND_MIN},{V_BAND_MAX}] pu · "
            "bus color = worst voltage deviation from 1.0pu · line color = max AC PF loading</sub>"
        ),
        legend=dict(bgcolor="rgba(255,255,255,0.85)", font=dict(size=10)),
        width=1600,
        height=1200,
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    fig.write_html(args.output)
    print(f"\nMap written to {args.output}")
    png_path = os.path.splitext(args.output)[0] + ".png"
    fig.write_image(png_path, scale=2)
    print(f"Static image written to {png_path}")


if __name__ == "__main__":
    main()
