"""
jet_nozzle_extension.py -- Stage 3: the jet / nozzle sheet
=============================================================================
What the sheet is
-----------------
The workbook's jet sheet (named "Jet Steam Fluid Mechanics"; "Steam" is
presumably a typo for "Stream") holds a Pitot-style radial traverse of a free
liquid jet at several nozzle-to-target distances. The apparatus is a
head-driven nozzle: velocity is Torricelli's V = sqrt(2 g h), so the liquid is
water, not steam. It is a separate experiment from the pump test, but its
physics is the NOZZLE stage of a real oilfield technology -- the hydraulic
jet pump (nozzle -> throat -> diffuser; high-pressure power fluid, water- or
oil-based, entrains produced fluid through the Venturi effect; in use in
artificial lift since the 1930s; the nozzle-to-throat area ratio is the
primary design parameter). Source: SPE JPT, "Jet Pumps: An Efficient
Technology for Production Enhancement of Mature Oil Fields" (2021),
https://jpt.spe.org/twa/jet-pumps-an-efficient-technology-for-production-enhancement-of-mature-oil-fields
This bench data has no throat or diffuser, so this stage stops at nozzle
characterization; it is not a jet-pump performance model.

Two formula problems found in the workbook revision examined here
-----------------------------------------------------------------
(They were found by recomputing every derived quantity from the raw radius /
distance / head measurements instead of trusting the sheet's own columns. The
corrected values below are recomputed from the raw data, so they stand
regardless of whether a later revision of the workbook has already changed
the sheet's formulas.)

  1. `Annular_Area_m2` = SQRT(2 * Radial_Increment_m * h): dimensionally a
     length, not an area (almost certainly the adjacent velocity formula with
     gravity swapped for the radial increment). The sheet already holds a
     correct annular-ring area elsewhere (PI*((r+dr/2)^2-(r-dr/2)^2)); that
     formula is used here.
  2. The density used in every momentum / energy / mass-flow formula was
     labelled `Air_Density_kg_m3 = 1.2`, for a head-driven liquid jet. Water's
     density at an assumed 20 degC (no temperature was logged for this bench)
     is used instead, from the same correlation as the pump stage.

How wrong was the original? (computed in the run, not typed) The two errors do
not cancel uniformly: at the jet centerline the original momentum flux lands
within roughly 40% of the corrected value by coincidence, while away from the
centerline it is several times to a few hundred times too small, because the
buggy "area" term depends on head (shrinking with radius) whereas the true
annular area grows with radius. The original therefore got the SHAPE of the
radial profile wrong, not just its scale.

Fluid extension: first-order density scaling only
-------------------------------------------------
Jet velocity V = sqrt(2 g h) is independent of density under the ideal
Torricelli/Bernoulli assumption, so it does not change with fluid. Momentum
and kinetic-energy flux scale linearly with density at fixed head and
geometry. This stage applies exactly that (using the same fluids and the same
density function as stage 2) -- it is a first-order scaling under an ideal
nozzle model, NOT a prediction of crude-oil jet-pump performance. Viscosity's
effect on the nozzle discharge coefficient is real but not quantified, because
this bench did not measure a discharge-coefficient-vs-Reynolds-number curve.
=============================================================================
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import project_config as cfg
import pump_performance_model as base
import oil_gas_extension as og

warnings.filterwarnings("ignore")

G = 9.80665
ASSUMED_WATER_T_C = 20.0  # no temperature was logged for this bench: a stated assumption
DIST_COLORS = {115: "#1f77b4", 250: "#ff7f0e", 310: "#2ca02c"}
SHEET_AIR_DENSITY = 1.2   # the labelled "air" density in the examined revision (kg/m3), for the comparison only
JET_RAW_HEADERS = {
    "Test_Id": "testid",
    "Radius_mm": "radiusmm",
    "Nozzle_to_Target_Distance_mm": "nozzletotargetdistancemm",
    "Head_Difference_h_m": "headdifferencehm",
}
JET_BOUNDS = {"Radius_mm": (0.0, 500.0), "Nozzle_to_Target_Distance_mm": (10.0, 5000.0),
              "Head_Difference_h_m": (0.0, 10.0)}
MIN_EXPERIMENTAL_ROWS = 15


# =============================================================================
# 1. LOADING  --  experimental rows selected by content, not by position
# =============================================================================
def load_jet_sheet(path, sheet_name=None) -> pd.DataFrame:
    """Load the raw (radius, distance, head) measurements.

    The four raw columns are found by header name (case/spacing-insensitive)
    and the experimental rows are the ones where all four are numeric. Rows
    below the data that hold labels or parameter values (gravity, density, ...)
    fail that test and are excluded -- and listed, so a legitimately dropped
    measurement would be visible rather than silent.
    """
    path = Path(path)
    if sheet_name is None:
        sheet_name = cfg.resolve_jet_sheet(path)
    raw = pd.read_excel(path, sheet_name=sheet_name, header=0)
    lookup = {cfg.norm(c): c for c in raw.columns}
    missing = [k for k, n in JET_RAW_HEADERS.items() if n not in lookup]
    if missing:
        raise ValueError(f"Jet sheet {sheet_name!r} is missing expected columns {missing}. "
                         f"Headers found: {list(raw.columns)}")

    sub = pd.DataFrame({k: pd.to_numeric(raw[lookup[n]], errors="coerce") for k, n in JET_RAW_HEADERS.items()})
    is_exp = sub.notna().all(axis=1)
    excluded = raw.loc[~is_exp & raw.notna().any(axis=1)]
    exp = sub[is_exp].reset_index(drop=True)

    problems = []
    if len(exp) < MIN_EXPERIMENTAL_ROWS:
        problems.append(f"only {len(exp)} experimental rows found (expected at least {MIN_EXPERIMENTAL_ROWS})")
    for col, (lo, hi) in JET_BOUNDS.items():
        vmin, vmax = float(exp[col].min()), float(exp[col].max())
        if vmin < lo or vmax > hi:
            problems.append(f"{col}: observed [{vmin:.4g}, {vmax:.4g}] outside plausible [{lo}, {hi}]")
    if exp["Test_Id"].duplicated().any():
        problems.append("duplicate Test_Id values among experimental rows")
    if not problems:
        for dist, g in exp.groupby("Nozzle_to_Target_Distance_mm"):
            r = np.sort(g["Radius_mm"].to_numpy())
            if len(r) < 3 or np.ptp(np.diff(r)) > 1e-6:
                problems.append(f"distance {dist:g} mm: radii {r.tolist()} are not uniformly spaced "
                                f"(the annular-area formula needs a constant radial step)")
    if problems:
        raise ValueError(f"Refusing to proceed: jet-sheet validation failed for {path.name} "
                         f"(sheet {sheet_name!r}):\n  - " + "\n  - ".join(problems))

    steps = {round(float(np.diff(np.sort(g["Radius_mm"].to_numpy())).mean()), 9)
             for _, g in exp.groupby("Nozzle_to_Target_Distance_mm")}
    if len(steps) != 1:
        raise ValueError(f"Radial step differs between distance groups: {sorted(steps)} mm")
    exp["Radius_m"] = exp["Radius_mm"] / 1000.0
    exp["Radial_Increment_m"] = steps.pop() / 1000.0   # derived from the data, not hard-coded

    if sorted(exp["Test_Id"].astype(int)) != list(range(1, len(exp) + 1)):
        print("[warn] Test_Id values are not 1..N consecutive -- check that no measurement row was dropped")
    print(f"[input] workbook: {path.name} | jet sheet: {sheet_name!r}")
    print(f"[input] {len(exp)} experimental rows; distances {sorted(exp['Nozzle_to_Target_Distance_mm'].unique().tolist())} mm; "
          f"radial step {exp['Radial_Increment_m'].iloc[0] * 1000:g} mm")
    if len(excluded):
        shown = [[v for v in row if not (isinstance(v, float) and np.isnan(v))][:4]
                 for row in excluded.itertuples(index=False)]
        print(f"[input] {len(excluded)} non-experimental row(s) excluded (labels/parameters): {shown[:4]}")
    return exp


# =============================================================================
# 2. RECOMPUTATION
# =============================================================================
def recompute(df: pd.DataFrame, rho_kgm3: float, use_sheet_area_formula: bool) -> pd.DataFrame:
    """Recompute the derived chain from the raw (radius, distance, head)
    measurements, with either the sheet's own (dimensionally wrong) area
    formula -- for the before/after comparison only -- or the corrected one.
    Density is always an explicit argument."""
    r = df["Radius_m"].to_numpy()
    h = df["Head_Difference_h_m"].to_numpy()
    dr = df["Radial_Increment_m"].to_numpy()
    V = np.sqrt(2 * G * h)                      # Torricelli: independent of density
    if use_sheet_area_formula:
        A = np.sqrt(2 * dr * h)                  # what the sheet computed: a length, labelled as an area
    else:
        A = np.pi * ((r + dr / 2) ** 2 - np.clip(r - dr / 2, 0, None) ** 2)  # annular ring
    Q = A * V
    out = df.copy()
    out["Velocity_m_s"] = V
    out["Area_m2"] = A
    out["Flow_m3_s"] = Q
    out["Momentum_Flux_N"] = rho_kgm3 * Q * V
    out["KE_Flux_W"] = 0.5 * rho_kgm3 * Q * V ** 2
    out["Mass_Flow_kg_s"] = rho_kgm3 * Q
    out["Dynamic_Pressure_Pa"] = 0.5 * rho_kgm3 * V ** 2
    return out


def bug_impact_stats(cmp: pd.DataFrame) -> dict:
    """Computed description of how wrong the original momentum-flux column was."""
    ratio = cmp["ratio_original_over_corrected"]
    center = cmp[cmp.Radius_mm == 0]
    off = cmp[(cmp.Radius_mm > 0) & ratio.notna() & np.isfinite(ratio)]
    r = off["ratio_original_over_corrected"]
    return {
        "center": {int(d): float(v) for d, v in zip(center.Distance_mm, center.ratio_original_over_corrected)},
        "off_min_ratio": float(r.min()), "off_max_ratio": float(r.max()), "off_median_ratio": float(r.median()),
        "too_small_min": float(1.0 / r.max()), "too_small_max": float(1.0 / r.min()),
        "too_small_median": float(1.0 / r.median()), "n_off": int(len(r)),
    }


# =============================================================================
# 3. MAIN
# =============================================================================
def main():
    cfg.ensure_output_dirs()
    cfg.apply_plot_style()
    workbook = cfg.resolve_workbook()
    jet_sheet = cfg.resolve_jet_sheet(workbook)
    df_raw = load_jet_sheet(workbook, jet_sheet)

    rho_water = float(base.water_density_kgm3(ASSUMED_WATER_T_C))
    print(f"[assume] water at {ASSUMED_WATER_T_C:.0f} degC -> rho = {rho_water:.2f} kg/m3 "
          f"(no temperature was logged for this bench)")

    corrected = recompute(df_raw, rho_water, use_sheet_area_formula=False)
    original = recompute(df_raw, SHEET_AIR_DENSITY, use_sheet_area_formula=True)
    corrected.to_csv(cfg.results_dir() / "jet_corrected.csv", index=False)

    cmp = pd.DataFrame({
        "Test_Id": df_raw.Test_Id.astype(int), "Radius_mm": df_raw.Radius_mm,
        "Distance_mm": df_raw.Nozzle_to_Target_Distance_mm,
        "Momentum_original_N": original.Momentum_Flux_N, "Momentum_corrected_N": corrected.Momentum_Flux_N,
    })
    cmp["ratio_original_over_corrected"] = np.where(
        cmp.Momentum_corrected_N > 0, cmp.Momentum_original_N / cmp.Momentum_corrected_N, np.nan)
    cmp.to_csv(cfg.results_dir() / "bug_impact_comparison.csv", index=False)
    stats = bug_impact_stats(cmp)
    print("\n[bug impact] original / corrected momentum flux")
    print("  centerline: " + ", ".join(f"{d} mm: {v:.2f}x" for d, v in stats["center"].items()))
    print(f"  off-centerline (n={stats['n_off']}): original is {stats['too_small_min']:.1f}x to "
          f"{stats['too_small_max']:.0f}x too small (median {stats['too_small_median']:.0f}x)")

    # ---- fluid scaling: same fluids + same density function as stage 2 ----
    fluid_rows = [{"Fluid": "Water as tested (T assumed)", "API": np.nan,
                   "T_C": ASSUMED_WATER_T_C, "rho_kgm3": rho_water}]
    for name, spec in og.FLUIDS.items():
        if name == "Water (tested)":
            continue  # stage 2's 25 degC pump-test water; the jet's own water is the row above
        fluid_rows.append({"Fluid": name, "API": spec.get("api") if spec.get("api") is not None else np.nan,
                           "T_C": spec["T_C"], "rho_kgm3": og.fluid_density_kgm3(spec)})
    fluids = pd.DataFrame(fluid_rows)
    fluids["rho_ratio_vs_tested_water"] = fluids["rho_kgm3"] / rho_water
    fluids.to_csv(cfg.results_dir() / "fluid_density_scaling.csv", index=False)
    print("\n[fluid scaling] momentum & kinetic-energy flux scale with density; velocity is unchanged")
    print(fluids.round(3).to_string(index=False))

    fdir = cfg.figures_dir()
    plot_velocity_profiles(corrected, fdir / "07_jet_velocity_profiles.png")
    plot_bug_impact(cmp, stats, fdir / "08_bug_impact_before_after.png")
    plot_fluid_comparison(corrected, fluids, fdir / "09_jet_fluid_comparison.png")
    (cfg.docs_dir() / "jet_nozzle_notes.md").write_text(render_notes(rho_water, stats, fluids), encoding="utf-8")
    print("[saved] figures/07-09, docs/jet_nozzle_notes.md")

    cfg.update_run_metadata(jet={"sheet": jet_sheet, "n_experimental_rows": int(len(df_raw)),
                                 "radial_step_mm": float(df_raw.Radial_Increment_m.iloc[0] * 1000),
                                 "assumed_water_T_C": ASSUMED_WATER_T_C, "water_density_kgm3": rho_water})
    print("\n" + "=" * 64 + "\n  STAGE 3 DONE\n" + "=" * 64)


# =============================================================================
# 4. PLOTS + NOTES
# =============================================================================
def plot_velocity_profiles(df, path):
    fig, ax = plt.subplots(figsize=(6.5, 4.6))
    for dist, g in df.groupby("Nozzle_to_Target_Distance_mm"):
        g = g.sort_values("Radius_mm")
        ax.plot(g.Radius_mm, g.Velocity_m_s, "o-", color=DIST_COLORS.get(int(dist), "#555"),
                label=f"{int(dist)} mm from nozzle")
    ax.set_xlabel("Radius from jet centerline (mm)")
    ax.set_ylabel("Local jet velocity (m/s), Torricelli V = sqrt(2gh)")
    ax.set_title("Free-jet radial velocity profile\n(unaffected by either sheet formula problem)")
    ax.legend(fontsize=9)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_bug_impact(cmp, stats, path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6))
    for dist, g in cmp.groupby("Distance_mm"):
        g = g.sort_values("Radius_mm")
        color = DIST_COLORS.get(int(dist), "#555")
        ax1.plot(g.Radius_mm, g.Momentum_original_N, "x--", color=color, alpha=0.6,
                 label=f"{int(dist)} mm, original")
        ax1.plot(g.Radius_mm, g.Momentum_corrected_N, "o-", color=color, label=f"{int(dist)} mm, corrected")
    ax1.set_xlabel("Radius (mm)"); ax1.set_ylabel("Momentum flux (N)")
    ax1.set_title("Momentum flux: original sheet formulas vs. corrected\n(dashed x = original, solid o = corrected)")
    ax1.legend(fontsize=6.5)

    ax2.plot(cmp.Radius_mm, cmp.ratio_original_over_corrected, "o", color="#8c564b")
    ax2.axhline(1.0, color="gray", ls=":", lw=1)
    ax2.set_yscale("log")
    ax2.set_xlabel("Radius (mm)"); ax2.set_ylabel("original / corrected (log scale)")
    c_pct = 10 * np.ceil(100 * max(abs(v - 1.0) for v in stats["center"].values()) / 10)
    ax2.set_title(f"Centerline coincidentally within ~{c_pct:.0f}%; elsewhere the original is\n"
                  f"{stats['too_small_min']:.0f}x to {stats['too_small_max']:.0f}x too small "
                  f"(1.0 = no error)")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_fluid_comparison(df, fluids, path):
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    g = df[df.Nozzle_to_Target_Distance_mm == df.Nozzle_to_Target_Distance_mm.min()].sort_values("Radius_mm")
    dist = int(g.Nozzle_to_Target_Distance_mm.iloc[0])
    palette = dict(og.PALETTE)
    for r in fluids.itertuples():
        color = palette.get(r.Fluid, "#1f77b4" if r.Fluid.startswith("Water as tested") else "#555")
        ax.plot(g.Radius_mm, g.Momentum_Flux_N * r.rho_ratio_vs_tested_water, "o-", color=color,
                label=f"{r.Fluid}: {r.rho_kgm3:.0f} kg/m3 @ {r.T_C:.0f} degC")
    ax.set_xlabel("Radius (mm)"); ax.set_ylabel("Momentum flux (N)")
    ax.set_title(f"Momentum flux by fluid at {dist} mm: first-order density scaling\n"
                 "(ideal nozzle model; viscosity / discharge coefficient not modeled)")
    ax.legend(fontsize=7.2)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def render_notes(rho_water: float, stats: dict, fluids: pd.DataFrame) -> str:
    center = "; ".join(
        f"{d} mm: {v:.2f}x ({(v - 1) * 100:+.0f}%)" for d, v in stats["center"].items())
    lines = [
        "# Stage 3 notes -- jet nozzle characterization and the oil & gas connection",
        "",
        "## The connection",
        "A hydraulic jet pump -- a proven oil & gas artificial-lift technology in use since the 1930s -- "
        "is a nozzle, a throat and a diffuser: high-pressure power fluid (water- or oil-based) accelerates "
        "through the nozzle, the low-pressure region it creates entrains produced fluid at the throat "
        "(Venturi effect), and the diffuser turns the mixed stream's velocity back into pressure. The "
        "nozzle-to-throat area ratio is the primary design parameter (source: SPE JPT, *Jet Pumps: An "
        "Efficient Technology for Production Enhancement of Mature Oil Fields*, 2021). This bench data "
        "characterises only the nozzle stage -- radial velocity and momentum/energy flux of a free jet at "
        "three downstream distances -- with the same physics. It has no throat or diffuser, so it is not a "
        "jet-pump performance model.",
        "",
        "## Two formula problems in the workbook revision examined",
        "Found by recomputing every derived quantity from the raw radius / distance / head measurements. "
        "The corrected values are recomputed from the raw data and stand regardless of whether a later "
        "workbook revision has already changed these formulas.",
        "1. `Annular_Area_m2` was SQRT(2 x radial increment x h): dimensionally a length, not an area. "
        "The sheet's own annular-ring formula (`Radial_Area_Exact_m2`) is used instead.",
        f"2. The density in every momentum / energy / mass-flow formula was labelled "
        f"`Air_Density_kg_m3 = {SHEET_AIR_DENSITY}` for a head-driven liquid jet. Water's density at an "
        f"assumed {ASSUMED_WATER_T_C:.0f} degC is used instead ({rho_water:.1f} kg/m3, the pump stage's own "
        f"correlation; no temperature was logged for this bench, so this is an assumption, not a measurement).",
        "",
        "## How wrong was the original momentum-flux column? (computed)",
        f"* At the jet centerline the two errors land close together by coincidence -- original / corrected "
        f"= {center}.",
        f"* At every other radius (n = {stats['n_off']}) the original is {stats['too_small_min']:.1f}x to "
        f"{stats['too_small_max']:.0f}x too small (median {stats['too_small_median']:.0f}x), because the "
        "buggy \"area\" term depends on head and shrinks with radius while the true annular-ring area grows "
        "with radius. The original therefore got the *shape* of the radial profile wrong as well as its "
        "scale. Per-row detail: `results/bug_impact_comparison.csv`.",
        "",
        "## Fluid extension -- first-order density scaling only",
        "Jet velocity V = sqrt(2 g h) does not depend on density under the ideal Torricelli/Bernoulli "
        "assumption, so it is unchanged. Momentum and kinetic-energy flux scale linearly with density at the "
        "same head and geometry. This uses the same fluids and the same density function as the pump/oil & "
        "gas stage (crude densities at 40 degC via the representative thermal-expansion coefficient):",
        "",
        "| Fluid | T (degC) | rho (kg/m3) | rho / rho(tested water) |",
        "|---|---|---|---|",
    ]
    for r in fluids.itertuples():
        lines.append(f"| {r.Fluid} | {r.T_C:.0f} | {r.rho_kgm3:.1f} | {r.rho_ratio_vs_tested_water:.3f} |")
    lines += [
        "",
        "This is a first-order scaling under an ideal nozzle model, **not** a prediction of crude-oil "
        "jet-pump performance. Not quantified: viscosity's effect on the nozzle discharge coefficient "
        "(a more viscous fluid typically discharges slightly below the ideal Torricelli velocity at lower "
        "Reynolds numbers, so the scaled figures are an upper bound for a viscous power fluid). Quantifying "
        "it would need this nozzle's own discharge-coefficient-vs-Reynolds-number curve, which this bench "
        "did not measure.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
