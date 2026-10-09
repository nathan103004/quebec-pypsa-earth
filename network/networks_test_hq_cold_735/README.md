# Test: HQ cold thermal rating on 735kV lines

Isolated test. Nothing in `networks_current/` or the committed scripts is changed.

## Setup

- Input: `networks_current/elec_reduced.nc`, run through `run_lopf_main_island.py` (week-calibrated
  demand, wind marginal cost -0.1) via `run_lopf_hq_cold_735.py`.
- 735/765kV line `s_nom` = HQ table cold rating, sqrt(3) x 735kV x 6,200 A = 7,893 MVA per circuit.
  315/345kV lines keep 3 x St Clair.
- 735kV network: `reduce_to_735kv.py`, then `build_735kv_corrections.py` (sending-end circuit counts,
  70% series compensation on 15 lines from uncompensated per-km x, `s_nom` scaled with circuit count,
  slack `114 ror`).
- Shunts: `add_shunt_impedance.py --reactor-scale 1.0 --capacitor-scale 0.7`.

## 315kV results (`summary_315kv.csv`)

| | 3 x St Clair (baseline) | HQ cold on 735kV |
|---|---|---|
| LOPF load shed | 0 MW | 0 MW |
| LOPF max loading (P) | 70.1% | 80.4% |
| 735kV lines at limit | 0 | 0 |
| DC largest line angle (lines > 30 deg) | 59.2 deg (10) | 72.0 deg (11) |
| AC PF converged (all generator buses PV) | 25/168, 0 plausible | 12/168, 0 plausible |

## 735kV AC PF results (`summary_735kv_acpf.csv`)

| | No reactive support | 70% series | 70% series + shunts (1.0 / 0.7) |
|---|---|---|---|
| Converged | 112/168 | 168/168 | 168/168 |
| Voltage range (pu) | 0.780-1.138* | 0.901-1.123 | 0.964-1.061 |
| Shunts | - | - | 8 cap (+3,950 MVAr) / 11 reac (-6,073 MVAr) |
| Buses / hours < 0.95 pu | 23 / 69* | 17 / 103 | 0 / 0 |
| Buses / hours > 1.05 pu | 17 / 112* | 14 / 168 | 19 / 18 (overnight, 22.0-24.9 GW) |
| Largest line angle (lines > 30 deg) | 51.4 deg (13)* | 24.6 deg (0) | 24.6 deg (0) |
| Max loading P / S | 63.7% / 67.1%* | 75.3% / 81.3% | 74.8% / 75.2% |

\* Converged hours only.
