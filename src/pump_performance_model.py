"""
pump_performance_model.py -- Stage 1: pump performance modeling (bench data, water)
=============================================================================
What this stage does
--------------------
Predicts the pump's measured sensor responses (water temperature, inlet and
outlet pressure, flow rate, electrical input power) from the two variables
the experimenter actually controlled -- pump speed and throttle-valve
position -- then pushes those predictions through a deterministic hydraulics
layer to obtain derived engineering quantities (head, efficiency, NPSH
available, Reynolds number, ...), with prediction uncertainty carried
through by Monte-Carlo propagation.

Validation design: leave-one-speed-out (LOSO)
---------------------------------------------
Three pump speeds were tested (1050, 1200, 1350 rpm). LOSO trains on two of
them and predicts the third. That is *not* extrapolation in all three folds:

    hold out 1050 rpm -> train 1200, 1350 -> target is BELOW the training range
                          (true speed extrapolation)
    hold out 1350 rpm -> train 1050, 1200 -> target is ABOVE the training range
                          (true speed extrapolation)
    hold out 1200 rpm -> train 1050, 1350 -> target is BRACKETED by the training
                          speeds (interpolation across speed)

Every LOSO result is therefore reported for the two extrapolation folds, for
the interpolation fold, and pooled over all three, and the code derives the
fold type from the speeds rather than hard-coding it. With only three speeds
these are small samples (n = 16 and n = 8 points) -- the ranking of methods
is the finding, not the third decimal of any R^2.

Baseline: the textbook pump affinity laws (Q ~ N, H ~ N^2), a zero-parameter
physical prediction, evaluated on exactly the same folds.

Problems fixed relative to the original "sprint7" script
--------------------------------------------------------
1) A DATA BUG, not a modeling bug. The original loader matched raw columns
   by header name and silently fell back to hard-coded positions that were
   shifted by one column, so pump *speed* was fed into the formulas as *water
   temperature*, and four other variables were shifted likewise. Columns are
   now read by validated position, checked against physically plausible
   ranges, and the physics layer is cross-checked against the workbook's own
   cached values (results/physics_vs_workbook_crosscheck.csv).
2) A METHODOLOGY problem ("paper tiger" machine learning). The original
   pipeline "predicted" 31 engineering quantities (head, efficiency, NPSH,
   Reynolds number, OPEX, CO2, ...), every one of which is a deterministic
   formula of the same six raw measurements used as features. A model cannot
   fail to score well at that. The task is now to predict the raw sensor
   responses; the engineering quantities are derived from them afterwards.

Run
---
    python src/pump_performance_model.py
The workbook is found via DATA_PUMP_XLSX, or the single .xlsx in data/ (see
data/README.md). Outputs go to results/, figures/ and docs/ (override the
root with DATA_PUMP_OUTPUT_DIR).
=============================================================================
"""
from __future__ import annotations

import platform
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import project_config as cfg

warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

EXPECTED_ROWS = 24
RANDOM_SEED = 42
N_MC_SAMPLES = 4000  # Monte-Carlo draws for uncertainty propagation
SPEED_COLORS = {1050: "#1f77b4", 1200: "#ff7f0e", 1350: "#2ca02c"}


# =============================================================================
# 1. DATA LOADING  --  validated positions + physical-range sanity checks
# =============================================================================
# Layout of the pump sheet (0-based column indices), confirmed by reproducing
# the workbook's own cached Total Head and Pump Efficiency columns exactly.
RAW_COLUMNS = {
    "SampleNo": 0,
    "Setting_pct": 1,
    "Speed_rpm": 2,
    "T_water_C": 3,
    "Pin_kPa": 4,
    "Pout_kPa": 5,
    "Q_lps": 6,
    "P_input_W": 15,
}

# Loose physical plausibility bounds. They catch a gross column shift (the
# original bug fed RPM into a temperature slot) but not every possible one;
# the physics cross-check below is the stronger guard.
RAW_RANGES = {
    "SampleNo": (1, 100),
    "Setting_pct": (0, 100),
    "Speed_rpm": (0, 10000),
    "T_water_C": (-5, 80),
    "Pin_kPa": (-50, 200),
    "Pout_kPa": (-50, 1000),
    "Q_lps": (0, 100),
    "P_input_W": (0, 5000),
}

# Soft check only (warns, never raises): a header at a validated position that
# no longer looks like the expected quantity is worth a human look, but a
# renamed header alone should not stop a run whose numbers all validate.
HEADER_HINTS = {
    "SampleNo": ("sample",),
    "Setting_pct": ("setting",),
    "Speed_rpm": ("speed", "rpm"),
    "T_water_C": ("temp",),
    "Pin_kPa": ("inlet", "pin"),
    "Pout_kPa": ("outlet", "pout"),
    "Q_lps": ("flow",),
    "P_input_W": ("p in", "input", "power"),
}


def load_pump_data(path=None, sheet_name=None, expected_rows: int = EXPECTED_ROWS) -> pd.DataFrame:
    """Load the pump test-bench sheet by validated column position.

    Raises (rather than substituting a wrong column) if the row count or any
    column's value range does not match what this workbook layout contains.
    path=None -> cfg.resolve_workbook(); sheet_name=None -> cfg.resolve_pump_sheet().
    """
    path = Path(path) if path is not None else cfg.resolve_workbook()
    if not path.is_file():
        raise FileNotFoundError(f"Workbook not found at: {path}")
    if sheet_name is None:
        sheet_name = cfg.resolve_pump_sheet(path)

    raw = pd.read_excel(path, sheet_name=sheet_name, header=0)
    # Drop fully empty ROWS only. Columns are deliberately kept: dropping an
    # all-empty column would shift every later position-based lookup.
    raw = raw.dropna(axis=0, how="all").reset_index(drop=True)

    if len(raw) != expected_rows:
        raise ValueError(
            f"Expected {expected_rows} data rows on sheet {sheet_name!r}, found {len(raw)}. "
            f"The column positions here were validated for the 24-row / 48-column pump "
            f"layout; a different row count means the sheet (or the sheet chosen) is not "
            f"that layout and the positions must be re-checked against its headers."
        )

    n_cols = raw.shape[1]
    df = pd.DataFrame(index=raw.index)
    problems = []
    for name, pos in RAW_COLUMNS.items():
        if pos >= n_cols:
            problems.append(f"  - {name}: expected column {pos}, sheet only has {n_cols} columns")
            continue
        series = pd.to_numeric(raw.iloc[:, pos], errors="coerce")
        if series.isna().any():
            problems.append(f"  - {name} (column {pos}, {raw.columns[pos]!r}): "
                            f"{int(series.isna().sum())} non-numeric value(s)")
            continue
        lo, hi = RAW_RANGES[name]
        vmin, vmax = float(series.min()), float(series.max())
        if vmin < lo or vmax > hi:
            problems.append(
                f"  - {name} (column {pos}, {raw.columns[pos]!r}): observed range "
                f"[{vmin:.3g}, {vmax:.3g}] is outside the plausible range [{lo}, {hi}] -- "
                f"the exact symptom of a column-position mismatch"
            )
            continue
        df[name] = series.to_numpy(dtype=float)

    if problems:
        raise ValueError(
            f"Refusing to proceed: raw-column validation failed for {path.name} "
            f"(sheet {sheet_name!r}).\n" + "\n".join(problems) +
            "\n\nExpected column layout (0-based position -> name):\n" +
            "\n".join(f"  [{p}] {n}" for n, p in RAW_COLUMNS.items())
        )

    for name, pos in RAW_COLUMNS.items():
        header = cfg.norm(raw.columns[pos])
        if not any(cfg.norm(h) in header for h in HEADER_HINTS[name]):
            print(f"[warn] header at position {pos} is {str(raw.columns[pos])!r}; "
                  f"expected something like {HEADER_HINTS[name]} for {name}. Numbers passed "
                  f"validation, but check the sheet layout has not changed.")

    df["SampleNo"] = np.rint(df["SampleNo"]).astype(int)
    n_steps = df.groupby("Setting_pct")["SampleNo"].transform("count")
    rank = df.groupby("Setting_pct")["SampleNo"].rank(method="first")
    df["valve_frac"] = (rank - 1) / (n_steps - 1)  # 0 (closed) .. 1 (fully open), per speed

    print(f"[input] workbook: {path.name} | pump sheet: {sheet_name!r}")
    print(f"[input] {len(df)} rows loaded and range-validated OK")
    print(f"[input] speeds tested: {sorted(df.Speed_rpm.unique().tolist())} rpm "
          f"({sorted(df.Setting_pct.unique().tolist())} % setting)")
    return df

# =============================================================================
# 2. PHYSICS LAYER  (verified correct -- see module docstring)
# =============================================================================
# Fixed rig constants (pipe geometry, motor assumption) as used in the
# original workbook/script; these describe the physical test rig, not the
# ML pipeline, and are unchanged here.
PI = np.pi
G = 9.81
HE = 0.075       # elevation head, m (fixed rig geometry)
D_IN = 0.0235    # inlet pipe diameter, m
D_OUT = 0.0175   # outlet pipe diameter, m
Q_MEFF = 0.5     # assumed constant motor efficiency fraction (rig spec, not fitted)
Z_PIPE = 0.916   # pipe length, m
D_PIPE = 0.032   # system pipe diameter, m
KN = 4.9         # minor-loss coefficient
CCOEF = 140      # Hazen-Williams C coefficient
MU = 0.000000833 # kinematic viscosity, m^2/s


def water_density_kgm3(T_C):
    """Water density (kg/m^3) vs. temperature (degC): the same polynomial the
    source workbook uses. Single definition, reused by the jet stage."""
    T = np.asarray(T_C, dtype=float)
    return (0.000015324364 * T**3 - 0.00584994855 * T**2
            + 0.016286058705 * T + 1000.04105055224)


def physics(setting_pct, speed_rpm, t_water_c, pin_kpa, pout_kpa, q_lps, p_input_w) -> dict:
    """Deterministic, row-wise hydraulics/thermo formulas mapping the raw
    measurements to the derived engineering fields. No fitting happens here --
    this is pure algebra, cross-checked against the workbook's own cached
    columns (see crosscheck_against_workbook). Anything that needs *other*
    rows (e.g. head deviation vs. the 1200-rpm curve) is not row-wise and
    lives at dataset level instead."""
    setting_pct = np.atleast_1d(np.asarray(setting_pct, dtype=float))
    speed_rpm = np.atleast_1d(np.asarray(speed_rpm, dtype=float))
    T = np.atleast_1d(np.asarray(t_water_c, dtype=float))
    Pin = np.atleast_1d(np.asarray(pin_kpa, dtype=float))
    Pout = np.atleast_1d(np.asarray(pout_kpa, dtype=float))
    Q = np.atleast_1d(np.asarray(q_lps, dtype=float))
    Pinput = np.atleast_1d(np.asarray(p_input_w, dtype=float))

    with np.errstate(all="ignore"):
        rho = water_density_kgm3(T)
        Vin = (Q / 1000.0) / (0.25 * PI * D_IN**2)
        Vout = (Q / 1000.0) / (0.25 * PI * D_OUT**2)
        Hs = ((Pout * 1000.0) - (Pin * 1000.0)) / (rho * G)
        Hv = (Vout**2 - Vin**2) / (2.0 * G)
        Ht = Hs + Hv + HE
        Ph = Ht * (Q / 1000.0) * rho * G
        Pm = Pinput * Q_MEFF
        eff_motor = np.divide(Pm * 100.0, Pinput, out=np.zeros_like(Pm), where=Pinput != 0)
        eff_pump = np.divide(100.0 * Ph, Pm, out=np.zeros_like(Ph), where=Pm != 0)

        Pv = (0.0000734164386479164 * T**3 - 0.000260526687053453 * T**2
              + 0.063826574528278 * T + 0.565171257254576)
        NPSHa = 10.194 + (Pin / G) + Hv - (Pv / G)

        term1 = (10.67 * Z_PIPE * (Q / 1000.0)**1.852) / (CCOEF**1.852 * D_PIPE**4.87)
        term2 = KN * (((Q / 1000.0) / (PI * (D_PIPE / 2.0)**2))**2) / (2.0 * G)
        HeadLoss = np.where(Q == 0, 0.0, term1 + term2)

        Flow1200 = np.where(speed_rpm == 0, 0.0, Q * (1200.0 / speed_rpm))
        Head1200 = np.where(speed_rpm == 0, 0.0, Ht * (1200.0 / speed_rpm)**2)

        DynWaste = Pinput - Ph
        WireWater = np.divide(Ph * 100.0, Pinput, out=np.zeros_like(Ph), where=Pinput != 0)
        Torque = np.divide(Pm, (2.0 * PI * speed_rpm) / 60.0, out=np.zeros_like(Pm), where=speed_rpm != 0)

        CavNum = np.where(Vin == 0, 0.0, ((Pin + 101.325 - Pv) * 1000.0) / (0.5 * rho * Vin**2))
        Re = np.where(Vout == 0, 0.0, (Vout * D_PIPE) / MU)
        Darcy = np.where(Re == 0, 0.0, 0.25 / (np.log10((0.000015 / (3.7 * D_PIPE)) + (5.74 / (Re**0.9)))**2))

        Ns = np.where(Q == 0, 0.0, (speed_rpm * np.sqrt(Q / 1000.0)) / np.where(Ht > 0, Ht, np.nan)**0.75)
        Nss = np.where(Q == 0, 0.0, (speed_rpm * np.sqrt(Q / 1000.0)) / np.where(NPSHa > 0, NPSHa, np.nan)**0.75)
        Ns = np.nan_to_num(Ns, nan=0.0)
        Nss = np.nan_to_num(Nss, nan=0.0)

        ThermoRise = np.where(Q == 0, 0.0, DynWaste / ((Q / 1000.0) * rho * 4184.0))
        SysRes = np.where(Q == 0, 0.0, HeadLoss / (Q / 1000.0)**2)
        Thoma = np.divide(NPSHa, Ht, out=np.zeros_like(Ht), where=Ht != 0)

        OPEX = (Pinput / 1000.0) * 8760.0 * 1500.0
        Waste = (DynWaste / 1000.0) * 8760.0 * 1500.0
        CO2 = (Pinput / 1000.0) * 8760.0 * 0.87

    return {
        "TotalHead_m": Ht, "PumpEff_pct": eff_pump, "HydPower_W": Ph, "MechPower_W": Pm,
        "InputPower_W": Pinput, "NPSHa_m": NPSHa, "CavNum": CavNum, "Thoma": Thoma,
        "Nss": Nss, "StaticHead_m": Hs, "VelHead_m": Hv, "ElevHead_m": np.full_like(Ht, HE),
        "Flow_lps": Q, "Vin_mps": Vin, "Vout_mps": Vout, "Re": Re, "Darcy_f": Darcy,
        "SysHeadLoss_m": HeadLoss, "SysResCoef": SysRes, "MotorEff_pct": eff_motor,
        "Torque_Nm": Torque, "ThermoRise_K": ThermoRise, "DynWaste_W": DynWaste,
        "WireWaterEff_pct": WireWater, "OPEX": OPEX, "FinWaste": Waste, "CO2": CO2,
        "Ns": Ns, "Flow1200_lps": Flow1200, "Head1200_m": Head1200,
        "Density_kgm3": rho, "Pv_kPa": Pv,
    }


def head_deviation_vs_1200_pct(sample_no, speed_rpm, head1200_m, total_head_m,
                                ref_speed_rpm: float = 1200.0) -> np.ndarray:
    """Dataset-level (cross-row) quantity, reproducing the workbook's
    "Head Deviation vs 1200 RPM [%]" column exactly: each non-reference row's
    head *normalised to the reference speed by the affinity law* is compared
    with the *measured* head of the reference-speed row that has the same
    sample number (same throttle step). Reference-speed rows are 0 by
    definition.

    This is a genuine cross-speed comparison, but it is not row-wise, so it
    cannot live in physics(). An earlier Python revision computed a
    self-referential stand-in (a row's head against a rescaling of itself)
    that collapsed to a constant per speed; that version has been removed.
    The more general affinity-law test is the LOSO baseline in this module.
    """
    sample_no = np.asarray(sample_no)
    speed_rpm = np.asarray(speed_rpm, dtype=float)
    ref_heads = {int(s): float(h) for s, h, v in zip(sample_no, total_head_m, speed_rpm)
                 if v == ref_speed_rpm}
    out_arr = np.zeros(len(sample_no), dtype=float)
    for i, (s, v, h1200) in enumerate(zip(sample_no, speed_rpm, head1200_m)):
        if v == ref_speed_rpm:
            continue
        if int(s) not in ref_heads:
            raise ValueError(f"No {ref_speed_rpm:.0f}-rpm row with sample number {int(s)} to compare against")
        out_arr[i] = abs(float(h1200) - ref_heads[int(s)]) / ref_heads[int(s)] * 100.0
    return out_arr


# Workbook column (0-based) holding the cached value for each derived field.
# HeadDev_vs1200_pct is the dataset-level quantity above.
WORKBOOK_KPI_COLUMNS = {
    "Density_kgm3": 7, "Vin_mps": 8, "Vout_mps": 9, "StaticHead_m": 10, "VelHead_m": 11,
    "TotalHead_m": 13, "HydPower_W": 14, "MechPower_W": 17, "MotorEff_pct": 18,
    "PumpEff_pct": 20, "Pv_kPa": 23, "NPSHa_m": 24, "SysHeadLoss_m": 29,
    "Flow1200_lps": 30, "Head1200_m": 31, "HeadDev_vs1200_pct": 32, "DynWaste_W": 33,
    "WireWaterEff_pct": 34, "Torque_Nm": 35, "CavNum": 36, "Re": 37, "Darcy_f": 38,
    "Ns": 39, "Nss": 40, "ThermoRise_K": 41, "SysResCoef": 42, "Thoma": 43,
    "OPEX": 44, "FinWaste": 46, "CO2": 47,
}
CRITICAL_CROSSCHECK = ("TotalHead_m", "PumpEff_pct")


def compute_derived(df: pd.DataFrame) -> dict:
    """Row-wise physics() on measured data plus the dataset-level head deviation."""
    ph = physics(df.Setting_pct, df.Speed_rpm, df.T_water_C, df.Pin_kPa,
                 df.Pout_kPa, df.Q_lps, df.P_input_W)
    ph["HeadDev_vs1200_pct"] = head_deviation_vs_1200_pct(
        df.SampleNo, df.Speed_rpm, ph["Head1200_m"], ph["TotalHead_m"])
    return ph


def crosscheck_against_workbook(df: pd.DataFrame, path, sheet_name) -> pd.DataFrame:
    """Compare every derived field that has a workbook column against that
    column's cached values, and save the comparison.

    Total Head and Pump Efficiency are the critical guard: they depend on all
    the raw inputs, so a wrong column mapping cannot reproduce them. If they
    disagree -- or cannot be read at all (no cached values) -- this raises,
    because then nothing protects the run from a mis-wired input. Every other
    column only produces a reported status (and a warning on mismatch).
    """
    sheet = pd.read_excel(path, sheet_name=sheet_name, header=0)
    sheet = sheet.dropna(axis=0, how="all").reset_index(drop=True)
    if len(sheet) != len(df):
        raise ValueError(f"Cross-check sheet has {len(sheet)} rows but {len(df)} were loaded")
    mine = compute_derived(df)

    rows = []
    for key, col in WORKBOOK_KPI_COLUMNS.items():
        rec = {"field": key, "workbook_column_index": col, "workbook_header": "",
               "max_abs_diff": np.nan, "max_rel_diff": np.nan, "status": ""}
        if col >= sheet.shape[1]:
            rec["status"] = "column not present in sheet"
        else:
            rec["workbook_header"] = " ".join(str(sheet.columns[col]).split())
            ref = pd.to_numeric(sheet.iloc[:, col], errors="coerce").to_numpy(float)
            if np.isnan(ref).any():
                rec["status"] = "no cached values in workbook"
            else:
                a = np.asarray(mine[key], dtype=float)
                rec["max_abs_diff"] = float(np.max(np.abs(a - ref)))
                rec["max_rel_diff"] = float(np.max(np.abs(a - ref) / np.maximum(np.abs(ref), 1e-9)))
                rec["status"] = "match" if rec["max_rel_diff"] < 1e-6 else "MISMATCH"
        rows.append(rec)
    table = pd.DataFrame(rows)

    crit = table[table.field.isin(CRITICAL_CROSSCHECK)]
    unreadable = crit[~crit.status.isin(["match", "MISMATCH"])]
    if len(unreadable):
        raise ValueError(
            "Cannot verify the run: the workbook has no readable cached values for "
            f"{list(unreadable.field)}. Open the workbook in Excel, let it recalculate, save it, "
            "and run again -- without these columns nothing guards against a mis-wired input.")
    if (crit.max_abs_diff > 1e-3).any():
        raise AssertionError(
            "physics() does not reproduce the workbook's own Total Head / Pump Efficiency "
            f"columns:\n{crit.to_string(index=False)}\nDo not trust downstream results.")

    n_match = int((table.status == "match").sum())
    n_checked = int(table.status.isin(["match", "MISMATCH"]).sum())
    bad = table[table.status == "MISMATCH"].field.tolist()
    print(f"[check] physics vs workbook cached values: {n_match}/{n_checked} derived fields match "
          f"(critical Total Head & Efficiency max abs diff "
          f"{float(crit.max_abs_diff.max()):.1e})")
    if bad:
        print(f"[warn] non-critical fields that do NOT match the workbook: {bad}")
    unavailable = table[~table.status.isin(["match", "MISMATCH"])].field.tolist()
    if unavailable:
        print(f"[note] not verifiable (column missing / no cached values): {unavailable}")

    cfg.ensure_output_dirs()
    table.to_csv(cfg.results_dir() / "physics_vs_workbook_crosscheck.csv", index=False)
    return table
# =============================================================================
# 3. THE ACTUAL PREDICTION TASK
# =============================================================================
# The two variables the experimenter controlled: pump speed (3 levels) and
# throttle-valve position (8 steps per speed, encoded as valve_frac in
# [0, 1]). Every derived engineering field is a deterministic function of
# what the pump does in response -- that response is what a model can
# legitimately be asked to predict.
DESIGN_COLS = ["Speed_rpm", "valve_frac"]
RESPONSE_COLS = ["T_water_C", "Pin_kPa", "Pout_kPa", "Q_lps", "P_input_W"]


def design_matrix(df: pd.DataFrame) -> np.ndarray:
    """[speed, valve_frac, speed*valve_frac] -- the interaction term lets a
    linear model represent that curve *shape* (not just level) changes with
    speed, which is exactly what centrifugal pump curves do."""
    speed = df["Speed_rpm"].to_numpy(float)
    vf = df["valve_frac"].to_numpy(float)
    return np.column_stack([speed, vf, speed * vf])


# ---- Candidate models --------------------------------------------------
def make_ridge():
    return make_pipeline(
        StandardScaler(),
        RidgeCV(alphas=np.logspace(-2, 3, 25)),  # alpha chosen by internal LOOCV on the training fold only
    )


def make_gp():
    kernel = (ConstantKernel(1.0, (1e-2, 1e3))
              * RBF(length_scale=[1.0, 1.0, 1.0], length_scale_bounds=(1e-2, 1e3))
              + WhiteKernel(noise_level=1e-2, noise_level_bounds=(1e-6, 1e2)))
    return make_pipeline(
        StandardScaler(),
        GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                  n_restarts_optimizer=8, random_state=RANDOM_SEED),
    )


MODEL_FACTORIES = {"Ridge": make_ridge, "GP": make_gp}


def predict_with_std(fitted_pipeline, X):
    """Return (mean, std). Ridge has no native predictive std, so it is
    reported as a point predictor (std = 0) -- honest rather than invented."""
    model = fitted_pipeline.steps[-1][1]
    if isinstance(model, GaussianProcessRegressor):
        Xs = fitted_pipeline[:-1].transform(X)
        mean, std = model.predict(Xs, return_std=True)
        return mean, std
    return fitted_pipeline.predict(X), np.zeros(len(X))


# ---- Zero-parameter physics baseline: textbook pump affinity laws ------
def affinity_law_baseline(df: pd.DataFrame, train_mask: np.ndarray,
                           test_mask: np.ndarray) -> dict:
    """Predict Flow and Total Head at the held-out speed by scaling each
    training speed's curve via Q ~ N, H ~ N^2 (matched on valve_frac) and
    averaging -- no fitting, this is the textbook affinity law applied
    directly (the workbook's 1200-rpm-normalised columns, made a predictor)."""
    train = df[train_mask].copy()
    test = df[test_mask].copy()
    ph_train = physics(train.Setting_pct, train.Speed_rpm, train.T_water_C,
                        train.Pin_kPa, train.Pout_kPa, train.Q_lps, train.P_input_W)
    train = train.assign(Ht=ph_train["TotalHead_m"])

    preds_Q, preds_Ht = [], []
    for _, row in test.iterrows():
        n_target = row.Speed_rpm
        matched = train[np.isclose(train.valve_frac, row.valve_frac, atol=1e-6)]
        q_scaled = matched.Q_lps * (n_target / matched.Speed_rpm)
        h_scaled = matched.Ht * (n_target / matched.Speed_rpm) ** 2
        preds_Q.append(float(q_scaled.mean()))
        preds_Ht.append(float(h_scaled.mean()))
    return {"Q_lps": np.array(preds_Q), "TotalHead_m": np.array(preds_Ht)}


# =============================================================================
# 4. EVALUATION HARNESS
# =============================================================================
def rmse(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def mae(a, b):
    return float(np.mean(np.abs(np.asarray(a) - np.asarray(b))))


def r2(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ss_res = np.sum((a - b) ** 2)
    ss_tot = np.sum((a - a.mean()) ** 2)
    if ss_tot <= 1e-12:
        return 1.0 if ss_res <= 1e-12 else float("nan")
    return float(1.0 - ss_res / ss_tot)


@dataclass
class CVResult:
    response: str
    model: str
    scheme: str          # "LOSO" (unseen speed) or "LOOCV" (one point held out)
    y_true: np.ndarray
    y_pred: np.ndarray
    y_std: np.ndarray
    fold_id: np.ndarray  # held-out speed (LOSO) or row index (LOOCV)


def run_cv(df: pd.DataFrame, response: str, model_name: str, scheme: str) -> CVResult:
    X_all = design_matrix(df)
    y_all = df[response].to_numpy(float)
    n = len(df)
    y_pred = np.zeros(n)
    y_std = np.zeros(n)
    fold_id = np.zeros(n)

    if scheme == "LOSO":
        speeds = df["Speed_rpm"].to_numpy(float)
        folds = sorted(df["Speed_rpm"].unique())
        for held_speed in folds:
            test_mask = speeds == held_speed
            train_mask = ~test_mask
            pipe = MODEL_FACTORIES[model_name]()
            pipe.fit(X_all[train_mask], y_all[train_mask])
            mean, std = predict_with_std(pipe, X_all[test_mask])
            y_pred[test_mask] = mean
            y_std[test_mask] = std
            fold_id[test_mask] = held_speed
    elif scheme == "LOOCV":
        for i in range(n):
            train_mask = np.ones(n, dtype=bool)
            train_mask[i] = False
            pipe = MODEL_FACTORIES[model_name]()
            pipe.fit(X_all[train_mask], y_all[train_mask])
            mean, std = predict_with_std(pipe, X_all[[i]])
            y_pred[i] = mean[0]
            y_std[i] = std[0]
            fold_id[i] = i
    else:
        raise ValueError(scheme)

    return CVResult(response, model_name, scheme, y_all, y_pred, y_std, fold_id)



# ---- LOSO fold semantics: extrapolation vs interpolation ------------------
def fold_kind(held_speed: float, all_speeds) -> str:
    """'extrapolation' if the held-out speed lies outside the range of the
    remaining (training) speeds, else 'interpolation'. Derived from the data,
    not hard-coded, so it stays right if the tested speeds ever change."""
    train = [s for s in all_speeds if s != held_speed]
    if not train:
        raise ValueError("need at least two distinct speeds")
    return "extrapolation" if (held_speed < min(train) or held_speed > max(train)) else "interpolation"


def loso_subsets(speeds) -> tuple[dict, dict]:
    """Boolean masks {'all','extrapolation','interpolation'} over rows, plus
    {held_speed: fold_kind}."""
    speeds = np.asarray(speeds, dtype=float)
    uniq = sorted(np.unique(speeds).tolist())
    kinds = {s: fold_kind(s, uniq) for s in uniq}
    masks = {
        "all": np.ones(len(speeds), dtype=bool),
        "extrapolation": np.isin(speeds, [s for s, k in kinds.items() if k == "extrapolation"]),
        "interpolation": np.isin(speeds, [s for s, k in kinds.items() if k == "interpolation"]),
    }
    return masks, kinds


def subset_metrics(y_true, y_pred, mask) -> dict:
    """Metrics pooled over the masked rows. R^2 uses the masked rows' own mean
    for SS_tot, so it is not comparable across subsets of different spread."""
    y_true, y_pred = np.asarray(y_true, float)[mask], np.asarray(y_pred, float)[mask]
    if len(y_true) == 0:
        return {"n": 0, "RMSE": np.nan, "MAE": np.nan, "R2": np.nan, "SD": np.nan}
    return {"n": int(len(y_true)), "RMSE": rmse(y_true, y_pred), "MAE": mae(y_true, y_pred),
            "R2": r2(y_true, y_pred), "SD": float(np.std(y_true))}

# =============================================================================
# 5. UNCERTAINTY PROPAGATION  --  raw-response predictions -> derived fields
# =============================================================================
def propagate_to_kpis(df: pd.DataFrame, pred_mean: dict, pred_std: dict,
                       n_samples: int = N_MC_SAMPLES, seed: int = RANDOM_SEED) -> dict:
    """Monte-Carlo propagate per-response predictive (mean, std) for the 5
    raw responses through physics() to get a sample distribution for every
    derived field at every row. Responses are drawn independently (a stated
    simplification: true residual covariance, e.g. between Pout and Q, is
    not modeled), so the bands are a lower bound on the true uncertainty.
    Returns {kpi_name: array of shape (n_rows, n_samples)}.
    """
    rng = np.random.default_rng(seed)
    n = len(df)
    kpi_samples: dict[str, np.ndarray] = {}
    setting = df["Setting_pct"].to_numpy(float)
    speed = df["Speed_rpm"].to_numpy(float)

    for i in range(n):
        draws = {}
        for r in RESPONSE_COLS:
            s = pred_std[r][i]
            draws[r] = (np.full(n_samples, pred_mean[r][i]) if s <= 0
                        else rng.normal(pred_mean[r][i], s, size=n_samples))
        ph = physics(np.full(n_samples, setting[i]), np.full(n_samples, speed[i]),
                     draws["T_water_C"], draws["Pin_kPa"], draws["Pout_kPa"],
                     draws["Q_lps"], draws["P_input_W"])
        for kpi_name, arr in ph.items():
            if kpi_name not in kpi_samples:
                kpi_samples[kpi_name] = np.zeros((n, n_samples))
            kpi_samples[kpi_name][i] = arr
    return kpi_samples


def kpi_point_predictions(df: pd.DataFrame, pred_mean: dict) -> dict:
    """Deterministic KPIs from point predictions only (no MC), used for
    quick RMSE/R^2 scoring against ground truth."""
    setting = df["Setting_pct"].to_numpy(float)
    speed = df["Speed_rpm"].to_numpy(float)
    return physics(setting, speed, pred_mean["T_water_C"], pred_mean["Pin_kPa"],
                   pred_mean["Pout_kPa"], pred_mean["Q_lps"], pred_mean["P_input_W"])


# =============================================================================
# 6. PLOTS
# =============================================================================
def _kind_tag(kind: str) -> str:
    return "extrapolation" if kind == "extrapolation" else "interpolation"


def plot_pump_curves_loso(df, loso_gp, loso_ridge, affinity_by_speed, ground_truth_kpi,
                           fold_kinds, path):
    """One panel per tested speed: measured Total Head vs valve position,
    overlaid with each method's prediction when that speed was held out.
    Panel titles state whether the fold is true extrapolation or interpolation."""
    speeds = sorted(df["Speed_rpm"].unique())
    fig, axes = plt.subplots(1, len(speeds), figsize=(4.6 * len(speeds), 4.8),
                              sharey=True, constrained_layout=False)
    for ax, sp in zip(axes, speeds):
        mask = (df["Speed_rpm"] == sp).to_numpy()
        vf = df.loc[mask, "valve_frac"].to_numpy()
        order = np.argsort(vf)
        color = SPEED_COLORS.get(int(sp), "#555555")

        ax.plot(vf[order], ground_truth_kpi["TotalHead_m"][mask][order],
                "o-", color="black", ms=6, lw=1.2, label="Measured", zorder=5)

        gp_mean = loso_gp["TotalHead_m"][mask][order]
        gp_lo = loso_gp["TotalHead_m_p05"][mask][order]
        gp_hi = loso_gp["TotalHead_m_p95"][mask][order]
        ax.plot(vf[order], gp_mean, "-", color=color, lw=2, label="GP (speed held out)")
        ax.fill_between(vf[order], gp_lo, gp_hi, color=color, alpha=0.20, label="GP 90% interval")

        ax.plot(vf[order], loso_ridge["TotalHead_m"][mask][order], "--",
                color=color, lw=1.6, alpha=0.85, label="Ridge (speed held out)")

        if sp in affinity_by_speed:
            aff = affinity_by_speed[sp]
            ax.plot(aff["valve_frac"], aff["TotalHead_m"], ":", color="dimgray",
                    lw=1.8, label="Affinity-law baseline")

        ax.set_title(f"{int(sp)} rpm held out\n({_kind_tag(fold_kinds[sp])})")
        ax.set_xlabel("Valve opening (0=closed, 1=open)")
        if ax is axes[0]:
            ax.set_ylabel("Total Head (m)")
    fig.subplots_adjust(bottom=0.30, top=0.83, wspace=0.06)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, 0.02), frameon=False)
    fig.suptitle("Pump curve reconstruction at a speed the model never saw (leave-one-speed-out)")
    fig.savefig(path)
    plt.close(fig)


def plot_affinity_validation(df, ground_truth_kpi, affinity_Ht_loso, fold_kinds, path):
    """Left: all three measured curves scaled to a common 1200 rpm reference
    via the affinity laws -- good overlap means the affinity law holds for
    this pump over this speed range. Right: the affinity law's cross-speed
    prediction error when each speed is held out and predicted from the
    other two (never from the point's own speed)."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    for sp in sorted(df["Speed_rpm"].unique()):
        mask = (df["Speed_rpm"] == sp).to_numpy()
        vf = df.loc[mask, "valve_frac"].to_numpy()
        order = np.argsort(vf)
        color = SPEED_COLORS.get(int(sp), "#555555")
        ax1.plot(vf[order], ground_truth_kpi["Head1200_m"][mask][order], "o-",
                  color=color, label=f"{int(sp)} rpm -> scaled to 1200 rpm")

        true_h = ground_truth_kpi["TotalHead_m"][mask][order]
        pred_h = affinity_Ht_loso[mask][order]
        pct_err = np.abs(pred_h - true_h) / np.abs(true_h) * 100.0
        ax2.plot(vf[order], pct_err, "o-", color=color,
                 label=f"{int(sp)} rpm held out ({_kind_tag(fold_kinds[sp])})")
    ax1.set_xlabel("Valve opening (0=closed, 1=open)")
    ax1.set_ylabel("Head scaled to 1200 rpm reference (m)")
    ax1.set_title("Affinity-law collapse (good overlap = law holds)")
    ax1.legend(fontsize=8)
    ax2.set_xlabel("Valve opening (0=closed, 1=open)")
    ax2.set_ylabel("Absolute error vs. measured (%)")
    ax2.set_title("Affinity-law error, head predicted from\nthe other two speeds only")
    ax2.axhline(0, color="black", lw=0.8)
    ax2.legend(fontsize=8)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_raw_response_fits(df, loocv_by_response, path):
    """Interpolation sanity check: does the GP recover the shape of each
    directly-measured sensor curve when only one point at a time is held
    out (the easy case), across all 5 raw responses?"""
    fig, axes = plt.subplots(2, 3, figsize=(13, 7.5))
    axes = axes.ravel()
    for ax, resp in zip(axes, RESPONSE_COLS):
        cv = loocv_by_response[resp]
        for sp in sorted(df["Speed_rpm"].unique()):
            mask = (df["Speed_rpm"] == sp).to_numpy()
            vf = df.loc[mask, "valve_frac"].to_numpy()
            order = np.argsort(vf)
            color = SPEED_COLORS.get(int(sp), "#555555")
            ax.plot(vf[order], cv.y_true[mask][order], "o", color=color, ms=5)
            ax.plot(vf[order], cv.y_pred[mask][order], "-", color=color, lw=1.6)
        ax.set_title(resp)
        ax.set_xlabel("Valve opening")
    axes[-1].axis("off")
    handles = [plt.Line2D([0], [0], marker="o", linestyle="-", color=c, label=f"{s} rpm")
               for s, c in SPEED_COLORS.items()]
    axes[-1].legend(handles=handles, loc="center", fontsize=10, frameon=False,
                     title="markers = measured\nlines = GP LOOCV fit")
    fig.suptitle("Raw sensor responses: GP interpolation fit (LOOCV, one point held out at a time)")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_loso_parity(kpi_scores_df, fold_kinds, path):
    """Predicted vs. measured for the headline derived fields, leave-one-speed-out.
    Filled markers = true extrapolation folds; hollow markers = the
    interpolation fold. Legend R^2 is given for the extrapolation folds only
    and for all folds pooled."""
    kpis = kpi_scores_df["KPI"].unique()
    fig, axes = plt.subplots(1, len(kpis), figsize=(4.8 * len(kpis), 4.6))
    if len(kpis) == 1:
        axes = [axes]
    markers = {"Affinity-law": "D", "Ridge": "s", "GP": "o"}
    for ax, kpi in zip(axes, kpis):
        sub = kpi_scores_df[kpi_scores_df["KPI"] == kpi]
        all_vals = np.concatenate([np.concatenate(sub["y_true"].to_list()),
                                    np.concatenate(sub["y_pred"].to_list())]).astype(float)
        lo, hi = np.nanmin(all_vals), np.nanmax(all_vals)
        pad = 0.05 * (hi - lo + 1e-9)
        ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], "k--", lw=1, alpha=0.6)
        for _, row in sub.iterrows():
            speeds = np.asarray(row["speed"], dtype=float)
            colors = np.array([SPEED_COLORS.get(int(s), "#555") for s in speeds])
            is_ext = np.array([fold_kinds[s] == "extrapolation" for s in speeds])
            mk = markers.get(row["Method"], "x")
            label = f"{row['Method']}  (R\u00b2 {row['R2_extrap']:.2f} extrap. | {row['R2_all']:.2f} all)"
            ax.scatter(np.asarray(row["y_true"])[is_ext], np.asarray(row["y_pred"])[is_ext], marker=mk,
                       c=colors[is_ext], edgecolor="black", linewidth=0.4, s=60, label=label)
            ax.scatter(np.asarray(row["y_true"])[~is_ext], np.asarray(row["y_pred"])[~is_ext], marker=mk,
                       facecolors="none", edgecolors=colors[~is_ext], linewidth=1.5, s=60)
        ax.set_xlabel(f"Measured {kpi}")
        ax.set_ylabel(f"Predicted {kpi}")
        ax.set_title(kpi)
        ax.legend(fontsize=7.5, loc="best")
    fig.suptitle("Leave-one-speed-out: filled = extrapolation folds (outer speeds), "
                 "hollow = interpolation fold (middle speed)")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# =============================================================================
# 7. MAIN
# =============================================================================
HEADLINE_KPIS = ("TotalHead_m", "PumpEff_pct", "NPSHa_m")
SUBSET_SHORT = {"all": "all", "extrapolation": "extrap", "interpolation": "interp"}


def build_response_metrics(df: pd.DataFrame, cv_results: dict) -> pd.DataFrame:
    """Raw sensor responses: LOSO pooled three ways, plus LOOCV."""
    masks, _ = loso_subsets(df["Speed_rpm"].to_numpy())
    rows = []
    for resp in RESPONSE_COLS:
        for model in MODEL_FACTORIES:
            res = cv_results[(resp, model, "LOSO")]
            for subset, mask in masks.items():
                rows.append({"Response": resp, "Model": model, "CV_scheme": "LOSO",
                             "Subset": subset, **subset_metrics(res.y_true, res.y_pred, mask)})
            res = cv_results[(resp, model, "LOOCV")]
            rows.append({"Response": resp, "Model": model, "CV_scheme": "LOOCV",
                         "Subset": "all", **subset_metrics(res.y_true, res.y_pred, masks["all"])})
    return pd.DataFrame(rows)


def build_kpi_metrics(truth: dict, method_preds: dict, speeds) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wide table (one row per KPI x method, metrics per fold subset) and a
    per-held-out-speed table. method_preds: {(kpi, method): predicted array}."""
    masks, kinds = loso_subsets(speeds)
    speeds = np.asarray(speeds, dtype=float)
    wide, per_fold = [], []
    for (kpi, method), pred in method_preds.items():
        y = truth[kpi]
        rec = {"KPI": kpi, "Method": method}
        for subset, mask in masks.items():
            m = subset_metrics(y, pred, mask)
            for key, val in m.items():
                rec[f"{key}_{SUBSET_SHORT[subset]}"] = val
        wide.append(rec)
        for s, kind in kinds.items():
            m = subset_metrics(y, pred, speeds == s)
            per_fold.append({"KPI": kpi, "Method": method, "held_out_speed_rpm": s,
                             "fold_kind": kind, "n": m["n"], "RMSE": m["RMSE"], "MAE": m["MAE"]})
    return pd.DataFrame(wide), pd.DataFrame(per_fold)


def affinity_single_source_bias(df: pd.DataFrame, truth: dict) -> pd.DataFrame:
    """Mean signed error (predicted - measured, metres) of the affinity law when
    the held-out speed's head is predicted from ONE other speed at a time.
    Shows *why* the law's two-source average behaves differently by fold type:
    scaling up over-predicts and scaling down under-predicts, so bracketed
    sources cancel while one-sided sources compound."""
    speeds = df["Speed_rpm"].to_numpy(float)
    _, kinds = loso_subsets(speeds)
    ht = truth["TotalHead_m"]
    rows = []
    for held in sorted(kinds):
        for src in sorted(kinds):
            if src == held:
                continue
            m_h, m_s = speeds == held, speeds == src
            order_h = np.argsort(df.loc[m_h, "valve_frac"].to_numpy())
            order_s = np.argsort(df.loc[m_s, "valve_frac"].to_numpy())
            pred = ht[m_s][order_s] * (held / src) ** 2   # matched on valve step
            rows.append({"held_out_speed_rpm": held, "fold_kind": kinds[held],
                         "source_speed_rpm": src,
                         "mean_signed_error_m": float(np.mean(pred - ht[m_h][order_h]))})
    return pd.DataFrame(rows)


def t_water_sensitivity(df: pd.DataFrame, truth: dict, gp_mean: dict) -> pd.DataFrame:
    """How much of the GP's extrapolation error in the headline fields is due to
    its (weak) water-temperature prediction? Re-derives the fields with the
    *measured* temperature substituted and compares extrapolation-fold RMSE."""
    masks, _ = loso_subsets(df["Speed_rpm"].to_numpy(float))
    ext = masks["extrapolation"]
    base_kpi = kpi_point_predictions(df, gp_mean)
    oracle_kpi = kpi_point_predictions(df, {**gp_mean, "T_water_C": df["T_water_C"].to_numpy(float)})
    rows = []
    for kpi in HEADLINE_KPIS:
        a = rmse(truth[kpi][ext], base_kpi[kpi][ext])
        b = rmse(truth[kpi][ext], oracle_kpi[kpi][ext])
        rows.append({"KPI": kpi, "RMSE_extrap_GP": a, "RMSE_extrap_GP_with_measured_T": b,
                     "change_pct": 100.0 * (b - a) / a})
    return pd.DataFrame(rows)


def _f(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def render_report(df, workbook_name, pump_sheet, crosscheck, response_metrics, kpi_metrics,
                  kinds, bias_table, tsens) -> str:
    speeds_sorted = sorted(kinds)
    n_ok = int((crosscheck.status == "match").sum())
    n_chk = int(crosscheck.status.isin(["match", "MISMATCH"]).sum())

    def km(kpi, method, col):
        return float(kpi_metrics[(kpi_metrics.KPI == kpi) & (kpi_metrics.Method == method)][col].iloc[0])

    def rm(resp, model, scheme, subset, col="R2"):
        q = response_metrics[(response_metrics.Response == resp) & (response_metrics.Model == model)
                             & (response_metrics.CV_scheme == scheme) & (response_metrics.Subset == subset)]
        return float(q[col].iloc[0])

    lines = [
        "# Pump performance model -- technical report (stage 1)",
        "",
        "Generated by `src/pump_performance_model.py`; every number below is computed by "
        "the run, not typed in.",
        "",
        "## What changed from the original \"sprint7\" script",
        "- **Data bug fixed.** Raw columns were being read from wrong positions (pump *speed* "
        "was fed into the formulas as *water temperature*, and four other variables were shifted "
        "likewise). The loader now reads validated positions, range-checks every column, and the "
        f"physics layer is cross-checked against the workbook's own cached values "
        f"({n_ok}/{n_chk} derived fields reproduce them; see "
        "`results/physics_vs_workbook_crosscheck.csv`).",
        "- **Modeling target fixed.** The original pipeline \"predicted\" 31 engineering quantities, "
        "every one a deterministic formula of the same six raw measurements used as features -- "
        "there was no learning task in that. This version predicts the raw sensor responses "
        "(water temperature, inlet/outlet pressure, flow, input power) from the two variables that "
        "were actually controlled (pump speed, valve position) and derives the engineering "
        "quantities (head, efficiency, NPSH available, ...) from those predictions.",
        "- **Validation fixed.** Leave-one-speed-out, benchmarked against the textbook affinity "
        "laws (see below).",
        "- **Dropped.** t-SNE, UMAP, Sobol indices, TabPFN/PINN/tree ensembles, and the "
        "\"synthetic degradation\" remaining-useful-life estimate: none is checkable at n = 24 "
        "with three groups, and a single static bench test contains no degradation history to "
        "estimate a remaining life from.",
        "",
        "## Data",
        f"- Workbook `{workbook_name}`, sheet `{pump_sheet}`: {len(df)} operating points = "
        f"{df.Setting_pct.nunique()} pump speeds "
        f"({', '.join(str(int(s)) for s in speeds_sorted)} rpm) x "
        f"{int(len(df) / df.Setting_pct.nunique())} throttle-valve steps, no replicates.",
        "",
        "## Validation design: leave-one-speed-out (LOSO)",
        "| Held-out speed | Training speeds | Fold type |",
        "|---|---|---|",
    ]
    for s in speeds_sorted:
        train = ", ".join(str(int(t)) for t in speeds_sorted if t != s)
        lines.append(f"| {int(s)} rpm | {train} | {kinds[s]} |")
    outer = [s for s in speeds_sorted if kinds[s] == "extrapolation"]
    steps = []
    for s in outer:
        nearest = min((t for t in speeds_sorted if t != s), key=lambda t: abs(t - s))
        steps.append(f"{int(s)} rpm is {abs(s - nearest) / nearest * 100:.1f}% "
                     f"{'below' if s < nearest else 'above'} the nearest training speed")
    lines += [
        "",
        "Only the outer-speed folds are speed *extrapolation* (" + "; ".join(steps) + "). The "
        "middle-speed fold is interpolation across speed. Results are therefore reported "
        "separately for the extrapolation folds (n = "
        f"{int(km('TotalHead_m', 'GP', 'n_extrap'))}), the interpolation fold (n = "
        f"{int(km('TotalHead_m', 'GP', 'n_interp'))}), and pooled over all folds (n = "
        f"{int(km('TotalHead_m', 'GP', 'n_all'))}). R^2 is computed on each pooled subset using "
        "that subset's own mean, so it is not comparable across subsets; RMSE is.",
        "",
        "## Raw sensor responses: R^2",
        "| Response | Ridge extrap. | GP extrap. | Ridge interp. | GP interp. | Ridge LOOCV | GP LOOCV |",
        "|---|---|---|---|---|---|---|",
    ]
    for resp in RESPONSE_COLS:
        lines.append(
            f"| {resp} | {_f(rm(resp,'Ridge','LOSO','extrapolation'))} | {_f(rm(resp,'GP','LOSO','extrapolation'))} | "
            f"{_f(rm(resp,'Ridge','LOSO','interpolation'))} | {_f(rm(resp,'GP','LOSO','interpolation'))} | "
            f"{_f(rm(resp,'Ridge','LOOCV','all'))} | {_f(rm(resp,'GP','LOOCV','all'))} |")
    neg = response_metrics[(response_metrics.CV_scheme == "LOSO") & (response_metrics.R2 < 0)]
    lines += ["", "LOOCV holds out one point at a time from speeds the model has otherwise seen -- the "
              "easiest question, shown for context only."]
    if len(neg):
        cells = "; ".join(f"{r.Response} / {r.Model} / {r.Subset}: R^2 {r.R2:.2f}, RMSE {r.RMSE:.3g} vs. "
                          f"SD of the measured values {r.SD:.3g}" for r in neg.itertuples())
        lines += ["", "Negative R^2 means the error exceeds the spread of the measured values *within that "
                  f"subset*, which can happen when that spread is tiny ({cells}). Compare the RMSE with "
                  "the response's physical scale before reading it as a large error."]
    lines += [
        "",
        "## Derived engineering fields under LOSO",
        "| Field | Method | RMSE extrap. | R^2 extrap. | RMSE interp. | R^2 interp. | R^2 all folds |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, r in kpi_metrics.iterrows():
        lines.append(f"| {r.KPI} | {r.Method} | {_f(r.RMSE_extrap, 4)} | {_f(r.R2_extrap)} | "
                     f"{_f(r.RMSE_interp, 4)} | {_f(r.R2_interp)} | {_f(r.R2_all)} |")

    a_e, g_e, r_e = (km("TotalHead_m", m, "RMSE_extrap") for m in ("Affinity-law", "GP", "Ridge"))
    a_i, g_i = km("TotalHead_m", "Affinity-law", "RMSE_interp"), km("TotalHead_m", "GP", "RMSE_interp")
    lines += [
        "",
        f"**Total Head.** On the extrapolation folds the GP's RMSE is {g_e:.3f} m against "
        f"{r_e:.3f} m for Ridge and {a_e:.3f} m for the affinity-law baseline "
        f"({a_e / g_e:.1f}x the GP's error). On the interpolation fold the affinity law is "
        f"{'competitive with' if a_i <= 1.15 * g_i else 'worse than'} the GP "
        f"({a_i:.3f} m vs {g_i:.3f} m). The signed errors below show why: scaling *up* over-predicts "
        "and scaling *down* under-predicts, so speeds on both sides of the target cancel while "
        "speeds on one side compound. So the honest statement is not \"the model beats the "
        "affinity law\" but \"the affinity law is adequate between tested speeds and degrades "
        "when extrapolated, where the data-driven model holds up\".",
        "",
        "| Held-out speed | Fold type | Predicted from | Mean signed error (m) |",
        "|---|---|---|---|",
        *[f"| {int(r.held_out_speed_rpm)} rpm | {r.fold_kind} | {int(r.source_speed_rpm)} rpm | "
          f"{r.mean_signed_error_m:+.3f} |" for r in bias_table.itertuples()],
        "",
        "## Caveats (stated, not hidden)",
        "- Only three speeds were tested, so each LOSO estimate rests on very few points and "
        "the ranking of methods, not the third decimal of any R^2, is the finding.",
    ]
    weak = []
    for resp in RESPONSE_COLS:
        gp_e, rg_e = rm(resp, "GP", "LOSO", "extrapolation"), rm(resp, "Ridge", "LOSO", "extrapolation")
        if rg_e - gp_e > 0.2:
            weak.append((resp, gp_e, rg_e))
    for resp, gp_e, rg_e in weak:
        why = (" Water temperature varies only about 2 degC across the whole dataset and trends "
               "roughly linearly with speed, which a linear model extrapolates naturally and a "
               "stationary-kernel GP tends not to (a hypothesis, not tested here)."
               if resp == "T_water_C" else "")
        lines.append(f"- The GP is materially worse than plain Ridge at extrapolating `{resp}` "
                     f"(R^2 {gp_e:.2f} vs {rg_e:.2f} on the extrapolation folds).{why}")
        if resp == "T_water_C":
            eff = "; ".join(f"{r.KPI} {r.change_pct:+.1f}%" for r in tsens.itertuples())
            lines.append("  - Effect on the derived fields, checked by substituting the *measured* "
                         "temperature for the GP's prediction and recomputing extrapolation-fold "
                         f"RMSE ({eff}). Temperature enters mainly through vapour pressure, so it "
                         "matters for NPSH available and is negligible for head and efficiency.")
    lines += [
        "- The five raw responses are modeled independently; their real covariance (e.g. Pout "
        "with Q) is not captured, so the Monte-Carlo bands are a lower bound on true uncertainty.",
        "- Motor efficiency is a fixed assumed constant in the source data (Q_meff = 0.5), so "
        "`MotorEff_pct` is 50% for every row by construction; it is not a modeling result.",
        "- `HeadDev_vs1200_pct` is a cross-row quantity (each non-1200-rpm row's normalised head "
        "vs. the measured 1200-rpm head at the same sample number), reproduced exactly from the "
        "workbook's definition. An earlier revision of this script computed a self-referential "
        "stand-in that collapsed to a constant per speed; it has been removed. The general "
        "affinity-law test is the LOSO baseline above (`figures/02`).",
        "",
        "## Files produced",
        "- `results/Data_Pump_full_KPI_table.csv` -- measured inputs plus all derived fields, "
        "computed deterministically from the measurements (ground truth, not a model output)",
        "- `results/physics_vs_workbook_crosscheck.csv` -- each derived field vs. the workbook's cached column",
        "- `results/raw_response_cv_metrics.csv` -- Ridge/GP accuracy on the raw responses",
        "- `results/headline_kpi_loso_accuracy.csv`, `results/loso_per_fold_metrics.csv` -- derived-field accuracy by fold type and by held-out speed",
        "- `results/affinity_law_signed_bias.csv`, `results/t_water_sensitivity.csv` -- the checks behind two statements in this report",
        "- `figures/01`-`04` -- see the README figure guide",
    ]
    return "\n".join(lines) + "\n"


def main():
    cfg.ensure_output_dirs()
    cfg.apply_plot_style()
    workbook = cfg.resolve_workbook()
    pump_sheet = cfg.resolve_pump_sheet(workbook)
    df = load_pump_data(workbook, pump_sheet)
    crosscheck = crosscheck_against_workbook(df, workbook, pump_sheet)

    # ---- ground-truth derived table: deterministic, from the measurements ----
    truth = compute_derived(df)
    full_table = df.copy()
    for k, v in truth.items():
        full_table[k] = v
    full_table.to_csv(cfg.results_dir() / "Data_Pump_full_KPI_table.csv", index=False)
    print(f"[saved] results/Data_Pump_full_KPI_table.csv ({len(full_table)} rows x "
          f"{full_table.shape[1]} columns, all deterministic; {len(truth)} derived fields)")

    # ---- cross-validation of the raw sensor responses -------------------------
    cv_results = {}
    for response in RESPONSE_COLS:
        for model_name in MODEL_FACTORIES:
            for scheme in ("LOSO", "LOOCV"):
                cv_results[(response, model_name, scheme)] = run_cv(df, response, model_name, scheme)
    response_metrics = build_response_metrics(df, cv_results)
    response_metrics.to_csv(cfg.results_dir() / "raw_response_cv_metrics.csv", index=False)
    print("[saved] results/raw_response_cv_metrics.csv")

    speeds = df["Speed_rpm"].to_numpy(float)
    masks, kinds = loso_subsets(speeds)
    print("\nLOSO fold types:", {int(s): k for s, k in kinds.items()})

    def gather(model_name):
        mean = {r: cv_results[(r, model_name, "LOSO")].y_pred for r in RESPONSE_COLS}
        std = {r: cv_results[(r, model_name, "LOSO")].y_std for r in RESPONSE_COLS}
        return mean, std

    gp_mean, gp_std = gather("GP")
    ridge_mean, _ = gather("Ridge")
    gp_kpi = kpi_point_predictions(df, gp_mean)
    ridge_kpi = kpi_point_predictions(df, ridge_mean)

    print(f"[compute] Monte-Carlo propagating GP LOSO uncertainty through the physics layer "
          f"({N_MC_SAMPLES} draws/row)...")
    gp_mc = propagate_to_kpis(df, gp_mean, gp_std)
    gp_p05 = {k: np.percentile(v, 5, axis=1) for k, v in gp_mc.items()}
    gp_p95 = {k: np.percentile(v, 95, axis=1) for k, v in gp_mc.items()}

    # ---- textbook affinity-law baseline, same folds ---------------------------
    affinity_Ht = np.zeros(len(df))
    for held in sorted(df["Speed_rpm"].unique()):
        test_mask = speeds == held
        affinity_Ht[test_mask] = affinity_law_baseline(df, ~test_mask, test_mask)["TotalHead_m"]
    affinity_by_speed = {}
    for sp in sorted(df["Speed_rpm"].unique()):
        m = (df["Speed_rpm"] == sp).to_numpy()
        order = np.argsort(df.loc[m, "valve_frac"].to_numpy())
        affinity_by_speed[sp] = {"valve_frac": df.loc[m, "valve_frac"].to_numpy()[order],
                                 "TotalHead_m": affinity_Ht[m][order]}

    # ---- headline derived-field accuracy --------------------------------------
    method_preds = {("TotalHead_m", "Affinity-law"): affinity_Ht,
                    ("TotalHead_m", "Ridge"): ridge_kpi["TotalHead_m"],
                    ("TotalHead_m", "GP"): gp_kpi["TotalHead_m"]}
    for kpi in ("PumpEff_pct", "NPSHa_m"):
        method_preds[(kpi, "Ridge")] = ridge_kpi[kpi]
        method_preds[(kpi, "GP")] = gp_kpi[kpi]
    kpi_metrics, per_fold = build_kpi_metrics(truth, method_preds, speeds)
    kpi_metrics.to_csv(cfg.results_dir() / "headline_kpi_loso_accuracy.csv", index=False)
    per_fold.to_csv(cfg.results_dir() / "loso_per_fold_metrics.csv", index=False)
    print("[saved] results/headline_kpi_loso_accuracy.csv, results/loso_per_fold_metrics.csv")
    show = ["KPI", "Method", "RMSE_extrap", "R2_extrap", "RMSE_interp", "R2_all"]
    print("\n--- Derived fields under LOSO (extrap = 1050 & 1350 rpm folds; interp = 1200 rpm fold) ---")
    print(kpi_metrics[show].round(4).to_string(index=False))

    # ---- figures ---------------------------------------------------------------
    fdir = cfg.figures_dir()
    plot_pump_curves_loso(
        df, {"TotalHead_m": gp_kpi["TotalHead_m"], "TotalHead_m_p05": gp_p05["TotalHead_m"],
             "TotalHead_m_p95": gp_p95["TotalHead_m"]},
        {"TotalHead_m": ridge_kpi["TotalHead_m"]}, affinity_by_speed, truth, kinds,
        fdir / "01_pump_curves_loso.png")
    plot_affinity_validation(df, truth, affinity_Ht, kinds, fdir / "02_affinity_law_validation.png")
    plot_raw_response_fits(df, {r: cv_results[(r, "GP", "LOOCV")] for r in RESPONSE_COLS},
                            fdir / "03_raw_response_fits.png")
    scores = []
    for (kpi, method), pred in method_preds.items():
        row = kpi_metrics[(kpi_metrics.KPI == kpi) & (kpi_metrics.Method == method)].iloc[0]
        scores.append({"KPI": kpi, "Method": method, "y_true": truth[kpi], "y_pred": pred,
                       "speed": speeds, "R2_all": row.R2_all, "R2_extrap": row.R2_extrap})
    plot_loso_parity(pd.DataFrame(scores), kinds, fdir / "04_loso_parity.png")
    print("[saved] figures/01-04")

    bias_table = affinity_single_source_bias(df, truth)
    bias_table.to_csv(cfg.results_dir() / "affinity_law_signed_bias.csv", index=False)
    tsens = t_water_sensitivity(df, truth, gp_mean)
    tsens.to_csv(cfg.results_dir() / "t_water_sensitivity.csv", index=False)

    (cfg.docs_dir() / "FINAL_report.md").write_text(
        render_report(df, workbook.name, pump_sheet, crosscheck, response_metrics, kpi_metrics,
                      kinds, bias_table, tsens), encoding="utf-8")
    print("[saved] docs/FINAL_report.md")

    cfg.update_run_metadata(
        python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__,
        scikit_learn=sklearn.__version__, matplotlib=matplotlib.__version__,
        workbook_file=workbook.name, pump_sheet=pump_sheet, n_rows=len(df),
        speeds_rpm=sorted(df.Speed_rpm.unique().tolist()), random_seed=RANDOM_SEED,
        n_mc_samples=N_MC_SAMPLES, n_derived_fields=len(truth))
    print("\n" + "=" * 64 + "\n  STAGE 1 DONE\n" + "=" * 64)


if __name__ == "__main__":
    main()
