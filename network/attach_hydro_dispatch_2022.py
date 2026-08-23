# -*- coding: utf-8 -*-
"""
attach_hydro_dispatch_2022.py

Replace atlite's hydro-basin inflow (resources/renewable_profiles/profile_hydro.nc,
already wired into networks/elec_real_generators.nc by attach_real_generators.py)
with a calibration against Hydro-Quebec's real 2022 aggregate hydro dispatch.

Why: atlite's hydrological-basin inflow model runs systematically low in
winter (Quebec's rivers genuinely have low natural January inflow -- but
HQ's reservoirs bank spring/summer inflow and release it through winter
for the seasonal demand peak, which a naive current-month inflow signal
can't see). Left as-is, this caused large hydro underutilization and
didn't match real historical dispatch for winter snapshots.

Method (same approach used in this project before rebuilding it):
1. Take the real, historical 2022 hourly "Hydraulique" (hydro) dispatch
   total from network/2022-sources-electricite-quebec.csv.
2. Split that total between the ror and hydro (reservoir) carriers by
   their share of combined installed capacity (not by atlite's inflow
   shape at all -- that's the whole point, since the shape is what's
   suspect).
3. Within each carrier, every plant gets the SAME implied per-unit
   utilization rate, since the split is capacity-proportional: this
   collapses to one formula for the whole hydro fleet --
       p_max_pu(t) = Hydraulique(t) / (ror_total_capacity + hydro_total_capacity)
   applied uniformly to every ror generator and hydro storage unit
   (each generator's own MW share still follows from its own p_nom).
4. ror has no reservoir, so p_min_pu is set equal to p_max_pu there
   (forced dispatch -- water not used is water spilled, matching real
   run-of-river operation). Hydro (reservoir) storage units instead get
   this same real trajectory fed in as `inflow` (their energy input,
   in MW) and are left free to dispatch anywhere between 0 and their
   full nameplate power (p_min_pu/p_max_pu, real reservoir sizing and
   initial state of charge set in attach_real_generators.py) --
   so the optimizer decides when to draw down banked reservoir energy
   vs. match inflow directly, same as the real plant can.

This assumes uniform utilization across the whole hydro fleet at every
hour -- a simplification (real dispatch varies plant-to-plant with
individual reservoir levels and river conditions), but it directly fixes
the "doesn't match real historical totals" problem, which matters more
for this project than plant-level dispatch realism.

Usage
-----
    python network/attach_hydro_dispatch_2022.py --network networks/elec_real_generators.nc
"""
import argparse
import os

import pandas as pd
import pypsa

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_real_generators.nc")
SOURCES_CSV = os.path.join(NETWORK_DIR, "2022-sources-electricite-quebec.csv")


def align_to_snapshots(df: pd.DataFrame, date_col: str, snapshots: pd.DatetimeIndex, shift: pd.Timedelta) -> pd.DataFrame:
    """Shift interval-ending timestamps back to interval-beginning and
    reindex onto the network's snapshots (see attach_2022_data.py, same
    convention: this source file is half-hour-ending)."""
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col]) - shift
    # DST fall-back creates one duplicate timestamp per year (e.g. Nov 6
    # 2022); irrelevant to our snapshot window but breaks reindex() below.
    out = out.drop_duplicates(subset=date_col, keep="first")
    out = out.set_index(date_col).sort_index()
    aligned = out.reindex(snapshots)
    missing = aligned.isna().any(axis=1)
    if missing.any():
        raise ValueError(
            f"{missing.sum()} of {len(snapshots)} snapshots have no matching data in "
            f"{SOURCES_CSV} after alignment -- source file may not cover this window."
        )
    return aligned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--sources", default=SOURCES_CSV)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)
    snapshots = n.snapshots

    ror = n.generators[n.generators.carrier == "ror"]
    hydro = n.storage_units[n.storage_units.carrier == "hydro"]
    ror_cap = ror.p_nom.sum()
    hydro_cap = hydro.p_nom.sum()
    combined_cap = ror_cap + hydro_cap
    print(f"ror capacity: {ror_cap:.0f} MW, hydro (reservoir) capacity: {hydro_cap:.0f} MW, combined: {combined_cap:.0f} MW")

    src = pd.read_csv(args.sources)
    src.columns = [c.strip() for c in src.columns]
    aligned = align_to_snapshots(src[["Date", "Hydraulique"]], "Date", snapshots, shift=pd.Timedelta(minutes=30))
    real_hydro_mw = aligned["Hydraulique"]
    print(
        f"Real 2022 Hydraulique dispatch over this window: mean={real_hydro_mw.mean():.0f} MW, "
        f"min={real_hydro_mw.min():.0f}, max={real_hydro_mw.max():.0f}"
    )

    uniform_cf = real_hydro_mw / combined_cap
    n_over = (uniform_cf > 1.0).sum()
    if n_over:
        print(
            f"  [warn] {n_over}/{len(uniform_cf)} snapshots imply >100% utilization of combined "
            f"hydro capacity (real dispatch exceeds this network's modeled hydro capacity at "
            f"those hours) -- clipped to 1.0, so those hours will underrepresent real dispatch."
        )
    uniform_cf = uniform_cf.clip(upper=1.0)
    print(f"Implied uniform capacity factor: mean={uniform_cf.mean():.3f}, min={uniform_cf.min():.3f}, max={uniform_cf.max():.3f}")

    for gen in ror.index:
        n.generators_t.p_max_pu[gen] = uniform_cf
        n.generators_t.p_min_pu[gen] = uniform_cf

    for su in hydro.index:
        p_nom = n.storage_units.at[su, "p_nom"]
        n.storage_units_t.inflow[su] = uniform_cf * p_nom

    print(
        f"Forced dispatch (p_min_pu = p_max_pu = uniform_cf) on {len(ror)} ror generators "
        f"(no reservoir -- unused water is spilled). Fed the same real trajectory as `inflow` "
        f"into {len(hydro)} hydro storage units, left free to dispatch 0..p_nom against their "
        f"own reservoir state of charge (set in attach_real_generators.py) instead of forcing "
        f"their power output to track the fleet-wide average too."
    )

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_hydro2022{ext}"
    n.export_to_netcdf(output)
    print(f"\nSaved -> {output}")


if __name__ == "__main__":
    main()
