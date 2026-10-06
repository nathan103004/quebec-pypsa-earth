# Visualise un reseau MATPOWER (.m) : carte coloree par tension + histogramme.
# Usage : python visu_reseau.py network/quebec_735kv.m
import re, sys
import numpy as np, pandapower as pp
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from pandapower.converter.pypower import from_ppc

def lire_mpc(chemin):
    txt = open(chemin).read()
    mpc = {"version":"2","baseMVA":float(re.search(r"mpc\.baseMVA\s*=\s*([\d.]+)",txt).group(1))}
    for nom in ["bus","gen","branch"]:
        bloc = re.search(r"mpc\.%s\s*=\s*\[(.*?)\];"%nom, txt, re.S).group(1)
        rows=[]
        for l in bloc.split("\n"):
            l=l.split("%")[0].strip().rstrip(";").strip()
            if l: rows.append([float(x) for x in l.split()])
        mpc[nom]=np.array(rows)
    return mpc

chemin = sys.argv[1] if len(sys.argv)>1 else "network/quebec_735kv.m"
nom_reseau = chemin.split("/")[-1].split("\\")[-1]
net = from_ppc(lire_mpc(chemin), f_hz=60)
try:
    pp.runpp(net, numba=False); converge=True
except Exception:
    converge=False

# graphe
G = nx.Graph()
G.add_nodes_from(net.bus.index)
for _,r in net.line.iterrows():
    G.add_edge(int(r.from_bus), int(r.to_bus))
pos = nx.spring_layout(G, seed=42, k=1.2/np.sqrt(len(G)))

# taille des noeuds = demande locale
charge = np.zeros(len(net.bus))
idx = {b:i for i,b in enumerate(net.bus.index)}
for _,r in net.load.iterrows():
    charge[idx[int(r.bus)]] += r.p_mw
tailles = 40 + 260*(charge/charge.max() if charge.max()>0 else charge)

fig,(ax1,ax2) = plt.subplots(1,2, figsize=(14,6.5), gridspec_kw={"width_ratios":[2,1]})
nx.draw_networkx_edges(G,pos,ax=ax1,alpha=.35,width=1,edge_color="#888")

if converge:
    vm = net.res_bus.vm_pu.reindex(list(G.nodes())).values
    nodes = nx.draw_networkx_nodes(G,pos,ax=ax1,node_color=vm,cmap="coolwarm",
                                   vmin=0.9,vmax=1.1,node_size=tailles,edgecolors="#333",linewidths=.4)
    cb = plt.colorbar(nodes,ax=ax1,fraction=.046,pad=.02); cb.set_label("Tension (pu)")
    # slack
    sb = int(net.ext_grid.bus.iloc[0])
    ax1.scatter(*pos[sb],s=320,marker="*",c="gold",edgecolors="k",linewidths=.8,zorder=5,label="Slack")
    ax1.legend(loc="upper right",fontsize=9)
    titre = f"{nom_reseau} — CONVERGE · {len(net.bus)} nœuds · tension {vm.min():.3f}–{vm.max():.3f} pu"
    ax2.hist(vm,bins=18,color="#4a90a4",edgecolor="white")
    ax2.axvline(0.95,color="#b23b3b",ls="--",lw=1.3); ax2.axvline(1.05,color="#b23b3b",ls="--",lw=1.3)
    ax2.axvspan(0.95,1.05,color="#2f7d4f",alpha=.08)
    ax2.set_xlabel("Tension (pu)"); ax2.set_ylabel("Nombre de nœuds")
    ax2.set_title("Distribution des tensions\n(bande 0,95–1,05 en vert)",fontsize=10)
else:
    nx.draw_networkx_nodes(G,pos,ax=ax1,node_color="#bbb",node_size=tailles,edgecolors="#333",linewidths=.4)
    titre = f"{nom_reseau} — NE CONVERGE PAS · {len(net.bus)} nœuds (topologie seule)"
    ax2.text(.5,.5,"Power flow\nnon convergé",ha="center",va="center",fontsize=13,color="#b23b3b")
    ax2.axis("off")

ax1.set_title(titre,fontsize=11); ax1.axis("off")
plt.tight_layout()
sortie = nom_reseau.replace(".m","")+"_visu.png"
plt.savefig(sortie,dpi=140,bbox_inches="tight")
print("Image enregistree :",sortie)
