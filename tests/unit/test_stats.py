import pytest

from agent_sql.eval.stats import bootstrap_mean_ci, holm, mcnemar_exact, paired_bootstrap_diff, wilson


def test_wilson_known_value():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)


def test_mcnemar_exact_matches_planning_arithmetic():
    a = [True] * 27 + [False] * 13 + [True] * 10
    b = [False] * 27 + [True] * 13 + [True] * 10
    r = mcnemar_exact(a, b)
    assert (r["a_only"], r["b_only"]) == (27, 13) and r["p_value"] == pytest.approx(0.0385, abs=5e-4)


def test_holm_monotone():
    adj = holm({"H1": 0.01, "H2": 0.04, "H3": 0.03})
    assert adj["H1"] == pytest.approx(0.03) and adj["H3"] == pytest.approx(0.06) and adj["H2"] == pytest.approx(0.06)


def test_bootstrap_deterministic():
    assert bootstrap_mean_ci([0, 1, 1, 0, 1]) == bootstrap_mean_ci([0, 1, 1, 0, 1])
    d, lo, hi = paired_bootstrap_diff([1, 1, 1, 1], [0, 0, 0, 0])
    assert d == 1.0 and lo == 1.0 and hi == 1.0
