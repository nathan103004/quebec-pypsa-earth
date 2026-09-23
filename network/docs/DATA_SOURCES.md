# Data sources

## Topology

- **OpenStreetMap**, via PyPSA-Earth's own extraction pipeline (`base.nc` / `elec.nc` /
  `elec_full.nc`) -- buses, lines, transformers, voltage levels, geographic coordinates.
- **GADM administrative boundaries** (`data/gadm/gadm41_CAN/gadm41_CAN.gpkg`) -- used to scope
  the reduction to Quebec's own provincial polygon plus a buffer around the Churchill Falls
  interconnection.

## Generation and demand (real, 2022)

All under `network/`:

- `2022-sources-electricite-quebec.csv` -- Hydro-Québec's published hourly generation by source
  (`Hydraulique`, `Thermique`, etc.) for 2022. Used to derive real dispatch ceilings for
  run-of-river hydro and OCGT.
- `hq_hydro_stations_official.csv` -- official list of Hydro-Québec hydro generating stations
  (name, capacity, location), used to place and size real `ror` generators and hydro storage
  units.
- `hq_wind_farms.csv` -- real onshore wind farm list (name, capacity, location).
- `hq_major_facilities_2023.csv` -- other major real generation facilities.
- `hq_regional_demand_region_2022-01.csv` / `hq_regional_demand_municipality_2022-01.csv` --
  real regional and municipal electricity consumption, used to replace PyPSA-Earth's synthetic
  population/GDP demand proxy (`regional_demand.py`, `rescale_demand_regional.py`). Matched to
  17 of Quebec's real administrative regions; see
  [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for buses outside that match.
- `historique-demande-electricite-quebec.csv` -- real hourly Hydro-Québec system-wide demand,
  full year 2022. Used to calibrate `DEMAND_SCALE_FACTOR` in `run_lopf_main_island.py` against
  the real whole-January-2022 mean (32,421 MW), and to validate the solved network's demand
  shape/level (correlation 0.979 against this series once timezone-aligned correctly).

## Line electrical parameters

- `hq_line_characteristics_by_voltage.csv` -- Hydro-Quebec's own real line-characteristics table
  (transcribed from an HQ planning document), giving exact per-km positive-sequence r/x/b by
  voltage. Applied by `apply_hq_line_characteristics.py` to 315/345kV and 735/765kV lines
  specifically (345 treated as 315-tier, 765 as 735-tier) -- the two voltage tiers this project's
  reduced networks actually keep as topology. This is the most authoritative source available for
  those two tiers, and supersedes the interpolated estimate below wherever it applies.
- `overhead_line_parameters_by_voltage.csv` -- real 60Hz per-km r/x/b by voltage class (Hypersim/
  EMTP knowledge-base table, Menard 2023), log-log interpolated across voltages by
  `fix_line_reactance_hypersim.py` and applied to every AC line >= 220kV *not* covered by the more
  specific HQ table above. Also used in `reduce_to_735kv.py` to derive the 735-765kV bridge line's
  reactance from the 735kV fleet's own real reactance-per-km, and in `st_clair.py`'s thermal
  (`s_nom`) calculation.
- Below 220kV: no real reactance data source exists in this project. Lines there still use
  PyPSA-Earth's generic default type-matching (`config.default.yaml`'s `lines.ac_types`, itself
  sourced from a German textbook, not Quebec-specific) -- see
  [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md). This doesn't affect the
  reduced/solved networks' own topology (they only keep >=315kV lines), only the unreduced
  `elec_full.nc` reference map.
- `st_clair_curve.csv` -- the St Clair curve (a standard planning-level thermal-limit-vs-length
  relationship for overhead transmission lines), used in `st_clair.py` to derive line thermal
  capacity (`s_nom`) from real length and voltage.

## Costs

- `resources/costs_2030_elec.csv` -- PyPSA-Earth's own default 2030 technology cost projections
  (not Quebec-specific). Used only to assign relative marginal costs between carriers for LOPF
  dispatch ordering, not as a claim about real Quebec generation costs.

## Not yet real / synthetic

- **Load-shedding generators** -- a modeling device (VOLL placeholders), not real capacity.
- **Reactive power data for AC PF** (power factors, transformer X/R, shunt capacitor sizing) --
  no real reactive/voltage data source was available; all generic, documented assumptions (see
  [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md)).
- **Solar generators** -- present (3 units, 12 MW total) but minor; capacity factors from
  PyPSA-Earth's standard renewable resource pipeline, not a Quebec-specific solar dataset.
