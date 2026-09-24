# Power flow results

## LOPF (linear optimal power flow) -- `elec_solved.nc`

Solved on the main 315kV working network's largest island (205 buses) over a one-week window
(2022-01-01 to 2022-01-07, 168 hourly snapshots).

- **0% load shed.** Every hour's demand is fully served from the modeled fleet.
- Mean demand ~30,200 MW, calibrated against real whole-January-2022 system demand
  (`historique-demande-electricite-quebec.csv`).
- Max line loading ~78.5%, **0 lines >= 90% loaded** -- down from ~96% (1 line >= 90%) before the
  line reactance correction and series compensation below; security margin (`s_max_pu`) is still
  relaxed from PyPSA-Earth's default 0.7 to 1.0, see
  [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for why that relaxation exists.
  No congestion close-up map is generated any more (`visualize_congestion_zoom.py` finds nothing
  to zoom into, by design, when no line clears the 90% threshold).

## DC power flow

Run via `run_pf.py --method lpf` as a linear sanity check against the LOPF dispatch. Has been
clean throughout this project on every network tried -- no convergence issues at any stage.

## AC power flow (full nonlinear Newton-Raphson) -- open, unresolved

**Neither reduced network fully converges under AC PF at current real demand.** Current state
(line reactance corrected against real Hydro-Quebec data, plus series compensation on the
identified weak corridors -- see the sections below):

| Network | Convergence at current demand | At reduced demand |
|---|---|---|
| 315kV (`elec_solved.nc`, 205 buses) | 0/168 | 0/168 at 82% -- no improvement |
| 735kV (`elec_735kv.nc`, 58 buses, 109 lines) | 155/168 | **168/168 at 82%** |

![735kV backbone AC PF results at 82% demand -- voltage deviation, line loading, slack/PV buses](../quebec_735kv_ac_pf_map.png)

**Line reactance correction (2026-09-23):** every line's r/x/b previously came from PyPSA-Earth's
generic default type (`Al/St 560/50 4-bundle 750.0`, a German textbook value at 50Hz -- see
[DATA_SOURCES.md](DATA_SOURCES.md)), not from the Hypersim/HQ data already sitting in this
project's own CSVs, which had only ever been wired into the St Clair thermal (`s_nom`) calculation.
Fixed via `fix_line_reactance_hypersim.py` (all AC lines >= 220kV, log-log interpolated from the
Hypersim/EMTP table) and `apply_hq_line_characteristics.py` (315/345kV and 735/765kV lines
specifically, overridden with Hydro-Quebec's own exact real line-characteristics table). Net
effect: 735kV line x +19-20%, 315kV line x roughly +36% (compared to the old generic-type default
-- the earlier "-8%" figure documented at one point was only relative to an intermediate
interpolated estimate, not the true starting point). This **materially tightened** AC PF
convergence at first -- before the series compensation fix below, the demand level needed for full
735kV convergence dropped from 85% to 62%, and 100%-demand convergence changed from 50/168 to
85/168. Treat any AC PF finding from before 2026-09-23 as referring to the old,
understated-reactance network.

### Diagnosing *why* -- voltage collapse at three specific buses

Continuation (homotopy) power flow -- ramping demand from an easy, converged level up in small
steps, warm-starting each step from the last -- was run on every snapshot that failed at 100%
demand on the corrected-reactance network. This reveals the network's *true* physical loadability
limit directly (the point where the AC power flow equations stop having a real solution at all),
rather than inferring it from solver failure alone. Across all 83 failing snapshots, only **three
buses** ever came up as the point of collapse:

| Bus | Times it was the collapse point | Local load | Local generation |
|---|---|---|---|
| 308 | 53 / 83 | 1,511 MW | none |
| 1291 | 27 / 83 | 135 MW | none |
| 312 | 3 / 83 | 690 MW | 411 MW (present but not voltage-controlling) |

All three are fed only by very long (250-500km) lines, with no local generation to hold voltage up
independently. Newton-Raphson's own Jacobian went exactly singular at one of these snapshots
(`MatrixRankWarning: Matrix is exactly singular`) -- the precise mathematical signature of a
saddle-node bifurcation, confirming this is genuine voltage collapse, not a solver quirk. Bus
1291's true collapse point traced a textbook nose curve as demand rose (v_mag_pu: 1.00 -> 0.99 ->
0.96 -> 0.90 -> 0.83 -> no solution). Note that having no local generation isn't itself the
predictor -- 36 of the 58 buses on this network share that trait harmlessly (e.g. bus 195 carries
10,354 MW of load with no local generation at all, but its lines are short, so voltage drop stays
small). What matters is the combination of long line length *and* no genuinely independent second
path to a real source; bus 308's apparent redundancy (6 lines) is largely illusory, since 3 of them
just lead to bus 312, itself stressed.

**Fix: 50% series compensation on the ten lines feeding these three buses**
(`apply_series_compensation.py`) -- a capacitor bank in series with the conductor, directly
cancelling half the line's own reactance (`x_new = x * (1 - 0.5)`). This is the standard real-world
fix for exactly this failure mode (a line whose length alone makes its reactance the binding
constraint), and matches Hydro-Quebec's own real 735kV practice on its longest corridors. A
synchronous-condenser approach (PV bus, unconstrained reactive injection) was tried first and
reached 151/168 at 100% demand; series compensation reached **157/168** and pushed the
full-convergence demand threshold from 62% to 82% -- more effective, and more realistic for this
specific problem, since it treats the actual root cause (line reactance) rather than adding a
device to work around it. `COMPENSATION_FRACTION = 0.50` is a generic planning-level assumption
(real EHV series compensation typically runs 30-70%), not a measured Hydro-Quebec figure for these
specific lines -- see [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md).

The remaining 13 failures (at 100% demand, post-compensation) are mostly the week's highest-demand
hours (30,400-36,564 MW) -- continuation power flow on the hardest of these shows a true collapse
point around 78-86% demand even with compensation, suggesting a genuine active-power/angle-stability
limit rather than a reactive-support gap. Not yet investigated further.

### Light-load overvoltage -- switched shunt reactors

Separately from the collapse issue (which is a heavy-load problem), several buses showed
overvoltage up to 1.13pu specifically under **light** load -- the Ferranti effect: these same long
lines' own shunt charging generates more reactive power than a lightly-loaded system can absorb.
8 buses were affected (469, 310, 3319, 345, 312, 308, 150, 2944), with voltage exceeding 1.05pu
in up to 158/168 snapshots. Fixed with `add_shunt_reactors.py`: a generator with a fixed negative
`q_set`, active only when total system demand is below that network's own mean (a **switched**
shunt reactor, not permanent) -- because buses 308 and 312 need reactive *support* under heavy load
(the collapse fix above) but *absorption* under light load; a permanently-on reactor there would
fight the collapse fix during exactly the hours it's needed. Bus 469 is the one exception: its
voltage barely correlates with system-wide demand (chronically ~1.125pu all week, unlike the
others' clear light-load pattern), so it runs permanently on instead of switched.

Result (on the 155 snapshots that converge both before and after): 345, 312, 308, and 2944 fully
cleared 1.05pu; 469 dropped from 1.125pu (155/155 snapshots over) to 1.016pu (0 over); 150 nearly
cleared (1 remaining instance); 310 and 3319 improved substantially but not fully (over-1.05 count
roughly halved, 109->35 and 112->38) -- pushing their reactor rating further caused new convergence
failures without further voltage improvement, so their fix is partial. Reactor ratings (900-2,500
MVAr depending on bus) were tuned empirically against this project's own AC PF runs, not derived
from a target-voltage solve -- see [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md).
Convergence at 100% demand dropped slightly as a side effect (157/168 -> 155/168); the 82%
full-convergence threshold is unaffected (still 168/168 with reactors included).

### What's been ruled out on the 735kV network (pre-reactance-fix testing)

The following was tested on the *old*, understated-reactance network, before either fix above --
not yet re-tested against the current network:

- 10x local reactive compensation at every bus: 1/71 (of the snapshots that fail at baseline)
- Strengthening any single stressed corridor, or the top 3/6 most-loaded corridors (thermal
  capacity increase, not reactance reduction): 0/71
- Doubling thermal capacity on **all 109 lines network-wide**: 0/71
- Modal (eigenvector) analysis consistently identified a critical bus cluster (132, 133, 3554, 136,
  1081, 195, 603, 1717 -- the 735/765kV bridge area) across every tested snapshot, but increasing
  loading capacity of the lines connected to it yielded no meaningful result: 0/71

None of these tested *reducing reactance itself* on the affected lines -- only thermal/capacity
reinforcement, which doesn't address a reactance-driven voltage-collapse mechanism. That's
consistent with the series compensation fix above working where these didn't: it's a different
kind of intervention, not a repeat of one already ruled out.

### The 315kV network fails differently

Unlike the 735kV network, reducing demand does **not** help the 315kV network at all (still 0/168
even at 82% demand, with far more extreme numerical blowup -- hundreds of lines "loaded" past
absurd percentages). This isn't a loadability-margin problem like the 735kV case; something
structurally different is going on, and it hasn't been diagnosed.

### MATPOWER cross-check

**Predates both the line reactance correction and the series compensation fix above -- not yet
re-run against the current network.** The numbers below describe the old, understated-reactance,
uncompensated 735kV network.

`export_to_matpower.py` exports a single snapshot (a static case format, not a time series) to an
independent solver. Tested at the easiest (lowest-loaded) snapshot for both the 85%-scaled and
real 100%-demand 735kV networks -- **both converged in MATPOWER**, confirming PyPSA's own result
independently at that hour. This is a relaxed test, not a stress test: the snapshot was
deliberately chosen as the easiest of the week, so it doesn't speak to the snapshots that fail.

| Metric | PyPSA (100%) | MATPOWER (100%) |
|---|---|---|
| Total load P/Q | 24,763.3 / 8,139.3 | 24,763.3 / 8,139.3 (exact match) |
| Total generation P | 25,837.6 | 25,914.0 |
| Voltage magnitude range | 0.995-1.115 pu | 0.927-1.082 pu |
| Slack P/Q | 3,586.1 / -594.8 | 4,361.6 / +504.7 (**sign flips**) |
| Line losses P | 1,074.3 MW | 1,150.7 MW |

Real power/load agree closely; reactive power and voltage magnitude diverge between solvers, more
so at 100% than at 85% demand -- consistent with the system's reactive-power solution becoming
less well-constrained as demand rises, in both solvers, not just PyPSA.

### Generic assumptions used only for AC PF

AC power flow needs reactive power data the LOPF/DC stages don't model. `run_pf.py` fills this in
with generic, documented assumptions at the point they're introduced -- see
[ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for the full list (load and
generator power factors, transformer X/R ratio, PV/PQ classification thresholds, and the current
demand-following reactive-compensation model).
