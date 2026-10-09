# Runs run_lopf_main_island.py unchanged, except 735/765kV lines get the HQ
# table's cold thermal rating (sqrt(3) * 735kV * 6200 A per circuit) instead
# of 3 x St Clair. 315/345kV lines keep 3 x St Clair.
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_lopf_main_island as rl

COLD_MVA_PER_CIRCUIT_735 = np.sqrt(3) * 735 * 6200 / 1000
_st_clair = rl.st_clair_s_nom_mva

def s_nom(length, v_nom, ref, num_parallel=1.0, **kw):
    if v_nom >= 735:
        return COLD_MVA_PER_CIRCUIT_735 * num_parallel / rl.ST_CLAIR_MARGIN_FACTOR  # caller multiplies by the margin
    return _st_clair(length, v_nom, ref, num_parallel=num_parallel, **kw)

rl.st_clair_s_nom_mva = s_nom
rl.main()
