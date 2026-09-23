# Quebec Transmission Grid Model

A PyPSA model of Quebec's transmission grid: OpenStreetMap topology, modeled with 2022 Hydro-Québec
generation and demand data, and a reduction pipeline that produces three networks at different
levels of detail for different purposes.

## Quick facts

| | Unreduced (Entire Canada) | 315kV (main) | 735kV backbone |
|---|---|---|---|
| Buses | 4,013 | 205 | 58 |
| Lines | 4,546 | 276 | 109 |
| Generators (real) | 68 | 65 | 24 |
| Storage units | 18 | 18 | 10 |
| Load served | -- | 0% shed | -- |
| AC power flow | not attempted | 0/168 | 157/168 (**168/168 at 82% of demand**) |

The 735kV backbone converges fully once demand is reduced to 82% of its current level (up from 62%
before 50% series compensation was added on the three identified weak corridors -- see
[POWER_FLOW.md](docs/POWER_FLOW.md)). The 315kV network doesn't respond to demand reduction
the same way; see [POWER_FLOW.md](docs/POWER_FLOW.md) for the full investigation.

See [docs/NETWORKS.md](docs/NETWORKS.md) for what each network is for and how they relate.

## Pipeline

```
elec_full.nc  (raw OSM extract, all of Canada)
      |  reduce_voltage_network.py  -- keep >=315kV backbone + Churchill Falls, in Quebec's region
      v
elec_reduced.nc
      |  run_lopf_main_island.py  -- extract main island, real dispatch ceilings, solve LOPF
      v
elec_solved.nc  (the main working network)
      |  run_pf.py --method lpf   -- DC power flow check
      |  reduce_to_735kv.py       -- reduce to 735/765kV backbone only
      v
elec_735kv.nc
      |  run_pf.py --method pf    -- full nonlinear AC power flow (does not fully converge at
      v                              current demand -- see POWER_FLOW.md)
elec_735kv_pf.nc
      |  export_to_matpower.py    -- optional MATPOWER cross-check
```

## Documentation

- [docs/NETWORKS.md](docs/NETWORKS.md) -- the three network tiers, what each is for
- [docs/GENERATORS.md](docs/GENERATORS.md) -- generation and storage fleet, real vs. synthetic
- [docs/POWER_FLOW.md](docs/POWER_FLOW.md) -- LOPF, DC PF, and AC PF results
- [docs/ASSUMPTIONS_AND_LIMITATIONS.md](docs/ASSUMPTIONS_AND_LIMITATIONS.md) -- every generic
  assumption used, and known gaps in the data
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) -- where the real data comes from

## Running the pipeline

Requires the `pypsa-earth` conda environment (`envs/environment.yaml` at the repo root; pinned to
PyPSA 0.30.x -- newer PyPSA releases have removed APIs these scripts depend on, e.g. `mremove`).

```
python network/run_lopf_main_island.py --network networks/elec_reduced.nc
python network/run_pf.py --network networks/elec_solved.nc --method lpf
python network/reduce_to_735kv.py --network networks/elec_solved.nc
python network/run_pf.py --network networks/elec_735kv.nc --method pf --pv-min-capacity 0 --pv-max-load-ratio 1
```
