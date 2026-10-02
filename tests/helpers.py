"""Synthetic stand-ins for the lab workbook, in the layouts the loaders must accept."""
from __future__ import annotations

import numpy as np
import pandas as pd

import pump_performance_model as base

# Header text at the validated raw-column positions (others get a placeholder name).
PUMP_HEADERS = {0: "Sample Number", 1: "Pump Setting S [%]", 2: "Pump Speed n [rpm]",
                3: "Water Temperature T [\u00b0C]", 4: "Inlet Pressure Pin [kPa]",
                5: "Outlet Pressure Pout [kPa]", 6: "Flow Rate Q [l/s]", 15: "P in (W)"}


def synthetic_raw() -> pd.DataFrame:
    """24 operating points: 3 speeds x 8 valve steps, smooth, physically plausible numbers."""
    rows = []
    for setting in (70, 80, 90):
        speed = setting * 15
        s = speed / 1350.0
        for k in range(1, 9):
            vf = (k - 1) / 7.0
            rows.append({
                "SampleNo": k, "Setting_pct": float(setting), "Speed_rpm": float(speed),
                "T_water_C": 28 + 1.8 * (speed - 1050) / 300 - 0.5 * vf,
                "Pin_kPa": 2.4 - 4.6 * vf, "Pout_kPa": 12 + 38 * s**2 - 22 * vf,
                "Q_lps": 1.2 * s * vf**1.1,
                "P_input_W": 142 + 8 * (speed - 1050) / 300 + 45 * vf,
            })
    return pd.DataFrame(rows)


def synthetic_pump_sheet() -> pd.DataFrame:
    """A 48-column pump sheet whose cached derived columns are consistent with physics()."""
    raw = synthetic_raw()
    derived = base.compute_derived(raw)
    sheet = pd.DataFrame({PUMP_HEADERS.get(i, f"col_{i}"): np.zeros(len(raw)) for i in range(48)})
    cols = list(sheet.columns)
    for name, pos in base.RAW_COLUMNS.items():
        sheet[cols[pos]] = raw[name].to_numpy()
    for key, pos in base.WORKBOOK_KPI_COLUMNS.items():
        sheet[cols[pos]] = np.asarray(derived[key], dtype=float)
    return sheet


def synthetic_jet_sheet(with_parameter_rows: bool = True) -> pd.DataFrame:
    rows, test_id = [], 1
    for dist, rmax in ((115, 25), (250, 35), (310, 40)):
        hmax = {115: 0.05, 250: 0.02, 310: 0.014}[dist]
        for r in range(0, rmax + 1, 5):
            rows.append({"Test_Id": test_id, "Radius_mm": float(r),
                         "Nozzle_to_Target_Distance_mm": float(dist),
                         "Head_Difference_h_m": round(hmax * (1 - (r / rmax) ** 2), 6)})
            test_id += 1
    sheet = pd.DataFrame(rows)
    if with_parameter_rows:  # labels / constants below the data, as in the revised workbook
        extra = pd.DataFrame([{"Test_Id": "Gravity_m_s2", "Radius_mm": 9.80665},
                              {"Test_Id": "Air_Density_kg_m3", "Radius_mm": 1.2}])
        sheet = pd.concat([sheet, extra], ignore_index=True)
    return sheet


def write_workbook(path, sheets) -> None:
    """sheets: list of (sheet_name, DataFrame), in workbook order."""
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, frame in sheets:
            frame.to_excel(xw, sheet_name=name, index=False)
