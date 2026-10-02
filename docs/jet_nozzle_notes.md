# Stage 3 notes -- jet nozzle characterization and the oil & gas connection

## The connection
A hydraulic jet pump -- a proven oil & gas artificial-lift technology in use since the 1930s -- is a nozzle, a throat and a diffuser: high-pressure power fluid (water- or oil-based) accelerates through the nozzle, the low-pressure region it creates entrains produced fluid at the throat (Venturi effect), and the diffuser turns the mixed stream's velocity back into pressure. The nozzle-to-throat area ratio is the primary design parameter (source: SPE JPT, *Jet Pumps: An Efficient Technology for Production Enhancement of Mature Oil Fields*, 2021). This bench data characterises only the nozzle stage -- radial velocity and momentum/energy flux of a free jet at three downstream distances -- with the same physics. It has no throat or diffuser, so it is not a jet-pump performance model.

## Two formula problems in the workbook revision examined
Found by recomputing every derived quantity from the raw radius / distance / head measurements. The corrected values are recomputed from the raw data and stand regardless of whether a later workbook revision has already changed these formulas.
1. `Annular_Area_m2` was SQRT(2 x radial increment x h): dimensionally a length, not an area. The sheet's own annular-ring formula (`Radial_Area_Exact_m2`) is used instead.
2. The density in every momentum / energy / mass-flow formula was labelled `Air_Density_kg_m3 = 1.2` for a head-driven liquid jet. Water's density at an assumed 20 degC is used instead (998.1 kg/m3, the pump stage's own correlation; no temperature was logged for this bench, so this is an assumption, not a measurement).

## How wrong was the original momentum-flux column? (computed)
* At the jet centerline the two errors land close together by coincidence -- original / corrected = 115 mm: 1.37x (+37%); 250 mm: 0.82x (-18%); 310 mm: 0.72x (-28%).
* At every other radius (n = 17) the original is 6.7x to 289x too small (median 51x), because the buggy "area" term depends on head and shrinks with radius while the true annular-ring area grows with radius. The original therefore got the *shape* of the radial profile wrong as well as its scale. Per-row detail: `results/bug_impact_comparison.csv`.

## Fluid extension -- first-order density scaling only
Jet velocity V = sqrt(2 g h) does not depend on density under the ideal Torricelli/Bernoulli assumption, so it is unchanged. Momentum and kinetic-energy flux scale linearly with density at the same head and geometry. This uses the same fluids and the same density function as the pump/oil & gas stage (crude densities at 40 degC via the representative thermal-expansion coefficient):

| Fluid | T (degC) | rho (kg/m3) | rho / rho(tested water) |
|---|---|---|---|
| Water as tested (T assumed) | 20 | 998.1 | 1.000 |
| Produced water (+3% density) | 25 | 1026.9 | 1.029 |
| Light crude - Minas 35 API | 40 | 834.5 | 0.836 |
| Medium crude - 27 API | 40 | 876.6 | 0.878 |
| Heavy crude - Duri 20.8 API | 40 | 912.3 | 0.914 |

This is a first-order scaling under an ideal nozzle model, **not** a prediction of crude-oil jet-pump performance. Not quantified: viscosity's effect on the nozzle discharge coefficient (a more viscous fluid typically discharges slightly below the ideal Torricelli velocity at lower Reynolds numbers, so the scaled figures are an upper bound for a viscous power fluid). Quantifying it would need this nozzle's own discharge-coefficient-vs-Reynolds-number curve, which this bench did not measure.
