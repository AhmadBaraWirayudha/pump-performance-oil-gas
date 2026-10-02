# Pump & jet-nozzle performance modeling for oil & gas service

Predicts a centrifugal pump's measured behaviour from 24 bench-test points, validates it on pump
speeds it never saw, and uses the result to screen how the same pump would behave on crude oil
(theoretically). A second sheet of the same lab workbook -- a free-jet nozzle traverse -- is
connected to hydraulic jet pumps, an oilfield artificial-lift technology, after two formula
problems in it were found and fixed.

Built as a portfolio project for entry-level oil & gas roles. It started as an inherited pipeline
whose accuracy numbers could not fail; the interesting part is what changed to make them mean
something (see [How this came about](#how-this-came-about)).

## Headline results

| | |
|---|---|
| **Unseen-speed accuracy** | Held-out-speed validation on the two genuinely *extrapolated* speeds (1050 and 1350 rpm, each 12.5% from the nearest training speed): a Gaussian-process model predicts Total Head with **RMSE 0.23 m (R² 0.92)**, against **0.60 m (R² 0.43)** for the textbook pump affinity laws. Pump efficiency R² 0.99, NPSH available R² 0.95. |
| **Where the affinity law is fine** | At the *interpolated* speed (1200 rpm, between two tested speeds) the affinity law is as good as the model (0.125 m vs 0.127 m). Scaling up over-predicts head and scaling down under-predicts it, so errors cancel when speeds bracket the target and compound when they don't. |
| **Data fix** | The original loader fed pump *speed* into the formulas as *water temperature* (a silent column shift). Raw columns are now range-checked, and the physics layer reproduces **all 30** workbook columns that have a counterpart to machine precision. |
| **Crude oil (theoretical)** | ANSI/HI 9.6.7 screening at 40 °C gives pump-efficiency factors of **0.73** (Minas-grade, 35° API), **0.56** (27° API) and **0.35** (Duri-grade, 20.8° API) relative to the water curve. Inside Beggs-Robinson's valid range the heavy grade leaves the method's B < 40 limit below **about 24.8 °C**. |
| **Jet sheet** | Two formula problems found in the workbook revision examined (an "area" that was a length; a density labelled *air* on a water jet). The original momentum-flux column was **6.7× to 289× too small** away from the centerline, with the wrong radial shape. |

**How far to trust this.** Three tested speeds means small folds (n = 16 and n = 8): read the
*ranking* of methods, not the third decimal. The crude-oil work is a screening-level application of
a named industry method to a small bench pump, not a validated prediction. Limitations are listed
[below](#limitations).

## Quick start

```bash
git clone <this repo> && cd pump-performance-oil-gas
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# put the lab workbook in data/ (see data/README.md), then:
python src/run_all.py                                     # ~30 s; writes results/, figures/, docs/
```

Options: `--workbook path/to.xlsx` (or the `DATA_PUMP_XLSX` environment variable), `--only pump oil_gas jet`
for a subset of stages, `DATA_PUMP_OUTPUT_DIR` to write somewhere other than the repo. Each stage
also runs on its own, e.g. `python src/pump_performance_model.py`.

The workbook is not redistributed; `data/README.md` describes the expected layout, how sheets are
identified, and what the loaders deliberately refuse. Tested on Python 3.11 and 3.12 (see
`requirements.txt`).

## What it does

| Stage | Script | Question | Writes |
|---|---|---|---|
| 1 | `src/pump_performance_model.py` | Can the pump's sensor responses be predicted at a speed that was never tested, and does that beat the affinity law? | `figures/01-04`, `docs/FINAL_report.md`, `results/` (KPI table, cross-check, CV metrics) |
| 2 | `src/oil_gas_extension.py` | What would the water curve look like on crude oil, and where does the standard's method stop applying? | `figures/05-06`, `docs/oil_gas_notes.md` |
| 3 | `src/jet_nozzle_extension.py` | What does the jet-nozzle sheet really say once its formulas are recomputed from the raw measurements? | `figures/07-09`, `docs/jet_nozzle_notes.md` |

Shared plumbing (paths, sheet resolution, plot style) lives in `src/project_config.py`.

**The modeling task.** The experimenter controlled two things: pump speed (three levels) and
throttle-valve position (eight steps per speed). The model predicts the five *measured* responses
(water temperature, inlet and outlet pressure, flow, electrical input power) from those two
variables, using Ridge regression and a Gaussian process. Head, efficiency, NPSH and the other
engineering quantities are then derived from the predictions by deterministic hydraulics formulas,
with prediction uncertainty carried through by Monte-Carlo propagation.

**Why validation is leave-one-speed-out.** Train on two speeds, predict the third:

| Held-out speed | Training speeds | Fold type |
|---|---|---|
| 1050 rpm | 1200, 1350 | extrapolation (below the training range) |
| 1200 rpm | 1050, 1350 | interpolation (between training speeds) |
| 1350 rpm | 1050, 1200 | extrapolation (above the training range) |

Results are reported for the extrapolation folds, the interpolation fold, and all folds pooled; the
code derives the fold type from the speeds rather than hard-coding it.

## Figures

| | |
|---|---|
| `figures/01_pump_curves_loso.png` | Pump curve rebuilt at each held-out speed: measured vs GP (with 90% band) vs Ridge vs affinity law |
| `figures/02_affinity_law_validation.png` | Do the three measured curves collapse onto one when scaled to 1200 rpm? Affinity-law error by valve position |
| `figures/03_raw_response_fits.png` | GP fit to each raw sensor response (one point held out at a time) |
| `figures/04_loso_parity.png` | Predicted vs measured Total Head, efficiency and NPSH; filled = extrapolation folds, hollow = interpolation fold |
| `figures/05_crude_oil_water_derating.png` | Head-flow and efficiency curves for water and three crude grades after viscosity correction |
| `figures/06_viscosity_temperature_sensitivity.png` | Viscosity and parameter B vs temperature; shaded = outside the viscosity correlation's validity range |
| `figures/07_jet_velocity_profiles.png` | Radial jet velocity at three distances from the nozzle |
| `figures/08_bug_impact_before_after.png` | Original vs corrected momentum flux, and the size of the original error |
| `figures/09_jet_fluid_comparison.png` | First-order density scaling of jet momentum flux across fluids |

## Repository layout

```
├── src/            project_config.py, pump_performance_model.py, oil_gas_extension.py,
│                   jet_nozzle_extension.py, run_all.py
├── tests/          pytest suite (synthetic workbooks only)
├── data/           README only; put your workbook here (git-ignored)
├── results/        CSVs the stages write (KPI table, cross-check, metrics, crossings, ...)
├── figures/        the nine figures above
├── docs/           FINAL_report.md, oil_gas_notes.md, jet_nozzle_notes.md (generated; every
│                   number is computed by the run) and PORTFOLIO_WRITE_UP.md (process + interview notes)
├── requirements.txt, requirements-dev.txt, pytest.ini, LICENSE
```

## Limitations

- **Small sample.** 24 points, three speeds. LOSO folds hold 16 and 8 points; R² is computed on
  each pooled subset with that subset's own mean, so only RMSE is comparable across subsets.
- **Independent responses.** The five responses are modeled separately; their real covariance is
  not captured, so the Monte-Carlo bands are a lower bound on true uncertainty.
- **Weak spot, kept visible.** The GP extrapolates water temperature worse than plain Ridge
  (R² 0.25 vs 0.88 on the extrapolation folds). Substituting the measured temperature changes
  extrapolation RMSE by ~0% for head and efficiency but by about -17% for NPSH available.
- **Crude-oil results are theoretical.** The rig only pumped water. The ANSI/HI 9.6.7 equations are
  used as reproduced in secondary sources (the standard is paywalled); its own flow/head bounds and
  this pump's design type could not be verified; plain water already gives B = 1.76 at this pump's
  size, a sign the method is being applied at the small end of its intended scale.
- **Reference point.** "BEP" is the model-derived best-efficiency point *within the tested range*;
  efficiency is still rising at the fully-open end, so the true BEP is probably at higher flow. The
  heavy-grade crossing temperature moves from 24.8 °C to 21.9 °C if Q_BEP is doubled (still inside
  the viscosity correlation's range).
- **Viscosity correlation.** Beggs-Robinson is a generic dead-oil correlation, valid for 16-58° API
  and 21.1-146.1 °C. Real crudes such as Duri deviate from generic correlations; use field data in
  practice. The 27° API grade's B = 40 crossing (15.6 °C) lies *below* the valid range and is not
  reported as a result.
- **Jet stage is nozzle-only and first-order.** No throat or diffuser data, so it is not a jet-pump
  model; fluid comparisons are density scaling under an ideal (inviscid) nozzle. The 20 °C water
  temperature behind the corrected density is an assumption (none was logged).
- **Not modeled:** crude vapour pressure (so no NPSH or cavitation analysis in crude service),
  salinity effects on produced-water viscosity, wax/pour-point behaviour, non-Newtonian flow.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

37 tests, about 3 seconds, no private data needed. They cover sheet resolution under renamed and
reordered layouts, the guardrails (a one-column shift, missing or wrong cached values, a malformed jet
sheet must all be refused), the extrapolation/interpolation fold logic, and hand-checkable parts of
the fluid-property and viscosity-correction code. The suite was mutation-checked: re-introducing
each historical bug in a scratch copy makes it fail.

## How this came about

The project began from an inherited, ~5,000-line pipeline that reported near-perfect R² on 31
"predicted" quantities. Two root problems turned up: a silent column mismapping (pump speed read as
water temperature), and targets that were deterministic formulas of the features, so no model could
fail. Rebuilding it, then extending it to crude oil and the jet sheet, was run as a sequence of
sprints; [`docs/PORTFOLIO_WRITE_UP.md`](docs/PORTFOLIO_WRITE_UP.md) has the backlog, sprint reviews and
retrospectives (including the errors found in earlier drafts of this very write-up), and interview-style
STAR accounts of the main episodes.

## License

MIT -- see [LICENSE](LICENSE).
