# Fait tourner un power flow AC sur un reseau MATPOWER (.m) de Nathan, avec pandapower.
# Usage : python power_flow_reel.py network/quebec_735kv.m
import re, sys
import numpy as np, pandapower as pp
from pandapower.converter.pypower import from_ppc

def lire_mpc(chemin):
    txt = open(chemin).read()
    mpc = {"version": "2",
           "baseMVA": float(re.search(r"mpc\.baseMVA\s*=\s*([\d.]+)", txt).group(1))}
    for nom in ["bus", "gen", "branch"]:
        bloc = re.search(r"mpc\.%s\s*=\s*\[(.*?)\];" % nom, txt, re.S).group(1)
        lignes = []
        for l in bloc.split("\n"):
            l = l.split("%")[0].strip().rstrip(";").strip()
            if l:
                lignes.append([float(x) for x in l.split()])
        mpc[nom] = np.array(lignes)
    return mpc

chemin = sys.argv[1] if len(sys.argv) > 1 else "network/quebec_735kv.m"
mpc = lire_mpc(chemin)
net = from_ppc(mpc, f_hz=60)
print(f"Reseau : {chemin}")
print(f"  {len(net.bus)} nœuds, {len(net.line)} lignes, {len(net.gen)} generateurs")
try:
    pp.runpp(net, numba=False)
    vm = net.res_bus.vm_pu
    print("  CONVERGE")
    print(f"  Tensions : {vm.min():.3f} - {vm.max():.3f} pu")
    print(f"  Nœuds < 0.95 pu : {(vm < 0.95).sum()}   > 1.05 pu : {(vm > 1.05).sum()}")
    print(f"  Charge de ligne max : {net.res_line.loading_percent.max():.0f} %")
    print(f"  Pertes actives : {net.res_line.pl_mw.sum():.0f} MW")
except Exception as e:
    print(f"  DIVERGE ({type(e).__name__}) — normal pour le 315 kV")