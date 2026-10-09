# AC PF on the rebuilt 735kV network for: no reactive support, 70% series
# compensation, and series + shunts (fixed 1.0/0.7 sizing, plus the grid-best
# pair for comparison). Writes a summary CSV next to this script.
import os, sys, warnings, logging
import numpy as np, pandas as pd, pypsa
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)
sys.path.insert(0, ".")
import run_pf
import add_shunt_impedance as asi
from apply_series_compensation import TARGET_LINES, COMPENSATION_FRACTION
from correct_sending_end_circuits import NEW_NUM_PARALLEL
R = os.path.dirname(os.path.abspath(__file__))
ids = sorted(set(TARGET_LINES) | set(NEW_NUM_PARALLEL))

def case(kind):
    if kind == "grid_best":
        base = pypsa.Network(os.path.join(R, "elec_735kv.nc"))
        n = pypsa.Network(os.path.join(R, "elec_735kv_shunt.nc"))
        # rescale capacitors from 0.7 to 0.4 of worst-hour need
        cap = n.shunt_impedances.index[n.shunt_impedances.b > 0]
        n.shunt_impedances.loc[cap, "b"] *= 0.4 / 0.7
        return n
    n = pypsa.Network(os.path.join(R, "elec_735kv_shunt.nc" if kind == "shunts" else "elec_735kv.nc"))
    if kind == "none":
        pl = [l for l in TARGET_LINES if l in n.lines.index]
        n.lines.loc[pl, "x"] /= (1 - COMPENSATION_FRACTION)
    return n

rows = {}
for kind, label in [("none", "No reactive support"), ("series", "Series compensation (70%)"), ("shunts", "Series + shunts (1.0 / 0.7)")]:
    n = case(kind); n.calculate_dependent_values(); n.determine_network_topology()
    run_pf.prepare_for_ac_pf(n, pv_min_capacity=0.0, pv_max_load_ratio=1.0)
    n.lpf(); c = n.pf(n.snapshots, use_seed=True).converged.all(axis=1)
    v = n.buses_t.v_mag_pu.loc[c]; L = n.loads_t.p_set.sum(1)[c]
    p0, q0 = n.lines_t.p0.loc[c], n.lines_t.q0.loc[c]
    S = np.sqrt(p0**2 + q0**2); sn = n.lines.s_nom
    sn_old = sn.copy(); sn_old[ids] = sn[ids] / n.lines.num_parallel[ids]
    a = np.degrees(n.buses_t.v_ang.loc[c]); d = np.abs(a[n.lines.bus0.values].values - a[n.lines.bus1.values].values)
    sh = n.shunt_impedances; q = sh.b * n.buses.v_nom.reindex(sh.bus).values**2
    r = {"Converged": f"{c.sum()}/168", "Voltage range (pu)": f"{v.min().min():.3f}-{v.max().max():.3f}",
         "Shunts": f"{int((q>0).sum())} cap (+{q[q>0].sum():,.0f} MVAr) / {int((q<0).sum())} reac ({q[q<0].sum():,.0f} MVAr)" if len(sh) else "-"}
    for lab, m in [("Under (<0.95)", v < 0.95), ("Over (>1.05)", v > 1.05)]:
        bh = m.sum(1); s = bh > 0; byh = bh.groupby(bh.index.hour).sum()
        r[f"{lab} buses"] = int(m.any().sum()); r[f"{lab} hours"] = int(s.sum())
        r[f"{lab} hours of day"] = ",".join(str(h) for h in byh[byh > 0].index) or "-"
        r[f"{lab} worst hours"] = ",".join(str(h) for h in byh.sort_values(ascending=False).head(4).index if byh[h] > 0) or "-"
        r[f"{lab} demand (GW)"] = f"{L[s].min()/1e3:.1f}-{L[s].max()/1e3:.1f}" if s.any() else "-"
    r["Largest line angle (deg)"] = round(d.max(), 1); r["Lines > 30 deg"] = int((d.max(0) > 30).sum())
    r["Max loading P (%)"] = round((p0.abs() / sn).max().max() * 100, 1); r["Max loading S (%)"] = round((S / sn).max().max() * 100, 1)
    r["Max loading S, unscaled s_nom (%)"] = round((S / sn_old).max().max() * 100, 1)
    rows[label] = r
df = pd.DataFrame(rows); df.to_csv(os.path.join(R, "summary_735kv_acpf.csv"))
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60)
print(df.to_string())
