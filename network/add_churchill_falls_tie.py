# -*- coding: utf-8 -*-
"""
add_churchill_falls_tie.py

Add the Churchill Falls -> Quebec 735 kV interconnection, missing
from the OSM-derived base network (Churchill Falls' bus/465 had exactly
one line, at 66 kV -- nowhere near enough for its 5,428 MW).

Source: OSM relation 4504531 ("Hydro-Quebec High-voltage transport
network - 735 KV"), fetched directly via Overpass
(network/hq_735kv_osm_relation.json) since PyPSA-Earth's own OSM extract
didn't pick these ways up. It contains the  Churchill Falls
substation (735/230/138 kV) and three  parallel 735 kV circuits:

    way        ref     length (km, from OSM route geometry)
    182119140  L7052   226.0
    182119571  L7051   225.9
    182119884  L7053   226.1

...running from Churchill Falls Station (53.529, -63.977) to Poste des
Montagnais (51.893, -65.728) -- which is already, correctly, bus 306 in
this network (v_nom=735, coordinates match to 3 decimal places).

Usage
-----
    python network/add_churchill_falls_tie.py --network networks/elec_real_generators_hydro2022.nc
"""
import argparse
import os
import sys

import pypsa

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(NETWORK_DIR)
sys.path.insert(0, NETWORK_DIR)
from st_clair import load_line_params, st_clair_s_nom_mva  # noqa: E402

DEFAULT_NETWORK = os.path.join(BASE_DIR, "networks", "elec_real_generators_hydro2022.nc")

CHURCHILL_BUS = "465"  # existing bus: a 66 kV local tap near Churchill Falls, left untouched
CHURCHILL_735_BUS = "465-735kv"  # new: the real 735 kV transmission yard at Churchill Falls Station
MONTAGNAIS_BUS = "306"
LINE_TYPE = "Al/St 560/50 4-bundle 750.0"
V_NOM = 735.0

# Churchill Falls Station substation, from the OSM relation (way 323166651)
CHURCHILL_735_LAT = 53.5289404
CHURCHILL_735_LON = -63.9768688

CIRCUITS = {
    "L7051": 225.9,
    "L7052": 226.0,
    "L7053": 226.1,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--output", default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    print(f"Loading network: {args.network}")
    n = pypsa.Network(args.network)

    for bus in (CHURCHILL_BUS, MONTAGNAIS_BUS):
        if bus not in n.buses.index:
            raise ValueError(f"Bus {bus} not found in network -- check it still matches this build.")
    if CHURCHILL_735_BUS in n.buses.index:
        raise ValueError(f"Bus {CHURCHILL_735_BUS} already exists -- has this fix already been applied?")

    # New dedicated 735 kV bus at Churchill Falls Station's real
    # coordinates (copy the standard bus attribute template from an
    # existing 735 kV bus, Montagnais, then override identity/location).
    template = n.buses.loc[MONTAGNAIS_BUS].to_dict()
    template.update(
        x=CHURCHILL_735_LON, y=CHURCHILL_735_LAT,
        lon=CHURCHILL_735_LON, lat=CHURCHILL_735_LAT,
        v_nom=V_NOM, country="CA",
    )
    n.add("Bus", CHURCHILL_735_BUS, **template)
    print(f"Added bus {CHURCHILL_735_BUS} at ({CHURCHILL_735_LAT}, {CHURCHILL_735_LON}), {V_NOM} kV")

    churchill_su = n.storage_units.index[
        (n.storage_units.bus == CHURCHILL_BUS) & (n.storage_units.carrier == "hydro")
    ]
    if len(churchill_su) != 1:
        raise ValueError(f"Expected exactly 1 hydro storage unit at bus {CHURCHILL_BUS}, found {len(churchill_su)}: {list(churchill_su)}")
    n.storage_units.loc[churchill_su[0], "bus"] = CHURCHILL_735_BUS
    print(f"Moved generator '{churchill_su[0]}' from bus {CHURCHILL_BUS} (66 kV) to {CHURCHILL_735_BUS} ({V_NOM} kV)")

    ref = load_line_params()
    for name, length_km in CIRCUITS.items():
        s_nom = st_clair_s_nom_mva(length_km, V_NOM, ref)
        line_name = f"{CHURCHILL_735_BUS}-{MONTAGNAIS_BUS} {name}"
        n.add(
            "Line",
            line_name,
            bus0=CHURCHILL_735_BUS,
            bus1=MONTAGNAIS_BUS,
            type=LINE_TYPE,
            length=length_km,
            num_parallel=1.0,
            s_nom=s_nom,
            s_nom_extendable=False,
        )
        # "v_nom" isn't in PyPSA's own Line schema -- PyPSA-Earth adds it as
        # an extra DataFrame column directly (n.add() silently drops
        # unknown kwargs), so set it the same way here for consistency
        # with every other line in this network.
        n.lines.loc[line_name, "v_nom"] = V_NOM
        print(f"Added {line_name}: {length_km} km, s_nom={s_nom:.0f} MVA (St Clair)")

    n_lines_now = n.lines.index[(n.lines.bus0 == CHURCHILL_735_BUS) | (n.lines.bus1 == CHURCHILL_735_BUS)]
    print(f"\n{CHURCHILL_735_BUS} now has {len(n_lines_now)} line(s):")
    print(n.lines.loc[n_lines_now, ["bus0", "bus1", "v_nom", "length", "s_nom", "num_parallel"]])

    output = args.network if args.in_place else args.output
    if output is None:
        root, ext = os.path.splitext(args.network)
        output = f"{root}_cftie{ext}"
    n.export_to_netcdf(output)
    print(f"\nSaved -> {output}")


if __name__ == "__main__":
    main()
