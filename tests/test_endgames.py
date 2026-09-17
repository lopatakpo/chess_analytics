"""Testy kategorizace koncovek (``endgames``) – hlavně regresní test na chybu,
co nahlásil uživatel: „jsem si jistý, že jsem hrál víc než jednu koncovku věž
proti věži s jedním pěšcem" – appka je ale skoro všechny schovala pod počet
pěšců z PRVNÍHO vstupu do kategorie (skoro vždy 8–14, kolik jich bylo, když
zmizely dámy/lehké figury), ne pod nejhlubší/nejčistší stav, kterého partie
v tý kategorii doopravdy dosáhla."""
import chess
import pytest

from endgames import (
    MAX_MATERIAL_DIFF, _piece_counts, _table_ok, analyze_endgames,
    position_category,
)
from pgn_game import LoadedGame

_RESULT_HDR = {"win": "1-0", "loss": "0-1", "draw": "1/2-1/2"}


def _game(fen: str, uci_moves: list[str], result: str = "win",
         white: str = "hrac", black: str = "souper") -> LoadedGame:
    headers = {"FEN": fen, "SetUp": "1", "White": white, "Black": black,
              "Result": _RESULT_HDR[result]}
    moves = [chess.Move.from_uci(u) for u in uci_moves]
    return LoadedGame(headers, moves)


# --------------------------------------------------------------- position_category
def test_position_category_rook_vs_rook_nezavisi_na_poctu_pescu():
    many = chess.Board("r3k3/pppppp2/8/8/8/8/PPPPPP2/R3K3 w - - 0 1")
    none = chess.Board("r3k3/8/8/8/8/8/8/R3K3 w - - 0 1")
    assert position_category(many) == "Věžová koncovka: V vs V"
    assert position_category(none) == "Věžová koncovka: V vs V"


def test_position_category_pawn_only():
    b = chess.Board("4k3/2p5/8/8/8/8/2P5/4K3 w - - 0 1")
    assert position_category(b) == "Pěšcová koncovka"
    bare = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    assert position_category(bare) == "Holí králové (K vs K)"


def test_table_ok_respektuje_max_material_diff():
    # věž + 5 pěšců navíc (rozdíl 5 bodů, obě strany mají věž) je nad MAX_MATERIAL_DIFF (4)
    b = chess.Board("r3k3/8/8/8/8/8/PPPPP3/R3K3 w - - 0 1")
    c = _piece_counts(b)
    assert not _table_ok(c)
    b2 = chess.Board("r3k3/8/8/8/8/8/PPPP4/R3K3 w - - 0 1")   # rozdíl 4 – ještě OK
    assert _table_ok(_piece_counts(b2))
    assert MAX_MATERIAL_DIFF == 4


# --------------------------------------------------------------- analyze_endgames
def test_analyze_endgames_pouzije_nejmene_pescu_ne_prvni_vstup():
    """Regresní test hlášeného problému: partie vstoupí do „V vs V" se 2 pěšci,
    o tah později (pořád stejná kategorie, věže se nemění) se sníží na 1 pěšce
    zachycenou výměnou. Appka musí zaznamenat pawns=1 (nejhlubší stav), NE
    pawns=2 (stav při prvním vstupu)."""
    fen = "r3k3/8/2p5/1P6/8/8/8/R3K3 w - - 0 1"   # bílý pěšec b5 x černý pěšec c6
    g = _game(fen, ["b5c6"])
    cats = analyze_endgames([g], "hrac")
    rook_cat = next(c for c in cats if c.label == "Věžová koncovka: V vs V")
    assert len(rook_cat.entries) == 1              # partie počítaná právě jednou
    e = rook_cat.entries[0]
    assert e.pawns == 1                             # NE 2 (stav při prvním vstupu)
    assert e.enter_ply == 1                          # NE 0
    assert e.result == "win"


def test_analyze_endgames_klesajici_pocet_pescu_pres_vice_tahu():
    # bxa6 (bílý), pak exd5 (černý) – dva po sobě jdoucí záběry, pořád stejná
    # kategorie (věže se nemění); pěšci klesají 4 → 3 → 2
    fen = "r3k3/8/p3p3/1P1P4/8/8/8/R3K3 w - - 0 1"
    g = _game(fen, ["b5a6", "e6d5"], result="draw")
    cats = analyze_endgames([g], "hrac")
    rook_cat = next(c for c in cats if c.label == "Věžová koncovka: V vs V")
    assert len(rook_cat.entries) == 1
    e = rook_cat.entries[0]
    # nejnižší dosažený počet pěšců (2, po obou záběrech), ne počet na začátku (4)
    assert e.pawns == 2
    assert e.enter_ply == 2


def test_analyze_endgames_kazda_partie_jednou_na_kategorii():
    fen = "r3k3/8/2p5/1P6/8/8/8/R3K3 w - - 0 1"
    g1 = _game(fen, ["b5c6"], result="win")
    g2 = _game(fen, ["b5c6"], result="loss")
    cats = analyze_endgames([g1, g2], "hrac")
    rook_cat = next(c for c in cats if c.label == "Věžová koncovka: V vs V")
    assert len(rook_cat.entries) == 2
    assert {e.result for e in rook_cat.entries} == {"win", "loss"}


def test_analyze_endgames_by_pawns_soucet_odpovida_poctu_partii():
    fen = "r3k3/8/2p5/1P6/8/8/8/R3K3 w - - 0 1"
    games = [_game(fen, ["b5c6"], result="win") for _ in range(5)]
    cats = analyze_endgames(games, "hrac")
    rook_cat = next(c for c in cats if c.label == "Věžová koncovka: V vs V")
    total_in_pawns = sum(len(entries) for _, entries in rook_cat.by_pawns())
    assert total_in_pawns == len(rook_cat.entries) == 5
