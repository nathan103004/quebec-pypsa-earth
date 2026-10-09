# Quebec Transmission Grid Model

A PyPSA model of Quebec's transmission grid: OpenStreetMap topology from PyPSA-Earth, line
parameters from Hydro-Québec's line-characteristics table, and 2022 Hydro-Québec generation and
demand data. A reduction pipeline produces three networks at different levels of detail.

## Quick facts

| | Unreduced (`elec_full.nc`) | 315kV (`elec_solved.nc`) | 735kV backbone (`elec_735kv_shunt.nc`) |
|---|---|---|---|
| Buses | 4,013 (all of Canada) | 205 | 58 |
| AC lines | 4,546 | 276 | 109 |
| DC links | 47 | 5 | 0 |
| Transformers | 796 | 22 | 0 |
| Loads | 3,192 | 647 | 58 |
| Generators (real) | 68 | 72 | 23 |
| Storage units | 18 | 22 | 11 |
| Shunts | 0 | 0 | 5 capacitors, 11 reactors |
| LOPF | not run | 0% load shed | carried from 315kV |
| AC power flow | not run | 1/168 | **168/168** |

One week, 2022-01-01 to 2022-01-07, 168 hourly snapshots. Mean demand 28,871 MW, calibrated to the
real mean of that week (28,870 MW). See [docs/NETWORKS.md](docs/NETWORKS.md) for what each network is
for and [docs/POWER_FLOW.md](docs/POWER_FLOW.md) for results.

## Workflow

```
PyPSA-Earth (snakemake, up to add_electricity)        -> elec.nc
  | data fixes on the raw network (see below)
  v
elec_full.nc            unreduced, every voltage level
  | reduce_voltage_network.py                         -> keep >=315kV in Quebec's region
  | fix_parallel_circuits_v2.py                       -> real circuit counts on >=315kV corridors
  | generation-capacity correction (manual)           -> see docs/GENERATORS.md
  v
elec_reduced.nc
  | run_lopf_main_island.py                           -> largest island, real dispatch ceilings, LOPF
  v
elec_solved.nc          315kV working network         (DC PF matches LOPF; AC PF diverges)
  | reduce_to_735kv.py                                -> 735/765kV backbone, slack = 114 ror
  v
elec_735kv.nc           AC PF 89/168
  | correct_sending_end_circuits.py                   -> 3 circuits on 5 sending-end corridors
  |                                                      AC PF 110/168
  | apply_series_compensation.py                      -> 70% on 15 lines
  |                                                      AC PF 168/168
  | add_shunt_impedance.py                            -> shunt reactors and capacitors
  v
elec_735kv_shunt.nc     AC PF 168/168, 0.966-1.059 pu
```

**Data fixes on the raw network.** These run on PyPSA-Earth's output before the 315kV reduction.
The order below is reconstructed from each script's default input and output file names; check
each script's docstring before running it. `networks_current/elec_full.nc` already includes them.

1. `regional_demand.py`, then `rescale_demand_regional.py`: real HQ regional demand.
2. `fix_line_lengths_from_geometry.py`: line lengths that contradict their own geometry.
3. `build_hq_contracts_data.py`, then `attach_real_generators.py`: real HQ and IPP generators.
4. `attach_hydro_dispatch_2022.py`: real 2022 hourly hydro dispatch ceilings.
5. `add_churchill_falls_tie.py`: the 735kV Churchill Falls interconnection, missing from OSM.
6. `apply_hq_line_characteristics.py`: Hydro-Québec r/x/b on 315/345/735/765kV lines.

**Generation-capacity correction (manual).** Add the missing hydro stations and scale wind and
solar to Hydro-Québec's official totals. The data and the method are in
[docs/GENERATORS.md](docs/GENERATORS.md) (`hq_hydro_stations_official.csv`,
`hq_major_facilities_2023.csv`). No script does this step.

**Order matters on the 735kV network.** Run `correct_sending_end_circuits.py` before
`apply_series_compensation.py`: the circuit correction recomputes x from the per-km rate, which
would remove compensation applied before it. Compensation is computed from the uncompensated per-km
reactance, so running it twice does not stack. Do not apply series compensation before the 315kV
reduction.

## Running it

Requires the `pypsa-earth` conda environment (`envs/environment.yaml` at the repo root, PyPSA
0.30.x; newer PyPSA releases removed APIs these scripts use, such as `mremove`). Every script
defaults to `network/networks_current/`. From the repo root:

```
python network/run_lopf_main_island.py --network network/networks_current/elec_reduced.nc --output network/networks_current/elec_solved.nc
python network/run_pf.py --network network/networks_current/elec_solved.nc --method lpf
python network/reduce_to_735kv.py
python network/run_pf.py --network network/networks_current/elec_735kv.nc --method pf --pv-min-capacity 0 --pv-max-load-ratio 1
python network/correct_sending_end_circuits.py --network network/networks_current/elec_735kv.nc --in-place
python network/apply_series_compensation.py --network network/networks_current/elec_735kv.nc --in-place
python network/run_pf.py --network network/networks_current/elec_735kv.nc --method pf --pv-min-capacity 0 --pv-max-load-ratio 1
python network/add_shunt_impedance.py --network network/networks_current/elec_735kv.nc --output network/networks_current/elec_735kv_shunt.nc
python network/run_pf.py --network network/networks_current/elec_735kv_shunt.nc --method pf --pv-min-capacity 0 --pv-max-load-ratio 1
python network/summarize_735kv_acpf.py
```

`run_pf.py` writes its result next to its input as `*_lpf.nc` / `*_pf.nc`.

Outputs:

- `summarize_735kv_acpf.py` -> `networks_current/summary_735kv_acpf.csv`: the three
  reactive-support cases side by side.
- `export_to_matpower.py --network ... --output ...` -> MATPOWER case for an independent AC PF in
  MATLAB (`quebec_735kv.m`, `quebec_main_island.m`; shunts carried as bus `Bs`).
- `visualize_ac_pf_map.py`, `visualize_solved_network_map.py`, `visualize_real_network_map.py` ->
  HTML + PNG maps.

On machines where Windows Application Control blocks the netCDF4 DLL, open and save networks with
xarray's `h5netcdf` engine instead.

## Documentation

- [docs/NETWORKS.md](docs/NETWORKS.md): the three network tiers and what each is for
- [docs/COMPONENTS.md](docs/COMPONENTS.md): how each PyPSA component type is modeled
- [docs/GENERATORS.md](docs/GENERATORS.md): generation fleet, capacity correction, dispatch
- [docs/POWER_FLOW.md](docs/POWER_FLOW.md): LOPF, DC PF and AC PF results
- [docs/ASSUMPTIONS_AND_LIMITATIONS.md](docs/ASSUMPTIONS_AND_LIMITATIONS.md): generic assumptions
  and known gaps, including open work
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md): where the real data comes from
