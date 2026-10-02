# Data

The source lab workbook is **not included** in this repository (its redistribution rights are the
author's / institution's to decide), and `.gitignore` keeps `data/*.xlsx` out of version control.
Everything the scripts produce is in [`../results/`](../results/) and [`../figures/`](../figures/).

> `results/Data_Pump_full_KPI_table.csv` contains the 24 raw pump measurements next to the derived
> values. If you are not sure you may publish those measurements, remove that one file before pushing.

## To reproduce the results

Put your workbook in this folder (any file name), or point at it explicitly:

```bash
python src/run_all.py --workbook "path/to/your workbook.xlsx"
# or:  DATA_PUMP_XLSX="path/to/your workbook.xlsx" python src/run_all.py
```

If `data/` holds exactly one `.xlsx`, it is picked up automatically.

## Expected layout

Sheets are found by **name and by content**, and the resolved names are printed on every run, so a
renamed or reordered sheet cannot be picked up by accident.

| Sheet | Expected name (older names also accepted) | Layout |
|---|---|---|
| Pump | `Pump Machine Performance` (or `Pump`; any name containing "pump"; else the only sheet with 40+ columns) | 24 data rows, 48 columns. Raw inputs at fixed 0-based positions: 0 sample no., 1 setting %, 2 speed rpm, 3 water temp. C, 4 inlet pressure kPa, 5 outlet pressure kPa, 6 flow l/s, 15 electrical input W. Derived columns must carry **cached values** (open and re-save the file in Excel if they are blank). |
| Jet | `Jet Steam Fluid Mechanics` (or `... Pract`; any name containing "jet"; else the only sheet with the required headers) | Headers `Test_Id`, `Radius_mm`, `Nozzle_to_Target_Distance_mm`, `Head_Difference_h_m`. Rows where all four are numeric are the experiment; label or parameter rows beneath are excluded and listed. Radii must be uniformly spaced within each distance. |

## What the loaders refuse (on purpose)

They stop with an explanatory error, instead of continuing with wrong numbers, when: a raw column is
outside a physically plausible range or non-numeric (the symptom of a shifted column); the pump
sheet's own cached Total Head / Pump Efficiency cannot be reproduced from the raw inputs (or are
missing); a required jet header is absent; or no sheet can be identified. See `tests/`.
