"""Testy metrik přesnosti (``accuracy``) – čistá matematika z evalů, bez enginu."""
import pytest

from accuracy import (
    win_pct, win_prob, win_series, expected_points, move_accuracy, classify,
    per_move, game_accuracy, quick_summary, composite_index, ipr_from_acpl,
    fit_linear, WIN_INACC, WIN_MIST, WIN_BLUND,
)


def test_win_pct_stred_a_smer():
    assert win_pct(0) == pytest.approx(50.0)
    assert win_pct(1000) > 95.0
    assert win_pct(-1000) < 5.0
    # symetrie kolem nuly
    assert win_pct(300) + win_pct(-300) == pytest.approx(100.0, abs=1e-6)


def test_win_pct_je_clampnute():
    assert win_pct(50000) == win_pct(1000)
    assert win_pct(-50000) == win_pct(-1000)


def test_win_prob_pouzije_wdl_kdyz_je():
    # čisté W/D/L 1000/0/0 → 100 %
    assert win_prob(0, [1000, 0, 0]) == pytest.approx(100.0)
    assert win_prob(0, [0, 1000, 0]) == pytest.approx(50.0)
    assert win_prob(0, [200, 300, 500]) == pytest.approx(35.0)


def test_win_prob_fallback_na_sigmoidu():
    assert win_prob(0, None) == pytest.approx(win_pct(0))
    assert win_prob(0, [0, 0, 0]) == pytest.approx(win_pct(0))


def test_win_series_bez_wdl_je_seznam_win_pct():
    evals = [0, 50, -30, 200]
    assert win_series(evals) == [win_pct(e) for e in evals]


def test_win_series_s_wdl():
    evals = [0, 0]
    wdls = [[1000, 0, 0], [0, 0, 1000]]
    assert win_series(evals, wdls) == pytest.approx([100.0, 0.0])


def test_move_accuracy_bez_poklesu_je_sto():
    assert move_accuracy(60.0, 60.0) == pytest.approx(100.0, abs=1e-6)
    assert move_accuracy(60.0, 70.0) == pytest.approx(100.0, abs=1e-6)  # zlepšení


def test_move_accuracy_klesa_s_poklesem_win_pct():
    assert move_accuracy(60.0, 40.0) < move_accuracy(60.0, 55.0)
    assert 0.0 <= move_accuracy(90.0, 10.0) <= 100.0


def test_classify_prahy():
    assert classify(0.0) is None
    assert classify(WIN_INACC) == "?!"
    assert classify(WIN_MIST) == "?"
    assert classify(WIN_BLUND) == "??"
    assert classify(WIN_BLUND + 20) == "??"


def test_per_move_delka_a_strany():
    evals = [0, 30, 10, -20, 5]
    mv = per_move(evals)
    assert len(mv) == len(evals) - 1
    assert [m["white"] for m in mv] == [True, False, True, False]


def test_per_move_cp_loss_z_pohledu_hrace():
    # bílý zahraje 0 -> -100 (ztratil 100 cp)
    mv = per_move([0, -100])
    assert mv[0]["white"] is True
    assert mv[0]["cp_loss"] == pytest.approx(100)
    assert mv[0]["win_drop"] > 0


def test_per_move_dobra_pozice_ztrata_nula():
    mv = per_move([0, 0])
    assert mv[0]["cp_loss"] == 0
    assert mv[0]["win_drop"] == pytest.approx(0.0)
    assert mv[0]["acc"] == pytest.approx(100.0, abs=1e-6)


def test_game_accuracy_perfektni_partie():
    acc = game_accuracy([0] * 21)
    assert acc["white"] == pytest.approx(100.0, abs=0.1)
    assert acc["black"] == pytest.approx(100.0, abs=0.1)


def test_game_accuracy_prazdne_evaly():
    assert game_accuracy([]) == {"white": None, "black": None}
    assert game_accuracy([0]) == {"white": None, "black": None}


def test_game_accuracy_horsi_hra_nizsi_cislo():
    dobra = game_accuracy([0, 10, 0, 10, 0, 10, 0, 10, 0, 10, 0])
    spatna = game_accuracy([0, -300, 0, -300, 0, -300, 0, -300, 0, -300, 0])
    assert spatna["white"] < dobra["white"]


def test_quick_summary_struktura():
    s = quick_summary([0, 50, -400, 20, 0])
    assert set(s) == {"accuracy", "ep_lost"}
    assert set(s["ep_lost"]) == {"white", "black"}
    assert s["ep_lost"]["black"] >= 0.0


def test_expected_points_je_win_pct_deleno_sto():
    assert expected_points(0) == pytest.approx(0.5)
    assert expected_points(1000) == pytest.approx(win_pct(1000) / 100.0)


def test_composite_index_monotonie_a_meze():
    lepsi = composite_index(90.0, 10.0, 0.5, 80.0)
    horsi = composite_index(60.0, 90.0, 4.0, 40.0)
    assert 0.0 <= horsi < lepsi <= 100.0
    assert composite_index(None, 10, 1, 50) is None


def test_composite_index_funguje_bez_t1():
    assert composite_index(80.0, 20.0, 1.0, None) is not None


def test_ipr_from_acpl_klesa_s_acpl_a_je_v_mezich():
    assert ipr_from_acpl(10) > ipr_from_acpl(100)
    assert 400.0 <= ipr_from_acpl(1000) <= 2900.0
    assert 400.0 <= ipr_from_acpl(0.001) <= 2900.0


def test_fit_linear_presna_rovina():
    # y = 1 + 2*x1 + 3*x2
    rows = [(0, 0, 1), (1, 0, 3), (0, 1, 4), (1, 1, 6), (2, 1, 8), (2, 3, 14)]
    fit = fit_linear(rows)
    assert fit["beta"] == pytest.approx([1.0, 2.0, 3.0], abs=1e-6)
    assert fit["r2"] == pytest.approx(1.0, abs=1e-9)


def test_fit_linear_maly_vzorek_je_none():
    assert fit_linear([(0, 0, 1), (1, 1, 2)]) is None
