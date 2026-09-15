# Network versions

Three distinct topologies, each built for a different purpose -- not three versions of the same
thing, and not interchangeable.

## 1. Unreduced (`elec_full.nc`)

The complete raw topology from PyPSA-Earth's OSM extraction, covering every voltage level, all of
Canada. 4,013 buses, 4,546 lines, 796 transformers, 47 links.

Ground truth and the ancestor of both reduced networks below. Too large, and covers too much
outside Quebec's actual grid, to run LOPF or power flow on directly. Used as the source
`reduce_voltage_network.py` reduces from, and for the full-detail reference map
(`visualize_real_network_map.py`).

## 2. 315kV reduced -- the main working network (`elec_reduced.nc` -> `elec_solved.nc`)

208 buses, 276 lines, 23 transformers, 7 links, 65 real generators, 18 storage units, 729 loads.

Built by `reduce_voltage_network.py`: scopes down to Quebec + the Churchill Falls interconnection,
keeps every bus at 315kV or above, and folds every lower-voltage local bus onto its nearest
surviving backbone bus -- no load, generator, or storage unit is dropped, only reassigned.
`run_lopf_main_island.py` then extracts the single largest connected island and solves LOPF on it.

This is the main working network: real 2022 dispatch is solved here, at the finest resolution
this pipeline reaches. Demand: 32,102 MW mean, 41,369 MW peak over the solved week
(2022-01-01 to 2022-01-07). **0% load shed.**

This is also the network AC power flow would need to converge on for genuinely representative
contingency analysis -- and the network on which it has not yet converged (see
[POWER_FLOW.md](POWER_FLOW.md)).

## 3. 735kV backbone (`elec_735kv.nc` -> `elec_735kv_pf.nc`)

58 buses. A further reduction from the solved 315kV network down to just the 735/765kV backbone
(`reduce_to_735kv.py`), aggregating everything else onto backbone buses by graph shortest-path
(real cumulative line length, not straight-line distance). Carries forward the already-solved
dispatch from `elec_solved.nc` as fixed injections rather than re-optimizing.

Purpose-built for AC power flow tractability: small and heavily meshed enough that full nonlinear
convergence is achievable, which the 315kV network has not managed. Trade-off: it's a coarser
representation, so results on it stand in for backbone-level stress, not a substitute for solving
the full 315kV network.

Two variants exist on disk:
- `elec_735kv.nc` (109 lines) -- the current, direct output of the reduction pipeline described
  above, reflecting the same real per-corridor circuit counts as `elec_full.nc`.
- `elec_735kv_pf.nc` (115 lines) -- the network validated against full AC power flow (168/168
  snapshots converged). Built from an earlier point in the pipeline; see
  [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for the known gap between the
  two and why it hasn't yet been closed.
