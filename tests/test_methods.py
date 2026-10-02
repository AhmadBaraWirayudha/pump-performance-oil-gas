"""Checks on the validation logic and the hand-checkable methods."""
import numpy as np
import pytest

import jet_nozzle_extension as jet
import oil_gas_extension as og
import pump_performance_model as base


# ---- leave-one-speed-out semantics -----------------------------------------
@pytest.mark.parametrize("held, kind", [(1050, "extrapolation"), (1200, "interpolation"),
                                        (1350, "extrapolation")])
def test_fold_kind_for_three_speeds(held, kind):
    assert base.fold_kind(held, [1050, 1200, 1350]) == kind


def test_loso_subsets_counts():
    speeds = np.repeat([1050.0, 1200.0, 1350.0], 8)
    masks, kinds = base.loso_subsets(speeds)
    assert masks["extrapolation"].sum() == 16 and masks["interpolation"].sum() == 8
    assert (masks["extrapolation"] ^ masks["interpolation"]).all()
    assert kinds == {1050.0: "extrapolation", 1200.0: "interpolation", 1350.0: "extrapolation"}


def test_subset_metrics_perfect_and_biased():
    y = np.array([1.0, 2.0, 3.0, 4.0])
    m = base.subset_metrics(y, y, np.ones(4, bool))
    assert m["RMSE"] == 0 and m["R2"] == 1
    m = base.subset_metrics(y, y + 0.5, np.ones(4, bool))
    assert m["RMSE"] == pytest.approx(0.5) and m["MAE"] == pytest.approx(0.5)


# ---- cross-row head deviation ------------------------------------------------
def test_head_deviation_reproduces_hand_example():
    sample = [1, 2, 1, 2]
    speed = [1050, 1050, 1200, 1200]
    head = [4.0, 3.0, 5.0, 4.0]
    head1200 = [5.0, 3.6, 5.0, 4.0]               # normalised heads (value at 1200 rpm = itself)
    dev = base.head_deviation_vs_1200_pct(sample, speed, head1200, head)
    # sample 1: |5.0 - 5.0| / 5.0 = 0 % ; sample 2: |3.6 - 4.0| / 4.0 = 10 % ; 1200-rpm rows are 0
    assert dev[0] == pytest.approx(0.0) and dev[1] == pytest.approx(10.0)
    assert dev[2] == 0.0 and dev[3] == 0.0


def test_head_deviation_needs_a_reference_row():
    with pytest.raises(ValueError, match="No 1200-rpm row"):
        base.head_deviation_vs_1200_pct([1], [1050], [5.0], [4.0])


# ---- fluid properties against tabulated values ------------------------------
def test_water_density_matches_tables():
    assert float(base.water_density_kgm3(20.0)) == pytest.approx(998.2, abs=0.2)
    assert float(base.water_density_kgm3(40.0)) == pytest.approx(992.2, abs=0.3)


def test_water_viscosity_matches_tables():
    assert float(og.water_viscosity_cp(20.0)) == pytest.approx(1.002, abs=0.01)
    assert float(og.water_viscosity_cp(40.0)) == pytest.approx(0.653, abs=0.01)


def test_beggs_robinson_trends_and_validity_flags():
    T = np.array([25.0, 40.0, 60.0])
    mu = og.beggs_robinson_dead_oil_viscosity_cp(T, 27.0)
    assert (np.diff(mu) < 0).all()                                       # warmer -> thinner
    assert og.beggs_robinson_dead_oil_viscosity_cp(40.0, 20.0) > og.beggs_robinson_dead_oil_viscosity_cp(40.0, 35.0)
    ok = og.beggs_robinson_in_range(np.array([15.0, 21.0, 21.2, 146.0, 150.0]), 27.0)
    assert ok.tolist() == [False, False, True, True, False]              # 70 F = 21.1 C lower limit
    assert not og.beggs_robinson_in_range(40.0, 10.0) and not og.beggs_robinson_in_range(40.0, 60.0)


def test_api_to_sg():
    assert og.api_to_sg(10.0) == pytest.approx(1.0)                      # API 10 == water
    assert og.api_to_sg(35.0) == pytest.approx(0.8498, abs=1e-3)


# ---- ANSI/HI 9.6.7 implementation --------------------------------------------
def test_hi_regimes():
    low = og.hi_9_6_7_correction_factors(0.5)
    assert low["CQ"] == 1.0 and low["Ceta"] == 1.0
    high = og.hi_9_6_7_correction_factors(50.0)
    assert np.isnan(high["CQ"]) and np.isnan(high["Ceta"])
    mid = og.hi_9_6_7_correction_factors(10.0)
    assert mid["CQ"] == pytest.approx(0.8483, abs=1e-3) and mid["Ceta"] == pytest.approx(0.5395, abs=1e-3)


def test_b_parameter_scales_with_sqrt_viscosity_and_head_factor_endpoints():
    b1 = og.hi_9_6_7_b_parameter(10.0, 3.0, 4.0, 1200.0)
    b4 = og.hi_9_6_7_b_parameter(40.0, 3.0, 4.0, 1200.0)
    assert b4 / b1 == pytest.approx(2.0)
    assert og.hi_9_6_7_head_factor(0.8, 1.0) == pytest.approx(0.8)      # CH(Q_bep) = CQ
    assert og.hi_9_6_7_head_factor(0.8, 0.0) == pytest.approx(1.0)      # shut-off: no head correction


def test_b_limit_temperature_conventions():
    ref = {"H_bep_m": 3.3, "Q_bep_m3h": 3.6}
    t = og.b_limit_temperature(20.8, ref)                                # heavy crude crosses within 15-80 C
    assert 15.0 < t < 80.0
    assert np.isnan(og.b_limit_temperature(58.0, ref))                   # very light: never above the limit


# ---- jet recomputation --------------------------------------------------------
def test_jet_recompute_hand_calculation():
    import pandas as pd
    df = pd.DataFrame({"Radius_m": [0.01], "Radial_Increment_m": [0.005], "Head_Difference_h_m": [0.02]})
    out = jet.recompute(df, rho_kgm3=1000.0, use_sheet_area_formula=False)
    V = (2 * 9.80665 * 0.02) ** 0.5
    A = np.pi * (0.0125**2 - 0.0075**2)
    assert out.Velocity_m_s[0] == pytest.approx(V)
    assert out.Area_m2[0] == pytest.approx(A)
    assert out.Momentum_Flux_N[0] == pytest.approx(1000.0 * A * V * V)
    assert out.KE_Flux_W[0] == pytest.approx(0.5 * 1000.0 * A * V**3)
    bad = jet.recompute(df, rho_kgm3=1.2, use_sheet_area_formula=True)    # the sheet's own formula
    assert bad.Area_m2[0] == pytest.approx((2 * 0.005 * 0.02) ** 0.5)


def test_jet_centerline_area_is_a_full_disk():
    import pandas as pd
    df = pd.DataFrame({"Radius_m": [0.0], "Radial_Increment_m": [0.005], "Head_Difference_h_m": [0.01]})
    out = jet.recompute(df, 1000.0, use_sheet_area_formula=False)
    assert out.Area_m2[0] == pytest.approx(np.pi * 0.0025**2)


def test_bug_impact_stats_inverts_ratios():
    import pandas as pd
    cmp = pd.DataFrame({"Radius_mm": [0, 0, 5, 10, 15], "Distance_mm": [115, 250, 115, 115, 115],
                        "ratio_original_over_corrected": [1.4, 0.8, 0.2, 0.1, np.nan]})
    s = jet.bug_impact_stats(cmp)
    assert s["center"] == {115: 1.4, 250: 0.8}
    assert s["too_small_min"] == pytest.approx(5.0) and s["too_small_max"] == pytest.approx(10.0)
    assert s["n_off"] == 2
