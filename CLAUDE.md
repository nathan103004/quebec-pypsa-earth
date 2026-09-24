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
     converges 157/168 snapshots, and a clean 168/168 at 82% of that demand
     (`elec_735kv_scaled82.nc`) — now a diagnosed and partly-fixed finding (voltage collapse at
     three specific buses, fixed with series compensation on their feeding lines; see
     `network/docs/POWER_FLOW.md`).
  4. A copy of the current, verified pipeline outputs (`elec_full.nc`, `elec_reduced.nc`,
     `elec_solved.nc`, `elec_735kv.nc`, `elec_735kv_scaled82.nc`/`_pf.nc`) is kept in
     `network/networks_current/` — use that to know which files in `networks/` are the real
     current state vs. stale/diagnostic leftovers. Pre-line-reactance-fix originals are backed up
     in `network/networks_backup_pre_hypersim/`.
- Full documentation lives under `network/docs/` — read `POWER_FLOW.md` before re-investigating
  anything AC-PF-related; it documents the full elimination history (what's been ruled out) and
  the current best finding.

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
   → produces `elec_reduced.nc`, the current input to the live pipeline below.
3. **`run_lopf_main_island.py`** — the main, repeatedly-rerun LOPF script. Takes `elec_reduced.nc`,
   extracts the largest connected island, applies marginal costs, real generation ceilings
   (ror/storage capped by real hourly `Hydraulique` dispatch data, OCGT by real `Thermique` data),
   line/transformer capacity fixes (St Clair-derived, ×3 margin; flat 100,000 MVA transformers),
   adds VOLL load-shedding generators, assigns slack, and solves LOPF.
   → produces `elec_solved.nc`, **the current best network** (0% shed, demand within 0.2% of real
   HQ data for the window solved). Currently solved for a 1-week window (168 snapshots,
   2022-01-01 to 2022-01-07); can be re-run with `--snapshots N` for other windows.
   - **Slack assignment**: picks the bus with the largest TOTAL generation capacity (Generator +
     StorageUnit combined), not just the largest plain-Generator bus. Storage-only buses (PyPSA's
     `find_bus_controls()` never reads `StorageUnit.control`, confirmed from source) get a
     zero-dispatch placeholder Generator added so they can actually host the slack flag.
     `reduce_to_735kv.py` defaults to whichever bus this lands on (currently bus 339 — La
     Grande-2-A + Robert-Bourassa storage, via placeholder `339 slack-placeholder`; **not** bus
     340, which has zero storage capacity — an error that was in `GENERATORS.md` for a while), but
     the 735kV AC PF work in this project has consistently used `114 ror` as slack instead (set
     manually after reduction) — a real generator, not a placeholder.
4. **`run_pf.py`** — runs DC (`--method lpf`) or full AC (`--method pf`) power flow on a solved
   network, using its dispatch as fixed injections. DC PF has been clean throughout this whole
   project on every network tried. AC PF has been the hard problem — see
   `network/docs/POWER_FLOW.md`. Key things this script does specifically for AC PF: assigns
   generic power factors (0.95 load, 0.9 generator, tied to nameplate not dispatch), adds
   placeholder generators on storage-only buses for PV eligibility, classifies PV/PQ buses by a
   size/load-ratio heuristic (tunable via `--pv-min-capacity` / `--pv-max-load-ratio` — the right
   threshold is topology-dependent, see `POWER_FLOW.md` for why 735kV and 315kV need different
   values), adds demand-following reactive compensation (one zero-P generator per bus,
   `--reactive-compensation-ratio`, default 0.70), and **restores the slack generator after the
   PQ/PV reset** (a real bug existed here until fixed — was silently picking an arbitrary fallback
   slack, sometimes a `load_shedding` placeholder, for every AC PF run before the fix).
5. **`reduce_to_735kv.py`** — reduces the solved network to just its 735/765kV backbone (unified
   as one tier), for a smaller network suitable for contingency analysis. Aggregates all
   loads/generators/storage from non-backbone buses onto the nearest backbone bus via graph
   shortest-path (real line length — NOT the straight-line method `reduce_voltage_network.py`
   uses). Preserves actual solved dispatch rather than re-deriving a ceiling; the reduced network
   is never re-optimized. → produces `elec_735kv.nc`. Slack defaults to whichever bus has the
   largest total generation capacity; set to `114 ror` manually afterward for AC PF work (see
   step 3's slack note above).
6. **`export_to_matpower.py`** — exports a solved network to MATPOWER `.m` case format for an
   independent AC PF cross-check, writing to both `network/` and
   `C:\Users\hjgua\Documents\MATLAB\matpower8.1\data\`. Handles PyPSA's split per-unit conventions
   (lines on an implicit 1 MVA base, transformers on their own `s_nom`) correctly — verified
   numerically exact against source. Carries over shunt capacitors and a DC-PF-seeded starting
   angle (both were missing and had to be added).
7. `visualize_real_network_map.py` / `visualize_solved_network_map.py` / `visualize_ac_pf_map.py` /
   `visualize_congestion_zoom.py` — HTML + PNG map outputs (Plotly/Scattergeo, using Plotly's
   built-in Natural Earth basemap, not external map tiles — OSM's tile server and Carto's free
   style both stopped working for this kind of embedded use), each purpose-built: full raw
   network, LOPF+DCPF congestion/dispatch/shedding, AC-PF-specific results (voltage
   magnitude/angle, diverging color scale), and a zoomed view of the most-congested lines.

## Current state / open threads (as of 2026-09-24)

- **Line reactance was corrected against real Hydro-Québec data** (`fix_line_reactance_hypersim.py`,
  `apply_hq_line_characteristics.py`) — every AC line's r/x/b had actually been coming from
  PyPSA-Earth's generic default type (a German textbook value at 50Hz), never from either of this
  project's own real-parameter CSVs, which had only ever fed the St Clair thermal (`s_nom`) calc.
  This materially tightened AC PF convergence (see below) — treat any AC-PF-related finding dated
  before 2026-09-23 as describing the old, understated-reactance network. Pre-fix networks are
  backed up in `network/networks_backup_pre_hypersim/`.
- **The AC PF mechanism on the 735kV network IS now identified** (unlike what the elimination
  testing below once suggested): continuation (homotopy) power flow on every failing snapshot
  showed genuine voltage collapse (Newton-Raphson's Jacobian went exactly singular at one point —
  a saddle-node bifurcation, not a solver quirk) at exactly **three buses** (308, 1291, 312) — all
  fed only by 250-500km lines with no local voltage-controlling generation. Having no local
  generation isn't itself predictive (36/58 buses share that trait harmlessly, e.g. bus 195 carries
  10,354 MW with no local gen but short lines); what matters is long line length combined with no
  genuinely independent second path to a real source.
- **Fix: 50% series compensation on the ten lines feeding those three buses**
  (`apply_series_compensation.py`, `COMPENSATION_FRACTION = 0.50`, a generic planning-level
  assumption not a measured HQ figure) — matches real Hydro-Québec practice on its longest 735kV
  corridors. A synchronous-condenser (PV bus) approach was tried first and reached 151/168 at 100%
  demand; series compensation reached **157/168** and is more realistic for this specific failure
  mode (it treats the actual cause, line reactance, rather than adding a device to work around it).
- **Fix: switched shunt reactors at 8 buses for light-load overvoltage**
  (`add_shunt_reactors.py`) — a separate problem from the collapse above: long lines' own charging
  generates excess reactive power under light load (Ferranti effect), pushing voltage up to 1.13pu.
  Modeled as a generator with fixed negative `q_set`, active only when demand is below that
  network's own mean (buses 308/312 need reactive *support* under heavy load, from the series
  compensation fix, so a permanently-on reactor there would fight it — switching avoids that). Bus
  469 runs permanently on instead, since its voltage barely correlates with system demand at all.
  Result: 345/312/308/2944 fully cleared 1.05pu, 150 nearly cleared, 310/3319 improved
  substantially but not fully (further tuning stopped helping and started costing convergence).
  Cost: 100%-demand convergence dropped slightly (157/168 → 155/168); the 82% full-convergence
  threshold is unaffected.
- **735kV backbone** (`elec_735kv.nc`, 109 real lines): 155/168 at current real demand (~30,200 MW
  mean, calibrated against real whole-January-2022 HQ data), **168/168 at 82% of that demand**
  (`elec_735kv_scaled82.nc`, up from 62% before series compensation). The remaining ~13 failures at
  100% demand are mostly the week's highest-demand hours — continuation power flow suggests a
  genuine active-power/angle-stability limit there, not a reactive-support gap; not yet investigated
  further. The earlier elimination process (reactive compensation, single/combined/all-lines
  *thermal* reinforcement, modal/eigenvector analysis) that found no localized fix worked was
  testing a different kind of intervention (capacity, not reactance) on the old, understated-
  reactance network — not a contradiction of the fix above, just untested against it.
  `network/quebec_735kv_ac_pf_map.html` is generated from this 82%-scaled, fully-converged network.
- **315kV full network** (`elec_solved.nc`, 205 buses): 0/168, and unlike the 735kV network,
  **demand reduction doesn't help at all** (still 0/168 even at 82% demand, with far more extreme
  numerical blowup). This is a structurally different, still-undiagnosed problem — the biggest
  open question in the project.
- **`reduce_voltage_network.py`'s straight-line reassignment** was replaced with graph
  shortest-path once (matching `reduce_to_735kv.py`'s method) and tested directly: AC PF barely
  changed at 100% demand and got *worse* at 85% (168→159/168). Reverted back to straight-line.
  The fix itself was real and correct (verified against real Quebec geography, e.g. the Gaspé
  Peninsula case) but is a much larger-scope change at this stage (~4,000-bus raw graph, ~350
  reassignments) than at the 735kV stage, and empirically hurts AC PF convergence rather than
  helping — do not re-apply without re-testing.
- **MATPOWER cross-check**: an earlier exported network did NOT converge in MATPOWER's `runpf()`,
  despite a numerically-exact export (DC PF branch flows matched PyPSA to the decimal). Seven
  hypotheses tested and ruled out. Not re-attempted against the current 109-line network.

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
