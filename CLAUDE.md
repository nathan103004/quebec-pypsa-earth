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
  3. **735kV reduced** (`elec_735kv.nc`, 58 buses) — a further reduction down to just the
     735/765kV backbone (`reduce_to_735kv.py`), aggregating everything else onto backbone buses by
     graph shortest-path. Purpose-built for AC PF tractability: small and heavily-meshed enough
     that full nonlinear convergence was actually achievable (168/168), which the 315kV network
     never was. Trade-off: it's a coarser representation, so contingency results on it are a
     stand-in for backbone-level stress, not a substitute for solving the real 315kV-detail network.
- A full narrative debugging report (AC PF convergence specifically) is published here:
  **https://claude.ai/code/artifact/aeb699d5-a8a3-4469-b8cd-edd9d6ebcc6b** — read that before
  re-investigating anything AC-PF-related, it documents ~15 diagnosed issues and what was ruled out.

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
     Supersedes and replaces `fix_parallel_circuits.py` (735kV-only, incomplete — safe to delete).
   - `fix_line_lengths_from_geometry.py` — 10 lines had `length` wildly inconsistent with their
     own stored geometry (one claimed 2% of its real path length).
   - `regional_demand.py` / `rescale_demand_regional.py` — replaced PyPSA-Earth's synthetic
     population/GDP demand proxy with real HQ municipal consumption data; fixed a 2.6x magnitude
     error in total system demand.
   - `attach_2022_data.py` / `attach_hydro_dispatch_2022.py` / `attach_real_generators.py` /
     `build_hq_contracts_data.py` — replaced synthetic generation with real 2022 dispatch data
     (Hydro-Québec's published hourly generation-by-source and IPP contract data).
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
   - **Slack assignment** (fixed 2026-09-02): picks the bus with the largest TOTAL generation
     capacity (Generator + StorageUnit combined), not just the largest plain-Generator bus. Storage
     -only buses (PyPSA's `find_bus_controls()` never reads `StorageUnit.control`, confirmed from
     source) get a zero-dispatch placeholder Generator added so they can actually host the slack
     flag. Currently lands on bus 339 (La Grande-2-A + Robert-Bourassa, 7,722 MW).
4. **`run_pf.py`** — runs DC (`--method lpf`) or full AC (`--method pf`) power flow on a solved
   network, using its dispatch as fixed injections. DC PF has been clean throughout this whole
   project on every network tried. AC PF has been the hard problem — see the report. Key things
   this script does specifically for AC PF: assigns generic power factors (0.95 load, 0.9
   generator, tied to nameplate not dispatch), adds placeholder generators on storage-only buses
   for PV eligibility, classifies PV/PQ buses by a size/load-ratio heuristic (tunable via
   `--pv-min-capacity` / `--pv-max-load-ratio` — the right threshold is topology-dependent, see
   the report for why 735kV and 315kV need different values), and **restores the slack generator
   after the PQ/PV reset** (a real bug existed here until fixed — was silently picking an arbitrary
   fallback slack, sometimes a `load_shedding` placeholder, for every AC PF run before the fix).
5. **`reduce_to_735kv.py`** — reduces the solved network to just its 735/765kV backbone (unified
   as one tier), for a smaller network suitable for contingency analysis. Aggregates all
   loads/generators/storage from non-backbone buses onto the nearest backbone bus via graph
   shortest-path (real line length — NOT the straight-line method `reduce_voltage_network.py`
   uses). Preserves actual solved dispatch rather than re-deriving a ceiling; the reduced network
   is never re-optimized. → produces `elec_735kv.nc`.
   - **⚠️ STALE as of 2026-09-03**: this was last run before the slack-assignment fix in step 3.
     `elec_735kv.nc`'s slack is still the old `114 ror` (4,042 MW), not the corrected `339`-based
     one. Re-run this against the current `elec_solved.nc` before trusting any 735kV AC PF result.
6. **`export_to_matpower.py`** — exports a solved network to MATPOWER `.m` case format for an
   independent AC PF cross-check, writing to both `network/` and
   `C:\Users\hjgua\Documents\MATLAB\matpower8.1\data\`. Handles PyPSA's split per-unit conventions
   (lines on an implicit 1 MVA base, transformers on their own `s_nom`) correctly — verified
   numerically exact against source. Carries over shunt capacitors and a DC-PF-seeded starting
   angle (both were missing and had to be added — see report).
7. `visualize_real_network_map.py` / `visualize_solved_network_map.py` / `visualize_ac_pf_map.py` /
   `visualize_congestion_zoom.py` — HTML map outputs (Plotly/Scattermapbox), each purpose-built:
   full raw network, LOPF+DCPF congestion/dispatch/shedding, AC-PF-specific results (voltage
   magnitude/angle, diverging color scale), and a zoomed view of the most-congested lines.

## Current state / open threads (as of 2026-09-03)

- **735kV backbone**: converged 168/168 in PyPSA's own AC PF (`n.pf()`) as of the last consistent
  run — but that result predates the slack fix (see step 5 above) and needs re-verifying.
  Required 3 real fixes (near-singular bridge-line reactance, storage-PV-eligibility gap, widened
  PV bus thresholds) + shunt capacitors (fixed/unswitched, 70% of local peak reactive demand) to
  reach full convergence without weakening the generator reactive-capability assumption.
- **315kV full network**: AC PF has **never converged**, 0/168 in every configuration tried
  (conservative and widened PV thresholds, before and after the slack fix, with and without the
  7 DC-link-connected island buses removed). Root cause **not found** — this is the biggest open
  problem. See report §03/§05 for what's been ruled out.
- **MATPOWER cross-check**: exported the 735kV network and it does NOT converge in MATPOWER's
  `runpf()`, despite the export being verified numerically exact against PyPSA (branch data,
  shunt scaling, DC-PF-seeded start, individual branch flows all matched to the decimal in a
  direct DC PF comparison). Seven independent hypotheses tested and ruled out (see report). Current
  read: a genuine algorithmic robustness difference between PyPSA's and MATPOWER's specific
  Newton-Raphson implementations on this data, not a data/export bug. Untested next step: try
  MATPOWER's fast-decoupled (`FDXB`) or cartesian-coordinate (`NR-SC`) algorithm variants.
- **Stale/diagnostic files in `networks/`** worth cleaning up once the 735kV re-run above is done:
  `elec_solved_lpf.nc`, `elec_solved_pf.nc` (predate the slack fix), `elec_735kv*.nc` (stale, see
  above), `elec_solved_noislands*.nc` (diagnostic — DC-link-island removal, ruled out as a cause,
  conclusion already extracted), `elec_735kv_pf_bcapped.nc` (diagnostic — line-charging cap test,
  ruled out as a cause).

## Working conventions established in this project

- Every generic/unmeasured assumption (power factors, X/R ratios, margins) is documented at the
  point it's introduced, with the reasoning for the specific value chosen — follow this pattern,
  don't silently add new placeholder assumptions.
- Prefer graph shortest-path (real line length) over straight-line geographic distance for any
  bus-reassignment logic — the straight-line method has caused two confirmed real misassignment
  bugs in this project already.
- When adding a workaround for a PyPSA API gap (e.g., `StorageUnit` never being PV-eligible), use
  a zero-dispatch placeholder `Generator` rather than trying to patch PyPSA itself — this pattern
  is used in three places now (`run_pf.py`'s PV eligibility, `run_lopf_main_island.py`'s slack
  assignment, and would apply to any future similar gap).
- Verify claims against actual re-runs before writing them into any report — this project caught
  itself asserting an unverified result once already; don't repeat that.
