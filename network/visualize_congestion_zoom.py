# -*- coding: utf-8 -*-
"""
visualize_congestion_zoom.py

Close-up map of whichever line(s) reach >=90% loading (LOPF result) in
the solved main-island network, plus their immediate 1-hop neighborhood
for context -- zoomed tight via exact_map_bounds rather than showing the
whole network. Shows lines (colored by max loading), buses (labeled),
and any local load/generation/storage at the buses involved.

Usage
-----
    python network/visualize_congestion_zoom.py
"""
import argparse
import os
import sys

import plotly.graph_objects as go
import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(NETWORK_DIR)
sys.path.insert(0, NETWORK_DIR)
from visualize_real_network_map import exact_map_bounds, geo_layout_from_bounds  # noqa: E402

DEFAULT_NETWORK = os.path.join(NETWORK_DIR, "networks_current", "elec_solved.nc")
DEFAULT_OUTPUT = os.path.join(NETWORK_DIR, "congestion_zoom_map.html")
LOADING_THRESHOLD = 0.9

CONGESTION_BUCKETS = [
    (0.0, 0.5, "#3fa34d", "< 50% loaded"),
    (0.5, 0.8, "#c9b32c", "50-80% loaded"),
    (0.8, 0.95, "#e08a2b", "80-95% loaded"),
    (0.95, 1.5, "#c1272d", ">= 95% loaded"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--threshold", type=float, default=LOADING_THRESHOLD)
    args = parser.parse_args()

    print(f"Loading: {args.network}")
    n = pypsa.Network(args.network)

    loading = n.lines_t.p0.abs().div(n.lines.s_nom, axis=1)
    max_loading = loading.max()
    hot_lines = n.lines.index[max_loading >= args.threshold]
    if len(hot_lines) == 0:
        print(f"No lines >= {args.threshold:.0%} loaded -- nothing to zoom into.")
        return
    print(f"{len(hot_lines)} line(s) >= {args.threshold:.0%}: {list(hot_lines)}")

    hot_buses = set(n.lines.loc[hot_lines, "bus0"]) | set(n.lines.loc[hot_lines, "bus1"])
    # 1-hop neighborhood for context
    context_lines = n.lines.index[n.lines.bus0.isin(hot_buses) | n.lines.bus1.isin(hot_buses)]
    context_buses = hot_buses | set(n.lines.loc[context_lines, "bus0"]) | set(n.lines.loc[context_lines, "bus1"])
    context_trafos = n.transformers.index[n.transformers.bus0.isin(context_buses) | n.transformers.bus1.isin(context_buses)]
    context_buses |= set(n.transformers.loc[context_trafos, "bus0"]) | set(n.transformers.loc[context_trafos, "bus1"])
    print(f"Context: {len(context_buses)} buses, {len(context_lines)} lines, {len(context_trafos)} transformers")

    fig = go.Figure()

    for lo, hi, color, label in CONGESTION_BUCKETS:
        grp = context_lines[(max_loading.reindex(context_lines) >= lo) & (max_loading.reindex(context_lines) < hi)]
        if len(grp) == 0:
            continue
        lats, lons, hover_lat, hover_lon, hover_text = [], [], [], [], []
        for l in grp:
            row = n.lines.loc[l]
            b0, b1 = n.buses.loc[row.bus0], n.buses.loc[row.bus1]
            lons.extend([b0.x, b1.x, None])
            lats.extend([b0.y, b1.y, None])
        width = 3.0 if hi <= 0.95 else 5.0
        fig.add_trace(go.Scattergeo(
            lat=lats, lon=lons, mode="lines",
            line=dict(width=width, color=color), opacity=0.9,
            name=f"{label} ({len(grp)} lines)",
            text=[f"{l}: {row.bus0}-{row.bus1}, {max_loading[l]:.1%}, {row.length:.1f} km, {row.v_nom:.0f} kV"
                  for l in grp for row in [n.lines.loc[l]]],
            hoverinfo="skip",
        ))

    # Transformers, drawn as dashed lines
    for t, row in n.transformers.loc[context_trafos].iterrows():
        b0, b1 = n.buses.loc[row.bus0], n.buses.loc[row.bus1]
        fig.add_trace(go.Scattergeo(
            lat=[b0.y, b1.y], lon=[b0.x, b1.x], mode="lines",
            line=dict(width=2.5, color="#6a3d9a"), opacity=0.8,
            name=f"Transformer {t}" if t == context_trafos[0] else None,
            legendgroup="transformers", showlegend=(t == context_trafos[0]),
            hoverinfo="skip",
        ))

    # Buses, labeled, sized by local gen+storage+load
    for b in context_buses:
        row = n.buses.loc[b]
        gen_mw = n.generators[(n.generators.bus == b) & (n.generators.carrier != "load_shedding")].p_nom.sum()
        su_mw = n.storage_units[n.storage_units.bus == b].p_nom.sum()
        load_ids = n.loads.index[n.loads.bus == b]
        load_mw = n.loads_t.p_set[load_ids].mean().sum() if len(load_ids) else 0.0
        highlight = b in hot_buses
        fig.add_trace(go.Scattergeo(
            lat=[row.y], lon=[row.x], mode="markers+text",
            marker=dict(size=16 if highlight else 9, color="#c1272d" if highlight else "#333333", opacity=0.9),
            text=[f"{b}"], textposition="top right", textfont=dict(size=11 if highlight else 9),
            hovertext=[f"Bus {b} ({row.v_nom:.0f} kV)<br>Local gen: {gen_mw:.0f} MW<br>"
                       f"Local storage: {su_mw:.0f} MW<br>Mean local load: {load_mw:.0f} MW"],
            hoverinfo="text",
            name="Congested bus" if highlight else "Neighboring bus",
            legendgroup="hot" if highlight else "neighbor",
            showlegend=(b == min(hot_buses)) if highlight else (b == min(context_buses - hot_buses, default=b)),
        ))

    all_lons = [n.buses.at[b, "x"] for b in context_buses]
    all_lats = [n.buses.at[b, "y"] for b in context_buses]
    import pandas as pd
    bounds = exact_map_bounds(pd.Series(all_lons), pd.Series(all_lats), margin_pct=0.15)

    fig.update_layout(
        geo=geo_layout_from_bounds(bounds),
        margin=dict(l=0, r=0, t=60, b=0), width=1200, height=1000,
        title=f"Congestion close-up: {', '.join(hot_lines)} (>= {args.threshold:.0%} loaded) and 1-hop neighborhood",
        legend=dict(bgcolor="rgba(255,255,255,0.9)", font=dict(size=10)),
    )

    fig.write_html(args.output)
    print(f"Map written to {args.output}")
    png_path = os.path.splitext(args.output)[0] + ".png"
    fig.write_image(png_path, scale=2)
    print(f"Static image written to {png_path}")


if __name__ == "__main__":
    main()
