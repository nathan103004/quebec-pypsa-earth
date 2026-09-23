# Assumptions and limitations

Every generic or unmeasured assumption used in this pipeline, with the reasoning behind the
specific value chosen..

## Generic assumptions (LOPF / general)

| Assumption | Value | Where | Why |
|---|---|---|---|
| Line security margin (`s_max_pu`) | 1.0 (relaxed from PyPSA-Earth's default 0.7) | `run_lopf_main_island.py` | The 0.7 default reserves 30% headroom for N-1 contingency; this model isn't doing contingency-constrained dispatch, so the margin only produced artificial shedding. |
| Transmission capacity margin (St Clair curve) | x3 on the derived thermal envelope | `run_lopf_main_island.py`, `st_clair.py` | St Clair is a planning-level analytical envelope from real Hypersim-sourced impedance data, not an empirical thermal rating -- treated as a starting point, multiplied up uniformly rather than taken as a hard limit. |
| Transformer thermal capacity | Flat 100,000 MVA | `run_lopf_main_island.py` | Real per-transformer `s_nom` from the OSM extract varies widely and binds at several major junctions carrying 38,000-155,000 MVA of real line capacity -- flattened so transformers aren't an artificial pinch point. Reactance is scaled up proportionally to keep the real per-unit value (`x_pu`) fixed. |
| Demand scale-up | x1.1142 globally | `run_lopf_main_island.py` | Calibrated against real whole-January-2022 Hydro-Québec system demand (`historique-demande-electricite-quebec.csv`, mean 32,421 MW), not just the solved week alone. Regional demand rescaling matches real HQ totals only for buses matched to one of Quebec's 17 administrative regions; buses outside that match keep smaller synthetic values, which this factor also compensates for. |
| Dispatch ceiling headroom | x1.10 on real-data-derived ratios | `run_lopf_main_island.py` | Applied to ror, OCGT, and hydro storage ceilings (all derived from real 2022 hourly dispatch ratios) so the model isn't forced to never exceed the exact historical dispatch level. Not applied to wind/solar, which use weather-derived capacity factors, not dispatch history. |
| Link 4349 (329-3975 DC tie) capacity | x3 | `run_lopf_main_island.py` | Undersized at its source value, forcing shedding upstream despite real generation capacity existing to cover it. Insensitive to going further (x6 gave the same result as x3). |
| Line reactance below 220kV | PyPSA-Earth's generic default type (German textbook, 50Hz, not Quebec-specific) | `elec_full.nc`'s `type` field, unmodified | No real Quebec data source exists at this voltage range. Doesn't affect the reduced/solved networks (they only keep >=315kV lines) -- only the unreduced reference map. At 315/345/735/765kV, real Hydro-Quebec data is used instead (`apply_hq_line_characteristics.py`); at other tiers >=220kV, an interpolated real 60Hz estimate is used (`fix_line_reactance_hypersim.py`) -- see [DATA_SOURCES.md](DATA_SOURCES.md). |

## Generic assumptions (AC power flow only)

| Assumption | Value | Where | Why |
|---|---|---|---|
| Load power factor | 0.95 lagging | `run_pf.py` | Reactive demand isn't in the source data; a generic industry-typical value. |
| Generator power factor | 0.9 | `run_pf.py` | Generic reactive-capability assumption, tied to nameplate capacity rather than real-time dispatch (a synchronous machine can supply close to full reactive capability near zero real output). |
| Transformer X/R ratio | 30 | `run_pf.py` | Real transformer resistance isn't in the source data; a generic value for large power transformers. |
| PV bus eligibility threshold | Topology-dependent -- widened to "every real generator bus" on the 58-bus 735kV network; a stricter `p_nom >= 100 MW, load < 10% of local generation` threshold on the 208-bus 315kV network | `run_pf.py` (`--pv-min-capacity`, `--pv-max-load-ratio`) | PV buses hold voltage with effectively unlimited reactive power. That's stabilizing on a small, heavily meshed network but destabilizing with many PV buses on a larger network with long, weak radial corridors -- the two networks needed opposite settings. |
| Reactive compensation | 70% of each bus's own reactive demand, every snapshot | `run_pf.py` | A zero-real-power PQ generator per bus, sized to track local reactive demand at each hour rather than a fixed peak-sized shunt. Improves but does not fully close the AC PF convergence gap on its own; see [POWER_FLOW.md](POWER_FLOW.md). |

## Known data limitations

- **Parallel-circuit count isn't reliably encoded in the raw data.** Real multi-circuit corridors
  show up as separate line records rather than a per-line circuit-count field, so any code path
  that trusted that field instead of counting real records would misstate corridor capacity.
- **Local-to-backbone bus reassignment uses straight-line geographic distance**, not real grid
  connectivity. This has repeatedly misassigned demand to the wrong backbone bus, sometimes
  pooling load from remote, unconnected areas onto a nearby-looking bus -- a real data-quality
  risk anywhere this reduction step is used, and a known contributor to unrealistic local stress
  in downstream results. A more accurate (graph-based) alternative exists elsewhere in the
  pipeline but hasn't been adopted for this step.
- **AC power flow does not converge on either reduced network at current real demand.** One
  network's convergence is sensitive to overall demand level (fails at 100%, succeeds at a
  reduced level) without an identified physical mechanism; the other fails much more severely and
  independently of demand level. Neither points to a specific fixable cause yet -- see
  [POWER_FLOW.md](POWER_FLOW.md).
