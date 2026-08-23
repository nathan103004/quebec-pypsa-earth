# -*- coding: utf-8 -*-
"""
visualize_solved_network_map.py

Map of the solved main-island (simplified/reduced) network
(networks/elec_main_island_solved.nc, see run_lopf_main_island.py):

- Lines, colored by congestion level (max loading over all snapshots as a
  fraction of s_nom) -- green/amber/orange/red buckets. Both the LOPF
  result and a fresh DC power flow cross-check (n.lpf()) are computed and
  embedded together; a dropdown switches which one the map shows. (DC PF
  uses p_set, not p, for generators/storage -- see the conversation this
  script came out of: p_set was 0.0 for every non-slack unit, which
  silently forced the whole system's real power through the slack bus
  alone until this was found and fixed.)
- Loads, sized by mean demand (MW).
- Load shedding, as a red overlay on top of shedding buses, sized by mean
  shed (MW) -- so a bus's normal load marker and its shedding both show.
- Hydro dispatch (ror + hydro storage), sized by MEAN dispatch (not
  nameplate capacity) so marker size reflects real utilization -- the
  same generator can look tiny here even with a large p_nom if it's
  mostly idle. (LOPF dispatch only -- DC PF doesn't re-dispatch, it just
  re-derives flows from the same fixed injections.)

HTML output only.

Usage
-----
    python network/visualize_solved_network_map.py
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
    marker_size,
    jitter_colocated,
)

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_main_island_solved.nc")
DEFAULT_OUTPUT = os.path.join(NETWORK_DIR, "quebec_reduced_network_map.html")

CONGESTION_BUCKETS = [
    (0.0, 0.5, "#3fa34d", "< 50% loaded"),
    (0.5, 0.8, "#c9b32c", "50-80% loaded"),
    (0.8, 0.95, "#e08a2b", "80-95% loaded"),
    (0.95, 1.0001, "#c1272d", ">= 95% loaded"),
]

HYDRO_STYLE = {
    "ror": dict(color="#17becf", symbol="circle", label="Run-of-river dispatch"),
    "hydro": dict(color="#1f4e8c", symbol="circle", label="Reservoir hydro dispatch"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--min-load-mw", type=float, default=5.0, help="Only show load markers above this mean MW, to avoid clutter.")
    args = parser.parse_args()

    print(f"Loading solved network: {args.network}")
    n = pypsa.Network(args.network)
    print(f"  {len(n.buses)} buses, {len(n.lines)} lines")

    fig = go.Figure()

    # --- Lines, grouped by congestion bucket -- LOPF result and a fresh DC
    # PF cross-check, both embedded, toggled via a dropdown below ---
    print("Running DC PF cross-check (n.lpf())...")
    n_dc = n.copy()
    n_dc.generators_t.p_set = n_dc.generators_t.p.copy()
    n_dc.storage_units_t.p_set = n_dc.storage_units_t.p.copy()
    n_dc.lpf(n_dc.snapshots)

    lopf_loading = n.lines_t.p0.abs().div(n.lines.s_nom, axis=1).max()
    dcpf_loading = n_dc.lines_t.p0.abs().div(n_dc.lines.s_nom, axis=1).max()
    print(f"  LOPF max loading: {lopf_loading.max():.1%} | DC PF max loading: {dcpf_loading.max():.1%}")

    n_congestion_traces = 0
    for source_label, max_loading, visible in (("LOPF", lopf_loading, True), ("DC PF", dcpf_loading, False)):
        for lo, hi, color, label in CONGESTION_BUCKETS:
            grp_lines = n.lines.index[(max_loading >= lo) & (max_loading < hi)]
            lats, lons = [], []
            for l in grp_lines:
                row = n.lines.loc[l]
                if row.bus0 not in n.buses.index or row.bus1 not in n.buses.index:
                    continue
                b0, b1 = n.buses.loc[row.bus0], n.buses.loc[row.bus1]
                lons.extend([b0.x, b1.x, None])
                lats.extend([b0.y, b1.y, None])
            width = 2.0 if hi <= 0.95 else 3.2
            fig.add_trace(
                go.Scattermapbox(
                    lat=lats, lon=lons, mode="lines",
                    line=dict(width=width, color=color),
                    opacity=0.85,
                    name=f"{label} ({len(grp_lines)} lines)",
                    hoverinfo="skip",
                    visible=visible,
                    legendgroup=source_label,
                )
            )
            n_congestion_traces += 1

    # --- Substations, for context ---
    n_lines_per_bus = pd.concat([n.lines.bus0, n.lines.bus1]).value_counts()
    sizes = n.buses.index.map(lambda b: 3 + min(n_lines_per_bus.get(b, 0), 10)).to_numpy()
    fig.add_trace(
        go.Scattermapbox(
            lat=n.buses.y, lon=n.buses.x,
            mode="markers",
            marker=dict(size=sizes, color="#333333", opacity=0.35),
            name="Substations",
            text=[f"Bus {b}<br>{n.buses.at[b, 'v_nom']:.0f} kV<br>{n_lines_per_bus.get(b, 0)} lines" for b in n.buses.index],
            hoverinfo="text",
        )
    )

    # --- Loads, sized by mean demand ---
    mean_load_by_bus = n.loads_t.p_set.T.groupby(n.loads.bus).sum().mean(axis=1)
    mean_load_by_bus = mean_load_by_bus[mean_load_by_bus >= args.min_load_mw]
    load_x = mean_load_by_bus.index.map(n.buses.x)
    load_y = mean_load_by_bus.index.map(n.buses.y)
    fig.add_trace(
        go.Scattermapbox(
            lat=load_y, lon=load_x,
            mode="markers",
            marker=dict(size=marker_size(mean_load_by_bus, 4, 20), color="#9467bd", opacity=0.55),
            name=f"Load ({len(mean_load_by_bus)} buses > {args.min_load_mw:.0f} MW mean)",
            text=[f"Bus {b}<br>Mean load: {m:.0f} MW" for b, m in mean_load_by_bus.items()],
            hoverinfo="text",
        )
    )

    # --- Load shedding, as a red overlay sized by mean shed MW ---
    shed_cols = [c for c in n.generators_t.p.columns if n.generators.at[c, "carrier"] == "load_shedding"]
    mean_shed = n.generators_t.p[shed_cols].mean()
    # real (unweighted) total shed over the actual snapshot window -- snapshot_weightings
    # annualizes to a full 8760h year, which would inflate this ~26x if applied here
    real_total_shed_mwh = n.generators_t.p[shed_cols].sum()
    shed_by_bus = mean_shed[mean_shed > 0.01].rename(lambda c: n.generators.at[c, "bus"])
    shed_total_by_bus = real_total_shed_mwh[mean_shed > 0.01].rename(lambda c: n.generators.at[c, "bus"])
    if len(shed_by_bus):
        shed_x = shed_by_bus.index.map(n.buses.x)
        shed_y = shed_by_bus.index.map(n.buses.y)
        fig.add_trace(
            go.Scattermapbox(
                lat=shed_y, lon=shed_x,
                mode="markers",
                marker=dict(
                    size=marker_size(shed_by_bus, 8, 34),
                    color="#e60000",
                    opacity=0.85,
                    symbol="circle",
                ),
                name=f"Load shedding ({len(shed_by_bus)} buses)",
                text=[
                    f"Bus {b}<br>Mean shed: {m:.0f} MW<br>Total over window: {shed_total_by_bus.get(b, 0):.0f} MWh"
                    for b, m in shed_by_bus.items()
                ],
                hoverinfo="text",
            )
        )
        print(f"Shedding at {len(shed_by_bus)} buses: {dict(shed_by_bus.round(0))}")
    else:
        print("No shedding in this solved network.")

    # --- Hydro dispatch (ror + storage), sized by MEAN dispatch, not p_nom ---
    ror = n.generators[n.generators.carrier == "ror"][["bus", "p_nom"]].copy()
    ror["mean_dispatch"] = n.generators_t.p.reindex(columns=ror.index).mean()
    ror["kind"] = "Generator"

    hydro_su = n.storage_units[n.storage_units.carrier == "hydro"][["bus", "p_nom"]].copy()
    hydro_su["mean_dispatch"] = n.storage_units_t.p.reindex(columns=hydro_su.index).mean().clip(lower=0)
    hydro_su["kind"] = "StorageUnit"

    for carrier, df in (("ror", ror), ("hydro", hydro_su)):
        df["x"] = df.bus.map(n.buses.x)
        df["y"] = df.bus.map(n.buses.y)
        df["display_name"] = df.index.str.split("-", n=1).str[-1].str.replace("-", " ")
        df = jitter_colocated(df)
        style = HYDRO_STYLE[carrier]
        fig.add_trace(
            go.Scattermapbox(
                lat=df.y, lon=df.x,
                mode="markers+text",
                marker=dict(size=marker_size(df["mean_dispatch"], 4, 26), color=style["color"], opacity=0.9),
                text=df["display_name"],
                textposition="top right",
                textfont=dict(size=9, color=style["color"]),
                name=f"{style['label']} ({len(df)}, {df['mean_dispatch'].sum():.0f} MW mean of {df['p_nom'].sum():.0f} MW capacity)",
                hovertext=[
                    f"{row.display_name}<br>{carrier} ({row.kind})<br>"
                    f"Mean dispatch: {row.mean_dispatch:.0f} MW / {row.p_nom:.0f} MW capacity "
                    f"({100*row.mean_dispatch/row.p_nom if row.p_nom else 0:.0f}% utilization)<br>Bus {row.bus}"
                    for row in df.itertuples()
                ],
                hoverinfo="text",
            )
        )

    bounds = exact_map_bounds(n.buses.x, n.buses.y, margin_pct=0.05)

    total_demand_real = n.loads_t.p_set.sum().sum()
    total_shed_real = n.generators_t.p[shed_cols].sum().sum()
    shed_pct = 100 * total_shed_real / total_demand_real if total_demand_real else 0.0

    # Dropdown to switch congestion source: first n_congestion_traces traces
    # are LOPF then DC PF buckets (fixed order, always all buckets added even
    # if empty, so indices are predictable); everything after them (buses,
    # loads, shedding, hydro dispatch) stays visible regardless of the choice.
    n_bucket = len(CONGESTION_BUCKETS)
    n_other = len(fig.data) - n_congestion_traces
    lopf_vis = [True] * n_bucket + [False] * n_bucket + [True] * n_other
    dcpf_vis = [False] * n_bucket + [True] * n_bucket + [True] * n_other

    fig.update_layout(
        mapbox=dict(style="open-street-map", bounds=bounds),
        margin=dict(l=0, r=0, t=90, b=0),
        title=(
            "Quebec main-island network -- congestion, load, shedding &amp; hydro dispatch"
            f"<br><sub>Shed: {shed_pct:.2f}% of demand over the window · "
            "line color = max loading over all snapshots, as a fraction of s_nom</sub>"
        ),
        updatemenus=[
            dict(
                type="buttons", direction="right", active=0,
                x=0.02, xanchor="left", y=1.06, yanchor="top",
                bgcolor="rgba(255,255,255,0.9)",
                buttons=[
                    dict(label="LOPF congestion", method="update", args=[{"visible": lopf_vis}]),
                    dict(label="DC PF congestion", method="update", args=[{"visible": dcpf_vis}]),
                ],
            )
        ],
        legend=dict(bgcolor="rgba(255,255,255,0.85)", font=dict(size=10)),
        width=1600,
        height=1200,
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    fig.write_html(args.output)
    print(f"\nMap written to {args.output}")


if __name__ == "__main__":
    main()
