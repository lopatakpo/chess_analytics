"""Rozpoznání a podrobná kategorizace koncovek napříč partiemi.

Definice koncovky (stačí jedna):
- Speelmanovo pravidlo 13 bodů: každá strana má bez krále nejvýše 13 bodů
  materiálu (D=9, V=5, S/J=3, P=1).
- Minevovo pravidlo 4 figur: na šachovnici jsou nejvýše 4 figury mimo krále
  a pěšce.

Kategorizují se pozice s rozdílem materiálu nejvýše 4 body; výjimkou jsou
pěšcové koncovky (jen král a pěšci) – ty se počítají vždy, bez omezení na počet
pěšců i na rozdíl materiálu.
Jedna partie může projít více kategoriemi (koncovky do sebe přecházejí).

Kategorie = pojmenovaný typ koncovky podle přítomných druhů figur (věžová,
jezdcová, střelcová stejné/opačné barvy, střelec proti jezdci, věž a střelec,
dáma a jezdec, …) + přesný soupis figur obou stran (kolik věží / dam / střelců /
jezdců). Je nezávislá na barvě hráče i na počtu pěšců – silnější strana (podle
hodnoty figur) je v zápisu první, takže stejné složení figur dá vždy jednu
kategorii. Rozbor se dělá vždy pro obě barvy hráče. Uvnitř kategorie jsou partie
rozděleny podle počtu pěšců ve chvíli, kdy do kategorie poprvé vstoupily – každá
partie je v kategorii započítaná právě jednou, takže součet přes počty pěšců
odpovídá počtu partií kategorie.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import chess

from player_analysis import game_matches, player_result


def sort_key(text: str) -> str:
    """Klíč pro české abecední řazení (složí diakritiku, malá písmena)."""
    return unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
MAX_MATERIAL_DIFF = 4
PIECE_LEGEND = "V = věž, D = dáma, S = střelec, J = jezdec; víc figur = silnější strana první"


# --------------------------------------------------------------- materiál
def _piece_counts(board: chess.Board) -> tuple:
    """(wp,bp, wn,bn, wb,bb, wr,br, wq,bq) přes bitboardy – rychlé."""
    w = board.occupied_co[chess.WHITE]
    b = board.occupied_co[chess.BLACK]
    pc = chess.popcount
    return (pc(board.pawns & w), pc(board.pawns & b),
            pc(board.knights & w), pc(board.knights & b),
            pc(board.bishops & w), pc(board.bishops & b),
            pc(board.rooks & w), pc(board.rooks & b),
            pc(board.queens & w), pc(board.queens & b))


def _side_values(c) -> tuple[int, int]:
    wp, bp, wn, bn, wb, bb, wr, br, wq, bq = c
    return (wp + 3 * (wn + wb) + 5 * wr + 9 * wq,
            bp + 3 * (bn + bb) + 5 * br + 9 * bq)


def side_material(board: chess.Board, color: chess.Color) -> int:
    wv, bv = _side_values(_piece_counts(board))
    return wv if color == chess.WHITE else bv


def material_diff(board: chess.Board) -> int:
    wv, bv = _side_values(_piece_counts(board))
    return abs(wv - bv)


def pawn_count(board: chess.Board) -> int:
    return chess.popcount(board.pawns)


def is_pawn_only(board: chess.Board) -> bool:
    """Na šachovnici jsou jen králové a pěšci."""
    return (board.knights | board.bishops | board.rooks | board.queens) == 0


def _table_ok(c) -> bool:
    wp, bp, wn, bn, wb, bb, wr, br, wq, bq = c
    pieces = wn + bn + wb + bb + wr + br + wq + bq
    if pieces == 0:
        return True                          # pěšcové koncovky vždy
    wv, bv = _side_values(c)
    speelman = wv <= 13 and bv <= 13
    minev = pieces <= 4 and not (wp >= 7 and bp >= 7)
    return (speelman or minev) and abs(wv - bv) <= MAX_MATERIAL_DIFF


def is_endgame(board: chess.Board) -> bool:
    c = _piece_counts(board)
    wv, bv = _side_values(c)
    pieces = sum(c[2:])
    return (wv <= 13 and bv <= 13) or (pieces <= 4 and not (c[0] >= 7 and c[1] >= 7))


def counts_for_table(board: chess.Board) -> bool:
    """Zařadí se pozice do tabulky koncovek? Viz `_table_ok`."""
    return _table_ok(_piece_counts(board))


# --------------------------------------------------------------- kategorie
def _sq_color(square: int) -> int:
    return (chess.square_rank(square) + chess.square_file(square)) % 2


def _sig(q: int, r: int, b: int, n: int) -> str:
    """Kompaktní zápis figur strany (mimo pěšce), pořadí D, V, S, J – např. '2V+S'."""
    parts = []
    for k, ch in ((q, "D"), (r, "V"), (b, "S"), (n, "J")):
        if k == 1:
            parts.append(ch)
        elif k >= 2:
            parts.append(f"{k}{ch}")
    return "+".join(parts) if parts else "—"


# název typu podle přítomných druhů figur (D, V, S, J) kdekoli na šachovnici
_TYPE_NAMES = {
    (0, 1, 0, 0): "Věžová koncovka",
    (1, 0, 0, 0): "Dámská koncovka",
    (0, 0, 1, 0): "Střelcová koncovka",
    (0, 0, 0, 1): "Jezdcová koncovka",
    (0, 0, 1, 1): "Lehké figury",
    (1, 1, 0, 0): "Těžké figury (dáma a věž)",
    (0, 1, 1, 0): "Věž a střelec",
    (0, 1, 0, 1): "Věž a jezdec",
    (0, 1, 1, 1): "Věž a lehké figury",
    (1, 0, 1, 0): "Dáma a střelec",
    (1, 0, 0, 1): "Dáma a jezdec",
    (1, 0, 1, 1): "Dáma a lehké figury",
    (1, 1, 1, 0): "Dáma, věž a střelec",
    (1, 1, 0, 1): "Dáma, věž a jezdec",
    (1, 1, 1, 1): "Dáma, věž a lehké figury",
}


def pawns_cz(n: int) -> str:
    if n == 0:
        return "bez pěšců"
    if n == 1:
        return "1 pěšec"
    if 2 <= n <= 4:
        return f"{n} pěšci"
    return f"{n} pěšců"


def position_category(board: chess.Board, counts: tuple | None = None) -> str:
    """Kategorie koncovky – nezávislá na barvě hráče i na počtu pěšců.

    Typ koncovky podle přítomných druhů figur + přesný soupis figur obou stran
    (kolik věží / dam / střelců / jezdců), silnější strana (podle hodnoty figur)
    v zápisu první. Stejné složení figur → vždy jedna kategorie.
    """
    wp, bp, wn, bn, wb, bb, wr, br, wq, bq = counts or _piece_counts(board)
    pawns = wp + bp
    n, b, r, q = wn + bn, wb + bb, wr + br, wq + bq
    minors = n + b

    if q == 0 and r == 0 and minors == 0:
        return "Holí králové (K vs K)" if pawns == 0 else "Pěšcová koncovka"

    # střelcová koncovka 1 vs 1 – barva střelců (přípona by nic nepřidala)
    if q == 0 and r == 0 and n == 0 and wb == 1 and bb == 1:
        same = (_sq_color(chess.lsb(board.bishops & board.occupied_co[chess.WHITE]))
                == _sq_color(chess.lsb(board.bishops & board.occupied_co[chess.BLACK])))
        return ("Střelcová koncovka – střelci stejné barvy" if same
                else "Střelcová koncovka – střelci opačné barvy")

    # střelec proti jezdci (1 + 1) – figury jsou vždy S vs J
    if q == 0 and r == 0 and minors == 2 and ((wb == 1 and bn == 1) or (wn == 1 and bb == 1)):
        return "Střelec proti jezdci"

    name = _TYPE_NAMES.get((int(q > 0), int(r > 0), int(b > 0), int(n > 0)), "Smíšená koncovka")

    sw, sb = _sig(wq, wr, wb, wn), _sig(bq, br, bb, bn)
    fw = wq * 9 + wr * 5 + wb * 3 + wn * 3
    fb = bq * 9 + br * 5 + bb * 3 + bn * 3

    def _rank(sig: str, val: int) -> tuple:
        return (val, 0 if sig == "—" else 1, sig)

    a, b_ = (sw, sb) if _rank(sw, fw) >= _rank(sb, fb) else (sb, sw)
    return f"{name}: {a} vs {b_}"


# ---------------------------------------------------------------- analýza
@dataclass
class EGEntry:
    game_index: int
    enter_ply: int       # půltah, kterým partie do kategorie vstoupila
    pawns: int           # počet pěšců při vstupu do kategorie
    result: str | None   # "win" / "draw" / "loss" / None (z pohledu hráče)


def _wdl(results) -> tuple[int, int, int]:
    return (sum(r == "win" for r in results),
            sum(r == "draw" for r in results),
            sum(r == "loss" for r in results))


def _score(results) -> float | None:
    w, d, lo = _wdl(results)
    dec = w + d + lo
    return (w + 0.5 * d) / dec if dec else None


@dataclass
class EGCategory:
    label: str
    entries: list[EGEntry] = field(default_factory=list)  # právě jedna na partii

    def wdl(self, subset: list[EGEntry] | None = None) -> tuple[int, int, int]:
        items = self.entries if subset is None else subset
        return _wdl([e.result for e in items])

    def score(self, subset: list[EGEntry] | None = None) -> float | None:
        items = self.entries if subset is None else subset
        return _score([e.result for e in items])

    def by_pawns(self) -> list[tuple[int, list[EGEntry]]]:
        groups: dict[int, list[EGEntry]] = {}
        for e in self.entries:
            groups.setdefault(e.pawns, []).append(e)
        return sorted(groups.items())


def analyze_endgames(games, player: str, keep=None) -> list[EGCategory]:
    """Rozbor koncovek hráče napříč všemi jeho partiemi (bílé i černé).

    Každá partie je v kategorii započítaná právě jednou – zařadí se podle počtu
    pěšců ve chvíli, kdy do té kategorie poprvé vstoupila. Součet partií
    v podřádcích podle počtu pěšců proto odpovídá počtu partií kategorie.
    """
    cats: dict[str, EGCategory] = {}
    for gi, g in enumerate(games):
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, "both"):
            continue
        result = player_result(g, player)
        seen: set[str] = set()
        board = g.start_board()          # jedna šachovnice, bez kopií na každý půltah
        moves = g.moves
        for ply in range(len(moves) + 1):
            if ply:
                board.push(moves[ply - 1])
            c = _piece_counts(board)
            if not _table_ok(c):
                continue
            label = position_category(board, c)
            if label in seen:
                continue
            seen.add(label)
            cats.setdefault(label, EGCategory(label)).entries.append(
                EGEntry(gi, ply, c[0] + c[1], result))
    return sorted(cats.values(), key=lambda c: sort_key(c.label))
