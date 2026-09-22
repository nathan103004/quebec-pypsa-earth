# -*- coding: utf-8 -*-
"""
reduce_voltage_network.py

Voltage-based network reduction: keep only the >=315 kV backbone
(buses/lines/transformers) within the Quebec + Churchill Falls region,
since that's the only voltage range with real, matched line impedance
data. Every load and every generator/storage unit is preserved -- none
are dropped, only reassigned onto the nearest surviving >=315 kV bus if
their own bus gets removed.

Steps
-----
1. Scope to the Quebec + Churchill Falls display region (same polygon
   as visualize_real_network_map.py).
2. Split region buses into "backbone" (v_nom >= 315 kV) and "local"
   (< 315 kV).
3. For each local bus, find its nearest backbone bus (straight-line
   distance).
4. Reassign every load, generator, and storage unit sitting on a local
   bus onto that nearest backbone bus. Goal is to make sure nothing is dropped or deleted
5. Drop local buses, and every line/transformer/link that isn't
   entirely between two surviving backbone buses.

Usage
-----
    python network/reduce_voltage_network.py --network networks/elec_full.nc
"""
import argparse
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
from shapely.geometry import Point

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
GADM_PATH = os.path.join(BASE_DIR, "data", "gadm", "gadm41_CAN", "gadm41_CAN.gpkg")

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_full.nc")

CHURCHILL_735_LAT = 53.5289404
CHURCHILL_735_LON = -63.9768688
CHURCHILL_BUFFER_DEG = 1.0

MIN_BACKBONE_KV = 315.0


def get_display_polygon():
    provinces = gpd.read_file(GADM_PATH, layer="ADM_ADM_1")
    qc = provinces[provinces.NAME_1.str.contains("bec", case=False)]
    geom = qc.geometry.union_all() if hasattr(qc.geometry, "union_all") else qc.geometry.unary_union
    geom = geom.simplify(0.005).buffer(0.1)
    cf_point = Point(CHURCHILL_735_LON, CHURCHILL_735_LAT).buffer(CHURCHILL_BUFFER_DEG)
    return geom.union(cf_point)


def nearest_bus(target_x, target_y, candidates: pd.DataFrame) -> str:
    d2 = (candidates["x"] - target_x) ** 2 + (candidates["y"] - target_y) ** 2
    return d2.idxmin()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    print("Building Quebec + Churchill Falls display region...")
    region_poly = get_display_polygon()
    in_region = n.buses.apply(lambda r: Point(r.x, r.y).within(region_poly), axis=1)
    region_buses = n.buses.loc[in_region]
    print(f"  {len(region_buses)} / {len(n.buses)} buses in region")

    backbone = region_buses[region_buses.v_nom >= MIN_BACKBONE_KV]
    local = region_buses[region_buses.v_nom < MIN_BACKBONE_KV]
    print(f"  {len(backbone)} backbone buses (>= {MIN_BACKBONE_KV:.0f} kV), {len(local)} local buses to fold in")

    if backbone.empty:
        raise ValueError("No backbone (>=315 kV) buses found in the region -- nothing to reduce onto.")

    # Nearest backbone bus for every local bus.
    bus_map = {b: b for b in backbone.index}
    for b, row in local.iterrows():
        bus_map[b] = nearest_bus(row.x, row.y, backbone)

    # --- Reassign loads (all of them, region-wide) ---
    region_load_ids = n.loads.index[n.loads.bus.isin(region_buses.index)]
    n_load_moved = 0
    for lid in region_load_ids:
        old_bus = n.loads.at[lid, "bus"]
        new_bus = bus_map.get(old_bus, old_bus)
        if new_bus != old_bus:
            n.loads.at[lid, "bus"] = new_bus
            n_load_moved += 1
    print(f"Reassigned {n_load_moved} / {len(region_load_ids)} region loads onto their nearest backbone bus")

    # --- Reassign generators (never dropped) ---
    region_gen_ids = n.generators.index[n.generators.bus.isin(region_buses.index)]
    n_gen_moved = 0
    for gid in region_gen_ids:
        old_bus = n.generators.at[gid, "bus"]
        new_bus = bus_map.get(old_bus, old_bus)
        if new_bus != old_bus:
            n.generators.at[gid, "bus"] = new_bus
            n_gen_moved += 1
    print(f"Reassigned {n_gen_moved} / {len(region_gen_ids)} region generators onto their nearest backbone bus (none dropped)")

    # --- Reassign storage units (never dropped) ---
    region_su_ids = n.storage_units.index[n.storage_units.bus.isin(region_buses.index)]
    n_su_moved = 0
    for sid in region_su_ids:
        old_bus = n.storage_units.at[sid, "bus"]
        new_bus = bus_map.get(old_bus, old_bus)
        if new_bus != old_bus:
            n.storage_units.at[sid, "bus"] = new_bus
            n_su_moved += 1
    print(f"Reassigned {n_su_moved} / {len(region_su_ids)} region storage units onto their nearest backbone bus (none dropped)")

    # --- Drop local buses (now unused -- everything on them was moved) ---
    n.mremove("Bus", local.index)

    # --- Drop everything outside the Quebec + Churchill Falls region
    #     entirely: this is meant to be a REDUCED network, and the rest of
    #     Canada is untouched leftover from the original all-Canada OSM
    #     build.
    # ---------------------------------------------------------------
    outside_buses = n.buses.index.difference(region_buses.index)
    outside_gens = n.generators.index[n.generators.bus.isin(outside_buses)]
    if len(outside_gens):
        raise AssertionError(
            f"{len(outside_gens)} generator(s) found outside the region -- "
            "expected none; refusing to silently drop real generators."
        )
    outside_su = n.storage_units.index[n.storage_units.bus.isin(outside_buses)]
    if len(outside_su):
        raise AssertionError(
            f"{len(outside_su)} storage unit(s) found outside the region -- "
            "expected none; refusing to silently drop real generators."
        )
    n.mremove("Load", n.loads.index[n.loads.bus.isin(outside_buses)])
    n.mremove("Line", n.lines.index[n.lines.bus0.isin(outside_buses) | n.lines.bus1.isin(outside_buses)])
    n.mremove("Transformer", n.transformers.index[n.transformers.bus0.isin(outside_buses) | n.transformers.bus1.isin(outside_buses)])
    n.mremove("Link", n.links.index[n.links.bus0.isin(outside_buses) | n.links.bus1.isin(outside_buses)])
    n.mremove("Bus", outside_buses)
    print(f"Dropped {len(outside_buses)} buses (and everything on them) outside the Quebec + Churchill Falls region")

    # --- Keep only lines/transformers/links fully within the surviving
    #     backbone bus set ---
    surviving_buses = set(n.buses.index)
    lines_to_drop = n.lines.index[~(n.lines.bus0.isin(surviving_buses) & n.lines.bus1.isin(surviving_buses))]
    n.mremove("Line", lines_to_drop)
    transformers_to_drop = n.transformers.index[
        ~(n.transformers.bus0.isin(surviving_buses) & n.transformers.bus1.isin(surviving_buses))
    ]
    n.mremove("Transformer", transformers_to_drop)
    links_to_drop = n.links.index[~(n.links.bus0.isin(surviving_buses) & n.links.bus1.isin(surviving_buses))]
    n.mremove("Link", links_to_drop)

    print(f"\nDropped {len(local)} local (<{MIN_BACKBONE_KV:.0f} kV) buses in region")
    print(f"Dropped {len(lines_to_drop)} lines, {len(transformers_to_drop)} transformers, {len(links_to_drop)} links no longer fully connected")

    print(f"\nFinal network: {len(n.buses)} buses, {len(n.lines)} lines, {len(n.transformers)} transformers, "
          f"{len(n.links)} links, {len(n.loads)} loads, {len(n.generators)} generators, {len(n.storage_units)} storage units")
    print("\nGenerator capacity by carrier (should be unchanged from before reduction):")
    print(n.generators.groupby("carrier").p_nom.sum())
    print(n.storage_units.groupby("carrier").p_nom.sum())

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_reduced{ext}"
    n.export_to_netcdf(output)
    print(f"\nSaved -> {output}")


if __name__ == "__main__":
    main()
