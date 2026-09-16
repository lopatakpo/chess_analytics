"""Práce s časem na hodinách z PGN anotace ``[%clk]`` – čistá matematika,
bez enginu. Používá karta *Časový management*.
"""
from __future__ import annotations

import chess


def parse_time_control(tc: str | None) -> tuple[float, float] | None:
    """PGN hlavička ``TimeControl`` (\"initial+increment\" ve vteřinách, např.
    \"180+2\") -> (initial, increment). ``None`` pro chybějící / nerozpoznatelné
    / korespondenční (\"-\", \"1/86400\" apod.)."""
    if not tc or tc in ("-", "?"):
        return None
    if "+" in tc:
        a, b = tc.split("+", 1)
        try:
            return float(a), float(b)
        except ValueError:
            return None
    try:
        return float(tc), 0.0
    except ValueError:
        return None


def move_seconds(clocks: list[float | None], initial: float,
                 increment: float) -> list[float | None]:
    """Kolik vteřin partie strávila na každém půltahu, ze zbývajících časů PO
    tahu (``[%clk]``). Vteřiny na tah k = (čas před tahem) + přírůstek −
    (čas po tahu); čas před prvním tahem = počáteční čas na hodinách. Záporný
    výsledek (drift v zaokrouhlení anotace) se ořízne na 0."""
    out: list[float | None] = []
    prev: float | None = initial
    for c in clocks:
        if c is None or prev is None:
            out.append(None)
            prev = c
            continue
        out.append(max(0.0, prev + increment - c))
        prev = c
    return out


# koše délky přemýšlení (vteřiny) – necháno stejné pro všechna tempa (appka
# ukazuje i rozpad podle tempa zvlášť, takže se dá porovnat "5s v bulletu" vs.
# "5s v rapidu" v kontextu vlastního koše podle tempa)
TIME_BINS = [(0.0, 2.0), (2.0, 5.0), (5.0, 15.0), (15.0, 30.0), (30.0, 60.0),
            (60.0, float("inf"))]
_BIN_LABELS = ["< 2 s", "2–5 s", "5–15 s", "15–30 s", "30–60 s", "60 s a víc"]


def time_bin(seconds: float) -> int:
    for i, (lo, hi) in enumerate(TIME_BINS):
        if lo <= seconds < hi:
            return i
    return len(TIME_BINS) - 1


def bin_label(i: int) -> str:
    return _BIN_LABELS[i]


PIECE_LABEL = {chess.PAWN: "pěšec", chess.KNIGHT: "jezdec", chess.BISHOP: "střelec",
              chess.ROOK: "věž", chess.QUEEN: "dáma", chess.KING: "král"}
PIECE_ORDER = [chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING]
