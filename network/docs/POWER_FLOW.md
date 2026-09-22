# Power flow results

## LOPF (linear optimal power flow) -- `elec_solved.nc`

Solved on the main 315kV working network's largest island (208 buses) over a one-week window
(2022-01-01 to 2022-01-07, 168 hourly snapshots).

- **0% load shed.** Every hour's demand is fully served from the modeled fleet.
- Mean demand 32,421 MW, calibrated against real whole-January-2022 system demand
  (`historique-demande-electricite-quebec.csv`).
- Max line loading ~96% -- security margin (`s_max_pu`) is relaxed from PyPSA-Earth's default 0.7
  to 1.0; see [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for why.

## DC power flow

Run via `run_pf.py --method lpf` as a linear sanity check against the LOPF dispatch. Has been
clean throughout this project on every network tried -- no convergence issues at any stage.

## AC power flow (full nonlinear Newton-Raphson) -- open, unresolved

**Neither reduced network fully converges under AC PF at current real demand.** Current state:

| Network | Convergence at current demand | At 85% of current demand |
|---|---|---|
| 315kV (`elec_solved.nc`, 208 buses) | 0/168 | 0/168 -- no improvement |
| 735kV (`elec_735kv.nc`, 58 buses, 109 lines) | 50/168 | **168/168** |

### What's been ruled out on the 735kV network

Several fixes attemped but all failed to meaningfully improve convergence:

- 10x local reactive compensation at every bus: 1/71 (of the snapshots that fail at baseline)
- Strengthening any single stressed corridor, or the top 3/6 most-loaded corridors (2x capacity): 0/71
- Doubling capacity on **all 109 lines network-wide**: 0/71
- Modal (eigenvector) analysis consistently identifies the same critical bus cluster (132, 133,
  3554, 136, 1081, 195, 603, 1717 -- the 735/765kV bridge area) across every tested snapshot, but
  increasing loading capacity of the lines connected to the exact bus yielded no meaningful result: 0/71

Only a uniform reduction of real+reactive power at every bus simultaneously works; Confirmed as 168/168 across the full network at 85% scale.

### The 315kV network fails differently

Unlike the 735kV network, reducing demand 15% does **not** help the 315kV network at all (still
0/168, with far more extreme numerical blowup -- 273/276 lines "loaded" past absurd percentages).
This isn't a loadability-margin problem like the 735kV case; something structurally different is
going on, and it hasn't been diagnosed.

### MATPOWER cross-check

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
