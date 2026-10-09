# Assumptions and limitations

Every generic or unmeasured assumption used in this pipeline, with the reasoning behind the
specific value chosen, followed by known gaps and open work.

## Generic assumptions (LOPF / general)

| Assumption | Value | Where | Why |
|---|---|---|---|
| Line security margin (`s_max_pu`) | 1.0 (relaxed from PyPSA-Earth's default 0.7) | `run_lopf_main_island.py` | The 0.7 default reserves 30% headroom for N-1 contingency and reactive flow; this model isn't doing contingency-constrained dispatch. LOPF limits real power only, so at 1.0 nothing is reserved for reactive flow: on the 735kV network apparent-power loading runs up to ~10 points above real-power loading on the most loaded lines. |
| Line rating (`s_nom`) | St Clair curve x 3 | `run_lopf_main_island.py`, `st_clair.py` | St Clair is a planning-level envelope from Hydro-Québec's line parameters: `SIL * min(3.5, sin 30deg / sin(beta L))`. It assumes no series compensation and no intermediate voltage support, so it underrates long 735kV corridors that use both; the x3 is a uniform stand-in for that support. It also inflates short lines to ~2.8x Hydro-Québec's own cold thermal rating (7,893 MVA per 735kV circuit, `hq_line_characteristics_by_voltage.csv`). A physically consistent alternative is `min(HQ thermal, V^2 sin 30deg / x_line)` with each line's compensated x. |
| Transformer thermal capacity | Flat 100,000 MVA | `run_lopf_main_island.py` | Real per-transformer `s_nom` from the OSM extract varies widely and binds at major junctions -- flattened so transformers aren't an artificial pinch point. Reactance is scaled up proportionally to keep `x_pu` fixed. |
| Demand scale-up | x1.0653 globally | `run_lopf_main_island.py` | Calibrated to the real mean demand of the solved week (2022-01-01 to 2022-01-07, 28,870 MW, `historique-demande-electricite-quebec.csv`). Model mean, minimum and peak are within 0.5% of actual. Also compensates buses outside the 17 administrative regions matched by the regional demand rescaling. |
| Dispatch ceiling headroom | x1.10 on real-data-derived ratios | `run_lopf_main_island.py` | Applied to ror, OCGT and hydro storage ceilings so the model isn't forced to never exceed the exact historical level. Not applied to wind/solar. |
| Wind marginal cost | -0.1 $/MWh | `run_lopf_main_island.py` | Breaks the cost tie with zero-cost hydro so available wind isn't curtailed. |
| Link 4349 (329-3975 DC tie) capacity | x3 | `run_lopf_main_island.py` | Undersized at its source value. Its buses are not in the current main island, so it has no effect on the solved network. |
| Line reactance at voltages other than 315/345/735/765kV | PyPSA-Earth's generic default type | `elec_full.nc` | Only the voltage tiers the reduced networks keep get Hydro-Québec's table values; affects only the unreduced map. |
| Series compensation degree | 70% | `apply_series_compensation.py` | On 15 lines: the ten 250-500 km lines feeding the voltage-collapse buses 308, 1291, 312, and the five highest-angle lines (162, 156, 1938, 1049, 1448). Real EHV series compensation typically runs 30-70%. Not Hydro-Québec's measured levels. 50% also converges (168/168), with slightly larger angles and more voltage violations. Sub-synchronous resonance is not modeled (steady state only). |
| Circuit count on 5 sending-end corridors | 3 circuits | `correct_sending_end_circuits.py` | 114-1291, 114-148, 340-457, 66-457 and 651-25 were modeled with 1-2 circuits; Hydro-Québec builds 3-circuit corridors out of its major stations. |
| Slack bus | `114 ror` on the 735kV network | `reduce_to_735kv.py` | A real generator at the largest generation hub (Robert-Bourassa). |

## Generic assumptions (AC power flow only)

| Assumption | Value | Where | Why |
|---|---|---|---|
| Load power factor | 0.95 lagging | `run_pf.py` | Reactive demand isn't in the source data. |
| Generator power factor | 0.9, on nameplate | `run_pf.py` | Generic reactive capability for PQ generators, tied to nameplate rather than dispatch. |
| Transformer X/R ratio | 30 | `run_pf.py` | Real transformer resistance isn't in the source data. |
| PV bus eligibility | Every generator bus whose mean load is below its local generation on the 735kV network (`0` / `1`); `p_nom >= 100 MW` and load < 10% of local generation on the 315kV network | `run_pf.py` (`--pv-min-capacity`, `--pv-max-load-ratio`) | PV buses hold voltage with unlimited reactive power; that's stabilizing on a small meshed network but destabilizing with many PV buses on long radial corridors. Ignores carrier. |
| Shunt sizing | Reactors 100%, capacitors 70% of worst-hour need | `add_shunt_impedance.py` | A fixed `b` sized for the worst hour is oversized at all other hours. 100% / 70% clears undervoltage; the sweep's best pair (fewest buses out of band) is 100% / 40%. Sizes are model-derived, not Hydro-Québec's installed equipment. |

## Known limitations and open work

- **Generator reactive limits aren't enforced.** PyPSA's `n.pf()` has no Q limits on PV
  generators, so they hold voltage with unlimited reactive power. Results are a lower bound on
  reactive stress; real Q limits could reveal more voltage violations or divergence.
- **Fixed shunts can't follow demand.** With series compensation and shunts, 15 735kV buses still
  exceed 1.05 pu for 16 overnight hours. Switched shunts or SVCs (not modeled as controllable
  devices in PyPSA) would be needed.
- **Line ratings.** See the St Clair x 3 row above. Loading percentages are relative to that
  inflated rating; under Hydro-Québec's cold thermal rating, 735kV max loading with series
  compensation is about 80% (apparent power).
- **Intermediate voltage support isn't modeled.** Real long corridors have synchronous condensers
  and SVCs at intermediate substations that hold voltage along the route. Their locations still
  need to be researched.
- **The 315kV network doesn't converge under AC power flow (1/168).** The circuit-count correction
  and series compensation are applied only to the 735kV network; applying them to the 315kV
  network before LOPF is the next step. A radial spur of 315kV buses (240, 1548, 1762, 1772, 2207,
  3719, 1693, 3836, 2812) is a second divergence area.
- **`reduce_voltage_network.py` reassigns buses by straight-line distance, not electrical
  connectivity.** This can pool demand onto the wrong bus. `reduce_to_735kv.py` uses graph
  shortest-path instead. Diagnosing the 315kV network should start with checking this topology.
- **Isolated buses in the 315kV reduction.** The HVDC line's southern terminal buses have no AC
  connection, so 445 MW of load is served only through the DC line (its real rating is ~2,000 MW;
  `p_nom` here is 737 MW). Two groups of buses are dropped by island extraction with 6 wind farms
  (438 MW) and 44 loads. All are artifacts of removing lines below 315kV.
- **Parallel-circuit count isn't reliably encoded in the OSM data.** Corridors beyond the five
  corrected ones may still be undercounted.
- **No exports.** Actual 2022 generation exceeds demand by ~4 GW of net exports; the model serves
  domestic demand only.
- **The generation-capacity correction is manual** (see [GENERATORS.md](GENERATORS.md)).
