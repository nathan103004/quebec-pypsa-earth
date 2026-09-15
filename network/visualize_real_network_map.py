# -*- coding: utf-8 -*-
"""
visualize_real_network_map.py

Interactive map of the real-generator Quebec network
(networks/elec_real_generators.nc, see attach_real_generators.py), showing:

- Substations (buses), sized by number of lines connected.
- Transmission lines, colored by voltage level, width scaled by voltage
  (num_parallel is not used for width: in this dataset it's a per-row
  attribute, almost always 1.0, not a real per-corridor circuit count --
  multi-circuit corridors are stored as multiple separate Line rows).
- Loads, as a separate marker layer sized by mean demand.
- Generators/storage units, as a separate marker layer per carrier,
  sized by capacity, labeled by plant name.

The network's own bus/line topology still spans all of Canada (only
generators/storage units were pared down to real Quebec plants -- see
attach_real_generators.py); this only DISPLAYS buses/lines in and around
Quebec, not the whole country.

Usage
-----
    python network/visualize_real_network_map.py
"""
import argparse
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pypsa
from shapely.geometry import Point

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
GADM_PATH = os.path.join(BASE_DIR, "data", "gadm", "gadm41_CAN", "gadm41_CAN.gpkg")

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_full.nc")
DEFAULT_OUTPUT = os.path.join(NETWORK_DIR, "quebec_real_network_map.html")

# "465-735kv" after add_churchill_falls_tie.py (the real 735 kV yard); falls
# back to the pre-fix "465" bus for networks predating that fix.
CHURCHILL_FALLS_BUS_CANDIDATES = ("465-735kv", "465")
CHURCHILL_BUFFER_DEG = 1.0  # generous, since it's outside the Quebec polygon entirely

CARRIER_STYLE = {
    "ror": dict(color="#17becf", symbol="circle", label="Run-of-river"),
    "hydro": dict(color="#1f77b4", symbol="circle", label="Reservoir hydro"),
    "onwind": dict(color="#2ca02c", symbol="triangle-up", label="Wind"),
    "solar": dict(color="#ff7f0e", symbol="star", label="Solar"),
    "OCGT": dict(color="#d62728", symbol="square", label="OCGT (gas)"),
}

VOLTAGE_COLORS = {
    63.0: "#c7e9b4", 66.0: "#c7e9b4", 69.0: "#c7e9b4", 72.0: "#c7e9b4",
    110.0: "#7fcdbb", 115.0: "#7fcdbb", 120.0: "#7fcdbb", 132.0: "#7fcdbb", 138.0: "#7fcdbb", 144.0: "#7fcdbb",
    161.0: "#41b6c4",
    220.0: "#1d91c0", 230.0: "#1d91c0", 240.0: "#1d91c0",
    287.0: "#225ea8", 315.0: "#225ea8", 345.0: "#225ea8", 360.0: "#225ea8",
    500.0: "#253494",
    735.0: "#800026", 765.0: "#800026",
}
DEFAULT_LINE_COLOR = "#999999"


def get_display_polygon():
    provinces = gpd.read_file(GADM_PATH, layer="ADM_ADM_1")
    qc = provinces[provinces.NAME_1.str.contains("bec", case=False)]
    geom = qc.geometry.union_all() if hasattr(qc.geometry, "union_all") else qc.geometry.unary_union
    geom = geom.simplify(0.005).buffer(0.1)
    return geom


def zoom_center_for_bounds(lons, lats, width_px=1600, height_px=1200, margin=1.25):
    """Mapbox zoom/center that tightly fits the given points (standard
    Web-Mercator fitBounds formula) -- much more accurate than a rough
    span-based guess, which was leaving the view zoomed out to show all
    of eastern North America instead of just Quebec."""
    min_lon, max_lon = float(lons.min()), float(lons.max())
    min_lat, max_lat = float(lats.min()), float(lats.max())
    center = {"lon": (min_lon + max_lon) / 2, "lat": (min_lat + max_lat) / 2}

    def lat_rad(lat):
        s = np.sin(np.radians(lat))
        return np.log((1 + s) / (1 - s)) / 2

    lat_fraction = (lat_rad(max_lat) - lat_rad(min_lat)) / np.pi
    lon_diff = max_lon - min_lon
    lon_fraction = ((lon_diff + 360) if lon_diff < 0 else lon_diff) / 360

    def zoom_level(px, fraction):
        return np.log2(px / 512 / fraction) if fraction > 0 else 21.0

    zoom = min(zoom_level(height_px, lat_fraction), zoom_level(width_px, lon_fraction), 21.0)
    zoom -= np.log2(margin)
    return float(np.clip(zoom, 2.0, 12.0)), center


def exact_map_bounds(lons, lats, margin_pct: float = 0.05) -> dict:
    """Exact fitBounds box (west/east/south/north) with a flat percentage
    margin added on each side -- passed straight to `layout.mapbox.bounds`,
    which makes Mapbox/Maplibre compute the precise zoom/center itself.
    Unlike zoom_center_for_bounds() (a manual zoom-level approximation
    clipped to max zoom 12, which stopped fitting tightly for small
    extents), this has no clipping and always fits exactly."""
    min_lon, max_lon = float(lons.min()), float(lons.max())
    min_lat, max_lat = float(lats.min()), float(lats.max())
    lon_pad = (max_lon - min_lon) * margin_pct or 0.01
    lat_pad = (max_lat - min_lat) * margin_pct or 0.01
    return {
        "west": min_lon - lon_pad, "east": max_lon + lon_pad,
        "south": min_lat - lat_pad, "north": max_lat + lat_pad,
    }


def marker_size(values: pd.Series, min_size: float = 4.0, max_size: float = 22.0) -> list:
    """sqrt-scaled marker size, clipped to [min_size, max_size] so the
    single largest plant/load doesn't dominate/obscure the whole map."""
    v = values.clip(lower=0)
    scaled = np.sqrt(v)
    if scaled.max() > 0:
        scaled = scaled / scaled.max()
    return (min_size + scaled * (max_size - min_size)).tolist()


def jitter_colocated(df: pd.DataFrame, x_col="x", y_col="y", step=0.015) -> pd.DataFrame:
    """Small deterministic offset for markers sharing an exact bus, so
    co-located generators (e.g. Robert-Bourassa + La Grande-2-A, both at
    bus 339) are all visible instead of stacking exactly on top of
    each other."""
    df = df.copy()
    for bus, group in df.groupby("bus"):
        n = len(group)
        if n <= 1:
            continue
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
        for (idx, _), ang in zip(group.iterrows(), angles):
            df.loc[idx, x_col] += step * np.cos(ang)
            df.loc[idx, y_col] += step * np.sin(ang)
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--min-load-mw", type=float, default=20.0, help="Only show load markers above this mean MW, to avoid clutter.")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    print("Building display region (Quebec + Churchill Falls area)...")
    qc_poly = get_display_polygon()
    churchill_bus_id = next((b for b in CHURCHILL_FALLS_BUS_CANDIDATES if b in n.buses.index), None)
    if churchill_bus_id is None:
        print("  [warn] no Churchill Falls bus found in this network -- region limited to the Quebec polygon")
        display_poly = qc_poly
    else:
        cf_bus = n.buses.loc[churchill_bus_id]
        cf_point = Point(cf_bus.x, cf_bus.y).buffer(CHURCHILL_BUFFER_DEG)
        display_poly = qc_poly.union(cf_point)

    in_region = n.buses.apply(lambda r: Point(r.x, r.y).within(display_poly), axis=1)
    region_buses = set(n.buses.index[in_region])
    print(f"  {len(region_buses)} / {len(n.buses)} buses in display region")

    lines = n.lines[n.lines.bus0.isin(region_buses) | n.lines.bus1.isin(region_buses)]
    print(f"  {len(lines)} lines touch the display region")

    fig = go.Figure()

    # --- Lines, grouped by voltage so each gets one legend entry ---
    # Width scales with voltage only. `num_parallel` is deliberately not used
    # here: in this dataset it's a per-row attribute (almost always 1.0), not
    # a per-corridor circuit count -- real multi-circuit corridors are stored
    # as multiple separate Line rows instead of one row with num_parallel>1,
    # so averaging that attribute is misleading (see the corridor-grouping
    # analysis done in the conversation this came from).
    for v_nom, grp in lines.groupby("v_nom"):
        color = VOLTAGE_COLORS.get(v_nom, DEFAULT_LINE_COLOR)
        width = float(np.clip(1.0 + 1.2 * np.log1p(v_nom / 100), 1.0, 8.0))
        lats, lons, hover = [], [], []
        for _, line in grp.iterrows():
            if line.bus0 not in n.buses.index or line.bus1 not in n.buses.index:
                continue
            b0, b1 = n.buses.loc[line.bus0], n.buses.loc[line.bus1]
            lons.extend([b0.x, b1.x, None])
            lats.extend([b0.y, b1.y, None])
        fig.add_trace(
            go.Scattermapbox(
                lat=lats, lon=lons, mode="lines",
                line=dict(width=width, color=color),
                opacity=0.85,
                name=f"{v_nom:.0f} kV ({len(grp)} lines)",
                hoverinfo="skip",
            )
        )

    # --- Substations (buses in region) ---
    region_bus_df = n.buses.loc[list(region_buses)]
    n_lines_per_bus = pd.concat([lines.bus0, lines.bus1]).value_counts()
    sizes = region_bus_df.index.map(lambda b: 3 + min(n_lines_per_bus.get(b, 0), 10)).to_numpy()
    fig.add_trace(
        go.Scattermapbox(
            lat=region_bus_df.y, lon=region_bus_df.x,
            mode="markers",
            marker=dict(size=sizes, color="#333333", opacity=0.6),
            name="Substations",
            text=[f"Bus {b}<br>{n.buses.at[b, 'v_nom']:.0f} kV<br>{n_lines_per_bus.get(b, 0)} lines" for b in region_bus_df.index],
            hoverinfo="text",
        )
    )

    # --- Loads ---
    mean_load = n.loads_t.p_set.mean()
    region_loads = n.loads[n.loads.bus.isin(region_buses)].copy()
    region_loads["mean_mw"] = region_loads.index.map(lambda l: mean_load.get(l, 0.0))
    region_loads = region_loads[region_loads["mean_mw"] >= args.min_load_mw]
    region_loads["x"] = region_loads.bus.map(n.buses.x)
    region_loads["y"] = region_loads.bus.map(n.buses.y)
    fig.add_trace(
        go.Scattermapbox(
            lat=region_loads.y, lon=region_loads.x,
            mode="markers",
            marker=dict(size=marker_size(region_loads["mean_mw"], 4, 18), color="#9467bd", opacity=0.55),
            name=f"Loads (>{args.min_load_mw:.0f} MW mean)",
            text=[f"Bus {b}<br>Mean load: {m:.0f} MW" for b, m in zip(region_loads.bus, region_loads["mean_mw"])],
            hoverinfo="text",
        )
    )

    # --- Generators + storage units, per carrier, with name labels ---
    gens = n.generators[n.generators.bus.isin(region_buses)][["bus", "carrier", "p_nom"]].copy()
    gens["kind"] = "Generator"
    sus = n.storage_units[n.storage_units.bus.isin(region_buses)][["bus", "carrier", "p_nom"]].copy()
    sus["kind"] = "StorageUnit"
    all_gen = pd.concat([gens, sus])
    all_gen["x"] = all_gen.bus.map(n.buses.x)
    all_gen["y"] = all_gen.bus.map(n.buses.y)
    all_gen["display_name"] = all_gen.index.str.split("-", n=1).str[-1].str.replace("-", " ")
    all_gen = jitter_colocated(all_gen)
    # Sized against the FULL generator fleet's capacity range, not each
    # carrier's own min/max -- otherwise a 12 MW solar plant renders as
    # large as a 5,616 MW hydro plant (each group was being normalized to
    # its own max).
    all_gen["marker_size"] = marker_size(all_gen["p_nom"], 5, 24)

    for carrier, grp in all_gen.groupby("carrier"):
        style = CARRIER_STYLE.get(carrier, dict(color="black", symbol="circle", label=carrier))
        fig.add_trace(
            go.Scattermapbox(
                lat=grp.y, lon=grp.x,
                mode="markers+text",
                marker=dict(size=grp["marker_size"].tolist(), color=style["color"], opacity=0.9),
                text=grp["display_name"],
                textposition="top right",
                textfont=dict(size=9, color=style["color"]),
                name=f"{style['label']} ({len(grp)}, {grp.p_nom.sum():.0f} MW)",
                hovertext=[
                    f"{row.display_name}<br>{row.carrier} ({row.kind})<br>{row.p_nom:.0f} MW<br>Bus {row.bus}"
                    for row in grp.itertuples()
                ],
                hoverinfo="text",
            )
        )

    # Tight bounds-fit over everything actually plotted (substations cover
    # the full extent already, from Quebec's US border down south to
    # Churchill Falls up north/east) -- this crops out the rest of eastern
    # North America instead of showing Toronto/New York for context.
    zoom, center = zoom_center_for_bounds(region_bus_df.x, region_bus_df.y, width_px=1600, height_px=1200)

    fig.update_layout(
        mapbox=dict(style="open-street-map", zoom=zoom, center=center),
        margin=dict(l=0, r=0, t=50, b=0),
        title="Quebec real-generator network -- voltage levels, substations, loads & generators",
        legend=dict(bgcolor="rgba(255,255,255,0.85)", font=dict(size=10)),
        width=1600,
        height=1200,
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    fig.write_html(args.output)
    print(f"\nMap written to {args.output}")


if __name__ == "__main__":
    main()
