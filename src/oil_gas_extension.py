"""
oil_gas_extension.py -- Stage 2: crude-oil / produced-water viscosity screening
=============================================================================
What this is (and is not)
-------------------------
The bench rig only ever pumped water. There is no crude oil in the data.
Everything crude-related below is a THEORETICAL, screening-level application
of a named industry method to the validated water curve from stage 1 -- not a
measurement, and not a substitute for a manufacturer's viscosity-corrected
curve on a real pump.

Methods (all named, so every number is checkable)
-------------------------------------------------
* Pump re-rating for viscosity: Hydraulic Institute ANSI/HI 9.6.7 parameter-B
  method, using the published correction-factor equations as reproduced in
  secondary sources (the standard itself is paywalled and was not consulted).
* Dead-oil viscosity: Beggs, H.D. and Robinson, J.R. (1975), "Estimating the
  Viscosity of Crude Oil Systems," JPT 27(9), SPE-5434-PA. Published validity
  range: 16-58 deg API, 70-295 deg F (21.1-146.1 deg C). Results outside that
  range are flagged, drawn as extrapolation, and never treated as findings.
* Crude density vs. temperature: SG at 60 deg F from API gravity, corrected
  with ONE representative thermal-expansion coefficient (~0.0007 /degC) -- an
  approximation of the API MPMS Ch. 11.1 / ASTM D1250 tables a real study
  would use.
* Water viscosity: Vogel equation, mu = 2.414e-5 * 10^(247.8/(T[K]-140)) Pa.s.

Fluids (typical reported assay values; verify against the assay you cite)
------------------------------------------------------------------------
    Light   - Minas ("Sumatran Light"), ~35.0 deg API   (Sumatra, Indonesia)
    Medium  - generic 27.0 deg API                       (no named field)
    Heavy   - Duri, ~20.8 deg API                        (Sumatra, Indonesia)
    plus produced water (treated as water-like; see notes).

Reference operating point
-------------------------
"BEP" below means the MODEL-DERIVED best-efficiency point within the tested
operating range: the maximum of the GP-fitted efficiency curve at 1200 rpm.
On this rig that maximum sits at the fully-open end of the tested valve range
with efficiency still rising, so the true BEP probably lies at higher flow
than was tested. B depends only weakly on Q_BEP (exponent -0.375); the effect
is quantified by a sensitivity run rather than assumed away.

Applicability, checked in code (docs/oil_gas_notes.md has the numbers)
---------------------------------------------------------------------
* Beggs-Robinson API and temperature ranges;
* HI 9.6.7 documented scope: Newtonian fluid, 1-4000 cSt, radial-discharge
  centrifugal pump with specific speed < 3000 (US units), and 1 < B < 40 for
  the correction equations;
* NOT verifiable here: the standard's own flow/head bounds and whether this
  small bench pump is a radial-flow centrifugal type -- so the whole
  extension is an illustration of the method, not a validated prediction.
=============================================================================
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import project_config as cfg
import pump_performance_model as base

warnings.filterwarnings("ignore")

REFERENCE_SPEED_RPM = 1200.0  # the workbook's own reference speed
THERMAL_EXPANSION_PER_C = 0.0007
BR_API_RANGE = (16.0, 58.0)
BR_T_RANGE_C = ((70.0 - 32.0) * 5.0 / 9.0, (295.0 - 32.0) * 5.0 / 9.0)  # 21.1 .. 146.1 degC
HI_B_LIMIT = 40.0
HI_NU_RANGE_CST = (1.0, 4000.0)
HI_NS_US_MAX = 3000.0
Q_BEP_SENSITIVITY_MULTIPLES = (1.0, 1.25, 1.5, 2.0)

FLUIDS = {
    "Water (tested)":               {"api": None, "T_C": 25.0},
    "Produced water (+3% density)": {"api": None, "T_C": 25.0, "sg_mult": 1.03},
    "Light crude - Minas 35 API":   {"api": 35.0, "T_C": 40.0},
    "Medium crude - 27 API":        {"api": 27.0, "T_C": 40.0},
    "Heavy crude - Duri 20.8 API":  {"api": 20.8, "T_C": 40.0},
}
CRUDE_GRADES = {k: v for k, v in FLUIDS.items() if v.get("api") is not None}
PALETTE = {"Water (tested)": "#1f77b4", "Produced water (+3% density)": "#17becf",
           "Light crude - Minas 35 API": "#2ca02c", "Medium crude - 27 API": "#ff7f0e",
           "Heavy crude - Duri 20.8 API": "#d62728"}


def _mult_col(mult: float) -> str:
    """Column name for a Q_BEP multiple, free of dots so it survives itertuples()."""
    return f"T_B40_C_at_QBEPx{mult:g}".replace(".", "_")


# =============================================================================
# 1. FLUID PROPERTIES
# =============================================================================
def api_to_sg(api: float) -> float:
    """Standard petroleum API-gravity definition (SG at 60 degF)."""
    return 141.5 / (131.5 + api)


def beggs_robinson_dead_oil_viscosity_cp(T_C, api: float):
    """Beggs & Robinson (1975) dead-oil viscosity in cP. T in degC (array or scalar).
    Valid only inside BR_API_RANGE / BR_T_RANGE_C -- see beggs_robinson_in_range."""
    T_F = np.asarray(T_C, dtype=float) * 9.0 / 5.0 + 32.0
    Z = 3.0324 - 0.02023 * api
    X = (10.0 ** Z) * (T_F ** -1.163)
    return 10.0 ** X - 1.0


def beggs_robinson_in_range(T_C, api: float):
    """True where both the API gravity and the temperature are inside the
    correlation's published validity range."""
    T = np.asarray(T_C, dtype=float)
    api_ok = BR_API_RANGE[0] <= api <= BR_API_RANGE[1]
    return api_ok & (T >= BR_T_RANGE_C[0]) & (T <= BR_T_RANGE_C[1])


def crude_oil_density_kgm3(T_C, api: float, T_ref_C: float = 15.6,
                            alpha_per_C: float = THERMAL_EXPANSION_PER_C):
    """SG(60 degF) x water density at 60 degF, corrected to T_C with a single
    representative thermal-expansion coefficient (an approximation)."""
    rho_ref = api_to_sg(api) * 999.012
    return rho_ref * (1.0 - alpha_per_C * (np.asarray(T_C, dtype=float) - T_ref_C))


def water_viscosity_cp(T_C):
    """Vogel equation for liquid water (cP)."""
    T_K = np.asarray(T_C, dtype=float) + 273.15
    return 2.414e-5 * 10.0 ** (247.8 / (T_K - 140.0)) * 1000.0


def fluid_density_kgm3(spec: dict) -> float:
    """Density of a scenario fluid at its scenario temperature. Single source of
    truth, shared with the jet stage so both stages use the same fluids."""
    if spec.get("api") is None:
        return float(base.water_density_kgm3(spec["T_C"])) * spec.get("sg_mult", 1.0)
    return float(crude_oil_density_kgm3(spec["T_C"], spec["api"]))


def kinematic_viscosity_cst(mu_cp, rho_kgm3):
    """cSt = cP / (density in g/cm^3) -- standard petroleum unit identity."""
    return np.asarray(mu_cp, dtype=float) / (np.asarray(rho_kgm3, dtype=float) / 1000.0)


def specific_speed_us(N_rpm: float, Q_lps: float, H_m: float) -> float:
    """N * sqrt(Q[gpm]) / H[ft]^0.75 -- the US-unit specific speed the HI scope refers to."""
    return N_rpm * np.sqrt(Q_lps * 15.8503) / (H_m * 3.28084) ** 0.75


# =============================================================================
# 2. ANSI/HI 9.6.7 VISCOSITY CORRECTION (parameter-B method)
# =============================================================================
def hi_9_6_7_b_parameter(nu_cst, H_bep_m, Q_bep_m3h, N_rpm):
    """SI form: nu in cSt, H in m, Q in m^3/h, N in rpm."""
    return (16.5 * np.asarray(nu_cst, dtype=float) ** 0.5 * H_bep_m ** 0.0625
            / (Q_bep_m3h ** 0.375 * N_rpm ** 0.25))


def hi_9_6_7_correction_factors(B: float) -> dict:
    """Regime, CQ (flow / BEP-head factor) and Ceta (efficiency factor)."""
    if B <= 1.0:
        return {"B": B, "regime": "B<=1: negligible, water performance applies", "CQ": 1.0, "Ceta": 1.0}
    if B >= HI_B_LIMIT:
        return {"B": B, "regime": f"B>={HI_B_LIMIT:.0f}: outside the method's range",
                "CQ": np.nan, "Ceta": np.nan}
    CQ = 2.71 ** (-0.165 * (np.log10(B)) ** 3.15)
    Ceta = B ** (-0.0547 * B ** 0.69)
    return {"B": B, "regime": "1<B<40: corrected", "CQ": CQ, "Ceta": Ceta}


def hi_9_6_7_head_factor(CQ: float, Q_over_Qbep):
    return 1.0 - (1.0 - CQ) * np.clip(Q_over_Qbep, 0, None) ** 0.75


# =============================================================================
# 3. WATER BASE CURVE  (final model: fit on all 24 points; stage 1's LOSO
#    results already establish how this model class generalises)
# =============================================================================
def fit_final_water_models(df: pd.DataFrame) -> dict:
    X = base.design_matrix(df)
    models = {}
    for resp in base.RESPONSE_COLS:
        pipe = base.make_gp()
        pipe.fit(X, df[resp].to_numpy(float))
        models[resp] = pipe
    return models


def predict_water_curve(models: dict, speed_rpm: float, valve_frac: np.ndarray) -> dict:
    X = np.column_stack([np.full_like(valve_frac, speed_rpm), valve_frac,
                          np.full_like(valve_frac, speed_rpm) * valve_frac])
    mean = {}
    for resp in base.RESPONSE_COLS:
        m, _ = base.predict_with_std(models[resp], X)
        mean[resp] = m
    setting_pct = speed_rpm / 15.0  # the rig's fixed Setting(%) x 15 = Speed(rpm) relation
    kpi = base.physics(np.full_like(valve_frac, setting_pct), np.full_like(valve_frac, speed_rpm),
                        mean["T_water_C"], mean["Pin_kPa"], mean["Pout_kPa"], mean["Q_lps"],
                        mean["P_input_W"])
    kpi["Q_lps"] = mean["Q_lps"]
    return kpi


# =============================================================================
# 4. ANALYSIS
# =============================================================================
def scenario_row(name: str, spec: dict, ref: dict) -> tuple[dict, dict | None]:
    """One fluid scenario: properties, B, correction factors, applicability
    flags, and (if a correction applies) the de-rated curve."""
    T_C, api = spec["T_C"], spec.get("api")
    water_like = api is None
    rho = fluid_density_kgm3(spec)
    if water_like:  # viscosity from the water correlation
        mu = float(water_viscosity_cp(T_C))
        br_ok = np.nan
    else:
        mu = float(beggs_robinson_dead_oil_viscosity_cp(T_C, api))
        br_ok = bool(beggs_robinson_in_range(T_C, api))
    nu = float(kinematic_viscosity_cst(mu, rho))
    B = float(hi_9_6_7_b_parameter(nu, ref["H_bep_m"], ref["Q_bep_m3h"], REFERENCE_SPEED_RPM))
    corr = hi_9_6_7_correction_factors(B)
    row = {
        "Fluid": name, "API": api, "T_C": T_C, "rho_kgm3": rho, "mu_cP": mu, "nu_cSt": nu, "B": B,
        "regime": ("water-like: tested curve used as-is (B is an applicability indicator only)"
                   if water_like else corr["regime"]),
        "CQ": 1.0 if water_like else corr["CQ"], "Ceta": 1.0 if water_like else corr["Ceta"],
        "br_in_range": br_ok, "nu_in_HI_range": bool(HI_NU_RANGE_CST[0] <= nu <= HI_NU_RANGE_CST[1]),
    }
    if not water_like and np.isnan(corr["CQ"]):     # B outside the method's range: no curve
        row.update({"Q_BEP_model_lps": np.nan, "H_BEP_model_m": np.nan, "eff_BEP_model_pct": np.nan})
        return row, None

    if water_like:
        CQ, Ceta = 1.0, 1.0
        curve = {"Q_lps": ref["Q_lps"], "TotalHead_m": ref["H_m"], "PumpEff_pct": ref["eff_pct"]}
    else:
        CQ, Ceta = corr["CQ"], corr["Ceta"]
        CH = hi_9_6_7_head_factor(CQ, ref["Q_lps"] / ref["Q_bep_lps"])
        curve = {"Q_lps": CQ * ref["Q_lps"], "TotalHead_m": CH * ref["H_m"],
                 "PumpEff_pct": Ceta * ref["eff_pct"]}
    row.update({"Q_BEP_model_lps": ref["Q_bep_lps"] * CQ, "H_BEP_model_m": ref["H_bep_m"] * CQ,
                "eff_BEP_model_pct": ref["eff_bep_pct"] * Ceta})
    return row, curve


def temperature_sweep(ref: dict, T_grid: np.ndarray) -> pd.DataFrame:
    """B vs. temperature for every crude grade, with the validity flag per point."""
    rows = []
    for name, spec in CRUDE_GRADES.items():
        api = spec["api"]
        rho = crude_oil_density_kgm3(T_grid, api)
        mu = beggs_robinson_dead_oil_viscosity_cp(T_grid, api)
        nu = kinematic_viscosity_cst(mu, rho)
        B = hi_9_6_7_b_parameter(nu, ref["H_bep_m"], ref["Q_bep_m3h"], REFERENCE_SPEED_RPM)
        ok = np.broadcast_to(beggs_robinson_in_range(T_grid, api), T_grid.shape)
        for T, m, n, b, o in zip(T_grid, mu, nu, B, ok):
            rows.append({"Fluid": name, "API": api, "T_C": float(T), "mu_cP": float(m),
                         "nu_cSt": float(n), "B": float(b), "br_in_range": bool(o),
                         "B_exceeds_40": bool(b >= HI_B_LIMIT)})
    return pd.DataFrame(rows)


def b_limit_temperature(api: float, ref: dict, q_multiple: float = 1.0) -> float:
    """Temperature (degC) at which B falls to the HI limit as the fluid warms
    (B decreases with temperature), found on a fine grid over 15-80 degC.
    Returns nan if B is already below the limit at 15 degC (never above it),
    and +inf if it is still above the limit at 80 degC."""
    T = np.arange(15.0, 80.0, 0.02)
    nu = kinematic_viscosity_cst(beggs_robinson_dead_oil_viscosity_cp(T, api),
                                 crude_oil_density_kgm3(T, api))
    B = hi_9_6_7_b_parameter(nu, ref["H_bep_m"], ref["Q_bep_m3h"] * q_multiple, REFERENCE_SPEED_RPM)
    above = np.where(B >= HI_B_LIMIT)[0]
    if len(above) == 0:
        return float("nan")
    i = int(above[-1])
    if i == len(T) - 1:
        return float("inf")
    return float(np.interp(HI_B_LIMIT, [B[i + 1], B[i]], [T[i + 1], T[i]]))


def limit_crossing_table(ref: dict) -> pd.DataFrame:
    """Where each grade leaves the HI B<40 range, whether that is inside the
    Beggs-Robinson validity range, and how sensitive it is to the BEP proxy."""
    rows = []
    for name, spec in CRUDE_GRADES.items():
        api = spec["api"]
        rec = {"Fluid": name, "API": api}
        for mult in Q_BEP_SENSITIVITY_MULTIPLES:
            rec[_mult_col(mult)] = b_limit_temperature(api, ref, mult)
        t0 = rec[_mult_col(1.0)]
        if np.isnan(t0):
            rec["assessment"] = f"B stays below {HI_B_LIMIT:.0f} across 15-80 degC"
        elif np.isinf(t0):
            rec["assessment"] = f"B is still at or above {HI_B_LIMIT:.0f} at 80 degC"
        elif t0 >= BR_T_RANGE_C[0]:
            rec["assessment"] = (f"B reaches {HI_B_LIMIT:.0f} at {t0:.1f} degC, INSIDE the Beggs-Robinson "
                                 f"range (>= {BR_T_RANGE_C[0]:.1f} degC): a valid-range screening flag")
        else:
            rec["assessment"] = (f"B reaches {HI_B_LIMIT:.0f} at {t0:.1f} degC, BELOW the Beggs-Robinson "
                                 f"range (< {BR_T_RANGE_C[0]:.1f} degC): extrapolated, not a finding")
        nu_lo = kinematic_viscosity_cst(beggs_robinson_dead_oil_viscosity_cp(BR_T_RANGE_C[0], api),
                                        crude_oil_density_kgm3(BR_T_RANGE_C[0], api))
        rec["B_at_lower_valid_T"] = float(hi_9_6_7_b_parameter(
            nu_lo, ref["H_bep_m"], ref["Q_bep_m3h"], REFERENCE_SPEED_RPM))
        rows.append(rec)
    return pd.DataFrame(rows)


# =============================================================================
# 5. MAIN
# =============================================================================
def main():
    cfg.ensure_output_dirs()
    cfg.apply_plot_style()
    workbook = cfg.resolve_workbook()
    pump_sheet = cfg.resolve_pump_sheet(workbook)
    df = base.load_pump_data(workbook, pump_sheet)
    base.crosscheck_against_workbook(df, workbook, pump_sheet)
    models = fit_final_water_models(df)

    vf = np.linspace(0, 1, 60)
    water = predict_water_curve(models, REFERENCE_SPEED_RPM, vf)
    i_bep = int(np.argmax(water["PumpEff_pct"]))
    at_boundary = i_bep in (0, len(vf) - 1)
    ref = {"Q_lps": water["Q_lps"], "H_m": water["TotalHead_m"], "eff_pct": water["PumpEff_pct"],
           "Q_bep_lps": float(water["Q_lps"][i_bep]), "H_bep_m": float(water["TotalHead_m"][i_bep]),
           "eff_bep_pct": float(water["PumpEff_pct"][i_bep])}
    ref["Q_bep_m3h"] = ref["Q_bep_lps"] * 3.6
    still_rising = float(water["PumpEff_pct"][-1] - water["PumpEff_pct"][-4]) > 0
    bep_edge = bool(at_boundary and still_rising)
    print(f"[model-derived BEP @ {REFERENCE_SPEED_RPM:.0f} rpm, water] Q={ref['Q_bep_lps']:.3f} l/s "
          f"({ref['Q_bep_m3h']:.3f} m3/h), H={ref['H_bep_m']:.3f} m, eff={ref['eff_bep_pct']:.2f}%"
          + ("  <-- at the edge of the tested range, efficiency still rising" if bep_edge else ""))

    rows, curves = [], {}
    for name, spec in FLUIDS.items():
        row, curve = scenario_row(name, spec, ref)
        rows.append(row)
        if curve is not None:
            curves[name] = curve
    summary = pd.DataFrame(rows)
    ns_us = float(specific_speed_us(REFERENCE_SPEED_RPM, ref["Q_bep_lps"], ref["H_bep_m"]))
    water_B = float(summary.loc[summary.Fluid == "Water (tested)", "B"].iloc[0])
    water_factors = hi_9_6_7_correction_factors(water_B)
    # Sensitivity: crude C_eta divided by the method's own C_eta for plain water at this B.
    # Water-like rows are the base curve, so their value is 1 by definition.
    summary["Ceta_rel_to_method_water"] = summary["Ceta"] / (
        1.0 if np.isnan(water_factors["Ceta"]) else water_factors["Ceta"])
    summary.loc[summary["API"].isna(), "Ceta_rel_to_method_water"] = 1.0
    summary.to_csv(cfg.results_dir() / "crude_oil_viscosity_correction_summary.csv", index=False)

    T_grid = np.round(np.arange(15.0, 60.0 + 1e-9, 0.5), 3)
    sweep = temperature_sweep(ref, T_grid)
    sweep.to_csv(cfg.results_dir() / "temperature_sensitivity.csv", index=False)
    crossings = limit_crossing_table(ref)
    crossings.to_csv(cfg.results_dir() / "hi_limit_crossing_summary.csv", index=False)
    print("[saved] results/crude_oil_viscosity_correction_summary.csv, temperature_sensitivity.csv, "
          "hi_limit_crossing_summary.csv")
    show = ["Fluid", "T_C", "nu_cSt", "B", "CQ", "Ceta", "br_in_range"]
    print(summary[show].round(3).to_string(index=False))
    print("\nB=40 crossings:")
    print(crossings[["Fluid", _mult_col(1.0), "assessment"]].to_string(index=False))

    plot_corrected_curves(curves, summary, cfg.figures_dir() / "05_crude_oil_water_derating.png")
    plot_temperature_sensitivity(sweep, crossings,
                                 cfg.figures_dir() / "06_viscosity_temperature_sensitivity.png")
    (cfg.docs_dir() / "oil_gas_notes.md").write_text(
        render_notes(summary, crossings, ref, ns_us, water_factors, bep_edge), encoding="utf-8")
    print("[saved] figures/05-06, docs/oil_gas_notes.md")

    cfg.update_run_metadata(oil_gas={
        "reference_speed_rpm": REFERENCE_SPEED_RPM, "model_bep_q_lps": ref["Q_bep_lps"],
        "model_bep_h_m": ref["H_bep_m"], "bep_at_edge_of_tested_range": bep_edge,
        "beggs_robinson_T_range_C": list(BR_T_RANGE_C), "beggs_robinson_API_range": list(BR_API_RANGE),
        "specific_speed_us": ns_us, "water_B_at_model_bep": water_B})
    print("\n" + "=" * 64 + "\n  STAGE 2 DONE\n" + "=" * 64)


# =============================================================================
# 6. PLOTS + NOTES
# =============================================================================
def plot_corrected_curves(curves: dict, summary: pd.DataFrame, path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.8))
    for name, c in curves.items():
        color = PALETTE.get(name, "#555")
        style = "-" if name == "Water (tested)" else "--"
        b = float(summary.loc[summary.Fluid == name, "B"].iloc[0])
        label = name if name == "Water (tested)" or "Produced" in name else f"{name} (B={b:.1f})"
        ax1.plot(c["Q_lps"], c["TotalHead_m"], style, color=color, lw=2, label=label)
        ax2.plot(c["Q_lps"], c["PumpEff_pct"], style, color=color, lw=2, label=label)
    ax1.set_xlabel("Flow rate Q (l/s)"); ax1.set_ylabel("Total Head (m)")
    ax1.set_title(f"Head-flow at {REFERENCE_SPEED_RPM:.0f} rpm, ANSI/HI 9.6.7 viscosity-corrected\n"
                  "(theoretical screening, not measured)")
    ax2.set_xlabel("Flow rate Q (l/s)"); ax2.set_ylabel("Pump efficiency (%)")
    ax2.set_title("Efficiency vs. flow, same correction\n(crudes at 40 degC)")
    ax1.legend(fontsize=7.5, loc="best")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_temperature_sensitivity(sweep: pd.DataFrame, crossings: pd.DataFrame, path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.8))
    t_lo = BR_T_RANGE_C[0]
    for ax in (ax1, ax2):
        ax.axvspan(sweep.T_C.min() - 1, t_lo, color="0.85", alpha=0.7, zorder=0)
    for name in CRUDE_GRADES:
        g = sweep[sweep.Fluid == name]
        color = PALETTE[name]
        ok, bad = g[g.br_in_range], g[~g.br_in_range]
        for ax, col in ((ax1, "nu_cSt"), (ax2, "B")):
            ax.plot(ok.T_C, ok[col], "-", color=color, lw=2, label=name if ax is ax1 else None)
            if len(bad):
                joint = pd.concat([bad.tail(1), ok.head(1)])
                ax.plot(bad.T_C, bad[col], "--", color=color, lw=1.4, alpha=0.7)
                ax.plot(joint.T_C, joint[col], "--", color=color, lw=1.4, alpha=0.7)
    ax1.set_yscale("log")
    ax1.set_xlabel("Fluid temperature (degC)"); ax1.set_ylabel("Kinematic viscosity (cSt, log scale)")
    ax1.set_title("Beggs-Robinson viscosity vs. temperature\n"
                  "(grey / dashed = below the correlation's 21.1 degC limit)")
    ax1.legend(fontsize=8)
    ax2.axhline(1.0, color="gray", ls=":", lw=1)
    ax2.axhline(HI_B_LIMIT, color="red", ls=":", lw=1.2)
    ax2.text(sweep.T_C.max(), HI_B_LIMIT * 1.03, "B = 40 (method limit)", color="red",
             ha="right", fontsize=8)
    ax2.text(sweep.T_C.max(), 1.03, "B = 1 (correction threshold)", color="gray", ha="right", fontsize=8)
    for r in crossings.itertuples():
        t0 = getattr(r, _mult_col(1.0))
        if np.isfinite(t0) and t0 >= t_lo:
            ax2.plot([t0], [HI_B_LIMIT], "o", color=PALETTE[r.Fluid], ms=8, zorder=5)
            ax2.annotate(f"{t0:.1f} degC", (t0, HI_B_LIMIT), textcoords="offset points",
                         xytext=(8, 8), fontsize=8, color=PALETTE[r.Fluid])
    ax2.set_yscale("log")
    ax2.set_xlabel("Fluid temperature (degC)"); ax2.set_ylabel("ANSI/HI 9.6.7 parameter B (log scale)")
    ax2.set_title("Correction regime vs. temperature\n(marker = B=40 crossing inside the valid range)")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _fmt(x, nd=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    if isinstance(x, float) and np.isinf(x):
        return ">80"
    return f"{x:.{nd}f}"


def render_notes(summary, crossings, ref, ns_us, water_factors, bep_edge) -> str:
    water_B = float(summary.loc[summary.Fluid == "Water (tested)", "B"].iloc[0])
    crude = summary[summary["API"].notna()]
    shrink = (crude["Ceta_rel_to_method_water"] - crude["Ceta"]) * 100.0
    shrink_lo, shrink_hi = float(shrink.min()), float(shrink.max())
    lines = [
        "# Stage 2 notes -- crude oil / produced water viscosity screening",
        "",
        "**Status: theoretical.** The bench only pumped water; nothing here is a crude-oil measurement. "
        "It applies the ANSI/HI 9.6.7 parameter-B method, with the Beggs-Robinson dead-oil viscosity "
        f"correlation, to the stage-1 water curve at {REFERENCE_SPEED_RPM:.0f} rpm.",
        "",
        "## Reference point",
        f"Model-derived best-efficiency point within the tested range (max of the GP-fitted efficiency "
        f"curve): Q = {ref['Q_bep_lps']:.3f} l/s ({ref['Q_bep_m3h']:.2f} m3/h), H = {ref['H_bep_m']:.3f} m, "
        f"efficiency = {ref['eff_bep_pct']:.1f}%."
        + (" This maximum sits at the fully-open end of the tested valve range with efficiency still "
           "rising, so the true BEP probably lies at higher flow than was tested. B depends on Q_BEP "
           "with exponent -0.375, so the effect on the conclusions is bounded; see the sensitivity "
           "columns in the crossing table below." if bep_edge else ""),
        "",
        "## Results at the scenario temperatures",
        "| Fluid | API | T (degC) | rho (kg/m3) | mu (cP) | nu (cSt) | B | Regime | CQ | C_eta | Beggs-Robinson in range |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in summary.itertuples():
        br = ("n/a (water)" if isinstance(r.br_in_range, float) and np.isnan(r.br_in_range)
              else ("yes" if r.br_in_range else "NO"))
        lines.append(f"| {r.Fluid} | {_fmt(r.API, 1)} | {r.T_C:.0f} | {r.rho_kgm3:.1f} | {_fmt(r.mu_cP)} | "
                     f"{_fmt(r.nu_cSt)} | {r.B:.2f} | {r.regime} | {_fmt(r.CQ, 3)} | {_fmt(r.Ceta, 3)} | {br} |")

    lines += [
        "",
        "## Applicability checks (computed)",
        f"* **Plain water gives B = {water_B:.2f}** at this pump's model-derived BEP -- above the "
        f"B<=1 'no correction' threshold. The method's own factors for water would then be "
        f"CQ = {water_factors['CQ']:.3f} and C_eta = {water_factors['Ceta']:.3f} instead of 1. That is a "
        f"symptom of applying a method built for industrial pumps to a {ref['Q_bep_m3h']:.1f} m3/h bench "
        f"pump. The crude C_eta values above follow the standard's equations; dividing by the method's "
        f"own water value (column `Ceta_rel_to_method_water` in the CSV) makes the efficiency losses "
        f"{shrink_lo:.1f}-{shrink_hi:.1f} percentage points smaller for these grades.",
        f"* Specific speed at the model BEP: {ns_us:.0f} (US units) -- under the documented limit of "
        f"{HI_NS_US_MAX:.0f}.",
        f"* Kinematic viscosity of the corrected (crude) scenarios lies inside the documented "
        f"{HI_NU_RANGE_CST[0]:.0f}-{HI_NU_RANGE_CST[1]:.0f} cSt range: "
        f"{'yes' if bool(crude.nu_in_HI_range.all()) else 'NO -- see the nu column'}. Plain water is "
        f"{float(summary.loc[summary.Fluid == 'Water (tested)', 'nu_cSt'].iloc[0]):.2f} cSt, just under "
        f"the 1 cSt lower end -- consistent with it being the base curve rather than a corrected case.",
        f"* Beggs-Robinson validity: API {BR_API_RANGE[0]:.0f}-{BR_API_RANGE[1]:.0f} and "
        f"{BR_T_RANGE_C[0]:.1f}-{BR_T_RANGE_C[1]:.1f} degC. All three scenario crudes at 40 degC are inside.",
        "* **Not verifiable here:** the standard's own flow/head bounds (it is paywalled and was not "
        "consulted), and whether this small bench pump is a radial-flow centrifugal type. The extension "
        "is therefore an illustration of the method, not a validated prediction.",
        "",
        "## Where each grade leaves the method's B<40 range",
        "| Fluid | B at 21.1 degC | T where B=40 | ...if Q_BEP is 1.25x | 1.5x | 2x | Assessment |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in crossings.itertuples():
        lines.append(f"| {r.Fluid} | {r.B_at_lower_valid_T:.1f} | {_fmt(getattr(r, _mult_col(1.0)), 1)} | "
                     f"{_fmt(getattr(r, _mult_col(1.25)), 1)} | {_fmt(getattr(r, _mult_col(1.5)), 1)} | "
                     f"{_fmt(getattr(r, _mult_col(2.0)), 1)} | {r.assessment} |")
    lines += [
        "",
        "Reading it: a crossing below 21.1 degC lies outside Beggs-Robinson's published temperature range, "
        "so it is drawn as extrapolation in figure 06 and is not reported as a finding. A crossing inside "
        "the range is a screening flag -- at that temperature the standard's simple correction should not be "
        "applied to that grade -- and it still rests on a generic correlation (real crudes such as Duri are "
        "known to deviate from generic API-based correlations; use the field's own viscosity-temperature "
        "data in practice) and on the BEP proxy, whose effect is bounded by the sensitivity columns.",
        "",
        "## Limitations",
        "* One representative thermal-expansion coefficient stands in for the API MPMS Ch. 11.1 / ASTM D1250 tables.",
        "* Dead-oil viscosity only (no dissolved gas); Newtonian behaviour assumed; wax and pour-point effects ignored.",
        "* Produced water is treated as water-like (tested curve used as-is). Salinity raises viscosity "
        "somewhat; because B scales with the square root of viscosity the effect on B is smaller still. "
        "Not modeled.",
        "* No crude vapour-pressure model, so nothing here addresses NPSH or cavitation margin in crude service.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
