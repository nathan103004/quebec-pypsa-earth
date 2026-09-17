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

**Neither reduced network fully converges under AC PF at current real demand.** This is an open
investigation, not a solved problem. Current state:

| Network | Convergence at current demand | At 85% of current demand |
|---|---|---|
| 315kV (`elec_solved.nc`, 208 buses) | 0/168 | 0/168 -- no improvement |
| 735kV (`elec_735kv.nc`, 58 buses, 109 lines) | 50/168 | **168/168** |

### What's been ruled out on the 735kV network

An extensive series of interventions, each tested directly against the real 109-line network at
full demand, all failed to meaningfully improve convergence:

- 10x local reactive compensation at every bus: 1/71 (of the snapshots that fail at baseline)
- Strengthening any single stressed corridor, or the top 3/6 most-loaded corridors (2x capacity): 0/71
- Doubling capacity on **all 109 lines network-wide**: 0/71
- Continuation power flow, warm-started from a converged 95%-demand solution: 0/71
- Removing any single PV bus's voltage-holding constraint: 0/71
- Modal (eigenvector) analysis consistently identifies the same critical bus cluster (132, 133,
  3554, 136, 1081, 195, 603, 1717 -- the 735/765kV bridge area) across every tested snapshot, but
  strengthening exactly that cluster: 0/71

Only a uniform reduction of real+reactive power at every bus simultaneously works (71/71 of the
previously-failing snapshots, then confirmed as 168/168 across the full network at 85% scale).
This pattern -- nothing localized helps, only reducing total system loading helps -- means the
constraint is systemic (the aggregate real+reactive injection pattern relative to the network's
whole impedance structure), not attributable to one fixable line, bus, or generator.

### A likely contributing cause: data quality, not physics

The critical bus cluster's local demand comes **entirely from reassignment**, not native load
(zero native demand at buses 3554/136/1081 in `elec_full.nc`). Tracing the reassignment:
`reduce_voltage_network.py`'s known straight-line-nearest-neighbor flaw (already documented as
causing a 257km misassignment elsewhere) pools **28 loads onto bus 3554 from as far as 281.6km
away** (1,482 MW total), and 21 loads onto bus 1081 from up to 87.9km away (1,084 MW). In reality
those substations almost certainly connect to closer backbone buses via the real grid, not through
this one thin bridge node. This looks like a real data-quality artifact concentrating demand on
the network's one electrically weak corridor -- not yet fixed (the fix, graph-distance
reassignment, already exists as a pattern in `reduce_to_735kv.py` but hasn't been retrofitted into
`reduce_voltage_network.py`).

### The 315kV network fails differently

Unlike the 735kV network, reducing demand 15% does **not** help the 315kV network at all (still
0/168, with far more extreme numerical blowup -- 273/276 lines "loaded" past absurd percentages).
This isn't a loadability-margin problem like the 735kV case; something structurally different is
going on, and it hasn't been diagnosed.

### Generic assumptions used only for AC PF

AC power flow needs reactive power data the LOPF/DC stages don't model. `run_pf.py` fills this in
with generic, documented assumptions at the point they're introduced -- see
[ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md) for the full list (load and
generator power factors, transformer X/R ratio, PV/PQ classification thresholds, and the current
demand-following reactive-compensation model).
