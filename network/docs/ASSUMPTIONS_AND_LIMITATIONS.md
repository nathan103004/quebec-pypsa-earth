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
| Demand scale-up | x1.042 globally | `run_lopf_main_island.py` | Regional demand rescaling matches real HQ totals only for buses matched to one of Quebec's 17 administrative regions; buses outside that match keep smaller synthetic values. This closes the remaining gap against real system-wide demand. |
| Dispatch ceiling headroom | x1.10 on real-data-derived ratios | `run_lopf_main_island.py` | Applied to ror, OCGT, and hydro storage ceilings (all derived from real 2022 hourly dispatch ratios) so the model isn't forced to never exceed the exact historical dispatch level. Not applied to wind/solar, which use weather-derived capacity factors, not dispatch history. |
| Link 4349 (329-3975 DC tie) capacity | x3 | `run_lopf_main_island.py` | Undersized at its source value, forcing shedding upstream despite real generation capacity existing to cover it. Insensitive to going further (x6 gave the same result as x3). |

## Generic assumptions (AC power flow only)

| Assumption | Value | Where | Why |
|---|---|---|---|
| Load power factor | 0.95 lagging | `run_pf.py` | Reactive demand isn't in the source data; a generic industry-typical value. |
| Generator power factor | 0.9 | `run_pf.py` | Generic reactive-capability assumption, tied to nameplate capacity rather than real-time dispatch (a synchronous machine can supply close to full reactive capability near zero real output). |
| Transformer X/R ratio | 30 | `run_pf.py` | Real transformer resistance isn't in the source data; a generic value for large power transformers. |
| PV bus eligibility threshold | Topology-dependent -- widened to "every real generator bus" on the 58-bus 735kV network; a stricter `p_nom >= 100 MW, load < 10% of local generation` threshold on the 208-bus 315kV network | `run_pf.py` (`--pv-min-capacity`, `--pv-max-load-ratio`) | PV buses hold voltage with effectively unlimited reactive power. That's stabilizing on a small, heavily meshed network but destabilizing with many PV buses on a larger network with long, weak radial corridors -- the two networks needed opposite settings. |
| Fixed shunt capacitors (735kV network only) | 70% of each bus's own peak reactive demand | `run_pf.py` | Closes the remaining reactive-power gap needed for full AC PF convergence without loosening the generator power factor assumption further. Trade-off: widens voltage magnitude spread (a fixed, unswitched bank over-compensates during low-demand hours) -- 35/58 buses fall below 0.95pu and 34/58 exceed 1.05pu at some point in the week. |

## Known data limitations

- **`num_parallel` does not represent real physical circuit count.** It's a per-row attribute,
  effectively always ~1.0 in the source data. Real multi-circuit corridors are instead represented
  as multiple separate `Line` rows sharing the same bus pair.
- **The AC-PF-validated 735kV network (`elec_735kv_pf.nc`, 115 lines) has 6 more lines than the
  network the current pipeline produces from scratch (`elec_735kv.nc`, 109 lines)**, concentrated
  at 4 corridors, 2 of which touch the network's two most electrically stressed buses. Checked
  directly against `elec_full.nc` (the unchanged raw source): neither version's circuit count at
  those corridors matches the source data exactly, but the 109-line version is the closer match.
  The extra circuits in the validated network came from an earlier stage of the pipeline
  (`fix_parallel_circuits_v2.py`, whose own source data files no longer exist in the repo) and
  were never fully propagated into the current `elec_full.nc`. Practically: the 168/168 AC PF
  convergence result is real and reproducible on `elec_735kv_pf.nc`, but is partly supported by
  transmission capacity at those two buses that isn't fully corroborated by the current source
  data. A fresh AC PF run against the exact current `elec_735kv.nc` has not yet been completed.
- **`elec_735kv_pf.nc`'s provenance predates the most recent fix to the 315kV network** (a
  one-way-only DC tie link between the Ottawa-area island and the main island, corrected in
  `elec_reduced.nc`). That fix is geographically distant from the 735kV backbone and unlikely to
  affect it, but the two files have not yet been regenerated together from a single consistent
  run.
- **The 315kV main working network has not converged under full AC power flow** in any
  configuration tried (see [POWER_FLOW.md](POWER_FLOW.md)). Root cause not fully isolated; two
  contributing mechanisms have been identified (a system-wide reactive power budget that narrows
  during high-demand hours, and real-power/voltage-angle stress independent of that budget) but
  neither alone or together has produced convergence on this network yet.
- **`reduce_voltage_network.py` uses straight-line geographic distance**, not graph shortest-path,
  to assign local buses to their nearest backbone bus. This has caused two confirmed real
  misassignments in this project already (a substation reassigned 257km away; Ottawa-area loads
  assigned to a Quebec bus across the provincial border). `reduce_to_735kv.py` uses graph
  shortest-path specifically to avoid repeating this, but the fix was not retrofitted into
  `reduce_voltage_network.py` itself.
