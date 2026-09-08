"""Testy streamovaného čtení PGN i z komprimovaných souborů (``pgn_stream``)."""
import gzip
import io

import pytest

from pgn_stream import iter_game_texts, open_text, quick_headers

_G1 = ('[Event "Rated Blitz game"]\n[White "a"]\n[Black "b"]\n'
       '[WhiteElo "2001"]\n[BlackElo "1950"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 1-0\n')
_G2 = ('[Event "Rated Rapid game"]\n[White "c"]\n[Black "d"]\n[Result "1/2-1/2"]\n\n'
       '1. d4 d5 1/2-1/2\n')
_PGN = _G1 + "\n" + _G2 + "\n"


def test_iter_game_texts_splits():
    games = list(iter_game_texts(io.StringIO(_PGN)))
    assert len(games) == 2
    assert games[0].startswith('[Event "Rated Blitz')
    assert "1. e4 e5" in games[0]
    assert '[Event "Rated Rapid' in games[1]


def test_iter_game_texts_single_and_empty():
    assert list(iter_game_texts(io.StringIO(_G1))) == [_G1]
    assert list(iter_game_texts(io.StringIO(""))) == []
    assert list(iter_game_texts(io.StringIO("[Event \"x\"]\n[White \"a\"]\n"))) == []


def test_quick_headers():
    h = quick_headers(_G1)
    assert h["Event"] == "Rated Blitz game"
    assert h["WhiteElo"] == "2001"
    assert h["Result"] == "1-0"
    assert "e4" not in "".join(h.values())        # nezasahuje do tahů


def test_open_text_plain(tmp_path):
    p = tmp_path / "x.pgn"
    p.write_text(_PGN, encoding="utf-8")
    with open_text(str(p)) as fh:
        assert len(list(iter_game_texts(fh))) == 2


def test_open_text_gzip(tmp_path):
    p = tmp_path / "x.pgn.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(_PGN)
    with open_text(str(p)) as fh:
        assert len(list(iter_game_texts(fh))) == 2


def test_open_text_zst_roundtrip(tmp_path):
    zst = pytest.importorskip("compression.zstd", reason="zstd (Python 3.14+ / zstandard)")
    p = tmp_path / "x.pgn.zst"
    with zst.ZstdFile(str(p), "w") as fh:
        fh.write(_PGN.encode("utf-8"))
    with open_text(str(p)) as fh:
        games = list(iter_game_texts(fh))
    assert len(games) == 2
    assert quick_headers(games[0])["WhiteElo"] == "2001"
