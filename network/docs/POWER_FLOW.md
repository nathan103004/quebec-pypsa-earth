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

**Neither reduced network fully converges under AC PF at current real demand.** Line parameters
(r/x/b) come from Hydro-Quebec's own line-characteristics table -- see
[DATA_SOURCES.md](DATA_SOURCES.md). Current state:

| Network | Convergence at current demand | At reduced demand |
|---|---|---|
| 315kV (`elec_solved.nc`, 205 buses) | 0/168 | see the 315kV section below |
| 735kV (`elec_735kv.nc`, 58 buses, 109 lines) | 153/168 | **168/168 at 86%** |

![735kV backbone AC PF results at 86% demand -- voltage deviation, line loading, slack/PV buses](../quebec_735kv_ac_pf_map.png)

### Fixing divergence: two components on the 735kV network

**Series compensation** (`apply_series_compensation.py`) -- a capacitor bank modeled in series
with the conductor on the ten longest lines (250-500km) feeding buses 308, 1291, and 312, the only
three points of AC PF voltage collapse on this network (found via continuation power flow; genuine
collapse, confirmed by Newton-Raphson's Jacobian going exactly singular there, not a solver quirk).
Modeled as `x_new = x * (1 - 0.5)` -- a straight 50% reduction of each line's own reactance.
Needed because these three buses have no local voltage-controlling generation, so their voltage
depends entirely on how much drop accumulates over a very long, high-reactance line; cutting that
reactance directly raises the loadability limit. Result: 100%-demand convergence rose from 84/168
to 155/168, and the full-convergence demand level from 62% to 86%.

**Switched shunt reactors** (`add_shunt_reactors.py`) -- a generator with a fixed negative
`q_set` at 8 buses (469, 310, 3319, 345, 312, 308, 150, 2944), active only when total system demand
is below that network's own mean (bus 469 is always-on instead, since its voltage doesn't track
system demand). Needed because the same long lines' own charging pushes voltage up to 1.13pu under
*light* load (Ferranti effect) -- the opposite problem from the collapse above, at the opposite end
of the demand range. Switching (rather than a permanent shunt) matters because buses 308/312 need
reactive *support* under heavy load from the series compensation; a fixed reactor there would
fight it during exactly the hours it's needed. Result: 469, 308, 150 and 2944 fully cleared 1.05pu,
345 and 312 dropped to a single snapshot over, and 310 and 3319 roughly halved (snapshots over
1.05pu: 128 -> 54 and 130 -> 56). Cost: 100%-demand convergence 155/168 -> 153/168; the 86%
full-convergence level is unchanged.

Both components' sizing (`COMPENSATION_FRACTION = 0.50`, reactor ratings 900-2,500 MVAr) are
generic, tuned-not-measured assumptions -- see
[ASSUMPTIONS_AND_LIMITATIONS.md](ASSUMPTIONS_AND_LIMITATIONS.md).

### The 315kV network

0/168 under AC PF. One known setup gap: `run_pf.py` doesn't copy the LOPF dispatch into `p_set`
(which `n.pf()` reads), so on this network every generator, storage unit and link injects zero
real power in that test and the slack carries the whole ~30 GW load. With the dispatch synced it
still fails (0/168), and the divergence localizes to two areas: a radial spur of buses 240, 1548,
1762, 1772, 2207, 3719, 1693, 3836 and 2812 (with it removed, the lowest-demand hour converges),
and, at higher demand, a wider set of buses that includes 308 and 469 -- also problem buses on the
735kV network (the shunt reactors exist only in the 735kV file). Root cause not yet found.
