"""Testy načtení PGN (``pgn_game``) – hlavně zachytávání hodin ``[%clk]``."""
from pgn_game import load_pgn_text

_PGN_WITH_CLOCKS = """[Event "Rated Blitz game"]
[White "a"]
[Black "b"]
[TimeControl "180+2"]
[Result "1-0"]

1. e4 { [%clk 0:03:02] } 1... e5 { [%clk 0:03:01] } 2. Nf3 { [%clk 0:02:58] }
2... Nc6 { [%clk 0:02:55] } 1-0

"""

_PGN_NO_CLOCKS = """[Event "Casual game"]
[White "a"]
[Black "b"]
[Result "1/2-1/2"]

1. d4 d5 1/2-1/2

"""

_PGN_WITH_HUMAN_COMMENT_AND_CLOCK = """[Event "Rated Blitz game"]
[White "a"]
[Black "b"]
[TimeControl "180+2"]
[Result "1-0"]

1. e4 { good move! [%clk 0:03:00] } 1... e5 1-0

"""


def test_clocks_captured():
    g = load_pgn_text(_PGN_WITH_CLOCKS)[0]
    assert g.has_clocks() is True
    assert g.clocks == [182.0, 181.0, 178.0, 175.0]
    assert len(g.clocks) == len(g.moves)


def test_no_clocks():
    g = load_pgn_text(_PGN_NO_CLOCKS)[0]
    assert g.has_clocks() is False
    assert g.clocks == [None, None]


def test_clock_and_human_comment_coexist():
    g = load_pgn_text(_PGN_WITH_HUMAN_COMMENT_AND_CLOCK)[0]
    assert g.has_clocks() is True
    assert g.clocks[0] == 180.0
    # lidský komentář zůstává (bez [%clk] anotace), stejně jako dřív
    assert "good move" in g.comment_at(1)
    assert "[%clk" not in g.comment_at(1)


def test_multiple_games_independent_clocks():
    games = load_pgn_text(_PGN_WITH_CLOCKS + "\n" + _PGN_NO_CLOCKS)
    assert len(games) == 2
    assert games[0].has_clocks() is True
    assert games[1].has_clocks() is False
