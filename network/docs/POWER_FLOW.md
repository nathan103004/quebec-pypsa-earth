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

### Fixing divergence: two components on the 735kV network

**Series compensation** (`apply_series_compensation.py`) -- a capacitor bank modeled in series
with the conductor on the ten longest lines (250-500km) feeding buses 308, 1291, and 312, the only
three points of AC PF voltage collapse on this network (found via continuation power flow; genuine
collapse, confirmed by Newton-Raphson's Jacobian going exactly singular there, not a solver quirk).
Modeled as `x_new = x * (1 - 0.5)` -- a straight 50% reduction of each line's own reactance.
Needed because these three buses have no local voltage-controlling generation, so their voltage
depends entirely on how much drop accumulates over a very long, high-reactance line; cutting that
reactance directly raises the loadability limit. Result: 100%-demand convergence rose from 85/168
to 157/168, and the full-convergence demand threshold from 62% to 82%.

**Switched shunt reactors** (`add_shunt_reactors.py`) -- a generator with a fixed negative
`q_set` at 8 buses (469, 310, 3319, 345, 312, 308, 150, 2944), active only when total system demand
is below that network's own mean (bus 469 is always-on instead, since its voltage doesn't track
system demand). Needed because the same long lines' own charging pushes voltage up to 1.13pu under
*light* load (Ferranti effect) -- the opposite problem from the collapse above, at the opposite end
of the demand range. Switching (rather than a permanent shunt) matters because buses 308/312 need
reactive *support* under heavy load from the series compensation fix; a fixed reactor there would
fight it during exactly the hours it's needed. Result: most affected buses cleared 1.05pu; small
convergence cost at 100% demand (157/168 -> 155/168), no effect on the 82% full-convergence case.

Both components' sizing (`COMPENSATION_FRACTION = 0.50`, reactor ratings 900-2,500 MVAr) are
generic, tuned-not-measured assumptions -- see
[ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md).

### The 315kV network fails differently

Unlike the 735kV network, reducing demand does **not** help the 315kV network at all (still 0/168
even at 82% demand, with far more extreme numerical blowup -- hundreds of lines "loaded" past
absurd percentages). This isn't a loadability-margin problem like the 735kV case; something
structurally different is going on, and it hasn't been diagnosed.

### MATPOWER cross-check

**Re-run 2026-09-24 against the fully-corrected network** (real reactance, series compensation,
shunt reactors). `export_to_matpower.py` exports a single snapshot (a static case format, not a
time series) to an independent solver -- tested at the easiest (lowest-loaded) snapshot of the
week for both the 100%-demand and 82%-scaled 735kV networks. **Both converged in MATPOWER**
(Newton's method, 4 iterations each), confirming PyPSA's own result independently at that hour.
This is a relaxed test, not a stress test: the snapshot was deliberately chosen as the easiest of
the week, so it doesn't speak to the snapshots that fail.

| Metric | PyPSA (100%) | MATPOWER (100%) | PyPSA (82%) | MATPOWER (82%) |
|---|---|---|---|---|
| Total load P/Q | 23,038.0 / 7,572.2 | 23,038.1 / 7,572.2 (exact match) | 18,891.2 / 6,209.2 | 18,891.2 / 6,209.2 (exact match) |
| Total generation P | 23,958.3 | 23,882.6 | 19,479.4 | 19,473.3 |
| Voltage magnitude range | 0.913-1.115 pu | 0.977-1.122 pu | 0.948-1.039 pu | 0.994-1.123 pu |
| Slack (bus 114) P/Q | 1,618.0 / -2,739.5 | 1,544.4 / -2,095.5 | 1,160.3 / -2,189.6 | 1,155.9 / -2,209.0 |
| Line losses P | 920.3 MW | 844.5 MW | 588.2 MW | 582.1 MW |

Agreement is much tighter than the earlier cross-check on the old, understated-reactance network --
notably, the slack reactive power **sign flip** documented before is gone; both solvers now agree
on sign and are within 5-25% on magnitude everywhere, closest at 82% demand (P losses within 1%,
slack P within 0.4%, slack Q within 1%). This is consistent with the earlier divergence having been
partly an artifact of the old export's understated line reactance, not purely a reactive-power
formulation difference between solvers.

### Generic assumptions used only for AC PF

AC power flow needs reactive power data the LOPF/DC stages don't model. `run_pf.py` fills this in
with generic, documented assumptions at the point they're introduced -- see
[ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for the full list (load and
generator power factors, transformer X/R ratio, PV/PQ classification thresholds, and the current
demand-following reactive-compensation model).
