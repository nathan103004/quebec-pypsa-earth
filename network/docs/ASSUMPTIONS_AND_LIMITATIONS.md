# Assumptions and limitations

Every generic or unmeasured assumption used in this pipeline, with the reasoning behind the
specific value chosen. Documented at the point it's introduced in code as well; this is the
consolidated list.

## Generic assumptions (LOPF / general)

| Assumption | Value | Where | Why |
|---|---|---|---|
| Line security margin (`s_max_pu`) | 1.0 (relaxed from PyPSA-Earth's default 0.7) | `run_lopf_main_island.py` | The 0.7 default reserves 30% headroom for N-1 contingency; this model isn't doing contingency-constrained dispatch, so the margin only produced artificial shedding. |
| Transmission capacity margin (St Clair curve) | x3 on the derived thermal envelope | `run_lopf_main_island.py`, `st_clair.py` | St Clair is a planning-level analytical envelope from real Hypersim-sourced impedance data, not an empirical thermal rating -- treated as a starting point, multiplied up uniformly rather than taken as a hard limit. |
| Transformer thermal capacity | Flat 100,000 MVA | `run_lopf_main_island.py` | Real per-transformer `s_nom` from the OSM extract varies widely and binds at several major junctions carrying 38,000-155,000 MVA of real line capacity -- flattened so transformers aren't an artificial pinch point. Reactance is scaled up proportionally to keep the real per-unit value (`x_pu`) fixed. |
| Demand scale-up | x1.1142 globally | `run_lopf_main_island.py` | Calibrated against real whole-January-2022 Hydro-Québec system demand (`historique-demande-electricite-quebec.csv`, mean 32,421 MW), not just the solved week alone. Regional demand rescaling matches real HQ totals only for buses matched to one of Quebec's 17 administrative regions; buses outside that match keep smaller synthetic values, which this factor also compensates for. |
| Dispatch ceiling headroom | x1.10 on real-data-derived ratios | `run_lopf_main_island.py` | Applied to ror, OCGT, and hydro storage ceilings (all derived from real 2022 hourly dispatch ratios) so the model isn't forced to never exceed the exact historical dispatch level. Not applied to wind/solar, which use weather-derived capacity factors, not dispatch history. |
| Link 4349 (329-3975 DC tie) capacity | x3 | `run_lopf_main_island.py` | Undersized at its source value, forcing shedding upstream despite real generation capacity existing to cover it. Insensitive to going further (x6 gave the same result as x3). |

## Generic assumptions (AC power flow only)

| Assumption | Value | Where | Why |
|---|---|---|---|
| Load power factor | 0.95 lagging | `run_pf.py` | Reactive demand isn't in the source data; a generic industry-typical value. |
| Generator power factor | 0.9 | `run_pf.py` | Generic reactive-capability assumption, tied to nameplate capacity rather than real-time dispatch (a synchronous machine can supply close to full reactive capability near zero real output). |
| Transformer X/R ratio | 30 | `run_pf.py` | Real transformer resistance isn't in the source data; a generic value for large power transformers. |
| PV bus eligibility threshold | Topology-dependent -- widened to "every real generator bus" on the 58-bus 735kV network; a stricter `p_nom >= 100 MW, load < 10% of local generation` threshold on the 208-bus 315kV network | `run_pf.py` (`--pv-min-capacity`, `--pv-max-load-ratio`) | PV buses hold voltage with effectively unlimited reactive power. That's stabilizing on a small, heavily meshed network but destabilizing with many PV buses on a larger network with long, weak radial corridors -- the two networks needed opposite settings. |
| Reactive compensation | 70% of each bus's own reactive demand, every snapshot | `run_pf.py` | A zero-real-power PQ generator per bus, sized to track local reactive demand at each hour rather than a fixed peak-sized shunt. Improves but does not fully close the AC PF convergence gap on its own; see [POWER_FLOW.md](POWER_FLOW.md). |

## Known data limitations

- **`num_parallel` does not represent real physical circuit count.** It's a per-row attribute,
  effectively always ~1.0 in the source data. Real multi-circuit corridors are instead represented
  as multiple separate `Line` rows sharing the same bus pair.
- **`reduce_voltage_network.py` uses straight-line geographic distance**, not graph shortest-path,
  to assign local buses to their nearest backbone bus. This has caused confirmed real
  misassignments in this project already (a substation reassigned 257km away; Ottawa-area loads
  assigned to a Quebec bus across the provincial border) -- and a newly-found, more severe case:
  **28 loads pooled onto 735kV bus 3554 from as far as 281.6km away** (1,482 MW total), and 21
  loads onto bus 1081 from up to 87.9km away (1,084 MW). Both buses sit on the network's one
  electrically weak 735/765kV bridge corridor, and this concentration is a likely contributing
  cause of the AC PF convergence gap there (see [POWER_FLOW.md](POWER_FLOW.md)). `reduce_to_735kv.py`
  uses graph shortest-path specifically to avoid this, but the fix was never retrofitted into
  `reduce_voltage_network.py` itself.
- **AC power flow does not converge on either reduced network at current real demand** (see
  [POWER_FLOW.md](POWER_FLOW.md) for the full investigation). The 735kV network converges fully at
  85% of current demand (a genuine loadability-margin finding); the 315kV network does not respond
  to demand reduction at all and fails much more severely -- a structurally different, still
  undiagnosed problem.
