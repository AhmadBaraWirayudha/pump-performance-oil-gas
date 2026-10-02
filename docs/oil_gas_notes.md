# Crude oil / produced water viscosity screening

**Status: theoretical.** The bench only pumped water; nothing here is a crude-oil measurement. It applies the ANSI/HI 9.6.7 parameter-B method, with the Beggs-Robinson dead-oil viscosity correlation, to the stage-1 water curve at 1200 rpm.

## Reference point
Model-derived best-efficiency point within the tested range (max of the GP-fitted efficiency curve): Q = 1.006 l/s (3.62 m3/h), H = 3.306 m, efficiency = 34.5%. This maximum sits at the fully-open end of the tested valve range with efficiency still rising, so the true BEP probably lies at higher flow than was tested. B depends on Q_BEP with exponent -0.375, so the effect on the conclusions is bounded; see the sensitivity columns in the crossing table below.

## Results at the scenario temperatures
| Fluid | API | T (degC) | rho (kg/m3) | mu (cP) | nu (cSt) | B | Regime | CQ | C_eta | Beggs-Robinson in range |
|---|---|---|---|---|---|---|---|---|---|---|
| Water (tested) | - | 25 | 997.0 | 0.89 | 0.89 | 1.76 | water-like: tested curve used as-is (B is an applicability indicator only) | 1.000 | 1.000 | n/a (water) |
| Produced water (+3% density) | - | 25 | 1026.9 | 0.89 | 0.87 | 1.74 | water-like: tested curve used as-is (B is an applicability indicator only) | 1.000 | 1.000 | n/a (water) |
| Light crude - Minas 35 API | 35.0 | 40 | 834.5 | 7.95 | 9.53 | 5.75 | 1<B<40: corrected | 0.933 | 0.726 | yes |
| Medium crude - 27 API | 27.0 | 40 | 876.6 | 23.08 | 26.32 | 9.56 | 1<B<40: corrected | 0.857 | 0.556 | yes |
| Heavy crude - Duri 20.8 API | 20.8 | 40 | 912.3 | 68.85 | 75.47 | 16.19 | 1<B<40: corrected | 0.741 | 0.353 | yes |

## Applicability checks (computed)
* **Plain water gives B = 1.76** at this pump's model-derived BEP -- above the B<=1 'no correction' threshold. The method's own factors for water would then be CQ = 0.998 and C_eta = 0.955 instead of 1. That is a symptom of applying a method built for industrial pumps to a 3.6 m3/h bench pump. The crude C_eta values above follow the standard's equations; dividing by the method's own water value (column `Ceta_rel_to_method_water` in the CSV) makes the efficiency losses 1.7-3.4 percentage points smaller for these grades.
* Specific speed at the model BEP: 802 (US units) -- under the documented limit of 3000.
* Kinematic viscosity of the corrected (crude) scenarios lies inside the documented 1-4000 cSt range: yes. Plain water is 0.89 cSt, just under the 1 cSt lower end -- consistent with it being the base curve rather than a corrected case.
* Beggs-Robinson validity: API 16-58 and 21.1-146.1 degC. All three scenario crudes at 40 degC are inside.
* **Not verifiable here:** the standard's own flow/head bounds (it is paywalled and was not consulted), and whether this small bench pump is a radial-flow centrifugal type. The extension is therefore an illustration of the method, not a validated prediction.

## Where each grade leaves the method's B<40 range
| Fluid | B at 21.1 degC | T where B=40 | ...if Q_BEP is 1.25x | 1.5x | 2x | Assessment |
|---|---|---|---|---|---|---|
| Light crude - Minas 35 API | 11.3 | - | - | - | - | B stays below 40 across 15-80 degC |
| Medium crude - 27 API | 24.5 | 15.6 | - | - | - | B reaches 40 at 15.6 degC, BELOW the Beggs-Robinson range (< 21.1 degC): extrapolated, not a finding |
| Heavy crude - Duri 20.8 API | 56.0 | 24.8 | 23.8 | 23.1 | 21.9 | B reaches 40 at 24.8 degC, INSIDE the Beggs-Robinson range (>= 21.1 degC): a valid-range screening flag |

Reading it: a crossing below 21.1 degC lies outside Beggs-Robinson's published temperature range, so it is drawn as extrapolation in figure 06 and is not reported as a finding. A crossing inside the range is a screening flag -- at that temperature the standard's simple correction should not be applied to that grade -- and it still rests on a generic correlation (real crudes such as Duri are known to deviate from generic API-based correlations; use the field's own viscosity-temperature data in practice) and on the BEP proxy, whose effect is bounded by the sensitivity columns.

## Limitations
* One representative thermal-expansion coefficient stands in for the API MPMS Ch. 11.1 / ASTM D1250 tables.
* Dead-oil viscosity only (no dissolved gas); Newtonian behaviour assumed; wax and pour-point effects ignored.
* Produced water is treated as water-like (tested curve used as-is). Salinity raises viscosity somewhat; because B scales with the square root of viscosity the effect on B is smaller still. Not modeled.
* No crude vapour-pressure model, so nothing here addresses NPSH or cavitation margin in crude service.
