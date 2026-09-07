"""Testy rozpoznávání taktických motivů (``motifs``) – čistě z pozice, bez enginu."""
import chess

from motifs import detect_motifs, motif_labels, MOTIF_ORDER, MOTIF_LABEL


def _m(fen, uci, **kw):
    return detect_motifs(chess.Board(fen), chess.Move.from_uci(uci), **kw)


def test_promena():
    got = _m("4k3/P7/8/8/8/8/8/6K1 w - - 0 1", "a7a8q")
    assert "promotion" in got


def test_mat_na_prvni_rade():
    got = _m("6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1", "e1e8")
    assert "mate" in got
    assert "back_rank" in got


def test_vidlicka_konem():
    got = _m("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "b5c7")
    assert "fork" in got


def test_vazba_strelcem_na_krale():
    got = _m("4k3/8/2n5/8/8/3B4/8/6K1 w - - 0 1", "d3b5")
    assert "pin" in got


def test_obet_se_prida_kdyz_je_priznak():
    fen = "4k3/8/8/8/8/8/5Q2/6K1 w - - 0 1"
    bez = _m(fen, "f2f5")
    s = _m(fen, "f2f5", is_sacrifice=True)
    assert "sacrifice" not in bez
    assert "sacrifice" in s


def test_klidny_tah_nema_zadny_motiv():
    assert _m("4k3/8/8/8/8/8/8/R5K1 w - - 0 1", "a1a4") == []


def test_vysledek_je_v_poradi_motif_order():
    got = _m("6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1", "e1e8")
    assert got == [m for m in MOTIF_ORDER if m in got]


def test_motif_labels_preklada():
    assert motif_labels(["mate", "fork"]) == "mat, vidlička"
    assert motif_labels(["neznamy"]) == "neznamy"
    assert set(MOTIF_LABEL) == set(MOTIF_ORDER)
