# -*- coding: utf-8 -*-
"""
add_shunt_reactors.py

Fixes light-load overvoltage on the 735kV network (voltages up to 1.13pu
observed at several buses -- the Ferranti effect: long lines' own shunt
charging generates more reactive power than a lightly-loaded system can
absorb). Modeled as switched shunt reactors: a generator with a fixed
negative q_set, active only when total system demand is below
SWITCH_THRESHOLD_MW -- not a permanently-on shunt.

Switching (not a permanent fixed shunt) matters here specifically: buses
308 and 312 need reactive *support* under heavy load (the voltage-collapse
fix, see apply_series_compensation.py) but reactive *absorption* under
light load. A fixed shunt reactor there would fight the collapse fix
during exactly the hours it's needed. Real EHV substations handle this the
same way -- reactor banks switched in/out on a schedule, not permanently
connected. SWITCH_THRESHOLD_MW (mean weekly demand) is a simple, real-data
proxy for that schedule, not a measured HQ switching plan.

Per-bus rating: iteratively tuned against this project's own AC PF results
so each bus's worst observed overvoltage lands near 1.05pu, not derived
from a target-voltage solve (a real switched bank doesn't do that either --
it's a fixed increment, not a continuously-variable device like the PV-bus
synchronous condenser approach already rejected for the collapse fix).

Usage
-----
    python network/add_shunt_reactors.py --network networks/elec_735kv.nc --output networks/elec_735kv_reactors.nc
"""
import argparse

import pypsa

# bus -> reactor rating (MVAr absorbed when active), tuned against observed overvoltage severity
REACTOR_RATINGS = {
    "469": 1300.0,
    "310": 1800.0,
    "3319": 1800.0,
    "345": 1200.0,
    "312": 2000.0,
    "308": 2500.0,
    "150": 900.0,
    "2944": 1000.0,
}

# bus 469's own voltage barely correlates with total system demand (near-constant
# ~1.125pu all week, unlike the others, which are clearly Ferranti/light-load driven) --
# a demand-based switch is the wrong trigger for it, so it runs permanently on instead.
ALWAYS_ON = {"469"}


def add_reactors(n: pypsa.Network, ratings=None) -> None:
    """Threshold is this network's OWN mean demand, not a fixed MW value --
    keeps the switching behavior consistent whether run on the 100%-demand
    network or a demand-scaled sensitivity test."""
    ratings = ratings or REACTOR_RATINGS
    demand = n.loads_t.p_set.sum(axis=1)
    threshold = demand.mean()
    switched_on = (demand < threshold).astype(float)

    for b, mvar in ratings.items():
        if b not in n.buses.index:
            print(f"  [warn] bus {b} not present, skipping")
            continue
        name = f"{b} shunt-reactor"
        n.add("Generator", name, bus=b, carrier="shunt_reactor", p_nom=0.0, control="PQ")
        if b in ALWAYS_ON:
            n.generators_t.q_set[name] = -mvar
        else:
            n.generators_t.q_set[name] = -mvar * switched_on
    n_switched = len(ratings) - len(ALWAYS_ON & set(ratings))
    print(f"Added {len(ratings)} shunt reactors: {n_switched} switched (active when demand < "
          f"{threshold:.0f} MW, this network's own mean -- {switched_on.sum():.0f} / "
          f"{len(n.snapshots)} snapshots), {len(ALWAYS_ON & set(ratings))} always-on ({sorted(ALWAYS_ON)})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    add_reactors(n)

    n.export_to_netcdf(args.output)
    print(f"Saved -> {args.output}")


if __name__ == "__main__":
    main()
