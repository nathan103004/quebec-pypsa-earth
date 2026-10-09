# LOPF dispatch, DC PF and AC PF on the 315kV network: baseline (3 x St Clair
# everywhere, networks_rebuild_weekcalib) vs this test (HQ cold rating on 735kV).
import os, sys, warnings, logging
import numpy as np, pandas as pd, pypsa
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)
sys.path.insert(0, ".")
import run_pf
T = os.path.dirname(os.path.abspath(__file__))
cases = {"3x St Clair (baseline)": "networks_rebuild_weekcalib/elec_solved.nc", "HQ cold on 735kV (test)": os.path.join(T, "elec_solved.nc")}
rows = {}
for label, f in cases.items():
    r = {}
    n = pypsa.Network(f)
    hv = n.lines.v_nom >= 735
    r["735kV line capacity (GW)"] = round(n.lines.s_nom[hv].sum() / 1e3)
    r["315kV line capacity (GW)"] = round(n.lines.s_nom[~hv].sum() / 1e3)
    g = n.generators; real = g.index[(g.carrier != "load_shedding")]
    disp = n.generators_t.p[real].T.groupby(g.carrier[real]).sum().T.mean()
    r["LOPF shed (MW)"] = round(n.generators_t.p[g.index[g.carrier == "load_shedding"]].sum(1).mean())
    r["LOPF hydro (MW)"] = round(disp.get("ror", 0) + n.storage_units_t.p.sum(1).mean()); r["LOPF wind (MW)"] = round(disp.get("onwind", 0))
    P = n.lines_t.p0.abs() / n.lines.s_nom
    r["LOPF max loading (P)"] = f"{P.max().max()*100:.1f}% ({P.max().idxmax()})"
    r["LOPF 735kV lines >=90% / at limit"] = f"{int((P.loc[:, hv].max() >= 0.9).sum())} / {int((P.loc[:, hv].max() >= 0.999).sum())}"
    r["LOPF 735kV mean loading"] = f"{P.loc[:, hv].values.mean()*100:.1f}%"
    for c in ("generators", "storage_units"):
        pt = getattr(n, c + "_t"); pt["p_set"] = pt["p"].copy()
    n.links_t.p_set = n.links_t.p0.copy()
    n.calculate_dependent_values(); n.determine_network_topology(); n.lpf()
    a = np.degrees(n.buses_t.v_ang); d = np.abs(a[n.lines.bus0.values].values - a[n.lines.bus1.values].values)
    r["DC largest line angle"] = f"{d.max():.1f} deg"; r["DC lines > 30 deg"] = int((d.max(0) > 30).sum())
    base = n.copy()
    for pvmin, pvr in [(100.0, 0.10), (0.0, 1.0)]:
        m = base.copy(); run_pf.prepare_for_ac_pf(m, pv_min_capacity=pvmin, pv_max_load_ratio=pvr)
        m.lpf(); c = m.pf(m.snapshots, use_seed=True).converged.all(axis=1)
        v = m.buses_t.v_mag_pu.loc[c]; sane = ((v > 0.7) & (v < 1.3)).all(1)
        key = "AC (PV rule 100MW/10%)" if pvmin else "AC (all gen buses PV)"
        r[key] = f"{int(c.sum())}/168 converged, {int(sane.sum())} plausible" + (f", V {v.min().min():.2f}-{v.max().max():.2f}" if c.any() else "")
    rows[label] = r
df = pd.DataFrame(rows); df.to_csv(os.path.join(T, "summary_315kv.csv")); pd.set_option("display.width", 200); print(df.to_string())
