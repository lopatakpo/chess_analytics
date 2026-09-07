"""Testy klasifikace tahů ve stylu chess.com (``move_class``) – bez enginu."""
import chess
import pytest

from move_class import (
    _book_plies, classify_moves, count_kinds, brilliant_plies,
    _classify_kinds, KIND_ORDER, _KIND_SYMBOL, CC_MARK_TEXT,
)


def _pseudo_game(n_plies=40):
    """Deterministická legální partie – pro strukturu (boards, moves_uci), ne krásu."""
    b = chess.Board()
    boards = [b.copy(stack=False)]
    ucis = []
    for _ in range(n_plies):
        moves = list(b.legal_moves)
        if not moves:
            break
        mv = sorted(moves, key=lambda m: m.uci())[len(ucis) % len(moves)]
        ucis.append(mv.uci())
        b.push(mv)
        boards.append(b.copy(stack=False))
    return ucis, boards


def test_book_plies_normalni_zahajeni_v_mezich():
    ucis, _ = _pseudo_game(20)
    d = _book_plies(ucis)
    assert 8 <= d <= 24


def test_book_plies_nesmysl_ma_fallback():
    assert _book_plies(["a2a4", "h7h5", "a4a5", "h5h4"]) == 8


def test_count_kinds_soucet_odpovida_poctu_pultahu():
    ucis, boards = _pseudo_game(30)
    evals = [0] * (len(ucis) + 1)
    counts = count_kinds(evals, ucis, boards)
    assert sum(counts.values()) == len(ucis)
    assert set(counts).issubset(set(KIND_ORDER))


def test_prvnich_book_pultahu_je_kniha():
    ucis, boards = _pseudo_game(30)
    evals = [0] * (len(ucis) + 1)
    kinds = _classify_kinds(evals, ucis, boards)
    bp = _book_plies(ucis)
    assert all(k == "book" for k in kinds[:bp])
    assert kinds[bp] != "book"


def test_classify_moves_mlci_knihu_a_vraci_platne_symboly():
    ucis, boards = _pseudo_game(30)
    evals = [0] * (len(ucis) + 1)
    marks = classify_moves(evals, ucis, boards)
    bp = _book_plies(ucis)
    assert all(ply > bp for ply in marks)          # kniha se neznačí
    assert all(sym in CC_MARK_TEXT for sym in marks.values())


def test_pozdni_hrubka_dostane_znacku():
    ucis, boards = _pseudo_game(40)
    evals = [0] * (len(ucis) + 1)
    evals[21] = -600                                # velký propad po tahu 21 (k=20)
    marks = classify_moves(evals, ucis, boards)
    assert marks.get(21) == "??"


def test_great_se_bez_crit_nikdy_neobjevi():
    ucis, boards = _pseudo_game(30)
    evals = [0] * (len(ucis) + 1)
    kinds = _classify_kinds(evals, ucis, boards, crit=None)
    assert "great" not in kinds


def test_brilliant_plies_vraci_platne_indexy():
    ucis, boards = _pseudo_game(30)
    evals = [0] * (len(ucis) + 1)
    out = brilliant_plies(evals, ucis, boards)
    assert isinstance(out, list)
    assert all(isinstance(k, int) and 0 <= k < len(ucis) for k in out)
    # brilantní tah je vždy podmnožina tahů označených symbolem !!
    marks = classify_moves(evals, ucis, boards)
    assert all(marks.get(k + 1) == "!!" for k in out)
