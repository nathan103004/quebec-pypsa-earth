# -*- coding: utf-8 -*-
"""
st_clair.py

St Clair curve: the classic (H.P. St Clair, AIEE 1953) rule-of-thumb
envelope for the maximum usable power transfer of an AC overhead
transmission line as a function of line length, expressed as a multiple
of Surge Impedance Loading (SIL = V^2 / Zc).

Rather than digitizing the original 1953 chart, this reproduces its two
governing regimes analytically from the line's own R/X/B parameters
(network/overhead_line_parameters_by_voltage.csv), which is the same
underlying physics St Clair's curves are a graphical summary of:

- Thermal / short-line plateau: for short lines the limit is conductor
  ampacity, not the line's electrical behaviour -- roughly flat and
  independent of length. Modeled as a fixed cap in multiples of SIL
  (``thermal_cap_pu``, default 3.5 -- the typical short-line plateau
  quoted for the St Clair curve family).
- Stability-limited long-line envelope: from the standard power-angle
  relation for a lossless line, P_max = (Vs*Vr/X') * sin(delta), with
  X' = Zc * sin(beta*L) and Vs = Vr = V:

      P / SIL = sin(delta_limit) / sin(beta * L)

  where beta = omega * sqrt(L'C') is the line's phase constant (rad/km)
  and delta_limit is a stability-margin angle (default 30 deg -- a
  conventional planning-level margin, not a true stability-study result).

The governing limit at any length is whichever is *lower*:

      P(L) / SIL = min(thermal_cap_pu, sin(delta_limit) / sin(beta*L))

This is a planning-level estimate for filling in a network's line
s_nom -- not a substitute for a real thermal or transient-stability
study.

Reference line parameters (Zc, L, C) are looked up by voltage via
log-log interpolation over the 5 tabulated levels (230/345/500/765/1100
kV), so it also covers voltages in between (e.g. Quebec's 735 kV) or
just outside the table without needing an exact match. A short sanity
check: this reproduces the standard textbook SIL numbers almost exactly
(500 kV -> ~1000 MW, 230 kV -> ~140 MW, 765 kV -> ~2275 MW).

Usage
-----
Regenerate the demo curve table:

    python network/st_clair.py curve

Populate s_nom on a finished network once it exists:

    python network/st_clair.py populate --network networks/elec.nc
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd

NETWORK_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARAMS_CSV = os.path.join(NETWORK_DIR, "overhead_line_parameters_by_voltage.csv")
DEFAULT_CURVE_CSV = os.path.join(NETWORK_DIR, "st_clair_curve.csv")

DEFAULT_DELTA_LIMIT_DEG = 30.0
DEFAULT_THERMAL_CAP_PU = 3.5


def load_line_params(csv_path: str = DEFAULT_PARAMS_CSV) -> pd.DataFrame:
    df = pd.read_csv(csv_path, comment="#").set_index("voltage_kv").sort_index()
    return df


def interpolate_params(voltage_kv: float, ref: pd.DataFrame) -> tuple[float, float, float]:
    """Log-log interpolate (zc_ohm, l_mH_per_km, c_uF_per_km) at voltage_kv
    from the reference table. Warns and flat-extrapolates outside the
    tabulated range (230-1100 kV)."""
    v_ref = ref.index.to_numpy(dtype=float)
    if voltage_kv < v_ref.min() or voltage_kv > v_ref.max():
        warnings.warn(
            f"{voltage_kv} kV is outside the tabulated range "
            f"[{v_ref.min()}, {v_ref.max()}] kV -- flat-extrapolating "
            "from the nearest end point. Treat the result as a rough estimate."
        )

    log_v = np.log(v_ref)
    log_v_query = np.log(voltage_kv)

    def interp(col):
        return float(np.exp(np.interp(log_v_query, log_v, np.log(ref[col].to_numpy(dtype=float)))))

    return interp("zc_ohm"), interp("l_mH_per_km"), interp("c_uF_per_km")


def sil_mw(voltage_kv: float, zc_ohm: float) -> float:
    """Surge Impedance Loading, MW (voltage_kv in kV, zc_ohm in ohm)."""
    return voltage_kv**2 / zc_ohm


def electrical_length_rad(
    length_km: float, l_mH_per_km: float, c_uF_per_km: float, freq_hz: float = 60.0
) -> float:
    omega = 2 * np.pi * freq_hz
    L = l_mH_per_km * 1e-3  # H/km
    C = c_uF_per_km * 1e-6  # F/km
    beta = omega * np.sqrt(L * C)  # rad/km
    return beta * length_km


def st_clair_loadability_pu(
    length_km: float,
    voltage_kv: float,
    ref: pd.DataFrame | None = None,
    delta_limit_deg: float = DEFAULT_DELTA_LIMIT_DEG,
    thermal_cap_pu: float = DEFAULT_THERMAL_CAP_PU,
) -> float:
    """Loadability at length_km/voltage_kv, in multiples of SIL."""
    if ref is None:
        ref = load_line_params()
    zc_ohm, l_mH_per_km, c_uF_per_km = interpolate_params(voltage_kv, ref)

    theta = electrical_length_rad(max(length_km, 1e-3), l_mH_per_km, c_uF_per_km)
    # sin(theta) -- and so the stability ratio -- is only monotonically
    # declining out to a quarter wavelength (theta=90deg), where it hits
    # its true minimum. Past that point sin(theta) heads back down toward
    # a half wavelength, which would make this simple lossless-line,
    # single-uncompensated-circuit formula swing back *up* -- an artifact
    # of the model, not a real capability. Real lines this long (in
    # Quebec: James Bay-Montreal corridors and beyond, and the Churchill
    # Falls tie) are series-compensated, split into effective segments,
    # or run as HVDC precisely to avoid operating out here, so clip the
    # ratio at its quarter-wavelength floor instead of letting it recover.
    theta = min(theta, np.pi / 2)

    stability_pu = np.sin(np.radians(delta_limit_deg)) / np.sin(theta)
    return min(thermal_cap_pu, stability_pu)


def st_clair_s_nom_mva(
    length_km: float,
    voltage_kv: float,
    ref: pd.DataFrame | None = None,
    delta_limit_deg: float = DEFAULT_DELTA_LIMIT_DEG,
    thermal_cap_pu: float = DEFAULT_THERMAL_CAP_PU,
    num_parallel: float = 1.0,
) -> float:
    """Estimated per-circuit-group thermal/stability MVA rating."""
    if ref is None:
        ref = load_line_params()
    zc_ohm, _, _ = interpolate_params(voltage_kv, ref)
    pu = st_clair_loadability_pu(length_km, voltage_kv, ref, delta_limit_deg, thermal_cap_pu)
    return sil_mw(voltage_kv, zc_ohm) * pu * num_parallel


def make_curve_table(
    voltages_kv=(230, 345, 500, 735, 765, 1100),
    lengths_km=(10, 25, 50, 75, 100, 150, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1200, 1500),
    delta_limit_deg: float = DEFAULT_DELTA_LIMIT_DEG,
    thermal_cap_pu: float = DEFAULT_THERMAL_CAP_PU,
) -> pd.DataFrame:
    ref = load_line_params()
    rows = []
    for v in voltages_kv:
        zc_ohm, _, _ = interpolate_params(v, ref)
        sil = sil_mw(v, zc_ohm)
        for length in lengths_km:
            pu = st_clair_loadability_pu(length, v, ref, delta_limit_deg, thermal_cap_pu)
            rows.append(
                {
                    "voltage_kv": v,
                    "length_km": length,
                    "sil_mw": sil,
                    "loadability_pu_of_sil": pu,
                    "s_nom_mva_per_circuit": sil * pu,
                }
            )
    return pd.DataFrame(rows)


def populate_s_nom(
    network_path: str,
    output_path: str | None = None,
    delta_limit_deg: float = DEFAULT_DELTA_LIMIT_DEG,
    thermal_cap_pu: float = DEFAULT_THERMAL_CAP_PU,
    in_place: bool = False,
) -> None:
    import pypsa

    n = pypsa.Network(network_path)
    ref = load_line_params()

    num_parallel = n.lines["num_parallel"] if "num_parallel" in n.lines else pd.Series(1.0, n.lines.index)
    num_parallel = num_parallel.where(num_parallel > 0, 1.0)

    old_s_nom = n.lines["s_nom"].copy()
    new_s_nom = pd.Series(index=n.lines.index, dtype=float)
    for line_id, row in n.lines.iterrows():
        new_s_nom[line_id] = st_clair_s_nom_mva(
            length_km=row["length"],
            voltage_kv=row["v_nom"],
            ref=ref,
            delta_limit_deg=delta_limit_deg,
            thermal_cap_pu=thermal_cap_pu,
            num_parallel=num_parallel[line_id],
        )

    n.lines["s_nom"] = new_s_nom

    print(f"Updated s_nom for {len(n.lines)} lines.")
    print(
        pd.DataFrame({"old_s_nom_mva": old_s_nom, "new_s_nom_mva": new_s_nom})
        .describe()
        .round(1)
    )

    output = network_path if in_place else output_path
    if output is None:
        root, ext = os.path.splitext(network_path)
        output = f"{root}_stclair{ext}"
    n.export_to_netcdf(output)
    print(f"Saved -> {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    curve_p = sub.add_parser("curve", help="(Re)generate the demo St Clair curve table CSV.")
    curve_p.add_argument("--output", default=DEFAULT_CURVE_CSV)
    curve_p.add_argument("--delta-limit-deg", type=float, default=DEFAULT_DELTA_LIMIT_DEG)
    curve_p.add_argument("--thermal-cap-pu", type=float, default=DEFAULT_THERMAL_CAP_PU)

    pop_p = sub.add_parser("populate", help="Populate s_nom on a finished network's lines.")
    pop_p.add_argument("--network", required=True)
    pop_p.add_argument("--output", default=None)
    pop_p.add_argument("--in-place", action="store_true")
    pop_p.add_argument("--delta-limit-deg", type=float, default=DEFAULT_DELTA_LIMIT_DEG)
    pop_p.add_argument("--thermal-cap-pu", type=float, default=DEFAULT_THERMAL_CAP_PU)

    args = parser.parse_args()

    if args.command == "curve":
        df = make_curve_table(delta_limit_deg=args.delta_limit_deg, thermal_cap_pu=args.thermal_cap_pu)
        df.to_csv(args.output, index=False)
        print(f"Wrote {len(df)} rows -> {args.output}")
    elif args.command == "populate":
        populate_s_nom(
            args.network,
            output_path=args.output,
            delta_limit_deg=args.delta_limit_deg,
            thermal_cap_pu=args.thermal_cap_pu,
            in_place=args.in_place,
        )


if __name__ == "__main__":
    main()
