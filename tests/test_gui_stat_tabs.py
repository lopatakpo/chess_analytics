"""Regresní smoke test karet se statistikami – že se stromy vůbec naplní.

Chytá třídu chyb „při úpravě vypadlo addTopLevelItem / addChild" (přesně to se
jednou stalo kartě Koncovky).
"""
import random

import chess
import chess.pgn
import pytest

import endgames as EG
from pgn_game import read_games


def _ply_depth(node):
    d = 0
    while node.parent is not None:
        d += 1
        node = node.parent
    return d


def _endgame_game(seed, white, black, result_hint=None):
    r = random.Random(seed)
    b = chess.Board()
    g = chess.pgn.Game()
    node = g
    for _ in range(240):
        if b.is_game_over():
            break
        legal = list(b.legal_moves)
        caps = [m for m in legal if b.is_capture(m)]
        m = r.choice(caps) if (caps and r.random() < 0.85) else r.choice(legal)
        node = node.add_variation(m)
        b.push(m)
        if EG._table_ok(EG._piece_counts(b)) and _ply_depth(node) > 24 and r.random() < 0.5:
            break
    res = b.result(claim_draw=True)
    if res == "*":
        res = result_hint or r.choice(["1-0", "0-1", "1/2-1/2"])
    g.headers.update({"Event": "Rated Blitz game", "Site": "?", "Date": "2024.01.01",
                      "White": white, "Black": black, "Result": res,
                      "WhiteElo": "2000", "BlackElo": "2010",
                      "ECO": "C00", "Opening": "Zahájení"})
    return g.accept(chess.pgn.StringExporter(headers=True, variations=False,
                                             comments=False))


@pytest.fixture
def window(qapp):
    import main_window
    text = "\n\n".join(
        _endgame_game(i, "hrac" if i % 2 else "souper",
                      "souper" if i % 2 else "hrac")
        for i in range(24)) + "\n\n"
    games = list(read_games(__import__("io").StringIO(text)))
    w = main_window.MainWindow()
    w.games = games
    w._filter_keep = None
    w.cmb_player.blockSignals(True)
    w.cmb_player.addItem("hrac", "hrac")
    w.cmb_player.setCurrentIndex(w.cmb_player.count() - 1)
    w.cmb_player.blockSignals(False)
    return w


def test_endgames_tab_populates(window):
    window._update_endgames()
    assert window.endgame_tree.topLevelItemCount() > 0
    it = window.endgame_tree.topLevelItem(0)
    assert it.childCount() > 0                    # počty pěšců
    assert it.child(0).childCount() > 0           # jednotlivé partie


def test_endgames_tab_with_population_overlay(window, tmp_path, monkeypatch):
    import endgame_dump
    monkeypatch.setattr(endgame_dump, "POP_PATH", str(tmp_path / "pop.json.gz"))
    st = endgame_dump.aggregate(_write_dump(tmp_path))
    endgame_dump.save_population(st)
    window._eg_pop = st
    window._update_endgames()
    assert window.endgame_tree.topLevelItemCount() > 0


def _write_dump(tmp_path):
    text = "\n\n".join(_endgame_game(100 + i, "a", "b") for i in range(30)) + "\n\n"
    p = tmp_path / "d.pgn"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_openings_tab_populates(window):
    window._update_openings()
    assert window.opening_stats_tree.topLevelItemCount() > 0


def test_patterns_tab_populates(window):
    window._update_patterns()
    assert window.patterns_tree.topLevelItemCount() > 0
