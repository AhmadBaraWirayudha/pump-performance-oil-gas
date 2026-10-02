"""The loaders must find the right sheets under any plausible renaming, and must
REFUSE (not silently mis-read) a shifted or unverifiable workbook."""
import numpy as np
import pandas as pd
import pytest

import jet_nozzle_extension as jet
import project_config as cfg
import pump_performance_model as base
from helpers import synthetic_jet_sheet, synthetic_pump_sheet, synthetic_raw, write_workbook

LAYOUTS = {
    "current names":           ("Jet Steam Fluid Mechanics", "Pump Machine Performance"),
    "legacy names":            ("Jet Steam Fluid Mechanics Pract", "Pump"),
    "case and spacing":        ("JET  steam fluid mechanics", "pump machine  performance"),
    "keyword only":            ("Jet test", "Pump data"),
    "unrecognisable names":    ("Sheet2", "Sheet1"),
}


@pytest.mark.parametrize("label", LAYOUTS)
@pytest.mark.parametrize("pump_first", [False, True])
def test_sheets_are_found_and_data_is_identical(tmp_path, label, pump_first):
    jet_name, pump_name = LAYOUTS[label]
    sheets = [(jet_name, synthetic_jet_sheet()), (pump_name, synthetic_pump_sheet())]
    path = tmp_path / "wb.xlsx"
    write_workbook(path, sheets[::-1] if pump_first else sheets)

    assert cfg.resolve_pump_sheet(path) == pump_name
    assert cfg.resolve_jet_sheet(path) == jet_name
    pump_df = base.load_pump_data(path)
    expected = synthetic_raw()
    for col in base.RAW_COLUMNS:
        assert np.allclose(pump_df[col], expected[col])
    jet_df = jet.load_jet_sheet(path)
    assert len(jet_df) == 23                       # the two parameter rows are not data
    assert jet_df["Radial_Increment_m"].iloc[0] == pytest.approx(0.005)
    assert (jet_df["Test_Id"] == np.arange(1, 24)).all()


def test_cross_check_passes_on_consistent_workbook(tmp_path):
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Pump Machine Performance", synthetic_pump_sheet())])
    table = base.crosscheck_against_workbook(base.load_pump_data(path), path,
                                             cfg.resolve_pump_sheet(path))
    assert (table.status == "match").all()


def test_interior_blank_column_does_not_shift_positions(tmp_path):
    # An all-empty column inside the sheet must not move later columns (an earlier
    # revision dropped empty columns, silently shifting every position after it).
    sheet = synthetic_pump_sheet()
    sheet.iloc[:, 12] = np.nan
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Pump Machine Performance", sheet)])
    df = base.load_pump_data(path)
    assert np.allclose(df["P_input_W"], synthetic_raw()["P_input_W"])


def test_values_shifted_one_column_left_are_refused(tmp_path):
    # The original bug: every raw variable read from the neighbouring column.
    sheet = synthetic_pump_sheet()
    shifted = sheet.iloc[:, 1:].copy()
    shifted["tail"] = 0.0
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Pump", shifted)])
    with pytest.raises(ValueError, match="validation failed"):
        base.load_pump_data(path)


def test_inserted_blank_column_is_refused(tmp_path):
    sheet = synthetic_pump_sheet()
    sheet.insert(1, "blank", np.nan)
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Pump", sheet)])
    with pytest.raises(ValueError, match="validation failed"):
        base.load_pump_data(path)


def test_missing_cached_total_head_is_refused(tmp_path):
    sheet = synthetic_pump_sheet()
    sheet.iloc[:, 13] = np.nan
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Pump", sheet)])
    with pytest.raises(ValueError, match="no readable cached values"):
        base.crosscheck_against_workbook(base.load_pump_data(path), path, "Pump")


def test_wrong_cached_total_head_is_refused(tmp_path):
    sheet = synthetic_pump_sheet()
    sheet.iloc[:, 13] = sheet.iloc[:, 13] * 1.05
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Pump", sheet)])
    with pytest.raises(AssertionError, match="does not reproduce"):
        base.crosscheck_against_workbook(base.load_pump_data(path), path, "Pump")


def test_unrecognisable_workbook_is_refused(tmp_path):
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("A", pd.DataFrame({"x": [1, 2]})), ("B", pd.DataFrame({"y": [3]}))])
    with pytest.raises(ValueError, match="Could not find"):
        cfg.resolve_pump_sheet(path)


def test_jet_sheet_missing_head_column_is_refused(tmp_path):
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Jet", synthetic_jet_sheet().rename(columns={"Head_Difference_h_m": "h"}))])
    with pytest.raises(ValueError, match="missing expected columns"):
        jet.load_jet_sheet(path, "Jet")


def test_jet_non_uniform_radii_are_refused(tmp_path):
    sheet = synthetic_jet_sheet(with_parameter_rows=False)
    sheet.loc[2, "Radius_mm"] = 7.0
    path = tmp_path / "wb.xlsx"
    write_workbook(path, [("Jet", sheet)])
    with pytest.raises(ValueError, match="not uniformly spaced"):
        jet.load_jet_sheet(path, "Jet")


def test_workbook_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_PUMP_XLSX", raising=False)
    monkeypatch.setattr(cfg, "REPO_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    with pytest.raises(FileNotFoundError, match="No workbook found"):
        cfg.resolve_workbook()
    only = tmp_path / "data" / "whatever name (2).xlsx"
    only.write_bytes(b"x")
    assert cfg.resolve_workbook() == only                       # single workbook: use it
    (tmp_path / "data" / "other.xlsx").write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match="Several workbooks"):
        cfg.resolve_workbook()
    explicit = tmp_path / "explicit.xlsx"
    explicit.write_bytes(b"x")
    monkeypatch.setenv("DATA_PUMP_XLSX", str(explicit))
    assert cfg.resolve_workbook() == explicit                   # env var wins
