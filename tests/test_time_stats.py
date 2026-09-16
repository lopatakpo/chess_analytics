"""Testy karty Časový management (``time_stats``) – bez enginu, čte z cache."""
import random

import chess
import chess.pgn
import pytest

from analysis_cache import AnalysisCache, game_key
from pgn_game import load_pgn_text
from time_stats import analyze_time_management


def _make_game(seed: int, event: str = "Rated Blitz game", tc: str = "180+2",
              white: str = "hrac", black: str = "souper") -> str:
    r = random.Random(seed)
    b = chess.Board()
    g = chess.pgn.Game()
    g.headers.update({"Event": event, "White": white, "Black": black,
                      "TimeControl": tc, "Result": "1-0"})
    node = g
    clk = float(tc.split("+")[0])
    inc = float(tc.split("+")[1]) if "+" in tc else 0.0
    for _ in range(24):
        if b.is_game_over():
            break
        m = r.choice(list(b.legal_moves))
        node = node.add_variation(m)
        b.push(m)
        clk = max(0.5, clk - r.uniform(0.5, 8.0) + inc)
        node.comment = f"[%clk 0:00:{clk:05.2f}]" if clk < 60 else f"[%clk 0:01:{clk-60:05.2f}]"
    return g.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=True))


@pytest.fixture
def cache_and_games(tmp_path):
    text = "\n\n".join(_make_game(i) for i in range(6)) + "\n\n"
    games = load_pgn_text(text)
    cache = AnalysisCache()
    for g in games:
        n = len(g.moves)
        evals = [random.Random(1).randint(-60, 60) for _ in range(n + 1)]
        cache.put(game_key(g), depth=8, evals=evals, bestmoves=[m.uci() for m in g.moves])
    return cache, games


def test_analyze_time_management_basic(cache_and_games):
    cache, games = cache_and_games
    res = analyze_time_management(games, "hrac", cache, "both")
    assert res["requested"] == 6
    assert res["n_games"] == 6
    assert res["n_no_clock"] == 0
    assert res["n_no_tc"] == 0
    assert res["n_no_cache"] == 0
    assert res["overall"]["n"] > 0
    assert 0.0 <= res["overall"]["accuracy"] <= 100.0
    assert res["overall"]["mean_time"] > 0


def test_analyze_time_management_skips_uncached(tmp_path):
    text = "\n\n".join(_make_game(i) for i in range(3)) + "\n\n"
    games = load_pgn_text(text)
    cache = AnalysisCache()      # prázdná – nic nerozebráno
    res = analyze_time_management(games, "hrac", cache, "both")
    assert res["n_games"] == 0
    assert res["n_no_cache"] == 3
    assert res["overall"]["n"] == 0


def test_analyze_time_management_skips_missing_clocks(tmp_path):
    b = chess.Board()
    g = chess.pgn.Game()
    g.headers.update({"Event": "Casual game", "White": "hrac", "Black": "souper",
                      "TimeControl": "180+2", "Result": "1-0"})
    node = g
    for _ in range(6):
        m = list(b.legal_moves)[0]
        node = node.add_variation(m)
        b.push(m)
    text = g.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
    games = load_pgn_text(text)
    cache = AnalysisCache()
    cache.put(game_key(games[0]), depth=8, evals=[0] * (len(games[0].moves) + 1),
             bestmoves=["e2e4"] * len(games[0].moves))
    res = analyze_time_management(games, "hrac", cache, "both")
    assert res["n_no_clock"] == 1
    assert res["n_games"] == 0


def test_analyze_time_management_only_players_own_moves(cache_and_games):
    cache, games = cache_and_games
    res_hrac = analyze_time_management(games, "hrac", cache, "both")
    res_souper = analyze_time_management(games, "souper", cache, "both")
    total_moves = sum(len(g.moves) for g in games)
    # hráč + soupeř dohromady pokryjí přibližně všechny tahy (minus okraje bez
    # navazujícího [%clk] záznamu), nikdy víc než celkový počet
    assert res_hrac["overall"]["n"] + res_souper["overall"]["n"] <= total_moves
    assert res_hrac["overall"]["n"] > 0
    assert res_souper["overall"]["n"] > 0


def test_by_bin_covers_all_moves(cache_and_games):
    cache, games = cache_and_games
    res = analyze_time_management(games, "hrac", cache, "both")
    n_in_bins = sum(st["n"] for _, st in res["by_bin"])
    assert n_in_bins == res["overall"]["n"]
