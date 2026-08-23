# -*- coding: utf-8 -*-
"""
attach_2022_data.py

Replace a PyPSA-Earth Quebec network's synthetic demand and generation
profiles with real 2022 Hydro-Quebec data, over whatever snapshot window
the network was built with.

Two real data sources are used (both dropped in this `network/` folder):

- ``2022-demande-electricite-quebec.xlsx``: hourly system-wide demand
  (columns "Date", "Moyenne (MW)"), covering all of 2022.
- ``2022-sources-electricite-quebec.csv``: generation mix by source
  (Hydraulique, Eolien, Autres, Solaire, Thermique, Total), in MW,
  half-hourly, covering all of 2022.

Both files use interval-*ending* timestamps (e.g. the demand row at
01:00:00 is the average over 00:00-01:00; the sources row at 00:30:00
covers 00:00-01:00). PyPSA snapshots are interval-*beginning*, so both
series are shifted back before being aligned to ``n.snapshots``.

Demand
------
Per-bus load shares are re-derived at every snapshot from the network's
CURRENT ``loads_t.p_set`` (each bus's fraction of total system demand).
That spatial pattern is preserved; only the system-wide total at each
snapshot is replaced with the real 2022 total. Adapted from a surviving
script (``attach_2022_demand.py``) that did the same for a fixed
2019-01-19..25 / 2022-01-19..25 week; this version aligns by timestamp
instead of raw row position, so it works for any snapshot window.

Production
----------
The HQ source-mix categories are mapped onto the model's generator
carriers (see ``CARRIER_MAP`` below -- adjust it once you can see the
network's actual carriers with ``--list-carriers``). Within each HQ
category, generators keep their existing relative shape (their current
availability profile, or nameplate capacity for carriers with no time
series) and are rescaled so the group's total exactly matches the real
HQ MW value at each snapshot. By default this also pins ``p_min_pu`` to
``p_max_pu`` (a "must run at the historical level" / forced-dispatch
network, matching real 2022 operation rather than leaving generation to
be re-optimized) -- pass ``--no-force-dispatch`` to only set the upper
bound (``p_max_pu``) and leave dispatch free.

This only calibrates the *carrier-level* mix -- it does not (yet) assign
real individual plants (names, exact capacities, bus locations). That
comes later once the generator list is provided; this script only
rescales whatever generators the workflow already put on the network.

Usage
-----
    python network/attach_2022_data.py --list-carriers
    python network/attach_2022_data.py --network networks/elec.nc
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd
import pypsa

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec.nc")
DEFAULT_DEMAND = os.path.join(NETWORK_DIR, "2022-demande-electricite-quebec.xlsx")
DEFAULT_SOURCES = os.path.join(NETWORK_DIR, "2022-sources-electricite-quebec.csv")

# HQ source-mix category -> candidate model generator carriers.
# Quebec 2022 reality: hydro is ~93% of supply, wind a few %, thermal and
# solar close to negligible (see 2022-sources-electricite-quebec.csv
# summary stats -- Thermique averages ~1 MW, Solaire is essentially new).
# Verify / adjust against your model with --list-carriers before relying
# on this for Thermique/Autres, since PyPSA-Earth's conventional carrier
# set may not line up 1:1 with HQ's own bucketing.
CARRIER_MAP = {
    "Hydraulique": ["hydro", "ror", "PHS"],
    "Eolien": ["onwind", "offwind-ac", "offwind-dc"],
    "Solaire": ["solar"],
    "Thermique": ["OCGT", "CCGT", "oil", "coal", "lignite"],
    "Autres": ["biomass", "geothermal"],
}


def align_to_snapshots(
    df: pd.DataFrame,
    date_col: str,
    n_snapshots: pd.DatetimeIndex,
    shift: pd.Timedelta,
) -> pd.DataFrame:
    """Shift interval-ending timestamps back to interval-beginning and
    reindex onto the network's snapshots, raising if any are missing."""
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col]) - shift
    out = out.set_index(date_col).sort_index()
    aligned = out.reindex(n_snapshots)

    missing = aligned.isna().any(axis=1)
    if missing.any():
        first, last = n_snapshots[missing][0], n_snapshots[missing][-1]
        raise ValueError(
            f"{missing.sum()} of {len(n_snapshots)} snapshots have no matching "
            f"data after alignment (e.g. {first} .. {last}). The source file "
            "may not cover this snapshot window, or the interval-ending shift "
            "assumption is wrong for this file."
        )
    return aligned


def attach_demand(n: pypsa.Network, demand_path: str) -> None:
    print(f"\n=== Demand: {os.path.basename(demand_path)} ===")

    old_total = n.loads_t.p_set.sum(axis=1)
    print(
        f"  Old (synthetic) total demand: mean={old_total.mean():.0f} MW  "
        f"min={old_total.min():.0f}  max={old_total.max():.0f}"
    )

    # Per-snapshot per-bus shares from the currently attached profile.
    shares = n.loads_t.p_set.div(old_total, axis=0)

    dem = pd.read_excel(demand_path)
    dem.columns = [c.strip() for c in dem.columns]
    aligned = align_to_snapshots(
        dem[["Date", "Moyenne (MW)"]],
        "Date",
        n.snapshots,
        shift=pd.Timedelta(hours=1),
    )
    new_total = aligned["Moyenne (MW)"]
    print(
        f"  New (real 2022) total demand: mean={new_total.mean():.0f} MW  "
        f"min={new_total.min():.0f}  max={new_total.max():.0f}"
    )

    n.loads_t.p_set = shares.mul(new_total, axis=0)

    check_total = n.loads_t.p_set.sum(axis=1)
    max_diff = (check_total - new_total).abs().max()
    print(f"  Verification: max |new total - target| = {max_diff:.6f} MW")


def attach_production(
    n: pypsa.Network,
    sources_path: str,
    carrier_map: dict,
    force_dispatch: bool = True,
) -> None:
    print(f"\n=== Production: {os.path.basename(sources_path)} ===")

    src = pd.read_csv(sources_path)
    src.columns = [c.strip() for c in src.columns]
    value_cols = [c for c in carrier_map if c in src.columns]
    aligned = align_to_snapshots(
        src[["Date"] + value_cols], "Date", n.snapshots, shift=pd.Timedelta(minutes=30)
    )

    p_nom = n.generators["p_nom"]

    for category, carriers in carrier_map.items():
        if category not in aligned.columns:
            print(f"  [skip] '{category}' not found in {sources_path}")
            continue

        gens = n.generators.index[n.generators["carrier"].isin(carriers)]
        if len(gens) == 0:
            print(
                f"  [skip] '{category}' -> {carriers}: no matching generators "
                "in the network (check --list-carriers and adjust CARRIER_MAP)"
            )
            continue

        target = aligned[category]  # real HQ MW for this category, per snapshot

        # Build each generator's current availability profile in MW: use the
        # time-varying p_max_pu if it has one, else its static p_max_pu
        # (typically 1.0 for dispatchable thermal) broadcast across time.
        static_pmax = n.generators.loc[gens, "p_max_pu"]
        profile_mw = pd.DataFrame(
            np.outer(np.ones(len(n.snapshots)), static_pmax.values * p_nom[gens].values),
            index=n.snapshots,
            columns=gens,
        )
        if gens.isin(n.generators_t.p_max_pu.columns).any():
            ts_gens = gens[gens.isin(n.generators_t.p_max_pu.columns)]
            profile_mw[ts_gens] = n.generators_t.p_max_pu[ts_gens].mul(
                p_nom[ts_gens], axis=1
            )

        group_total = profile_mw.sum(axis=1)

        # Where the group has no existing profile at all (group_total == 0)
        # but real production is non-zero, fall back to a capacity-weighted
        # split instead of leaving it at zero.
        zero_mask = group_total == 0
        if zero_mask.any():
            cap_weights = p_nom[gens] / p_nom[gens].sum()
            fallback = pd.DataFrame(
                np.outer(np.ones(zero_mask.sum()), cap_weights.values),
                index=n.snapshots[zero_mask],
                columns=gens,
            )
            profile_mw.loc[zero_mask, gens] = fallback
            group_total = profile_mw.sum(axis=1)

        shares = profile_mw.div(group_total.replace(0, np.nan), axis=0).fillna(
            1.0 / len(gens)
        )
        new_profile_mw = shares.mul(target, axis=0)

        new_pmax_pu = new_profile_mw.div(p_nom[gens], axis=1)
        n_over = (new_pmax_pu > 1.0).to_numpy().sum()
        if n_over:
            print(
                f"  [warn] '{category}': {n_over} generator-snapshots exceed "
                "nameplate p_nom (real output > model capacity) -- clipped to "
                "1.0. Model capacities are PyPSA-Earth estimates; replace them "
                "once real plant capacities are available."
            )
        new_pmax_pu = new_pmax_pu.clip(upper=1.0)

        for g in gens:
            n.generators_t.p_max_pu[g] = new_pmax_pu[g]
            if force_dispatch:
                n.generators_t.p_min_pu[g] = new_pmax_pu[g]

        print(
            f"  '{category}' -> {len(gens)} generators ({sorted(set(n.generators.loc[gens, 'carrier']))}): "
            f"target mean={target.mean():.0f} MW, model mean={new_profile_mw.sum(axis=1).mean():.0f} MW"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--demand", default=DEFAULT_DEMAND)
    parser.add_argument("--sources", default=DEFAULT_SOURCES)
    parser.add_argument(
        "--output",
        default=None,
        help="Where to save. Default: <network>_2022data.nc next to --network.",
    )
    parser.add_argument(
        "--in-place", action="store_true", help="Overwrite --network instead."
    )
    parser.add_argument("--skip-demand", action="store_true")
    parser.add_argument("--skip-production", action="store_true")
    parser.add_argument(
        "--no-force-dispatch",
        action="store_true",
        help="Only set p_max_pu (availability cap); leave p_min_pu alone "
        "instead of pinning generators to the historical level.",
    )
    parser.add_argument(
        "--list-carriers",
        action="store_true",
        help="Print the network's generator carriers and exit (no data attached).",
    )
    args = parser.parse_args()

    if not os.path.exists(args.network):
        raise FileNotFoundError(
            f"Network not found at {args.network}. Build it first "
            "(e.g. `snakemake -j 1 add_electricity`)."
        )

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)
    print(f"  Buses: {len(n.buses)}  Generators: {len(n.generators)}  Snapshots: {len(n.snapshots)}")
    print(f"  Snapshot range: {n.snapshots[0]} .. {n.snapshots[-1]}")

    if args.list_carriers:
        print("\nGenerator carriers in this network:")
        print(n.generators["carrier"].value_counts())
        return

    if not args.skip_demand:
        attach_demand(n, args.demand)

    if not args.skip_production:
        attach_production(
            n, args.sources, CARRIER_MAP, force_dispatch=not args.no_force_dispatch
        )

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_2022data{ext}"

    n.export_to_netcdf(output)
    print(f"\nSaved -> {output}")


if __name__ == "__main__":
    main()
