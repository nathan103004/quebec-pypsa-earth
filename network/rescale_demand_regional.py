# -*- coding: utf-8 -*-
"""
rescale_demand_regional.py

Fix the network's demand at its source: PyPSA-Earth's synthetic per-bus
demand (a population/GDP-style proxy applied Canada-wide) turned out to
be wrong two different ways once checked against real Hydro-Quebec data:

1. Total magnitude. The raw network's system-wide total for Jan 1-15 2022
   averages ~79,800 MW -- 2.6x the real HQ total for the same window
   (~30,900 MW, from 2022-demande-electricite-quebec.xlsx). This means
   attach_2022_data.py's demand-scaling step was never actually run on
   the network this project's pipeline builds from.

2. Spatial distribution. Found while investigating unexplained LOPF
   shedding at two remote 315 kV buses (1638, 2913) near Fermont: their
   combined synthetic demand was ~2,060 MW, but the REAL Cote-Nord +
   Nord-du-Quebec regions combined average only ~54 MW for the entire
   month (hq_regional_demand_region_2022-01.csv, real HQ regional
   consumption, same Jan 2022 window) -- a ~38x overstatement. Even
   attach_2022_data.py's approach wouldn't have caught this: it only
   rescales the system-wide TOTAL and explicitly preserves the existing
   (synthetic, wrong) per-bus SHARES.

Fix: rescale demand in two nested steps, using two different real
datasets for each:

    new_p_set(bus, t) = real_system_total(t)                      [1: total magnitude, 2022-demande-electricite-quebec.xlsx]
                         * real_region_share(region(bus))          [2: spatial distribution, hq_regional_demand_region_2022-01.csv]
                         * (old_p_set(bus, t) / old_region_total(region(bus), t))   [3: within-region shape -- no real per-bus data exists, so the
                                                                                         existing synthetic relative pattern is kept WITHIN a region only]

Each bus's home region is found by point-in-polygon against Quebec's 99
GADM MRC-equivalent divisions (data/gadm/gadm41_CAN.gpkg, ADM_ADM_2),
grouped into the 17 real Hydro-Quebec administrative regions via
REGION_MAP below (transcribed from the official Quebec government
MRC-by-region breakdown -- this GADM release uses some pre-2000s-reform
MRC names, e.g. "Sept-Rivieres--Caniapiscau" for what's since split into
separate MRCs, and "Communaute-Urbaine-de-Montreal" for the Montreal
agglomeration).

Buses outside Quebec (e.g. the Churchill Falls / Labrador side of the
network, or spillover into Ontario/New Brunswick) don't match any of the
17 regions and are left completely untouched -- they're outside HQ's
regional demand data's scope entirely, and get dropped later anyway by
reduce_voltage_network.py's Quebec-only display region.

Usage
-----
    python network/rescale_demand_regional.py --network networks/elec.nc
"""
import argparse
import os
import unicodedata

import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
from shapely.geometry import Point

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
GADM_PATH = os.path.join(BASE_DIR, "data", "gadm", "gadm41_CAN", "gadm41_CAN.gpkg")

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec.nc")
DEFAULT_DEMAND_TOTAL = os.path.join(NETWORK_DIR, "2022-demande-electricite-quebec.xlsx")
DEFAULT_REGIONAL_SHARES = os.path.join(NETWORK_DIR, "hq_regional_demand_region_2022-01.csv")

# GADM ADM_ADM_2 (MRC-equivalent) name -> real HQ administrative region.
# Transcribed from the Quebec government's official region/MRC breakdown.
REGION_MAP = {
    # 01 Bas-Saint-Laurent
    "Kamouraska": "Bas-Saint-Laurent", "La Matapedia": "Bas-Saint-Laurent",
    "La Mitis": "Bas-Saint-Laurent", "Les Basques": "Bas-Saint-Laurent",
    "Matane": "Bas-Saint-Laurent", "Rimouski-Neigette": "Bas-Saint-Laurent",
    "Riviere-du-Loup": "Bas-Saint-Laurent", "Temiscouata": "Bas-Saint-Laurent",
    # 02 Saguenay-Lac-Saint-Jean
    "Lac-Saint-Jean-Est": "Saguenay--Lac-Saint-Jean", "Le Domaine-du-Roy": "Saguenay--Lac-Saint-Jean",
    "Le Fjord-du-Saguenay": "Saguenay--Lac-Saint-Jean", "Maria-Chapdelaine": "Saguenay--Lac-Saint-Jean",
    # 03 Capitale-Nationale
    "Charlevoix": "Capitale-Nationale", "Charlevoix-Est": "Capitale-Nationale",
    "La Cote-de-Beaupre": "Capitale-Nationale", "La Jacques-Cartier": "Capitale-Nationale",
    "L'Ile-d'Orleans": "Capitale-Nationale", "Portneuf": "Capitale-Nationale",
    "Communaute-Urbaine-de-Quebec": "Capitale-Nationale",
    # 04 Mauricie
    "Champlain": "Mauricie", "Francheville": "Mauricie", "Le Centre-de-la-Mauricie": "Mauricie",
    "Le Haut-Saint-Maurice": "Mauricie", "Maskinonge": "Mauricie", "Mekinac": "Mauricie",
    # 05 Estrie
    "Asbestos": "Estrie", "Coaticook": "Estrie", "Le Granit": "Estrie",
    "Le Haut-Saint-Francois": "Estrie", "Le Val-Saint-Francois": "Estrie",
    "Memphremagog": "Estrie", "La Region-Sherbrookoise": "Estrie",
    # 06 Montreal
    "Communaute-Urbaine-de-Montreal": "Montréal",
    # 07 Outaouais
    "La Vallee-de-la-Gatineau": "Outaouais", "Les Collines-de-l'Outaouais": "Outaouais",
    "Papineau": "Outaouais", "Pontiac": "Outaouais", "Communaute-Urbaine-de-l'Outaouai": "Outaouais",
    # 08 Abitibi-Temiscamingue
    "Abitibi": "Abitibi-Témiscamingue", "Abitibi-Ouest": "Abitibi-Témiscamingue",
    "Rouyn-Noranda": "Abitibi-Témiscamingue", "Temiscamingue": "Abitibi-Témiscamingue",
    "Vallee-de-l'Or": "Abitibi-Témiscamingue",
    # 09 Cote-Nord
    "La Haute-Cote-Nord": "Côte-Nord", "Manicouagan": "Côte-Nord",
    "Minganie--Basse-Cote-Nord": "Côte-Nord", "Sept-Rivieres--Caniapiscau": "Côte-Nord",
    # 10 Nord-du-Quebec
    "Nord-du-Quebec": "Nord-du-Québec",
    # 11 Gaspesie-Iles-de-la-Madeleine
    "Avignon": "Gaspésie--Îles-de-la-Madeleine", "Bonaventure": "Gaspésie--Îles-de-la-Madeleine",
    "La Cote-de-Gaspe": "Gaspésie--Îles-de-la-Madeleine", "La Haute-Gaspesie": "Gaspésie--Îles-de-la-Madeleine",
    "Le Rocher-Perce": "Gaspésie--Îles-de-la-Madeleine", "Les Iles-de-la-Madeleine": "Gaspésie--Îles-de-la-Madeleine",
    # 12 Chaudiere-Appalaches
    "Beauce-Sartigan": "Chaudière-Appalaches", "Bellechasse": "Chaudière-Appalaches",
    "L'Amiante": "Chaudière-Appalaches", "L'Islet": "Chaudière-Appalaches",
    "La Nouvelle-Beauce": "Chaudière-Appalaches", "Les Chutes-de-la-Chaudiere": "Chaudière-Appalaches",
    "Les Etchemins": "Chaudière-Appalaches", "Lotbiniere": "Chaudière-Appalaches",
    "Montmagny": "Chaudière-Appalaches", "Robert-Cliche": "Chaudière-Appalaches",
    "Desjardins": "Chaudière-Appalaches",
    # 13 Laval
    "Laval": "Laval",
    # 14 Lanaudiere
    "D'Autray": "Lanaudière", "Joliette": "Lanaudière", "L'Assomption": "Lanaudière",
    "Les Moulins": "Lanaudière", "Matawinie": "Lanaudière", "Montcalm": "Lanaudière",
    # 15 Laurentides
    "Antoine-Labelle": "Laurentides", "Argenteuil": "Laurentides", "Deux-Montagnes": "Laurentides",
    "La Riviere-du-Nord": "Laurentides", "Les Laurentides": "Laurentides",
    "Les Pays-d'en-Haut": "Laurentides", "Mirabel": "Laurentides", "Therese-De Blainville": "Laurentides",
    # 16 Monteregie
    "Acton": "Montérégie", "Beauharnois-Salaberry": "Montérégie", "Brome-Missisquoi": "Montérégie",
    "La Haute-Yamaska": "Montérégie", "La Vallee-du-Richelieu": "Montérégie", "Le Bas-Richelieu": "Montérégie",
    "Le Haut-Richelieu": "Montérégie", "Le Haut-Saint-Laurent": "Montérégie", "Lajemmerais": "Montérégie",
    "Les Jardins-de-Napierville": "Montérégie", "Les Maskoutains": "Montérégie", "Roussillon": "Montérégie",
    "Rouville": "Montérégie", "Vaudreuil-Soulanges": "Montérégie",
    # 17 Centre-du-Quebec
    "Arthabaska": "Centre-du-Québec", "Becancour": "Centre-du-Québec", "Drummond": "Centre-du-Québec",
    "L'Erable": "Centre-du-Québec", "Nicolet-Yamaska": "Centre-du-Québec",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")


NORMALIZED_REGION_MAP = {strip_accents(k).lower(): v for k, v in REGION_MAP.items()}


def build_region_geometries() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(GADM_PATH, layer="ADM_ADM_2")
    qc = gdf[gdf.NAME_1.str.contains("bec", case=False)].copy()
    qc["region"] = qc["NAME_2"].map(lambda n: NORMALIZED_REGION_MAP.get(strip_accents(n).lower()))
    unmatched = qc[qc["region"].isna()]
    if len(unmatched):
        print(f"  [warn] {len(unmatched)} MRC(s) not found in REGION_MAP, left ungrouped: {unmatched['NAME_2'].tolist()}")
    matched = qc.dropna(subset=["region"])
    dissolved = matched.dissolve(by="region")
    print(f"  Built {len(dissolved)} / 17 real HQ administrative regions from {len(matched)} / {len(qc)} MRCs")
    return dissolved


# A bus within this distance of a region polygon (but not strictly inside it) is
# snapped to that region anyway -- catches GADM MRC boundary-precision gaps for
# real Quebec towns near a polygon edge (e.g. Schefferville, ~2.6km outside the
# Cote-Nord polygon despite being unambiguously a real Quebec municipality) --
# without pulling in genuinely-out-of-province buses, which sit much farther out
# (the next-closest unmatched large-demand bus after Schefferville was 20km away).
REGION_SNAP_TOLERANCE_DEG = 0.05  # ~5.5 km


def assign_bus_regions(buses: pd.DataFrame, regions: gpd.GeoDataFrame) -> pd.Series:
    bus_geom = gpd.GeoDataFrame(
        buses, geometry=[Point(xy) for xy in zip(buses.x, buses.y)], crs=regions.crs
    )
    regions_flat = regions[["geometry"]].reset_index()  # "region" becomes a normal column
    joined = gpd.sjoin(bus_geom, regions_flat, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    result = joined["region"].reindex(buses.index)

    unmatched = result[result.isna()].index
    n_snapped = 0
    for b in unmatched:
        pt = bus_geom.loc[b, "geometry"]
        dists = regions["geometry"].apply(lambda g: pt.distance(g))
        dmin = dists.min()
        if dmin <= REGION_SNAP_TOLERANCE_DEG:
            result.loc[b] = dists.idxmin()
            n_snapped += 1
    if n_snapped:
        print(f"  Snapped {n_snapped} bus(es) within {REGION_SNAP_TOLERANCE_DEG*111:.1f} km of a region polygon edge (boundary-precision gaps)")
    return result


def align_to_snapshots(df: pd.DataFrame, date_col: str, snapshots: pd.DatetimeIndex, shift: pd.Timedelta) -> pd.DataFrame:
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col]) - shift
    out = out.drop_duplicates(subset=date_col, keep="first").set_index(date_col).sort_index()
    aligned = out.reindex(snapshots)
    missing = aligned.isna().any(axis=1)
    if missing.any():
        raise ValueError(f"{missing.sum()} / {len(snapshots)} snapshots missing after alignment.")
    return aligned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--demand-total", default=DEFAULT_DEMAND_TOTAL)
    parser.add_argument("--regional-shares", default=DEFAULT_REGIONAL_SHARES)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    old_total = n.loads_t.p_set.sum(axis=1)
    print(f"Old (synthetic) total demand: mean={old_total.mean():.0f} MW  min={old_total.min():.0f}  max={old_total.max():.0f}")

    print("\nBuilding real HQ administrative regions from GADM MRC boundaries...")
    regions = build_region_geometries()

    print("Assigning each load bus to its real administrative region...")
    load_buses = n.buses.loc[n.loads.bus.unique()]
    bus_region = assign_bus_regions(load_buses, regions)
    n_matched = bus_region.notna().sum()
    print(f"  {n_matched} / {len(bus_region)} load buses matched to a Quebec region (rest are outside Quebec, left untouched)")

    print(f"\nLoading real regional demand shares: {os.path.basename(args.regional_shares)}")
    reg_shares = pd.read_csv(args.regional_shares).set_index("region")["share_of_total"]
    print(reg_shares.sort_values(ascending=False).to_string())

    print(f"\nLoading real system-wide total demand: {os.path.basename(args.demand_total)}")
    dem = pd.read_excel(args.demand_total)
    dem.columns = [c.strip() for c in dem.columns]
    aligned = align_to_snapshots(dem[["Date", "Moyenne (MW)"]], "Date", n.snapshots, shift=pd.Timedelta(hours=1))
    real_total = aligned["Moyenne (MW)"]
    print(f"  Real total demand: mean={real_total.mean():.0f} MW  min={real_total.min():.0f}  max={real_total.max():.0f}")

    print("\nRescaling: real system total x real region share x within-region synthetic shape...")
    new_p_set = n.loads_t.p_set.copy()
    load_region = n.loads.bus.map(bus_region)

    for region, share in reg_shares.items():
        region_loads = load_region.index[load_region == region]
        if len(region_loads) == 0:
            print(f"  [note] '{region}': no loads in this network matched to it, skipping")
            continue
        old_region_total = n.loads_t.p_set[region_loads].sum(axis=1)
        target_region_total = real_total * share
        with np.errstate(invalid="ignore", divide="ignore"):
            within_region_shape = n.loads_t.p_set[region_loads].div(old_region_total, axis=0)
        # Where the synthetic model had literally zero load in a real region
        # (can happen for the smallest regions), split evenly instead of
        # producing NaN/0 forever.
        zero_mask = old_region_total == 0
        if zero_mask.any():
            within_region_shape.loc[zero_mask] = 1.0 / len(region_loads)
        new_p_set[region_loads] = within_region_shape.mul(target_region_total, axis=0)
        print(f"  '{region}': {len(region_loads)} loads, old mean={old_region_total.mean():.0f} MW -> new mean={target_region_total.mean():.0f} MW")

    n.loads_t.p_set = new_p_set
    new_total = n.loads_t.p_set.sum(axis=1)
    print(f"\nNew total demand (Quebec-matched buses rescaled, others untouched): mean={new_total.mean():.0f} MW  min={new_total.min():.0f}  max={new_total.max():.0f}")
    print(f"(Real system-wide target was mean={real_total.mean():.0f} MW -- gap is demand on buses outside the matched Quebec regions, e.g. Churchill Falls/Labrador side)")

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_demandregional{ext}"
    n.export_to_netcdf(output)
    print(f"\nSaved -> {output}")


if __name__ == "__main__":
    main()
