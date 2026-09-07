"""Testy statistických nástrojů (``stats_util``) – žádný engine, čistá matematika."""
import math

import pytest

from stats_util import (
    norm_cdf, two_prop_p, bh_reject, winrate_outliers, wilson_interval,
    estimate_shrink_m, shrink_rate, elo_expected, elo_expected_from_delta,
    luck_z, kaplan_meier, logistic_fit, logistic_predict,
)


def test_norm_cdf_zakladni_hodnoty():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert norm_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_two_prop_p_stejne_podily_je_jedna():
    assert two_prop_p(50, 100, 50, 100) == pytest.approx(1.0)


def test_two_prop_p_prazdny_vzorek_je_none():
    assert two_prop_p(0, 0, 5, 10) is None
    assert two_prop_p(5, 10, 0, 0) is None


def test_two_prop_p_rozdil_klesa_s_velikosti_vzorku():
    maly = two_prop_p(6, 10, 4, 10)
    velky = two_prop_p(600, 1000, 400, 1000)
    assert velky < maly
    assert velky < 0.001


def test_two_prop_p_je_v_rozsahu_0_1():
    for args in [(1, 3, 90, 100), (0, 50, 50, 50), (7, 7, 0, 7)]:
        p = two_prop_p(*args)
        assert p is None or 0.0 <= p <= 1.0


def test_bh_reject_znama_sekvence():
    # klasický příklad: první dvě projdou při FDR 5 %, zbytek ne
    assert bh_reject([0.001, 0.02, 0.04, 0.3, 0.8]) == [True, True, False, False, False]


def test_bh_reject_none_hodnoty_nikdy_neprojdou():
    out = bh_reject([0.001, None, 0.001])
    assert out == [True, False, True]


def test_bh_reject_je_konzervativnejsi_nez_holy_alfa_prah():
    pvals = [0.03, 0.04, 0.045]
    holy = [p < 0.05 for p in pvals]
    bh = bh_reject(pvals, alpha=0.05)
    assert sum(bh) <= sum(holy)


def test_winrate_outliers_oznaci_odlisny_kos():
    # celek 55 % z 2000; koše 60 % a 50 % z 1000 každý
    buckets = [(600, 1000), (500, 1000)]
    flags, pvals = winrate_outliers(buckets, w0=1100, n0=2000)
    assert flags == [True, True]
    assert all(p is not None and p < 0.05 for p in pvals)


def test_winrate_outliers_efektni_prah_utlumi_trivialni_rozdil():
    # obří vzorek, rozdíl jen ~1 p.b. → statisticky možná ano, věcně ne
    buckets = [(5100, 10000)]
    flags, _ = winrate_outliers(buckets, w0=10000, n0=20000, min_effect=0.025)
    assert flags == [False]


def test_wilson_interval_obklopuje_bodovy_odhad():
    lo, hi = wilson_interval(50, 100)
    assert lo < 0.5 < hi
    assert 0.0 <= lo < hi <= 1.0
    # větší vzorek → užší interval
    lo2, hi2 = wilson_interval(500, 1000)
    assert (hi2 - lo2) < (hi - lo)


def test_wilson_interval_prazdny_je_none():
    assert wilson_interval(0, 0) is None


def test_estimate_shrink_m_homogenni_kose_velke_m():
    # všechny koše ~50 % → koším se nevěří → velké m
    homog = [(50, 100)] * 5
    assert estimate_shrink_m(homog) >= 100.0
    # hrubě odlišné koše → menší m
    hetero = [(10, 100), (90, 100), (50, 100), (30, 100), (70, 100)]
    assert estimate_shrink_m(hetero) < estimate_shrink_m(homog)


def test_shrink_rate_stahuje_k_prumeru():
    # 3/3 = 100 %, ale s priorem se stáhne k p0
    assert shrink_rate(3, 3, p0=0.5, m=10) == pytest.approx((3 + 5) / 13)
    # velký vzorek se skoro nestáhne
    assert shrink_rate(600, 1000, p0=0.5, m=10) == pytest.approx((600 + 5) / 1010)


def test_elo_expected_symetrie_a_smer():
    assert elo_expected(1500, 1500) == pytest.approx(0.5)
    assert elo_expected(1900, 1500) > 0.9
    assert elo_expected(1500, 1500) + elo_expected(1500, 1500) == pytest.approx(1.0)
    assert elo_expected_from_delta(400) == pytest.approx(elo_expected(1900, 1500))


def test_luck_z_nulove_kdyz_skore_odpovida_ocekavani():
    pairs = [(0.5, 0.5)] * 10
    assert luck_z(pairs) == pytest.approx(0.0)
    # samé výhry tam, kde se čekala remíza → kladné z
    assert luck_z([(1.0, 0.5)] * 10) > 0


def test_luck_z_prazdny_je_none():
    assert luck_z([]) is None


def test_kaplan_meier_median():
    # polovina událostí do času 5
    recs = [(3, True), (4, True), (10, False), (12, False)]
    km = kaplan_meier(recs)
    assert km["n"] == 4
    assert km["n_events"] == 2
    assert km["median"] is not None


def test_logistic_fit_najde_smer_zavislosti():
    # y=1 pro x>0, y=0 pro x<0 – koeficient u x musí být kladný
    X = [[-3.0], [-2.0], [-1.0], [-0.5], [0.5], [1.0], [2.0], [3.0], [4.0]]
    y = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    fit = logistic_fit(X, y)
    assert fit is not None
    assert fit["beta"][1] > 0
    assert logistic_predict(fit["beta"], [5.0]) > logistic_predict(fit["beta"], [-5.0])


def test_logistic_fit_maly_vzorek_je_none():
    assert logistic_fit([[1.0], [2.0]], [0.0, 1.0]) is None
