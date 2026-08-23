# -*- coding: utf-8 -*-
"""
build_hq_contracts_data.py

Hydro-Quebec publishes its independent-power-producer purchase contracts
(wind farms, small hydro, biomass/biogas/gas cogeneration) as a JSON feed,
with per-project coordinates -- unlike the 2023 Annual Report PDF used
earlier (network/hq_major_facilities_2023.csv), which only gave aggregate
totals for these IPP categories with no per-project breakdown or location.

Source: https://www.hydroquebec.com/themes/achats-electricite-quebec/donnees/contrats-electricite.json

Note: the source JSON's "longitude" and "lattitude" fields are swapped --
"longitude" actually holds the latitude value and "lattitude" holds the
longitude (verified: e.g. Baie-des-Sables wind farm, in Bas-Saint-Laurent
on the St. Lawrence's south shore, has longitude=48.70 (a plausible
Quebec latitude) and lattitude=-67.87 (a plausible Quebec longitude)).
This script corrects that into properly named lat/lon columns.

Cross-check: summing this feed's 39 "In service" wind farms gives
3,721.75 MW, matching the 3,722 MW "Onwind" total reported elsewhere in
this project almost exactly -- so this is very likely the same source
underlying that figure, just now broken out per-project with coordinates.

Usage
-----
    python network/build_hq_contracts_data.py
"""
import argparse
import json
import os

import pandas as pd
import requests

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(NETWORK_DIR, "contrats-electricite.json")
SOURCE_URL = (
    "https://www.hydroquebec.com/themes/achats-electricite-quebec/donnees/"
    "contrats-electricite.json?v=2025-04-04"
)


def fetch(path: str = DEFAULT_JSON, url: str = SOURCE_URL, force: bool = False) -> str:
    if os.path.exists(path) and not force:
        return path
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def load_contracts(path: str = DEFAULT_JSON) -> pd.DataFrame:
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    df = pd.DataFrame(records)

    # source fields are swapped -- see module docstring
    df["lat"] = df["longitude"]
    df["lon"] = df["lattitude"]
    df = df.drop(columns=["longitude", "lattitude"])

    # at least one row (Broughton wind farm) has a comma decimal separator
    # left over from a French-locale export (e.g. "-71,1309") instead of a
    # period -- normalize before casting to float.
    for col in ["lat", "lon"]:
        df[col] = (
            df[col].astype(str).str.replace(",", ".", regex=False).astype(float)
        )

    df = df.rename(
        columns={
            "nom-projet-en": "name",
            "nom-projet-fr": "name_fr",
            "abreviation-projet": "abbreviation",
            "type-projet-en": "category",
            "type-cogeneration-en": "cogeneration_type",
            "statut-projet-en": "status",
            "region-administrative-quebec": "region",
            "promoteur": "promoter",
            "puissance-mw-en": "capacity_mw",
            "date-mise-en-service": "commissioning_date",
            "no-appel": "call_for_tenders_ref",
            "site-internet": "website",
        }
    )

    cols = [
        "id",
        "name",
        "name_fr",
        "abbreviation",
        "category",
        "cogeneration_type",
        "status",
        "capacity_mw",
        "commissioning_date",
        "region",
        "lat",
        "lon",
        "promoter",
        "call_for_tenders_ref",
        "website",
    ]
    return df[cols].sort_values(["category", "capacity_mw"], ascending=[True, False])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-path", default=DEFAULT_JSON)
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--output-dir", default=NETWORK_DIR)
    args = parser.parse_args()

    path = fetch(args.json_path, args.url, force=args.force_download)
    df = load_contracts(path)

    all_path = os.path.join(args.output_dir, "hq_ipp_contracts.csv")
    df.to_csv(all_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(df)} IPP contracts (all categories) -> {all_path}")

    wind = df[df.category == "Wind farm"]
    wind_path = os.path.join(args.output_dir, "hq_wind_farms.csv")
    wind.to_csv(wind_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(wind)} wind farms -> {wind_path}")

    print()
    print(df.groupby(["category", "status"]).agg(n=("id", "size"), mw=("capacity_mw", "sum")))
    print()
    in_service_wind = wind[wind.status == "In service"]
    print(
        f"Wind farms in service: {len(in_service_wind)}, "
        f"{in_service_wind.capacity_mw.sum():.2f} MW total"
    )


if __name__ == "__main__":
    main()
