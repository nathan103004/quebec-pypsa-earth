# -*- coding: utf-8 -*-
"""
attach_real_generators.py

Strip the raw network (networks/elec.nc) down to real Quebec generation
only: hydro, wind, solar, and Becancour (carrier "OCGT", HQ's own "gas
turbine" label). Everything else PyPSA-Earth attached (coal, nuclear,
oil, lignite, geothermal, offwind, PHS, and every plant physically
located outside Quebec) is dropped.

Why this is needed
-------------------
Two different problems in the freshly-built network:

1. Real, but wrong scope. `ror` (run-of-river) generators, `hydro`
   StorageUnits, and `CCGT` generators are already REAL matched plants
   from resources/powerplants.csv (with real names, coordinates, and a
   bus already assigned by PyPSA-Earth's own build_powerplants step) --
   but a plain geographic (in-Quebec) filter over-includes: it pulled in
   ~48.5 GW of hydro (vs. the ~36.9 GW figure used everywhere else in
   this project) because it doesn't distinguish Hydro-Quebec's own fleet
   from other real plants merely located in the province -- e.g.
   Isle-Maligne (448 MW) is a private Rio Tinto Alcan plant powering its
   own aluminum smelter, not part of HQ's public grid supply. Likewise
   Becancour's CCGT capacity here is 962 MW (technical/nameplate) vs. the
   411 MW Hydro-Quebec's own Annual Report lists as what it actually
   operates/purchases. So instead: match by name (normalized, since
   spelling/hyphenation differs) against network/hq_major_facilities_2023.csv
   -- Hydro-Quebec's own "62 hydroelectric generating stations" +
   Becancour list transcribed from its 2023 Annual Report -- and use
   THAT capacity, not powerplantmatching's. Only named individual
   stations are matched this way; the report's small aggregate rows
   (16 stations <100 MW, unitemized IPP hydro) aren't -- no way to place
   an aggregate on a specific bus, so ~1.4 GW is knowingly left out
   rather than guessed at.
2. Not real at all. `onwind` and `solar` have exactly one generator per
   onshore bus (3197 of each, nationwide) -- this is PyPSA-Earth's
   synthetic, land-area-based extendable POTENTIAL, not real plants (see
   `electricity.extendable_carriers` / `renewable.onwind.extendable` in
   config.default.yaml). There is zero real wind/solar capacity in the
   raw network at all. These are dropped entirely and replaced with real
   plants: wind from network/hq_wind_farms.csv (Hydro-Quebec's own IPP
   contract data, real coordinates); solar from
   network/hq_major_facilities_2023.csv's 2 named plants (Gabrielle-Bodis,
   Robert-A.-Boyd), name-matched against resources/powerplants.csv for a
   bus/coordinates since the HQ source itself has none.

Name matching
--------------
Names are normalized (strip accents, drop numeric suffixes like "-4" or
"-PA", lowercase) before matching, since e.g. "La Grande-4" needs to
match powerplantmatching's plain "La Grande" -- multiple HQ station
numbers on the same river (La Grande-1/2-A/3/4, Manic-1/5/5-PA, etc.)
collapse to one base name on the matched-database side, so candidates
sharing a base name are disambiguated by nearest capacity (HQ's list is
already capacity-sorted per group, and so, usually, is the matched
data). Unmatched HQ stations are reported and skipped rather than
guessed at.

Churchill Falls (5,428 MW, matches exactly by name, real bus already
assigned) is included even though it is physically in Labrador (NL), not
Quebec -- Hydro-Quebec has contracted almost all of its output until 2041,
so its power is effectively part of the Quebec supply.

Time-varying profiles
-----------------------
- onwind / solar generators get their p_max_pu capacity-factor time
  series looked up by BUS from the bus-indexed
  resources/renewable_profiles/profile_{onwind,solar}.nc files (the same
  ones PyPSA-Earth's own add_electricity.py uses) -- these cover every
  eligible onshore bus, not just the ones that happened to get a
  synthetic generator.
- hydro StorageUnits and ror generators keep their existing inflow /
  p_max_pu time series untouched (only capacity is overridden), since
  resources/renewable_profiles/profile_hydro.nc is indexed by matched
  PLANT, not by bus, and the existing values were already correctly
  assigned by add_electricity.py for the specific plant we matched to.
- CCGT keeps its existing (static) p_max_pu.

Usage
-----
    python network/attach_real_generators.py --network networks/elec.nc
"""
import argparse
import os
import re
import unicodedata

import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
import xarray as xr
from shapely.geometry import Point

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec.nc")
GADM_PATH = os.path.join(BASE_DIR, "data", "gadm", "gadm41_CAN", "gadm41_CAN.gpkg")
POWERPLANTS_CSV = os.path.join(BASE_DIR, "resources", "powerplants.csv")
HQ_FACILITIES_CSV = os.path.join(NETWORK_DIR, "hq_major_facilities_2023.csv")
HQ_HYDRO_OFFICIAL_CSV = os.path.join(NETWORK_DIR, "hq_hydro_stations_official.csv")
WIND_FARMS_CSV = os.path.join(NETWORK_DIR, "hq_wind_farms.csv")
ONWIND_PROFILE = os.path.join(BASE_DIR, "resources", "renewable_profiles", "profile_onwind.nc")
SOLAR_PROFILE = os.path.join(BASE_DIR, "resources", "renewable_profiles", "profile_solar.nc")
HYDRO_PROFILE = os.path.join(BASE_DIR, "resources", "renewable_profiles", "profile_hydro.nc")

QUEBEC_BUFFER_DEG = 0.05  # ~5 km, swallows GADM simplification artifacts, used for wind siting only
CAPACITY_MATCH_TOLERANCE = 0.5  # matched candidate capacity must be within 50% of HQ's figure

# Hydro-Quebec's own cited total reservoir energy storage capacity across its
# 28 large reservoirs: "176 billion kWh" / 176 TWh (hydroquebec.com,
# "Reservoirs | Dams | Hydropower" -- also corroborated at ~173 TWh elsewhere).
# We don't have a per-plant reservoir volume/head breakdown, so this real
# aggregate figure is allocated across our named reservoir (non-ror) hydro
# StorageUnits proportional to nameplate capacity -- the least-biased split
# available without per-plant data, and a large improvement over an
# arbitrary hours-of-power placeholder.
HQ_TOTAL_RESERVOIR_ENERGY_MWH = 176_000_000.0


def get_quebec_polygon(buffer_deg: float = QUEBEC_BUFFER_DEG):
    provinces = gpd.read_file(GADM_PATH, layer="ADM_ADM_1")
    qc = provinces[provinces.NAME_1.str.contains("bec", case=False)]
    geom = qc.geometry.union_all() if hasattr(qc.geometry, "union_all") else qc.geometry.unary_union
    # The raw (unsimplified) GADM coastline for Quebec is enormous (Hudson/James
    # Bay plus thousands of islands) -- simplify before buffering, purely a
    # performance fix; a plant-location filter doesn't need survey precision.
    geom = geom.simplify(0.005)
    return geom.buffer(buffer_deg)


def nearest_bus(buses: pd.DataFrame, lat: float, lon: float, candidates: pd.Index | None = None) -> str:
    pool = buses.loc[candidates] if candidates is not None else buses
    d2 = (pool["x"] - lon) ** 2 + (pool["y"] - lat) ** 2
    return d2.idxmin()


def load_profile_table(nc_path: str, snapshots: pd.DatetimeIndex) -> pd.DataFrame:
    """time x bus capacity-factor table from a bus-indexed renewable_profiles/*.nc."""
    ds = xr.open_dataset(nc_path)
    df = ds["profile"].to_pandas()
    ds.close()
    df.columns = df.columns.astype(str)
    df.index = snapshots
    return df


def load_hydro_inflow_table(nc_path: str, snapshots: pd.DatetimeIndex) -> pd.DataFrame:
    """time x plant-index (original resources/powerplants.csv row index)
    inflow (MW-equivalent) table from resources/renewable_profiles/profile_hydro.nc."""
    ds = xr.open_dataset(nc_path)
    df = ds["inflow"].to_pandas().T  # -> time x plant
    ds.close()
    df.index = snapshots
    return df


def normalize_plant_name(name: str) -> str:
    """Collapse "La Grande-4" / "Manic-5-PA" / "Sainte-Marguerite-3" style HQ
    names down to a base name comparable against powerplantmatching's
    plainer names ("La Grande", "Manic", "Sainte Marguerite")."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\([^)]*\)", "", s)  # strip parenthetical qualifiers, e.g. "(gas turbine)"
    s = re.sub(r"-\d+", "", s)  # strip "-4", "-5", "-3" etc.
    s = re.sub(r"-[A-Za-z]{1,3}$", "", s)  # strip trailing "-PA", "-A" etc.
    s = re.sub(r"[^a-zA-Z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def slug(name: str) -> str:
    """Filesystem/PyPSA-name-safe short form, e.g. for building generator names."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-")


# Hydro-Quebec renamed several stations in 2015 (honoring former premiers);
# resources/powerplants.csv's matched database still uses some of the old
# names. Applied to the HQ-side name before matching.
HQ_STATION_RENAME_ALIASES = {
    "Jean-Lesage": "Manic-2",  # capacities match exactly: 1229 MW
    "Rene-Levesque": "Manic-3",  # capacities match exactly: 1326 MW
    "Rocher-de-Grand-Mere": "Grand Mere",  # matched db drops the "Rocher-de-" prefix
}


def match_hq_plants_by_name(
    hq_rows: pd.DataFrame,
    candidates: pd.DataFrame,
    hq_capacity_col: str = "capacity_mw",
    candidate_capacity_col: str = "Capacity",
) -> tuple[pd.DataFrame, list[str]]:
    """For each row in hq_rows, find the best-matching row in candidates by
    normalized base name.

    Within a name group (e.g. all "La Grande-*" or "Manic-*" HQ stations,
    which collapse to one base name in the matched database -- see
    normalize_plant_name), candidates are assigned optimally by capacity
    (scipy Hungarian algorithm) rather than greedily, since a greedy
    nearest-first pass can cascade into wrong pairings when multiple
    same-family stations are close in capacity.

    Two additional rules, since HQ's own station list is finer-grained
    than the matched database in places:
    - A group with exactly one HQ station and exactly one candidate is
      accepted regardless of capacity difference (unique name match is
      strong enough evidence on its own -- e.g. Becancour's matched
      capacity is nameplate/technical, not HQ's reported operated
      capacity, and Bernard-Landry's differs for unclear reasons, but
      both are unambiguously the same single named plant).
    - If a group has more HQ stations than candidates (e.g. Manic-5 and
      Manic-5-PA vs. one combined "Manic" database record at their
      combined capacity), the unmatched excess is assigned to its
      nearest-capacity already-matched sibling's bus, on the assumption
      that same-name-family stations without their own database record
      are additional units at the same site (true for underground
      additions like Manic-5-PA and multi-unit stations like
      Shawinigan-2/3, which is exactly the combined capacity of two
      Shawinigan units in the matched data).

    Returns (matched hq_rows with candidate columns attached, list of
    unmatched HQ plant names)."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    hq = hq_rows.copy()
    hq["_norm"] = hq["name"].apply(
        lambda nm: normalize_plant_name(HQ_STATION_RENAME_ALIASES.get(nm, nm))
    )
    candidates = candidates.copy()
    candidates["_norm"] = candidates["Name"].apply(normalize_plant_name)
    candidates["_orig_idx"] = candidates.index  # preserved for profile_hydro.nc's plant-index lookup

    matched_rows = []
    unmatched = []

    def make_row(hq_row, best, capacity_share=1.0):
        merged = hq_row.to_dict()
        merged["matched_name"] = best["Name"]
        merged["matched_capacity"] = best[candidate_capacity_col]
        merged["bus"] = str(best["bus"])
        merged["lat"] = best["lat"]
        merged["lon"] = best["lon"]
        merged["Technology"] = best.get("Technology")
        merged["orig_idx"] = best.get("_orig_idx")
        merged["capacity_share"] = capacity_share
        return merged

    for norm_name, group in hq.groupby("_norm"):
        pool = candidates[candidates["_norm"] == norm_name]
        group = group.reset_index(drop=True)
        pool = pool.reset_index(drop=True)

        if pool.empty:
            unmatched.extend(group["name"].tolist())
            continue

        if len(group) == 1 and len(pool) == 1:
            matched_rows.append(make_row(group.iloc[0], pool.iloc[0]))
            continue

        cost = np.abs(
            group[hq_capacity_col].to_numpy()[:, None] - pool[candidate_capacity_col].to_numpy()[None, :]
        )
        hq_idx, cand_idx = linear_sum_assignment(cost)
        # Undersupplied group (fewer database candidates than HQ stations):
        # a capacity mismatch is *expected* here (the candidate is likely a
        # combined record for multiple co-located units), not a sign of a
        # wrong-plant match, so skip the tolerance check entirely.
        undersupplied = len(pool) < len(group)

        considered_hq = set(hq_idx.tolist())
        group_matches = {}  # hq positional index -> matched dict
        for i, j in zip(hq_idx, cand_idx):
            hq_row = group.iloc[i]
            best = pool.iloc[j]
            target = hq_row[hq_capacity_col]
            rel_diff = abs(best[candidate_capacity_col] - target) / max(target, 1e-6)
            if not undersupplied and rel_diff > CAPACITY_MATCH_TOLERANCE:
                unmatched.append(
                    f"{hq_row['name']} (closest candidate '{best['Name']}' "
                    f"{best[candidate_capacity_col]:.0f} MW vs HQ {target:.0f} MW -- too far off, skipped)"
                )
                continue
            group_matches[i] = make_row(hq_row, best)

        # more HQ stations than candidates in this group -> the leftover
        # station(s) likely share a site with an already-matched sibling
        for i in range(len(group)):
            if i in considered_hq:
                continue
            hq_row = group.iloc[i]
            if not group_matches:
                unmatched.append(f"{hq_row['name']} (no candidate available in its name group)")
                continue
            # Prefer a sibling that's literally the same station (e.g.
            # "Manic-5-PA" is an addition to "Manic-5", not to whichever
            # other Manic-family station happens to be closest in
            # capacity) -- fall back to nearest-capacity only when no
            # sibling name is a prefix of this one (or vice versa).
            same_site = [
                k for k in group_matches
                if hq_row["name"].startswith(group.iloc[k]["name"])
                or group.iloc[k]["name"].startswith(hq_row["name"])
            ]
            pick_from = same_site if same_site else list(group_matches)
            nearest_i = min(
                pick_from, key=lambda k: abs(group.iloc[k][hq_capacity_col] - hq_row[hq_capacity_col])
            )
            sibling = group_matches[nearest_i]
            # Both stations draw on the one shared inflow record -- split it
            # by each station's share of their combined HQ-reported capacity
            # so water isn't double-counted between them.
            sibling_cap = group.iloc[nearest_i][hq_capacity_col]
            this_cap = hq_row[hq_capacity_col]
            total_cap = sibling_cap + this_cap
            sibling["capacity_share"] = sibling_cap / total_cap
            fake_best = {
                "Name": f"{sibling['matched_name']} (shared site with {group.iloc[nearest_i]['name']})",
                candidate_capacity_col: sibling["matched_capacity"],
                "bus": sibling["bus"],
                "lat": sibling["lat"],
                "lon": sibling["lon"],
                "Technology": sibling["Technology"],
                "_orig_idx": sibling["orig_idx"],
            }
            matched_rows.append(make_row(hq_row, fake_best, capacity_share=this_cap / total_cap))

        matched_rows.extend(group_matches.values())

    if not matched_rows:
        return pd.DataFrame(
            columns=list(hq.columns)
            + ["matched_name", "matched_capacity", "bus", "lat", "lon", "Technology", "orig_idx", "capacity_share"]
        ), unmatched
    return pd.DataFrame(matched_rows), unmatched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)
    snapshots = n.snapshots

    powerplants = pd.read_csv(POWERPLANTS_CSV)
    powerplants["bus"] = powerplants["bus"].astype(str)
    hq = pd.read_csv(HQ_FACILITIES_CSV, comment="#")

    # ---------------------------------------------------------------
    # 1. Clear every existing generator/storage unit -- everything that was
    #    there (coal/nuclear/oil/etc., and the "real but wrong scope" or
    #    "not real at all" hydro/onwind/solar/CCGT -- see module docstring)
    #    is being rebuilt from named HQ data below, not filtered in place.
    # ---------------------------------------------------------------
    print(f"Dropping all {len(n.generators)} generators and {len(n.storage_units)} storage units")
    n.mremove("Generator", n.generators.index)
    n.mremove("StorageUnit", n.storage_units.index)

    # ---------------------------------------------------------------
    # 2. Hydro: name-match HQ's named stations (+ Churchill Falls) against
    #    resources/powerplants.csv for bus/technology/profile provenance
    # ---------------------------------------------------------------
    hq_hydro = hq[hq.type.isin(["hydro", "hydro_ppa_named"])]
    pp_hydro = powerplants[powerplants.Fueltype == "Hydro"]
    matched_hydro, unmatched_hydro = match_hq_plants_by_name(hq_hydro, pp_hydro)
    if unmatched_hydro:
        print(f"  [warn] {len(unmatched_hydro)} HQ hydro stations not matched, skipped:")
        for u in unmatched_hydro:
            print(f"    - {u}")

    # hq_major_facilities_2023.csv's capacity_mw is from HQ's 2023 Annual
    # Report; hq_hydro_stations_official.csv is transcribed from HQ's own
    # live generating-stations page (more current, e.g. reflects
    # refurbishments/uprates since 2023) -- prefer it by name where a plant
    # is on both lists (all of them except Churchill Falls, which isn't
    # HQ's own station -- see that CSV's header).
    official = pd.read_csv(HQ_HYDRO_OFFICIAL_CSV, comment="#")

    def _norm_official(name):
        s = unicodedata.normalize("NFD", str(name).split("(")[0])
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return re.sub(r"[^a-z0-9]", "", s.lower())

    official["_norm"] = official["name"].apply(_norm_official)
    official_capacity = official.set_index("_norm")["capacity_mw"]
    matched_hydro = matched_hydro.copy()
    matched_hydro["_norm"] = matched_hydro["name"].apply(_norm_official)
    updated = matched_hydro["_norm"].map(official_capacity)
    n_updated = 0
    for idx in matched_hydro.index:
        official_mw = updated.at[idx]
        if pd.notna(official_mw) and not np.isclose(official_mw, matched_hydro.at[idx, "capacity_mw"], rtol=0.001):
            print(f"  [capacity] {matched_hydro.at[idx,'name']}: {matched_hydro.at[idx,'capacity_mw']:.0f} MW "
                  f"(HQ 2023 Annual Report) -> {official_mw:.0f} MW (HQ official generating-stations page)")
            matched_hydro.at[idx, "capacity_mw"] = official_mw
            n_updated += 1
    print(f"  Updated capacity_mw for {n_updated} plants from HQ's official generating-stations page")

    inflow_table = load_hydro_inflow_table(HYDRO_PROFILE, snapshots)

    is_ror_row = matched_hydro["Technology"].astype(str).str.lower().str.startswith("run")
    reservoir_cap_total = matched_hydro.loc[~is_ror_row, "capacity_mw"].sum()
    reservoir_max_hours = (
        HQ_TOTAL_RESERVOIR_ENERGY_MWH / reservoir_cap_total if reservoir_cap_total > 0 else 6.0
    )
    print(
        f"Reservoir hydro: {reservoir_cap_total:.0f} MW matched, allocated a share of HQ's real "
        f"{HQ_TOTAL_RESERVOIR_ENERGY_MWH/1e6:.0f} TWh total reservoir capacity proportional to "
        f"nameplate -> max_hours={reservoir_max_hours:.0f} ({reservoir_max_hours/24:.0f} days) per unit, "
        f"started full (winter reservoirs are charged heading into the season)"
    )

    n_ror = n_res = 0
    no_inflow = []
    for _, row in matched_hydro.iterrows():
        is_ror = str(row["Technology"]).lower().startswith("run")
        gen_name = f"{row['bus']} {'ror' if is_ror else 'hydro'}-{slug(row['name'])}"

        orig_idx = row["orig_idx"]
        share = row["capacity_share"] if pd.notna(row["capacity_share"]) else 1.0
        plant_inflow = None
        if orig_idx is not None and orig_idx in inflow_table.columns:
            plant_inflow = inflow_table[orig_idx] * share

        if is_ror:
            n.add(
                "Generator",
                gen_name,
                bus=row["bus"],
                carrier="ror",
                p_nom=row["capacity_mw"],
                p_nom_extendable=False,
            )
            if plant_inflow is not None:
                n.generators_t.p_max_pu[gen_name] = (plant_inflow / row["capacity_mw"]).clip(upper=1.0)
            else:
                no_inflow.append(gen_name)
            n_ror += 1
        else:
            n.add(
                "StorageUnit",
                gen_name,
                bus=row["bus"],
                carrier="hydro",
                p_nom=row["capacity_mw"],
                max_hours=reservoir_max_hours,
                state_of_charge_initial=reservoir_max_hours * row["capacity_mw"],
                cyclic_state_of_charge=False,
                p_min_pu=0.0,  # conventional reservoir hydro, not pumped storage -- can't charge from the grid
                p_max_pu=1.0,
            )
            if plant_inflow is not None:
                n.storage_units_t.inflow[gen_name] = plant_inflow
            else:
                no_inflow.append(gen_name)
            n_res += 1
    print(
        f"Added {n_ror} run-of-river + {n_res} reservoir hydro stations "
        f"({matched_hydro['capacity_mw'].sum():.0f} MW total, {len(matched_hydro)}/{len(hq_hydro)} "
        f"of HQ's named list matched)"
    )
    if no_inflow:
        print(f"  [warn] no inflow data available for: {no_inflow} (left at default: ror p_max_pu=1.0 always, hydro inflow=0.0 always)")

    # ---------------------------------------------------------------
    # 3. OCGT: Becancour only, at HQ's reported operated capacity (411 MW,
    #    not powerplantmatching's 962 MW nameplate). Carrier is "OCGT" to
    #    match this project's established convention (HQ's own report
    #    calls it a "gas turbine"); it's matched against the source
    #    database's "CCGT" Fueltype field since that's what Becancour is
    #    filed under there -- that's a lookup key, not our carrier choice.
    # ---------------------------------------------------------------
    hq_ocgt = hq[hq.type == "thermal_gas"]
    pp_ocgt = powerplants[powerplants.Fueltype == "CCGT"]
    matched_ocgt, unmatched_ocgt = match_hq_plants_by_name(hq_ocgt, pp_ocgt)
    if unmatched_ocgt:
        print(f"  [warn] OCGT (Becancour) not matched, skipped: {unmatched_ocgt}")
    for _, row in matched_ocgt.iterrows():
        gen_name = f"{row['bus']} OCGT-{slug(row['name'])}"
        n.add(
            "Generator",
            gen_name,
            bus=row["bus"],
            carrier="OCGT",
            p_nom=row["capacity_mw"],
            p_nom_extendable=False,
        )
        # Kept in the network (real, contracted capacity) but forced
        # inactive: Quebec real-world data (network/2022-sources-electricite-
        # quebec.csv, "Thermique" column) shows this dispatched an average
        # of ~1.3 MW across all of 2022 -- an expensive peaker essentially
        # never actually run. This PyPSA version has no "active" component
        # flag, so p_max_pu=0 is the portable way to disable dispatch
        # while still keeping the generator (and its capacity) present.
        n.generators_t.p_max_pu[gen_name] = 0.0
        n.generators_t.p_min_pu[gen_name] = 0.0
    print(f"Added {len(matched_ocgt)} OCGT plant(s), {matched_ocgt['capacity_mw'].sum():.0f} MW total (forced inactive, p_max_pu=0)")

    # ---------------------------------------------------------------
    # 4. Wind: network/hq_wind_farms.csv (real coordinates), nearest-bus
    # ---------------------------------------------------------------
    print("Loading Quebec boundary (buffered) for wind farm bus siting...")
    qc_poly = get_quebec_polygon()
    in_qc = n.buses.apply(lambda r: Point(r.x, r.y).within(qc_poly), axis=1)
    qc_bus_index = pd.Index(sorted(n.buses.index[in_qc]))

    wind = pd.read_csv(WIND_FARMS_CSV)
    wind = wind[wind.status == "In service"].copy()
    wind["bus"] = wind.apply(
        lambda r: nearest_bus(n.buses, r.lat, r.lon, candidates=qc_bus_index), axis=1
    )
    wind["name"] = wind["bus"] + " onwind-" + wind["abbreviation"].astype(str)

    onwind_profiles = load_profile_table(ONWIND_PROFILE, snapshots)
    missing_profile = []
    for _, row in wind.iterrows():
        n.add(
            "Generator",
            row["name"],
            bus=row["bus"],
            carrier="onwind",
            p_nom=row["capacity_mw"],
            p_nom_extendable=False,
        )
        if row["bus"] in onwind_profiles.columns:
            n.generators_t.p_max_pu[row["name"]] = onwind_profiles[row["bus"]].clip(lower=0, upper=1)
        else:
            missing_profile.append(row["name"])
    if missing_profile:
        print(f"  [warn] no onwind profile for bus of: {missing_profile} (left at default p_max_pu)")
    print(f"Added {len(wind)} real wind farms, {wind.capacity_mw.sum():.1f} MW total")

    # ---------------------------------------------------------------
    # 5. Solar: HQ's 2 named plants, name-matched against
    #    resources/powerplants.csv for bus/coordinates (HQ source has none)
    # ---------------------------------------------------------------
    hq_solar = hq[hq.type == "solar"]
    pp_solar = powerplants[powerplants.Fueltype == "Solar"]
    matched_solar, unmatched_solar = match_hq_plants_by_name(hq_solar, pp_solar)

    if matched_solar.empty:
        # HQ's 2 named plants (Gabrielle-Bodis, Robert-A.-Boyd) don't share a
        # name with anything in the matched database and have no coordinates
        # of their own, so there's nothing to place them at. Rather than
        # attach zero solar capacity, fall back to the real, actually
        # located Quebec solar plants the matched database does have --
        # different specific plants than HQ's report names, but real and
        # in Quebec, and the combined capacity is a comparable order of
        # magnitude (~12 MW vs HQ's ~10 MW).
        print(
            f"  [warn] {len(unmatched_solar)} HQ solar plant(s) not matched by name "
            "and have no coordinates of their own -- falling back to real, "
            "located Quebec solar plants from resources/powerplants.csv instead "
            "(different specific plants, similar total capacity):"
        )
        for u in unmatched_solar:
            print(f"    - {u}")
        qc_solar = pp_solar[pp_solar.apply(lambda r: Point(r.lon, r.lat).within(qc_poly), axis=1)].copy()
        qc_solar["name"] = qc_solar["Name"]
        qc_solar["capacity_mw"] = qc_solar["Capacity"]
        qc_solar["bus"] = qc_solar["bus"].astype(str)
        matched_solar = qc_solar

    solar_profiles = load_profile_table(SOLAR_PROFILE, snapshots)
    for _, row in matched_solar.iterrows():
        gen_name = f"{row['bus']} solar-{slug(row['name'])}"
        n.add(
            "Generator",
            gen_name,
            bus=row["bus"],
            carrier="solar",
            p_nom=row["capacity_mw"],
            p_nom_extendable=False,
        )
        if row["bus"] in solar_profiles.columns:
            n.generators_t.p_max_pu[gen_name] = solar_profiles[row["bus"]].clip(lower=0, upper=1)
    print(f"Added {len(matched_solar)} real solar plants, {matched_solar['capacity_mw'].sum():.1f} MW total")

    # ---------------------------------------------------------------
    # Summary + save
    # ---------------------------------------------------------------
    print("\nFinal generator carriers:")
    print(n.generators.carrier.value_counts())
    print("\nFinal generator capacity by carrier (MW):")
    print(n.generators.groupby("carrier").p_nom.sum())
    print("\nFinal storage_unit carriers:")
    print(n.storage_units.carrier.value_counts())
    print(f"Final hydro storage capacity: {n.storage_units.p_nom.sum():.1f} MW")

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_real_generators{ext}"
    n.export_to_netcdf(output)
    print(f"\nSaved -> {output}")


if __name__ == "__main__":
    main()
