# Quebec Transmission Grid Model — Project Context

A PyPSA model of Quebec's real transmission grid, built from OSM topology and calibrated against
real 2022 Hydro-Québec dispatch/demand data. This file exists so a new session can pick up the
project without re-deriving everything from scratch.

## What this is

- Reconstructed from OpenStreetMap-derived topology, then systematically corrected against real
  Hydro-Québec infrastructure and dispatch data at every stage that turned out to be wrong
  (including a missing 735kV Churchill Falls interconnection and undercounted parallel circuits
  across the whole ≥315kV backbone).
- The goal driving most of this work: get a network that (a) matches real HQ demand/dispatch,
  (b) solves with 0% unserved load in LOPF, and (c) converges under full nonlinear AC power flow —
  needed for downstream N-1 contingency analysis.
- **Three distinct topologies exist, each built for a different purpose — not three versions of
  the same thing:**
  1. **Unreduced** (`elec_full.nc`, ~4,000+ buses, every voltage level in the OSM extract) — the
     complete raw topology. Ground truth / ancestor of the other two; too large and includes
     too much outside Quebec's actual grid of interest to run LOPF or PF on directly. Used for
     the full-detail reference map (`visualize_real_network_map.py`) and as the source the 315kV
     reduction is built from.
  2. **315kV reduced** (`elec_reduced.nc` → `elec_solved.nc`, ~200 buses) — collapses the
     unreduced network's minor/low-voltage buses onto the nearest backbone bus
     (`reduce_voltage_network.py`), keeping real local substations and demand detail at 315kV-
     class resolution. This is the main working network: real dispatch is solved here (LOPF), and
     it's the level of detail AC PF would need to converge at for genuinely representative
     contingency analysis — which is exactly the network AC PF has never converged on (see below).
  3. **735kV reduced** (`elec_735kv.nc`, 58 buses, 109 lines) — a further reduction down to just
     the 735/765kV backbone (`reduce_to_735kv.py`), aggregating everything else onto backbone
     buses by graph shortest-path. Purpose-built for AC PF tractability. At current real demand it
     converges **168/168** snapshots after a circuit-count correction on five sending-end
     corridors plus 70% series compensation on 15 lines; shunt reactors and capacitors
     (`elec_735kv_shunt.nc`) bring voltages to 0.966–1.059 pu (see `network/docs/POWER_FLOW.md`).
  4. The current pipeline outputs (`elec_full.nc`, `elec_reduced.nc`, `elec_solved.nc`,
     `elec_735kv.nc`/`_pf.nc`, `elec_735kv_shunt.nc`/`_pf.nc`, `summary_735kv_acpf.csv`) are in
     `network/networks_current/`, which every script defaults to. Earlier versions are in
     `networks_current/_archive/`; files in `networks/` are stale/diagnostic leftovers.
     `network/networks_test_hq_cold_735/` is a test with Hydro-Québec cold thermal ratings on
     735kV lines (README inside).
- Full documentation lives under `network/docs/` — read `POWER_FLOW.md` before re-investigating
  anything AC-PF-related; it documents the current AC PF state, the fixes applied, and where the
  315kV divergence localizes.

## Pipeline (in order, each stage's `.nc` output feeds the next)

1. `base.nc` / `elec.nc` / `elec_full.nc` — PyPSA-Earth's raw OSM-derived build. Rebuilding these
   from scratch is a PyPSA-Earth pipeline run, not part of this project's own scripts.
2. One-off data-fidelity fixes, each run once and baked into the network (see each script's own
   docstring for the specific defect found and fixed — do not re-run blindly, check first whether
   the fix is already reflected in `elec_reduced.nc`):
   - `add_churchill_falls_tie.py` — added the real 735kV Churchill Falls interconnection, missing
     from the OSM extract (the bus had only a 66kV line for 5,428 MW of capacity).
   - `fix_parallel_circuits_v2.py` — corrected undercounted parallel circuits across the whole
     ≥315kV backbone, using real per-circuit OSM relations (99 relations → 55 real corridors).
     Supersedes `fix_parallel_circuits.py` (735kV-only, incomplete), already deleted.
   - `fix_line_lengths_from_geometry.py` — 10 lines had `length` wildly inconsistent with their
     own stored geometry (one claimed 2% of its real path length).
   - `apply_hq_line_characteristics.py` — writes Hydro-Québec's own r/x/b onto 315/345kV and
     735/765kV lines (from `hq_line_characteristics_by_voltage.csv`) and clears their `type` so
     `calculate_dependent_values()` doesn't recompute them.
   - `regional_demand.py` / `rescale_demand_regional.py` — replaced PyPSA-Earth's synthetic
     population/GDP demand proxy with real HQ municipal consumption data; fixed a 2.6x magnitude
     error in total system demand.
   - `attach_hydro_dispatch_2022.py` / `attach_real_generators.py` / `build_hq_contracts_data.py` —
     replaced synthetic generation with real 2022 dispatch data (Hydro-Québec's published hourly
     generation-by-source and IPP contract data). (`attach_2022_data.py`, an earlier/cruder version
     of this same calibration, is deleted — fully superseded by these plus `run_lopf_main_island.py`'s
     own OCGT ceiling.)
   - `reduce_voltage_network.py` — collapses low-voltage buses onto the nearest backbone bus.
     **Known flaw, already found and worked around downstream**: uses straight-line geographic
     nearest-neighbor, which caused two confirmed real misassignments (a bus 257km away from
     substations it "absorbed"; 25 Ottawa-area loads wrongly assigned to a Quebec bus). Any new
     reduction work should use graph shortest-path over real line length instead (see
     `reduce_to_735kv.py` for the pattern) — this was deliberately NOT retrofitted into
     `reduce_voltage_network.py` itself, only worked around downstream.
   - Generation-capacity correction — manual (14 missing hydro stations added, wind/solar scaled
     to HQ totals); method and data in `network/docs/GENERATORS.md`. No script.
   → produces `elec_reduced.nc`, the current input to the live pipeline below. It has no series
   compensation; compensation is applied only on the 735kV network.
3. **`run_lopf_main_island.py`** — the main, repeatedly-rerun LOPF script. Takes `elec_reduced.nc`,
   extracts the largest connected island, applies marginal costs, real generation ceilings
   (ror/storage capped by real hourly `Hydraulique` dispatch data, OCGT by real `Thermique` data),
   line/transformer capacity fixes (St Clair-derived, ×3 margin; flat 100,000 MVA transformers),
   adds VOLL load-shedding generators, assigns slack, and solves LOPF.
   → produces `elec_solved.nc`, **the current best network** (0% shed; demand scale 1.0653
   calibrated to the solved week's real mean, 28,870 MW; wind marginal cost -0.1 so wind isn't
   curtailed in the tie with zero-cost hydro). Currently solved for a 1-week window (168
   snapshots, 2022-01-01 to 2022-01-07); can be re-run with `--snapshots N` for other windows
   (re-calibrate `DEMAND_SCALE_FACTOR` if so).
   - **Slack assignment**: picks the bus with the largest TOTAL generation capacity (Generator +
     StorageUnit combined), not just the largest plain-Generator bus. Storage-only buses (PyPSA's
     `find_bus_controls()` never reads `StorageUnit.control`, confirmed from source) get a
     zero-dispatch placeholder Generator (`p_max_pu = 0`) added so they can host the slack flag.
     On the 315kV network this is bus 339 (`339 slack-placeholder`). The 735kV network uses
     `114 ror`, set by `reduce_to_735kv.py --slack-generator`.
4. **`run_pf.py`** — runs DC (`--method lpf`) or full AC (`--method pf`) power flow on a solved
   network, using its dispatch as fixed injections (copies LOPF `p`/`p0` into `p_set` when
   missing). DC PF reproduces LOPF flows. AC PF has been the hard problem — see
   `network/docs/POWER_FLOW.md`. Key things this script does specifically for AC PF: assigns
   generic power factors (0.95 load, 0.9 generator, tied to nameplate not dispatch), adds
   placeholder generators on storage-only buses for PV eligibility, classifies PV/PQ buses by a
   size/load-ratio heuristic (tunable via `--pv-min-capacity` / `--pv-max-load-ratio` — the right
   threshold is topology-dependent, see `POWER_FLOW.md` for why 735kV and 315kV need different
   values), and **restores the slack generator after the PQ/PV reset** (otherwise PyPSA picks an
   arbitrary fallback slack, sometimes a `load_shedding` placeholder).
5. **`reduce_to_735kv.py`** — reduces the solved network to just its 735/765kV backbone (unified
   as one tier), for a smaller network suitable for contingency analysis. Aggregates all
   loads/generators/storage from non-backbone buses onto the nearest backbone bus via graph
   shortest-path (real line length — NOT the straight-line method `reduce_voltage_network.py`
   uses). Preserves actual solved dispatch rather than re-deriving a ceiling; the reduced network
   is never re-optimized. → produces `elec_735kv.nc` with slack `114 ror`. Then, in this order:
   - `correct_sending_end_circuits.py` — 3 circuits on five 735kV corridors leaving major
     generating stations (114-1291, 114-148, 340-457, 66-457, 651-25); recomputes r/x/b from HQ
     per-km rates and scales `s_nom`. Must run before compensation (it resets x).
   - `apply_series_compensation.py` — 70% on 15 lines, x computed from the uncompensated per-km
     value (idempotent; never stacks).
   - `add_shunt_impedance.py` — probe-sized shunt reactors/capacitors (reactors 100%, capacitors
     70% of worst-hour need) → `elec_735kv_shunt.nc`.
   - `summarize_735kv_acpf.py` — AC PF summary of no support / series / series + shunts →
     `summary_735kv_acpf.csv`.
6. **`export_to_matpower.py`** — exports one snapshot (default: worst-loaded hour) to MATPOWER
   `.m` format for an independent AC PF cross-check (default output `network/`; copy to
   MATPOWER's `data/` folder to run). Handles PyPSA's split per-unit conventions (lines on an
   implicit 1 MVA base, transformers on their own `s_nom`). Carries shunts as bus `Bs` and a
   DC-PF-seeded starting angle.
7. `visualize_real_network_map.py` / `visualize_solved_network_map.py` / `visualize_ac_pf_map.py`
   — map outputs (Plotly/Scattergeo, using Plotly's built-in Natural Earth basemap, not external
   map tiles), each purpose-built: full raw network, LOPF+DCPF congestion/dispatch/shedding, and
   AC-PF-specific results (voltage magnitude/angle, diverging color scale). Only the PNGs are
   committed; the interactive HTML is regenerated by running the script.
8. `continuation_pf.py` — diagnostic, not a pipeline step: continuation power flow on failing
   snapshots to locate voltage-collapse buses.

## Current state / open threads (as of 2026-10-08)

- **Line parameters**: every 315/345kV and 735/765kV line's r/x/b comes from Hydro-Québec's own
  line-characteristics table (`network/hq_line_characteristics_by_voltage.csv`, applied by
  `apply_hq_line_characteristics.py`, 345 treated as 315-tier and 765 as 735-tier); `st_clair.py`
  reads the same table for its thermal-limit envelope. Other voltage levels (unreduced
  `elec_full.nc` only) keep PyPSA-Earth's default type.
- **735kV AC PF mechanism**: continuation power flow on failing snapshots showed genuine voltage
  collapse (Jacobian singular) at three buses (308, 1291, 312), all fed only by 250-500 km lines
  with no local voltage-controlling generation. Too few circuits on the sending-end corridors made
  their reactance up to 3x too high (PyPSA stores `x = x_per_km * length / num_parallel` per
  `Line`), so peak-hour transfers exceeded what they could deliver.
- **735kV convergence by pipeline step** (28,871 MW mean demand): after reduction 89/168; + circuit
  correction 110/168; + 70% series compensation 168/168 (50% also gives 168/168); + shunts 168/168.
  Both the circuit correction and series compensation are needed.
- **735kV results** (`summary_735kv_acpf.csv`): series compensation only 0.904–1.123 pu, 14 buses
  under / 14 over, max loading 60.6%; with shunts (5 capacitors +3,769 MVAr, 11 reactors
  -6,106 MVAr) 0.966–1.059 pu, 0 under, 15 over for 16 overnight hours. A fixed `b` can't follow
  demand; the remaining overvoltage needs switched shunts/SVCs.
- **315kV network** (`elec_solved.nc`, 205 buses): LOPF 0% shed, max loading 71.9%; AC PF 1/168.
  It lacks the circuit correction and series compensation (applied only after reduction to
  735kV), and a radial spur of 315kV buses (240, 1548, 1762, 1772, 2207, 3719, 1693, 3836, 2812)
  is a second divergence area. Next step: apply the corridor corrections before the 315kV LOPF.
- **Line ratings**: `s_nom` is St Clair x 3, which inflates short lines (~2.8x HQ's cold thermal
  rating) and ignores series compensation on long ones. A cold-rating test is in
  `network/networks_test_hq_cold_735/`.
- **Known gaps**: no generator Q limits in PyPSA's AC PF; HVDC terminal buses are DC-only islands
  (445 MW of load served only via DC); 6 wind farms (438 MW) dropped as islands; no exports
  modeled; capacity correction is manual. See `network/docs/ASSUMPTIONS_AND_LIMITATIONS.md`.
- **`reduce_voltage_network.py`'s straight-line reassignment** was replaced with graph
  shortest-path once and tested: AC PF barely changed at 100% demand and got worse at 85%.
  Reverted. Do not re-apply without re-testing.
- **MATPOWER**: `network/quebec_735kv.m` (from `elec_735kv_shunt.nc`) and
  `network/quebec_main_island.m` (from `elec_solved.nc`) are current; not yet re-solved in MATLAB.

## Working conventions established in this project

- Every generic/unmeasured assumption (power factors, X/R ratios, margins) is documented at the
  point it's introduced, with the reasoning for the specific value chosen — follow this pattern,
  don't silently add new placeholder assumptions.
- Graph shortest-path (real line length) is more accurate than straight-line geographic distance
  for bus-reassignment logic — the straight-line method has caused multiple confirmed real
  misassignment bugs in this project (most recently pooling demand from up to 281.6km away onto
  one bus on the 735kV network's weakest corridor). `reduce_to_735kv.py` uses the graph method for
  this reason. **But** applying the same fix to `reduce_voltage_network.py` was tried and reverted
  (see Current state above) — it's more correct but empirically worse for AC PF convergence at
  that stage's scale. Accuracy and this project's AC PF goal are not always aligned; don't assume
  "more correct" automatically means "better outcome" without testing.
- When adding a workaround for a PyPSA API gap (e.g., `StorageUnit` never being PV-eligible), use
  a zero-dispatch placeholder `Generator` rather than trying to patch PyPSA itself — this pattern
  is used in three places now (`run_pf.py`'s PV eligibility, `run_lopf_main_island.py`'s slack
  assignment, and would apply to any future similar gap).
- Verify claims against actual re-runs before writing them into any report — this project caught
  itself asserting an unverified result once already; don't repeat that.
