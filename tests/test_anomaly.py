"""Testy číselných pomocníků detekce anomálií (``anomaly``) – lineární algebra,
van der Waerdenova transformace, χ² percentil. Bez enginu, bez partií."""
import math
import random

import pytest

from anomaly import (
    _median, _mad, _mean_std, _norm_ppf, _normal_scores, _inv, _cov,
    _shrink, _mahalanobis_all, _chi2_percentile,
)


def test_median_a_mad():
    assert _median([]) == 0.0
    assert _median([3, 1, 2]) == 2
    assert _median([1, 2, 3, 4]) == 2.5
    assert _mad([1, 2, 3, 4, 5], 3) == 1.0


def test_mean_std():
    m, s = _mean_std([2, 4, 4, 4, 5, 5, 7, 9])
    assert m == pytest.approx(5.0)
    assert s == pytest.approx(2.0)


def test_norm_ppf_je_inverzni_k_cdf():
    for z in (-2.0, -0.5, 0.0, 0.5, 1.0, 2.5):
        p = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        assert _norm_ppf(p) == pytest.approx(z, abs=1e-4)
    assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)


def test_normal_scores_je_symetricky_a_serazeny():
    col = [5.0, 1.0, 3.0, 2.0, 4.0]
    ns = _normal_scores(col)
    # pořadí zachováno
    assert sorted(range(5), key=lambda i: ns[i]) == [1, 3, 2, 4, 0]
    # symetrie kolem 0 (lichý počet, medián → 0)
    assert sum(ns) == pytest.approx(0.0, abs=1e-9)


def test_normal_scores_zvlada_shodne_hodnoty():
    ns = _normal_scores([1.0, 1.0, 1.0, 1.0])
    assert ns == pytest.approx([0.0, 0.0, 0.0, 0.0])


def test_normal_scores_robustni_vuci_silne_sikmosti():
    # skoro samé nuly, jeden extrém – po transformaci žádné těžké chvosty
    col = [0.0] * 50 + [1e6]
    ns = _normal_scores(col)
    assert max(abs(x) for x in ns) < 3.0


def test_inv_identita_a_soucin():
    m = [[4.0, 3.0], [6.0, 3.0]]
    inv = _inv(m)
    prod = [[sum(m[i][k] * inv[k][j] for k in range(2)) for j in range(2)]
            for i in range(2)]
    assert prod[0] == pytest.approx([1.0, 0.0], abs=1e-9)
    assert prod[1] == pytest.approx([0.0, 1.0], abs=1e-9)


def test_inv_singularni_je_none():
    assert _inv([[1.0, 2.0], [2.0, 4.0]]) is None


def test_cov_je_symetricka_a_diagonala_je_rozptyl():
    rows = [[1.0, 2.0], [-1.0, -2.0], [2.0, 1.0], [-2.0, -1.0]]
    cov = _cov(rows)
    assert cov[0][1] == pytest.approx(cov[1][0])
    assert cov[0][0] == pytest.approx(2.5)   # E[x^2] pro centrovaná data


def test_shrink_tahne_k_jednotkove():
    cov = [[2.0, 1.5], [1.5, 2.0]]
    sh = _shrink(cov, 0.5)
    assert sh[0][0] == pytest.approx(0.5 * 2.0 + 0.5)
    assert sh[0][1] == pytest.approx(0.5 * 1.5)


def test_mahalanobis_vlozeny_outlier_ma_nejvetsi_d2():
    random.seed(0)
    rows = [[random.gauss(0, 1), random.gauss(0, 1)] for _ in range(60)]
    rows.append([8.0, -8.0])                  # jasný outlier
    ns = [_normal_scores([r[c] for r in rows]) for c in range(2)]
    z_rows = [[ns[c][i] for c in range(2)] for i in range(len(rows))]
    d2, inv = _mahalanobis_all(z_rows, alpha=0.25)
    assert d2.index(max(d2)) == len(rows) - 1


def test_chi2_percentile_roste_a_je_v_0_1():
    assert _chi2_percentile(0.0, 3) == 0.0
    a = _chi2_percentile(2.0, 3)
    b = _chi2_percentile(12.0, 3)
    assert 0.0 <= a < b <= 1.0
    # medián χ²_3 ≈ 2.366 → percentil ~0.5
    assert _chi2_percentile(2.366, 3) == pytest.approx(0.5, abs=0.02)
