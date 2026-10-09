# Applies the circuit-count correction and 70% series compensation to the
# rebuilt 735kV network from the uncompensated per-km HQ values, so every
# compensated line ends at exactly 70% (no stacking on earlier compensation).
# Also sets slack to 114 ror and scales s_nom with the corrected circuit count.
import sys, warnings, logging, pandas as pd, pypsa
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)
sys.path.insert(0, ".")
from apply_series_compensation import TARGET_LINES, COMPENSATION_FRACTION
from correct_sending_end_circuits import NEW_NUM_PARALLEL
from st_clair import load_line_params

path = sys.argv[1]
n = pypsa.Network(path)
row = load_line_params().loc[735]
ids = sorted(set(TARGET_LINES) | set(NEW_NUM_PARALLEL))
old_np = n.lines.loc[ids, "num_parallel"].copy()
for l in ids:
    np_ = NEW_NUM_PARALLEL.get(l, n.lines.at[l, "num_parallel"])
    L = n.lines.at[l, "length"]
    n.lines.at[l, "num_parallel"] = np_
    n.lines.at[l, "r"] = row.r_ohm_per_km * L / np_
    n.lines.at[l, "x"] = row.xl_ohm_per_km * L / np_ * ((1 - COMPENSATION_FRACTION) if l in TARGET_LINES else 1.0)
    n.lines.at[l, "b"] = row.bc_uS_per_km * 1e-6 * L * np_
n.lines.loc[ids, "s_nom"] *= n.lines.loc[ids, "num_parallel"] / old_np
n.generators["control"] = "PQ"
n.generators.loc["114 ror", "control"] = "Slack"
n.export_to_netcdf(path)
print(pd.DataFrame({"np": n.lines.num_parallel[ids], "x": n.lines.x[ids].round(2), "comp": [COMPENSATION_FRACTION if l in TARGET_LINES else 0 for l in ids], "s_nom": n.lines.s_nom[ids].round(0)}).to_string())
