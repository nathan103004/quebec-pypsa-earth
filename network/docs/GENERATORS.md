# Generators and storage

Fleet composition on the main working network (`elec_solved.nc`, 208 buses). Counts are real
units after `reduce_voltage_network.py`'s reassignment (none dropped); capacities are nameplate.

| Carrier | Count | Capacity (MW) | Source |
|---|---|---|---|
| Run-of-river hydro (`ror`) | 25 | 12,675 | Real HQ hydro station list + 2022 hourly `Hydraulique` dispatch |
| Hydro storage (`StorageUnit`, `hydro`) | 18 | 27,936 | Same station list; reservoir plants modeled with storage |
| Onshore wind (`onwind`) | 35 | 3,412 | Real HQ wind farm list; dispatched by weather-derived capacity factor |
| Solar (`solar`) | 3 | 12 | Real facility list; dispatched by weather-derived capacity factor |
| OCGT (gas) | 1 | 411 | Real 2022 hourly `Thermique` dispatch |
| Load shedding | 191 | 39,260 (sized to local peak) | Synthetic VOLL placeholder, $10,000/MWh, one per load bus |
| Churchill Falls import (`AC`, at slack) | 1 | 7,722 | Real interconnection, added by `add_churchill_falls_tie.py` |

Load-shedding generators are a modeling device, not real capacity: they exist so LOPF can shed
load at a heavy cost penalty instead of failing to solve, and their dispatch is the metric used to
report unserved load (currently 0% on the solved network).

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
set to a small negative marginal cost so the solver prefers dispatching it up to its real ceiling
over shedding load; onshore wind is set to zero.

## Slack bus

Assigned to whichever bus holds the largest *total* generation capacity (Generator + StorageUnit
combined) on the network's largest AC-connected component. PyPSA's own bus-control logic
(`find_bus_controls()` / `find_slack_bus()`) only ever reads `Generator.control`, never
`StorageUnit.control` -- confirmed directly from `pypsa/pf.py` source -- so a storage-only bus
gets a zero-dispatch placeholder `Generator` added so it can still be a slack candidate on equal
terms. On the current solved network this lands on bus 340 (7,722 MW, the Churchill Falls tie).
