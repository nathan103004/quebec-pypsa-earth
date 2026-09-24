# -*- coding: utf-8 -*-
"""
scale_dispatch.py

Uniformly scales every load's p_set and every generator/storage unit's
p_set by the same factor, on an already-solved network (e.g. elec_735kv.nc).
Loads and dispatch are scaled together, so total demand still exactly
equals total dispatch at every snapshot -- this is a controlled sensitivity
test (holding the real dispatch *pattern* fixed and only scaling its
level), not a re-optimized LOPF solve at a different demand level.

Usage
-----
    python network/scale_dispatch.py --network networks/elec_735kv.nc --factor 0.86 --output networks/elec_735kv_scaled86.nc
"""
import argparse

import pypsa


def scale_dispatch(n: pypsa.Network, factor: float) -> None:
    n.loads_t.p_set = n.loads_t.p_set * factor
    n.generators_t.p_set = n.generators_t.p_set * factor
    n.storage_units_t.p_set = n.storage_units_t.p_set * factor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True)
    parser.add_argument("--factor", type=float, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    total_before = n.loads_t.p_set.sum().sum()
    scale_dispatch(n, args.factor)
    total_after = n.loads_t.p_set.sum().sum()
    print(f"Scaled loads + generator/storage dispatch by {args.factor:.3f}x "
          f"(total demand: {total_before:.0f} -> {total_after:.0f} MWh over the window)")

    n.export_to_netcdf(args.output)
    print(f"Saved -> {args.output}")


if __name__ == "__main__":
    main()
