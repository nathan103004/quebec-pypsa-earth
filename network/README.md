# Quebec Transmission Grid Model

A PyPSA model of Quebec's transmission grid: real OpenStreetMap topology, real 2022 Hydro-Québec
generation and demand data, and a reduction pipeline that produces three networks at different
levels of detail for different purposes.

## Quick facts

| | Unreduced | 315kV (main) | 735kV backbone |
|---|---|---|---|
| Buses | 4,013 | 208 | 58 |
| Lines | 4,546 | 276 | 109-115* |
| Generators (real) | 68 | 65 | 27 |
| Storage units | 18 | 18 | 10 |
| Load served | -- | 0% shed | -- |
| AC power flow | not attempted | not yet converged | 168/168 converged |

*109 in the network as currently reduced from the live pipeline; 115 in the network validated
against full AC power flow (see [POWER_FLOW.md](docs/POWER_FLOW.md) for why these differ).

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
      |  run_pf.py --method pf    -- full nonlinear AC power flow
      v
elec_735kv_pf.nc
      |  export_to_matpower.py    -- optional MATPOWER cross-check
```

Each script's docstring describes its own inputs, outputs, and method. Debugging narrative --
what broke, how it was diagnosed, and what fixed it -- lives separately in
[docs/DEBUGGING_HISTORY.md](docs/DEBUGGING_HISTORY.md) so the scripts themselves stay readable.

## Documentation

- [docs/NETWORKS.md](docs/NETWORKS.md) -- the three network tiers, what each is for
- [docs/GENERATORS.md](docs/GENERATORS.md) -- generation and storage fleet, real vs. synthetic
- [docs/POWER_FLOW.md](docs/POWER_FLOW.md) -- LOPF, DC PF, and AC PF results
- [docs/ASSUMPTIONS_AND_LIMITATIONS.md](docs/ASSUMPTIONS_AND_LIMITATIONS.md) -- every generic
  assumption used, and known gaps in the data
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) -- where the real data comes from
- [docs/DEBUGGING_HISTORY.md](docs/DEBUGGING_HISTORY.md) -- issues found and fixed during
  development

## Running the pipeline

Requires the `pypsa-earth` conda environment (`envs/environment.yaml` at the repo root; pinned to
PyPSA 0.30.x -- newer PyPSA releases have removed APIs these scripts depend on, e.g. `mremove`).

```
python network/run_lopf_main_island.py --network networks/elec_reduced.nc
python network/run_pf.py --network networks/elec_solved.nc --method lpf
python network/reduce_to_735kv.py --network networks/elec_solved.nc
python network/run_pf.py --network networks/elec_735kv.nc --method pf --pv-min-capacity 0 --pv-max-load-ratio 1
```
