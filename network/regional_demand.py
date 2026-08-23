# -*- coding: utf-8 -*-
"""
regional_demand.py

PyPSA-Earth's default per-bus demand split is a synthetic population/GDP
(or, in this project's earliest version, a crude voltage-level) weighting
-- not real. This uses Hydro-Quebec's published historical municipal
consumption (network/consommation-historique-municipalite-11mars2024.xlsx,
monthly, by municipality and sector, Jan 2016 - Dec 2023) to derive real
SPATIAL shares of demand, at two granularities:

- municipality (1,128 municipalities reporting in Jan 2022) -- finer, but
  ~69% of municipality x sector rows are null for Jan 2022 (Hydro-Quebec
  suppresses small-customer-count cells for privacy). The missing rows
  skew toward small/rural municipalities, so total-kWh coverage is
  better than the raw row-suppression rate suggests, but is NOT complete
  -- treat municipality-level shares as an approximation, not an exact
  accounting identity, and don't be surprised if they don't sum to the
  provincial total.
- region (Quebec's 17 official administrative regions) -- coarser, but
  Jan 2022 municipality-level rows sum to ~11.97B kWh across all 17
  regions with no region entirely missing, so this level is much more
  complete and is the safer fallback for allocation.

This only replaces the SPATIAL split. The hourly SYSTEM-WIDE total
still comes from attach_2022_data.py (the real hourly demand xlsx) --
these regional/municipal shares just decide how that hourly total is
divided across buses, instead of PyPSA-Earth's synthetic per-bus shares.

Usage
-----
    python network/regional_demand.py --year-month 2022-01
"""
import argparse
import os

import pandas as pd

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_XLSX = os.path.join(
    NETWORK_DIR, "consommation-historique-municipalite-11mars2024.xlsx"
)

COLUMN_RENAME = {
    "REGION_ADM_QC_TXT": "region",
    "MRC_TXT": "mrc",
    "NOM_MUNICIPALITE": "municipality",
    "ANNEE_MOIS": "year_month",
    "SECTEUR": "sector",
    "Total (kWh)": "kwh",
}


def load_raw(path: str = DEFAULT_XLSX) -> pd.DataFrame:
    df = pd.read_excel(path)
    df = df.rename(columns=COLUMN_RENAME)
    return df


def municipality_shares(df: pd.DataFrame, year_month: str) -> pd.DataFrame:
    """Sum all sectors per municipality for year_month, return with each
    municipality's share of the (non-null-row) provincial total."""
    sub = df[df.year_month == year_month]
    if sub.empty:
        raise ValueError(f"No rows found for year_month={year_month!r}")

    n_missing = sub.kwh.isna().sum()
    print(
        f"[municipality] {year_month}: {len(sub)} municipality x sector rows, "
        f"{n_missing} ({n_missing / len(sub):.1%}) null (Hydro-Quebec privacy "
        "suppression) -- excluded from the sum, not treated as zero."
    )

    grouped = (
        sub.groupby(["region", "mrc", "municipality"], dropna=False)["kwh"]
        .sum(min_count=1)
        .reset_index()
        .rename(columns={"kwh": "total_kwh"})
    )
    grouped = grouped[grouped.total_kwh.notna()].copy()
    grouped["share_of_total"] = grouped.total_kwh / grouped.total_kwh.sum()
    return grouped.sort_values("total_kwh", ascending=False).reset_index(drop=True)


def region_shares(df: pd.DataFrame, year_month: str) -> pd.DataFrame:
    """Sum all sectors per Quebec administrative region for year_month --
    coarser than municipality_shares but much more complete."""
    sub = df[df.year_month == year_month]
    if sub.empty:
        raise ValueError(f"No rows found for year_month={year_month!r}")

    grouped = (
        sub.groupby("region")["kwh"]
        .sum(min_count=1)
        .reset_index()
        .rename(columns={"kwh": "total_kwh"})
    )
    grouped["share_of_total"] = grouped.total_kwh / grouped.total_kwh.sum()
    return grouped.sort_values("total_kwh", ascending=False).reset_index(drop=True)


def allocate_demand_to_buses(n, shares_df, bus_regions_path, name_col="municipality"):
    """Reassign per-bus demand shares using real regional/municipal shares
    instead of PyPSA-Earth's synthetic population/GDP split.

    NOT YET RUNNABLE -- blocked on two things that don't exist yet:
    1. resources/bus_regions/regions_onshore.geojson (each bus's Voronoi
       cell) -- produced by the build_bus_regions rule, part of the
       add_electricity pipeline still running as of this writing.
    2. A geometry source to spatially join `shares_df` rows to those bus
       cells. GADM's admin-2 layer (data/gadm/gadm41_CAN/gadm41_CAN.gpkg,
       already cached by build_shapes) follows Quebec's MRC boundaries,
       not individual municipalities and not the 17 administrative
       regions -- so this needs either fuzzy name-matching MRC_TXT against
       that layer (partial coverage, ~32k null MRC rows in the source
       file), or a dedicated regional-boundary source for the 17-region
       level.

    Once both exist, the approach is: point-in-polygon (or MRC/region
    name match) each bus's Voronoi cell against the shares geometry,
    aggregate matched shares per bus, renormalize to sum to 1, and use
    that in place of n.loads_t.p_set's current per-bus share matrix
    (see attach_2022_data.py's attach_demand -- same "shares x total"
    pattern, just with a better-sourced shares vector).
    """
    raise NotImplementedError(
        "Blocked on bus_regions + a municipality/region boundary geometry "
        "source -- see docstring. Network build must finish first."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", default=DEFAULT_XLSX)
    parser.add_argument("--year-month", default="2022-01")
    parser.add_argument("--output-dir", default=NETWORK_DIR)
    args = parser.parse_args()

    df = load_raw(args.xlsx)

    muni = municipality_shares(df, args.year_month)
    muni_path = os.path.join(
        args.output_dir, f"hq_regional_demand_municipality_{args.year_month}.csv"
    )
    muni.to_csv(muni_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(muni)} municipalities -> {muni_path}")

    reg = region_shares(df, args.year_month)
    reg_path = os.path.join(
        args.output_dir, f"hq_regional_demand_region_{args.year_month}.csv"
    )
    reg.to_csv(reg_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(reg)} regions -> {reg_path}")

    print()
    print(reg.to_string(index=False))


if __name__ == "__main__":
    main()
