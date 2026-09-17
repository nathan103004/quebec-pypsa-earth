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
this pipeline reaches. Demand: 32,421 MW mean over the solved week (2022-01-01 to 2022-01-07,
calibrated against real whole-January-2022 system demand). **0% load shed.**

This is also the network AC power flow would need to converge on for genuinely representative
contingency analysis. It does not: **0/168** under full nonlinear AC PF, and unlike the 735kV
network below, reducing demand doesn't help either (still 0/168 at 85% of current demand) -- its
failure mode is structurally different and not yet understood. See
[POWER_FLOW.md](POWER_FLOW.md).

## 3. 735kV backbone (`elec_735kv.nc`)

58 buses, 109 lines. A further reduction from the solved 315kV network down to just the 735/765kV
backbone (`reduce_to_735kv.py`), aggregating everything else onto backbone buses by graph
shortest-path (real cumulative line length, not straight-line distance). Carries forward the
already-solved dispatch from `elec_solved.nc` as fixed injections rather than re-optimizing.

Purpose-built for AC power flow tractability: small and heavily meshed, so it was expected to
converge more readily than the 315kV network. It gets closer but still doesn't fully converge at
current demand (**50/168**) -- see [POWER_FLOW.md](POWER_FLOW.md) for the investigation, including
a real fix at 85% of current demand (168/168) and a likely contributing data-quality issue
(straight-line load reassignment dumping demand from up to 281km away onto one weak bus).
