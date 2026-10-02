"""
project_config.py -- paths, workbook/sheet resolution and plot style shared by
all three stages (pump model, oil & gas extension, jet-nozzle extension).

Why this exists
---------------
An earlier revision hard-coded sheet names ("Pump", "Jet Steam Fluid
Mechanics Pract"). When the workbook's sheets were renamed, the loaders
either failed outright or -- worse -- silently fell back to the *first*
sheet (the jet sheet) and stopped only because a row-count guard happened to
trip. Sheets are now resolved by name *and validated by content*, and the
resolved name is printed on every run, so a renamed or reordered sheet
cannot be picked up by accident.

Nothing here touches the filesystem at import time (no mkdir, no rcParams);
directories are created and the plot style applied only when a stage's
main() asks for them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import matplotlib
import pandas as pd

SRC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SRC_DIR.parent

# Preferred sheet names in the current workbook, plus older names that earlier
# revisions of the workbook used (accepted as fallbacks).
PUMP_SHEET = "Pump Machine Performance"
JET_SHEET = "Jet Steam Fluid Mechanics"
PUMP_SHEET_LEGACY = ("Pump",)
JET_SHEET_LEGACY = ("Jet Steam Fluid Mechanics Pract",)

PUMP_MIN_COLUMNS = 40  # the pump sheet is a 48-column layout
JET_REQUIRED_HEADERS = ("radiusmm", "headdifferencehm")  # normalised header names


def norm(text) -> str:
    """Lower-case and strip everything except a-z0-9 (header/sheet matching)."""
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


# --------------------------------------------------------------------------
# Output locations (all under one root: repo root by default)
# --------------------------------------------------------------------------
def output_root() -> Path:
    return Path(os.getenv("DATA_PUMP_OUTPUT_DIR", str(REPO_ROOT)))


def results_dir() -> Path:
    return output_root() / "results"


def figures_dir() -> Path:
    return output_root() / "figures"


def docs_dir() -> Path:
    return output_root() / "docs"


def ensure_output_dirs() -> None:
    for d in (results_dir(), figures_dir(), docs_dir()):
        d.mkdir(parents=True, exist_ok=True)


def update_run_metadata(**items) -> None:
    """Merge key/values into results/run_metadata.json (package versions,
    workbook file name, resolved sheets ...) so a run can be reproduced."""
    ensure_output_dirs()
    path = results_dir() / "run_metadata.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update(items)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def apply_plot_style() -> None:
    matplotlib.rcParams.update({
        "figure.dpi": 110,
        "savefig.dpi": 160,
        "font.size": 10.5,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.grid": True,
        "grid.alpha": 0.25,
        "figure.constrained_layout.use": True,
    })


# --------------------------------------------------------------------------
# Workbook + sheet resolution
# --------------------------------------------------------------------------
def resolve_workbook(explicit: str | os.PathLike | None = None) -> Path:
    """Find the source workbook.

    Order: explicit argument -> DATA_PUMP_XLSX env var -> the single .xlsx in
    data/ (or the single one whose name contains "model" if there are several).
    """
    candidate = explicit or os.getenv("DATA_PUMP_XLSX")
    if candidate:
        p = Path(candidate)
        if not p.is_file():
            raise FileNotFoundError(f"Workbook not found: {p}")
        return p

    data_dir = REPO_ROOT / "data"
    found = sorted(p for p in data_dir.glob("*.xlsx") if not p.name.startswith("~$"))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        model_like = [p for p in found if "model" in p.name.lower()]
        if len(model_like) == 1:
            return model_like[0]
        raise FileNotFoundError(
            f"Several workbooks in {data_dir}: {[p.name for p in found]}. "
            f"Set DATA_PUMP_XLSX to the one to use."
        )
    raise FileNotFoundError(
        f"No workbook found. Put your .xlsx in {data_dir} or set DATA_PUMP_XLSX "
        f"to its path (see data/README.md for the expected sheets)."
    )


def _sheet_names(path: Path) -> list[str]:
    with pd.ExcelFile(path) as xl:
        return list(xl.sheet_names)


def _looks_like_pump(path: Path, sheet: str) -> bool:
    head = pd.read_excel(path, sheet_name=sheet, header=0, nrows=1)
    return head.shape[1] >= PUMP_MIN_COLUMNS


def _looks_like_jet(path: Path, sheet: str) -> bool:
    head = pd.read_excel(path, sheet_name=sheet, header=0, nrows=1)
    have = {norm(c) for c in head.columns}
    return all(h in have for h in JET_REQUIRED_HEADERS)


def _resolve(path: Path, preferred: str, legacy: tuple, keyword: str, validate) -> str:
    names = _sheet_names(path)
    wanted = {norm(n) for n in (preferred, *legacy)}
    ordered: list[str] = []
    for n in names:                                   # exact / case- and spacing-insensitive
        if norm(n) in wanted and n not in ordered:
            ordered.append(n)
    for n in names:                                   # any sheet whose name contains the keyword
        if keyword in n.lower() and n not in ordered:
            ordered.append(n)
    for n in ordered:
        if validate(path, n):
            return n
    if len(names) == 1 and validate(path, names[0]):  # single-sheet workbook, any name
        return names[0]
    by_content = [n for n in names if validate(path, n)]  # sheet renamed to something unexpected
    if len(by_content) == 1:
        print(f"[note] no sheet named like {preferred!r}; using {by_content[0]!r}, the only sheet "
              f"whose contents match the expected {keyword!r} layout")
        return by_content[0]
    raise ValueError(
        f"Could not find the {keyword!r} sheet in {path.name}. Sheets present: {names}. "
        f"Expected '{preferred}' (or a legacy name {list(legacy)}), and its contents must "
        f"pass the layout check for that sheet."
    )


def resolve_pump_sheet(path: Path) -> str:
    return _resolve(Path(path), PUMP_SHEET, PUMP_SHEET_LEGACY, "pump", _looks_like_pump)


def resolve_jet_sheet(path: Path) -> str:
    return _resolve(Path(path), JET_SHEET, JET_SHEET_LEGACY, "jet", _looks_like_jet)
