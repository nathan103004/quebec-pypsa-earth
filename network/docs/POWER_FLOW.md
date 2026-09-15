# Power flow results

## LOPF (linear optimal power flow) -- `elec_solved.nc`

Solved on the main 315kV working network's largest island (208 buses) over a one-week window
(2022-01-01 to 2022-01-07, 168 hourly snapshots).

- **0% load shed.** Every hour's demand is fully served from the modeled fleet.
- Mean demand 32,102 MW, peak 41,369 MW.
- Max line loading 100% (2 of 276 lines at or above 90%) -- security margin (`s_max_pu`) is
  relaxed from PyPSA-Earth's default 0.7 to 1.0; see
  [ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for why.

## DC power flow

Run via `run_pf.py --method lpf` as a linear sanity check against the LOPF dispatch. Has been
clean throughout this project on every network tried -- no convergence issues at any stage.

## AC power flow (full nonlinear Newton-Raphson)

**Converges 168/168 snapshots on the 735kV backbone network** (`elec_735kv_pf.nc`, 58 buses, 115
lines), using PyPSA's own `n.pf()`. Max line loading 60.5%, 0 lines above 90%.

Reaching this required three structural fixes plus one modeling addition, found through a lengthy
diagnostic process (see [DEBUGGING_HISTORY.md](DEBUGGING_HISTORY.md) for the full investigation):

1. **Near-singular bridge admittance** -- the transformer bridging the 735kV/765kV tiers had a
   per-unit reactance that converted to a near-short-circuit equivalent line; re-derived from the
   735kV fleet's own real reactance-per-km instead.
2. **Slack-bus bug** -- resetting every generator to PQ control before reclassifying some as PV
   silently wiped the slack flag, and PyPSA's fallback picked an arbitrary (sometimes nonsensical)
   replacement. Fixed by explicitly preserving the prior slack across the reset.
3. **Widened PV bus eligibility** -- letting the solver compute reactive power at every real
   generator bus, instead of fixing it at a generic power factor, was the single largest lever:
   convergence went from 43/336 to 334/336 snapshots on an earlier two-week test window.
4. **Fixed shunt capacitors**, sized to 70% of each bus's own peak reactive demand, closed the
   remaining gap to full convergence with generator reactive capability left at a realistic 0.9
   power factor (rather than loosening it to an unrealistic value).

An independent cross-check by exporting the same network to MATPOWER did not reproduce this
convergence, despite the export being verified numerically exact (DC power flow branch results
matched PyPSA to the decimal). Seven hypotheses for the discrepancy were tested and ruled out; the
working conclusion is a genuine robustness difference between PyPSA's and MATPOWER's specific
Newton-Raphson implementations on this data, not a data or export bug.

**The 315kV main working network has not yet converged under full AC power flow**, in any PV
threshold configuration tried. This remains open -- see
[ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md).

### Generic assumptions used only for AC PF

AC power flow needs reactive power data the LOPF/DC stages don't model. `run_pf.py` fills this in
with generic, documented assumptions at the point they're introduced -- see
[ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for the full list (load and
generator power factors, transformer X/R ratio, PV/PQ classification thresholds).
