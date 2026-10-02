# Process notes -- pump & jet-nozzle modeling for oil & gas service

How the project was run (Agile/Scrum record), how to talk about it (STAR accounts), and an honest
log of what earlier drafts got wrong. Technical detail is **not** repeated here: it lives in the
[README](../README.md) and in the generated reports ([`FINAL_report.md`](FINAL_report.md),
[`oil_gas_notes.md`](oil_gas_notes.md), [`jet_nozzle_notes.md`](jet_nozzle_notes.md)), where every
number is computed by the run.

## 1. One-paragraph summary (CV / cover letter)

Rebuilt an inherited machine-learning pipeline for 24-point centrifugal-pump bench data into one
whose accuracy claims mean something: fixed a silent column-mapping bug, replaced predictions of
deterministic formulas with predictions of the measured sensor responses, and validated on pump
speeds the model never saw (Total Head RMSE 0.23 m against 0.60 m for the textbook affinity law on the
two extrapolated speeds). Extended it to a theoretical crude-oil case with the ANSI/HI 9.6.7
viscosity-correction method and the Beggs-Robinson correlation, flagging where each method's own
validity range ends, and connected a second lab dataset (a free-jet nozzle traverse) to hydraulic jet
pumps after finding and fixing two formula errors in it. Delivered as four sprints, with a
37-test suite and a corrections log.

## 2. Agile / Scrum record

Solo project: the author held Product Owner, Scrum Master and Developer roles at once, stated
plainly rather than implied.

### Product backlog

| # | Item | Status |
|---|---|---|
| 1 | Find out why the inherited pipeline's numbers and plots could not be trusted | Done -- Sprint 8 |
| 2 | Rebuild on a defensible target with honest cross-validation | Done -- Sprint 8 |
| 3 | Extend to a viscous (crude oil) fluid with a named industry method | Done -- Sprint 9 |
| 4 | Integrate the workbook's jet-nozzle sheet if a genuine connection exists | Done -- Sprint 10 |
| 5 | Independent review: verify each finding, fix, consolidate into one release | Done -- Sprint 11 |
| 6 | Crude vapour-pressure model, to extend the crude case to NPSH / cavitation margin | Backlog |
| 7 | Replace the single thermal-expansion coefficient with API MPMS Ch. 11.1 density tables | Backlog |
| 8 | Use a field-measured viscosity curve for a specific crude instead of a generic correlation | Backlog |
| 9 | Check the ANSI/HI 9.6.7 flow/head scope limits and this pump's design type against the standard itself | Backlog |
| 10 | Jet-pump performance model (needs throat and diffuser data) | Backlog |
| 11 | Nozzle discharge coefficient vs Reynolds number, to quantify the viscosity effect on jet velocity | Backlog |
| 12 | More tested speeds, to tighten the leave-one-speed-out estimates | Backlog |

### Sprint 8 -- "Make it real"

- **Goal:** turn a pipeline that scored near-perfectly on everything into one whose numbers can be trusted.
- **Definition of Done:** every derived value reproduces the source workbook's own cached values;
  every accuracy number comes from data the model did not see; every plot checked by eye.
- **Review:** found the loader silently feeding pump *speed* into the formulas as *water temperature*
  (plus four other shifted columns); found the original "predicted" quantities were deterministic
  formulas of the features, so no model could fail; rebuilt around predicting the measured responses,
  validated leave-one-speed-out against the affinity law.
- **Retro:** cross-checking against the workbook's own cached values found the root cause at once.
  *Improve:* look at the rendered plots before calling visual work done (two first-draft figures had
  an overlapping legend and a self-referential "deviation" metric).

### Sprint 9 -- "Make it relevant to oil & gas"

- **Goal:** apply the validated water model to a fluid an O&G employer cares about, using a named
  standard rather than an invented correction.
- **Definition of Done:** the correction is ANSI/HI 9.6.7; viscosity is Beggs-Robinson; every
  simplification is stated; the method's own validity limits are checked and reported.
- **Review:** efficiency factors of 0.73 / 0.56 / 0.35 at 40 °C for Minas-grade (35° API), 27° API and
  Duri-grade (20.8° API) crude. *The first draft of this review overstated its case; see Sprint 11.*
- **Retro:** named methods make every number citable. *Improve:* check a method's validity range
  against the *whole* sweep, not just the headline scenario (this is exactly what Sprint 11 found).

### Sprint 10 -- "Integrate, don't just append"

- **Goal:** decide whether the workbook's jet sheet belongs in the project, and if so integrate it on
  the same terms.
- **Review:** the sheet is the nozzle stage of a real artificial-lift technology (hydraulic jet pumps);
  recomputing it from the raw measurements exposed two formula errors (an "area" that was a length; a
  density labelled *air* on a water jet). The original momentum-flux column was 6.7× to 289× too small
  away from the centerline (median 51×) and had the wrong radial shape; at the centerline it was
  coincidentally within about 40%.
- **Retro:** recomputing from raw data caught two more bugs on the first pass. *Improve:* the connection
  stops at the nozzle -- no throat or diffuser data -- and that is said plainly.

### Sprint 11 -- "Review, verify, harden, consolidate"

- **Goal:** one release in which every claim survives an independent check.
- **Backlog:** treat each point of an independent review as a hypothesis and recompute it; fix what is
  confirmed; make loaders robust to workbook renames; consolidate two packages into one repository;
  add tests.
- **Definition of Done:** every number in the README and docs is computed or machine-checked against
  the result files; every guardrail has a test that fails when the guard is removed (checked by
  re-introducing each historical bug in a scratch copy); the pipeline runs on Python 3.11 and 3.12
  with older and newer library stacks.
- **Review:** every numeric point the review made held up when recomputed. Three of the problems were
  my own errors, not subtle ones (see the corrections log). Verifying also exposed problems the review
  had not raised: an untested "negligible" claim that was only half true, an unchecked "B < 1" claim for
  water, and the second method's own applicability limits.
- **Retro:** *Worked well:* recompute before agreeing; build the evidence into the pipeline so prose is
  generated from numbers and cannot drift from them. *Improve:* any sentence saying "negligible" or
  "does not matter" needs a measured number behind it before it is written.
- **Known gap:** the reviewer's newest workbook (`Model(2).xlsx`) was not available in the working
  environment, so the loaders were verified on reconstructions of the described layout, not on that
  file. One confirming run on the real workbook is outstanding.

## 3. STAR accounts

Five cuts of one project, so it can answer several behavioural prompts.

### "A critical problem others missed"
- **Situation:** an inherited, ~5,000-line pump-analysis pipeline reported near-perfect R² on 31
  "predicted" quantities, with plots that did not render sensibly.
- **Task:** decide whether any of it could be trusted before building on it.
- **Action:** range-checked every raw input (a "water temperature" near 1,200 was the giveaway) and
  recomputed the workbook's own cached columns from first principles to see where the pipeline diverged.
- **Result:** found a silent fallback that read pump speed as water temperature (four more variables
  shifted likewise). Fixed at the source, and the loader now refuses a shifted layout: the physics
  layer must reproduce the workbook's Total Head and Efficiency, and it reproduces all 30 workbook
  columns that have a counterpart.

### "Improving analysis that seemed to work"
- **Situation:** even with correct data, the headline claim -- 31 accurately predicted quantities --
  did not survive scrutiny.
- **Task:** separate a real prediction task from one that only looked like one.
- **Action:** traced each target to its formula and found every one was algebra on the inputs used as
  features. Predicted the measured sensor responses instead, and validated by holding out a whole pump
  speed, against the affinity law as a zero-parameter baseline.
- **Result:** on the two genuinely extrapolated speeds the model's Total Head RMSE was 0.23 m against
  0.60 m for the affinity law; at the interpolated speed the two were equal (0.127 vs 0.125 m), and the
  signed errors show why -- so the claim became more precise, not more flattering. An honest negative
  was kept in view: a flexible model extrapolated water temperature worse than a linear one.

### "Applying an industry standard independently"
- **Situation:** the validated model only covered water; oil & gas pumps handle crude oil.
- **Task:** say something defensible about crude service with no crude data.
- **Action:** applied the ANSI/HI 9.6.7 parameter-B viscosity correction with the Beggs-Robinson
  viscosity correlation, on real Indonesian benchmarks (Minas, Duri), and checked each method's
  validity range against the whole temperature sweep -- which showed my own first "cold-start"
  conclusion sat below the viscosity correlation's range.
- **Result:** efficiency factors of 0.73 / 0.56 / 0.35 at 40 °C; a withdrawn out-of-range claim and an
  in-range replacement (the heavy grade leaves the method's limit below about 24.8 °C); and the
  observation that even plain water gives B = 1.76 at this pump's size -- the method is being used at
  the small end of its scale.

### "Deciding whether two things are related"
- **Situation:** a workbook arrived with a second sheet -- a free-jet nozzle experiment -- unrelated at
  a glance to the pump project.
- **Task:** decide whether to force it in, append it, or find (or rule out) a real link.
- **Action:** matched its physics (head-driven nozzle, Torricelli velocity, momentum flux) to the
  nozzle stage of hydraulic jet pumps; recomputed its derived columns from the raw measurements.
- **Result:** a connected second technology under the same standard, plus two more formula errors
  caught (the original momentum column was 6.7× to 289× too small away from the centerline).

### "Critical feedback"
- **Situation:** a near-final package was independently reviewed and returned with eleven findings.
- **Task:** work out which were right, rather than simply agreeing or defending.
- **Action:** recomputed each numeric claim independently; found they held, and that three of the
  problems were my own errors; fixed them at the source, replaced typed numbers with computed ones, and
  tested the fixes by re-introducing each bug to see the tests fail.
- **Result:** one repository, 37 tests, a corrections log, and a sharper headline finding. One honest
  caveat remains: the reviewer's newest workbook was unavailable, so that final confirming run is open.

## 4. Corrections log -- what earlier drafts got wrong

| Earlier claim | What was wrong | Now |
|---|---|---|
| Leave-one-speed-out is an "extrapolation test" | Only the 1050 and 1350 rpm folds extrapolate; 1200 interpolates | Fold type is derived and reported separately |
| "GP beats the affinity law, R² 0.91 vs 0.49" | Blended extrapolation with interpolation | Extrapolation folds: 0.92 vs 0.43. Interpolation fold: tied (RMSE 0.127 vs 0.125 m) |
| (Sprint 9 review) the code "now matches" the workbook's cross-speed head-deviation formula | **False** -- the code still computed a self-referential stand-in | Cross-row definition implemented and reproduces the workbook exactly |
| The GP's weak water-temperature prediction "does not meaningfully affect" the headline fields | Never tested | Measured: ~0% for head and efficiency, but about -17% for NPSH available |
| Below ~16 °C the HI limit is exceeded -- a cold-start risk | That crossing is below Beggs-Robinson's 21.1 °C lower limit | Drawn as extrapolation, not a finding. In-range replacement: heavy grade, below about 24.8 °C |
| Produced water: B stays under 1 | Never computed; B = 1.76 | Computed and explained in the notes |
| Original jet momentum flux "10 to 396× too small" | 396 was max/min of ratios *including* the centerline; 10 was typed | 6.7× to 289× (median 51×), computed |
| Centerline "within roughly 30%" | The 115 mm point is +37% | "Within about 40%", with per-distance values |
| "BEP" | It is the model-derived maximum *within the tested range*, at the fully-open edge and still rising | Named accurately; sensitivity to the proxy computed |
| "31 engineering quantities" | Stale: the physics layer returns 32 row-wise fields plus one cross-row | Count dropped from prose (kept only as history) |
| Jet fluid densities from 20 °C water-based SG; the oil & gas stage used 40 °C crude densities | Inconsistent | One shared density function |
| Hard-coded sheet names | Failed on a renamed workbook | Sheets resolved by name *and* content; tested |
