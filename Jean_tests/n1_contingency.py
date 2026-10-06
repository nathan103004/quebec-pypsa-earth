# Analyse de contingence N-1 sur un reseau MATPOWER (.m).
# On enleve chaque ligne une par une, on relance le power flow, et on classe
# les pannes les plus critiques. Puis on dessine la carte du pire cas.
# Usage : python n1_contingency.py network/quebec_735kv.m
import re, sys
import numpy as np, pandas as pd, pandapower as pp
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from pandapower.converter.pypower import from_ppc

def lire_mpc(chemin):
    txt=open(chemin).read()
    mpc={"version":"2","baseMVA":float(re.search(r"mpc\.baseMVA\s*=\s*([\d.]+)",txt).group(1))}
    for nom in ["bus","gen","branch"]:
        bloc=re.search(r"mpc\.%s\s*=\s*\[(.*?)\];"%nom,txt,re.S).group(1)
        rows=[[float(x) for x in l.split("%")[0].strip().rstrip(";").split()]
              for l in bloc.split("\n") if l.split("%")[0].strip().rstrip(";").strip()]
        mpc[nom]=np.array(rows)
    return mpc

chemin = sys.argv[1] if len(sys.argv)>1 else "network/quebec_735kv.m"
net = from_ppc(lire_mpc(chemin), f_hz=60)

# --- cas de base ---
pp.runpp(net, numba=False)
base_minv = net.res_bus.vm_pu.min()
base_maxload = net.res_line.loading_percent.max()
print(f"CAS DE BASE : tension min {base_minv:.3f} pu · charge ligne max {base_maxload:.0f} %\n")

# --- balayage N-1 ---
lignes=[]
for li in net.line.index:
    net.line.at[li,"in_service"]=False
    frm,to = int(net.line.at[li,"from_bus"]), int(net.line.at[li,"to_bus"])
    try:
        pp.runpp(net, numba=False)
        vm=net.res_bus.vm_pu
        lignes.append({"ligne":li,"de":frm,"vers":to,"etat":"converge",
                       "V_min":vm.min(),"charge_max":net.res_line.loading_percent.max(),
                       "hors_bande":int(((vm<0.95)|(vm>1.05)).sum())})
    except Exception:
        lignes.append({"ligne":li,"de":frm,"vers":to,"etat":"EFFONDREMENT",
                       "V_min":np.nan,"charge_max":np.nan,"hors_bande":99})
    net.line.at[li,"in_service"]=True

df=pd.DataFrame(lignes)
# severite : effondrement d'abord, puis tension la plus basse, puis surcharge
df["rang"]=df["etat"].eq("EFFONDREMENT").astype(int)
df=df.sort_values(["rang","V_min","charge_max"],ascending=[False,True,False])

print("PANNES LES PLUS CRITIQUES (top 10) :")
print(df.drop(columns="rang").head(10).to_string(index=False))
n_eff=(df.etat=="EFFONDREMENT").sum()
print(f"\n{n_eff} ligne(s) sur {len(df)} provoquent un effondrement si elles tombent.")

# --- carte du pire cas qui converge encore ---
pire = df[df.etat=="converge"].iloc[0]
li=int(pire["ligne"])
net.line.at[li,"in_service"]=False
pp.runpp(net, numba=False)
vm=net.res_bus.vm_pu

G=nx.Graph(); G.add_nodes_from(net.bus.index)
for _,r in net.line.iterrows():
    if r.in_service: G.add_edge(int(r.from_bus),int(r.to_bus))
pos=nx.spring_layout(G,seed=42,k=1.2/np.sqrt(len(G)))
fig,ax=plt.subplots(figsize=(10,8))
nx.draw_networkx_edges(G,pos,ax=ax,alpha=.35,width=1,edge_color="#888")
# ligne coupee en rouge
f,t=int(pire["de"]),int(pire["vers"])
if f in pos and t in pos:
    ax.plot([pos[f][0],pos[t][0]],[pos[f][1],pos[t][1]],color="#c0392b",ls="--",lw=2.2,zorder=1,label="Ligne coupée")
nodes=nx.draw_networkx_nodes(G,pos,ax=ax,node_color=vm.reindex(list(G.nodes())).values,
        cmap="coolwarm",vmin=0.9,vmax=1.1,node_size=70,edgecolors="#333",linewidths=.4)
plt.colorbar(nodes,ax=ax,fraction=.046,pad=.02).set_label("Tension (pu)")
ax.set_title(f"Pire N-1 : coupure ligne {li} ({f}→{t})\nV min {vm.min():.3f} pu · "
             f"{int(((vm<0.95)|(vm>1.05)).sum())} nœuds hors bande",fontsize=11)
ax.legend(loc="upper right",fontsize=9); ax.axis("off")
plt.tight_layout(); plt.savefig("n1_pire_cas.png",dpi=140,bbox_inches="tight")
print("\nCarte du pire cas enregistree : n1_pire_cas.png")
