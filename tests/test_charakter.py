"""Testy charakteru partie/pozice (``charakter``) – bez enginu."""
import chess
import pytest

from charakter import (
    phase_value, phase_name, volatility, conversion_record,
    criticality, complexity, sharpness, PHASE_MAX,
)


def test_phase_value_pocatecni_pozice_je_plna():
    assert phase_value(chess.Board()) == pytest.approx(1.0)


def test_phase_value_holy_kral_a_pesci_je_nula():
    b = chess.Board("4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w - - 0 1")
    assert phase_value(b) == pytest.approx(0.0)


def test_phase_name_prahy():
    assert phase_name(0.9) == "Zahájení"
    assert phase_name(0.5) == "Středhra"
    assert phase_name(0.1) == "Koncovka"


def test_volatility_klidna_partie():
    v = volatility([10, 12, 8, 11, 9, 10])
    assert v["max_swing"] < 5.0
    assert v["reversals"] == 0
    assert v["n"] == 5


def test_volatility_divoka_partie_ma_obraty():
    # střídavě velká výhoda bílého a černého
    v = volatility([500, -500, 500, -500, 500])
    assert v["reversals"] >= 3
    assert v["max_swing"] > 50.0


def test_volatility_kratky_vstup():
    v = volatility([10])
    assert v["n"] == 0
    assert v["max_swing"] == 0.0


def test_volatility_max_swing_ply_ukazuje_na_skok():
    v = volatility([0, 0, 0, 900, 0])   # velký skok na 3. půltahu (index 3)
    assert v["max_swing_ply"] == 3


def test_conversion_record_vyhranou_pozici_promarnil():
    # bílý má dlouho vyhráno (win% ~ 92), pak remíza
    evals = [0, 800, 800, 800, 800, 0]
    rec = conversion_record(evals, player_white=True, result="draw")
    assert rec["had_win"] is True
    assert rec["won"] is False
    assert rec["ep_wasted"] > 0.0


def test_conversion_record_cistou_vyhru_nepromarnil():
    evals = [0, 800, 800, 800, 800, 900]
    rec = conversion_record(evals, player_white=True, result="win")
    assert rec["had_win"] is True
    assert rec["won"] is True
    assert rec["ep_wasted"] == pytest.approx(0.0)


def test_conversion_record_pohled_cerneho():
    # z pohledu bílého velké mínus = černý má vyhráno
    evals = [0, -800, -800, -800, -800, -900]
    rec = conversion_record(evals, player_white=False, result="win")
    assert rec["had_win"] is True
    assert rec["won"] is True


def test_criticality_rozdil_dvou_nejlepsich():
    assert criticality([0.8, 0.5, 0.4]) == pytest.approx(0.3)
    assert criticality([0.5]) == 0.0
    assert criticality([0.4, 0.6]) == 0.0   # nezáporné


def test_complexity_je_smerodatna_odchylka():
    assert complexity([0.5, 0.5, 0.5]) == pytest.approx(0.0)
    assert complexity([0.2, 0.8]) == pytest.approx(0.3)


def test_sharpness_meze_a_smer():
    assert sharpness([], 10) == 0.0
    assert sharpness([0.5, 0.5], 1) == 0.0
    # jen jeden tah drží → blízko 1
    ostra = sharpness([0.9, 0.2, 0.1, 0.05], n_legal=20, tol=0.1)
    # skoro všechny drží → blízko 0
    klidna = sharpness([0.9, 0.88, 0.85, 0.83], n_legal=4, tol=0.1)
    assert ostra > klidna
    assert 0.0 <= ostra <= 1.0
