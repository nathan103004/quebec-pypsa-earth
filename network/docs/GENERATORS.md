# Generators and storage

Fleet composition on the main working network (`elec_solved.nc`, 205 buses). Capacities are
nameplate.

| Carrier | Count | Capacity (MW) | Source |
|---|---|---|---|
| Run-of-river hydro (`ror`) | 35 | 13,570 | Real HQ hydro station list + 2022 hourly `Hydraulique` dispatch |
| Hydro storage (`StorageUnit`, `hydro`) | 22 | 28,583 | Same station list; reservoir plants modeled with storage |
| Onshore wind (`onwind`) | 33 | 3,495 | Real HQ wind farm list, scaled to the official 3,933 MW; 6 farms (438 MW) are on buses dropped by island extraction (see [NETWORKS.md](NETWORKS.md)). Dispatched by weather-derived capacity factor |
| Solar (`solar`) | 3 | 10 | Real facility list (2 official stations: Gabrielle-Bodis 8 MW, Robert-A.-Boyd 2 MW); dispatched by weather-derived capacity factor |
| OCGT (gas) | 1 | 411 | Real 2022 hourly `Thermique` dispatch |
| Load shedding | 189 | (sized to local peak) | Synthetic VOLL placeholder, $10,000/MWh, one per load bus |
| Slack placeholder (`AC`) | 1 | 7,722 (`p_max_pu = 0`) | Carries the slack flag at a storage-only bus; PyPSA does not let a storage unit be slack. Cannot generate |

## Dispatch vs. actual, 2022-01-01 to 2022-01-07

Weekly means. Actual dispatch is Hydro-Québec's hourly generation by source
(`2022-sources-electricite-quebec.csv`).

| Carrier | Model capacity (MW) | Actual capacity (MW) | Model dispatch (MW) | Actual dispatch (MW) | Model utilization | Actual utilization |
|---|---|---|---|---|---|---|
| Hydro | 42,153 | 42,313 | 28,164 | 31,588 | 66.8% | 74.7% |
| Onshore wind | 3,495 | 3,933 | 690 | 746 | 19.7% | 19.0% |
| Solar | 10 | 10 | 0 | 0 | 0% | 0% |
| OCGT (gas) | 411 | 411 | 16 | 15 | 3.9% | 3.6% |
| Other ("Autres") | - | - | - | 553 | - | - |
| **Total** | **46,069** | **46,667** | **28,871** | **32,902** | **62.7%** | **69.3%** |

Actual total utilization excludes "Autres", whose capacity isn't in the data. Actual generation
exceeds actual demand (28,870 MW) by about 4 GW of net exports, which the model does not include.

Capacity not in the model: 438 MW of wind (dropped islands), 160 MW of hydro (6 stations with no
anchor, below), and 706 MW of IPP-operated hydro (excluded from both columns).

Load-shedding generators are a modeling device, not real capacity: they exist so LOPF can shed
load at a heavy cost penalty instead of failing to solve, and their dispatch is the metric used to
report unserved load (currently 0% on the solved network).

Churchill Falls' real 5,428 MW is included in the hydro storage total above (`465
hydro-Churchill-Falls`, at bus `465-735kv`, added by `add_churchill_falls_tie.py`).

## Generation-capacity correction (manual step)

Applied to `elec_reduced.nc` by hand; no script does it. Everything needed is in
`hq_hydro_stations_official.csv` (HQ's official station list with capacities and rivers),
`hq_major_facilities_2023.csv` and HQ's published wind and solar totals.

**Hydro capacity correction:** the original attach was missing 20 real HQ stations (~1,627 MW)
that don't name-match between `hq_major_facilities_2023.csv` and `hq_hydro_stations_official.csv`
-- either absent from the major-facilities list entirely, or present there with no coordinates.
14 of them (1,542 MW, including Eastmain-1 at 480 MW) were added manually, each attached to the
bus of an already-modeled station sharing the same river (e.g. Eastmain-1 -> Bernard-Landry's bus,
both on the Eastmain river) -- no plant-specific inflow data exists for them, so run-of-river units
are left at the default `p_max_pu=1.0` and reservoir units at zero inflow (matching
`attach_real_generators.py`'s own no-inflow fallback). The remaining 6 (~160 MW: Chute-Hemmings,
Drummondville, Lac-Robertson, Mitis-1, Mitis-2, Sept-Chutes) have no modeled station on their
river to anchor to and were left out. Hydro capacity now totals 42,153 MW against HQ's official
42,313 MW (36,885 MW HQ-owned stations + 5,428 MW Churchill Falls, excluding 706 MW of
IPP-operated hydro that isn't modeled at all).

**Wind and solar capacity correction:** both were scaled uniformly (per-generator, within each
carrier) to match HQ's official installed totals -- wind from 3,721.8 MW (39 units, pre-island-
extraction) to 3,933 MW (44 IPP-operated farms), solar from 12.3 MW (3 units) to 10.0 MW (2
stations). Station counts weren't reconciled 1:1 against the official lists, only the aggregate
capacity.

## Dispatch ceilings

Hydro (ror) and gas (OCGT) are bounded by their own real 2022 hourly dispatch from Hydro-Québec's
published generation-by-source data, not by a flat capacity factor -- `p_max_pu` at each hour is
that unit's share of the fleet-wide real dispatch ratio for its carrier at that hour, with a 10%
margin on top (`CEILING_MARGIN`) so the model isn't forced to never exceed history. Wind and solar
use standard weather-derived capacity factors from PyPSA-Earth's own renewable resource pipeline
(unchanged from the upstream default). Hydro storage reservoirs are bounded by the same real
fleet-wide ratio recovered from the hourly inflow data already attached to each unit.

## Marginal costs

Assigned per carrier from PyPSA-Earth's default `resources/costs_2030_elec.csv` (2030 cost
projections -- not Quebec-specific, used only to rank dispatch order between carriers). OCGT is
set to -0.3 $/MWh so the solver dispatches it up to its real ceiling before shedding load.
Onshore wind is set to -0.1 $/MWh: at 0 it ties with run-of-river and storage hydro (also 0), and
the solver can curtail wind arbitrarily. At -0.1 all available wind is dispatched.

## Slack bus

Assigned to whichever bus holds the largest *total* generation capacity (Generator + StorageUnit
combined) on the network's largest AC-connected component. PyPSA's own bus-control logic
(`find_bus_controls()` / `find_slack_bus()`) only ever reads `Generator.control`, never
`StorageUnit.control` -- confirmed directly from `pypsa/pf.py` source -- so a storage-only bus
gets a zero-dispatch placeholder `Generator` added so it can still be a slack candidate on equal
terms. On the current solved network this lands on bus 339 (7,722 MW combined: the
`339 hydro-La-Grande-2-A` and `339 hydro-Robert-Bourassa` storage units), via the placeholder
`339 slack-placeholder`. The placeholder has `p_max_pu = 0`, so LOPF cannot dispatch it.

The 735kV network uses `114 ror` (Robert-Bourassa run-of-river aggregate) as slack instead, set by
`reduce_to_735kv.py` (`--slack-generator`).
