import pypsa
n = pypsa.Network()
n.add("Bus", "b1", v_nom=735)
n.add("Bus", "b2", v_nom=735)
n.add("Generator", "slack", bus="b1", control="Slack")
n.add("Line", "L", bus0="b1", bus1="b2", x=50, r=5)      # Ohms
n.add("Load", "charge", bus="b2", p_set=1000, q_set=300)  # MW, MVAr
n.pf()
print(n.buses_t.v_mag_pu)   # b2 ≈ 0.957 pu : la charge tire la tension vers le bas
print(n.buses_t.v_ang)      # b2 ≈ -0.094 rad ≈ -5.4°