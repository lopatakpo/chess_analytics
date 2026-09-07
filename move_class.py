"""Klasifikace tahů ve stylu chess.com (Nejlepší/Výborný/Dobrý/Kniha/Nepřesnost/
Chyba/Hrubka + Brilantní/Skvělý tah/Přehlédnutí) – alternativa k lichess stylu
(``accuracy.classify`` – ??/?/?!, používaný teď v ``game_analyzer.py``).

Prahy pro základní žebříček (0 / 0,02 / 0,05 / 0,10 / 0,20 ztráty v očekávaných
bodech) odpovídají tomu, co chess.com veřejně popisuje ve svém FAQ. Brilantní/
Skvělý tah/Přehlédnutí ale chess.com nemá zdokumentované číselně – tohle je
vlastní přiblížení podle jejich slovního popisu:

- **Skvělý tah** – kritická pozice (velký rozdíl nejlepšího a druhého nejlepšího
  tahu, tj. "jediná dobrá volba"), hráč ji našel. Chce multipv (``crit``
  parametr) – bez něj se nikdy nepozná.
- **Brilantní** – hráč obětoval materiál (jeho figura po tahu "visí" – soupeř
  ji může vzít levnější figurou nebo bez obránce), pozice předtím nebyla už
  beztak vyhraná a po oběti zůstala v pořádku.
- **Přehlédnutí** – soupeřův předchozí tah byl sám chyba/hrubka (nabídl
  příležitost) a hráč na ni nezareagoval o nic líp (jeho tah je dál chyba/hrubka).

Na rozdíl od lichess stylu (kde se značí jen chyby, "dobrý" tah mlčí) dostane
značku KAŽDÝ tah mimo knihy – i Nejlepší/Výborný/Dobrý – stejně jako to dělá
chess.com; jen Kniha zůstává beze značky (je to kontext, ne kvalita tahu).
"""
from __future__ import annotations

import chess

from accuracy import per_move, win_series
from openings import classify_by_moves

# ------------------------------------------------------------- prahy (EP jednotky 0..1)
CCOM_BEST = 0.0
CCOM_EXCELLENT = 0.02
CCOM_GOOD = 0.05
CCOM_INACC = 0.10
CCOM_MIST = 0.20
# nad CCOM_MIST je hrubka

GREAT_CRIT = 0.30           # vyšší práh než Tactical Awareness (0.15) – "opravdu jediná cesta"
MISS_OPP_DROP = 0.10        # kolik EP musel soupeř před tím ztratit, ať je to "promarněná šance"
BRIL_MAX_WIN_BEFORE = 92.0  # % (POV hráče na tahu) – nad tím už bylo beztak vyhráno
BRIL_MIN_WIN_AFTER = 45.0   # % (POV hráče na tahu) – po oběti nesmí být zle
BRIL_MIN_NET_LOSS = 2.0     # pěšce – jak velká musí "oběť" reálně být (ne jen výměna)

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
               chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}

_KIND_SYMBOL = {
    "brilliant": "!!", "great": "!", "best": "✓✓", "excellent": "✓", "good": "·",
    "miss": "✗", "blunder": "??", "mistake": "?", "inaccuracy": "?!",
}
CC_MARK_KINDS = [
    ("!!", "brilantní", "#00897b", "#c2ece4"),
    ("!", "skvělý tah", "#1565c0", "#c8ddf5"),
    ("✓✓", "nejlepší", "#2e7d32", "#c8e6c9"),
    ("✓", "výborný", "#43a047", "#dcedc8"),
    ("·", "dobrý", "#689f38", "#e6f0d8"),
    ("✗", "přehlédnutí", "#8e24aa", "#e6cef2"),
    ("??", "hrubka", "#c62828", "#f2c6c6"),
    ("?", "chyba", "#e65100", "#f7d9b0"),
    ("?!", "nepřesnost", "#f9a825", "#f4eebb"),
]
CC_MARK_TEXT = {k: lbl for k, lbl, _, _ in CC_MARK_KINDS}
CC_MARK_FG = {k: fg for k, _, fg, _ in CC_MARK_KINDS}
CC_MARK_BG = {k: bg for k, _, _, bg in CC_MARK_KINDS}

# pořadí a popisky/barvy pro VŠECH 10 kategorií (i Kniha a "tiché" Nejlepší/
# Výborný/Dobrý) – pro koláčový graf rozložení tahů, ne pro seznam tahů
KIND_ORDER = ["brilliant", "great", "best", "excellent", "good", "book",
             "inaccuracy", "mistake", "blunder", "miss"]
KIND_LABEL = {
    "brilliant": "brilantní", "great": "skvělý tah", "best": "nejlepší",
    "excellent": "výborný", "good": "dobrý", "book": "kniha",
    "inaccuracy": "nepřesnost", "mistake": "chyba", "blunder": "hrubka",
    "miss": "přehlédnutí",
}
KIND_COLOR = {
    "brilliant": "#00897b", "great": "#1565c0", "best": "#2e7d32",
    "excellent": "#43a047", "good": "#689f38", "book": "#8d6e63",
    "inaccuracy": "#f9a825", "mistake": "#e65100", "blunder": "#c62828",
    "miss": "#8e24aa",
}


def _book_plies(moves_uci: list[str]) -> int:
    try:
        _, _, d = classify_by_moves(moves_uci)
        return min(max(int(d), 8), 24)
    except Exception:
        return 12


def _hanging_value(board_after: chess.Board, square: int, mover_white: bool):
    """Po tahu stojí na ``square`` hráčova figura, na tahu je soupeř. Vrátí
    čistou ztrátu (v pěšcích), pokud ji soupeř může vzít se ziskem, jinak None.

    Používá SKUTEČNĚ LEGÁLNÍ tahy (``board.legal_moves``), ne jen geometrický
    ``board.attackers()`` – ten by králem "viděl" i braní, které je ve
    skutečnosti nelegální (král by tahem na square vstoupil do šachu od
    odkryté/vazbou stažené figury). To je běžný tvar skutečné obětiny (dáma/věž
    obětovaná "na krytí" další figurou přes řadu/sloupec/diagonálu) – bez tyhle
    kontroly by se takové tahy mylně označily za "viset ve vzduchu"."""
    piece = board_after.piece_at(square)
    if piece is None or piece.piece_type == chess.KING or board_after.turn == mover_white:
        return None
    value = PIECE_VALUES[piece.piece_type]
    capturers = [mv for mv in board_after.legal_moves if mv.to_square == square]
    if not capturers:
        return None    # nic ho doopravdy legálně vzít nemůže – není to oběť
    best_net = 0.0
    for mv in capturers:
        attacker_val = PIECE_VALUES[board_after.piece_at(mv.from_square).piece_type]
        board2 = board_after.copy(stack=False)
        board2.push(mv)
        can_recapture = any(m2.to_square == square for m2 in board2.legal_moves)
        # hráč ztratí ``value`` (svou figuru); pokud smí legálně vzít zpátky,
        # získá aspoň ``attacker_val`` (soupeřovu figuru, co brala) – hlubší
        # řetězec výměn (další braní na tomtéž poli) tenhle odhad neřeší
        net = (value - attacker_val) if can_recapture else value
        best_net = max(best_net, net)
    return best_net if best_net > 0 else None


def _captured_value(board_before: chess.Board, move: chess.Move) -> float:
    if board_before.is_en_passant(move):
        return 1.0
    piece = board_before.piece_at(move.to_square)
    return float(PIECE_VALUES[piece.piece_type]) if piece is not None else 0.0


def _classify_kinds(evals: list[int], moves_uci: list[str], boards: list[chess.Board],
                    crit: list[float] | None = None, wdls=None) -> list[str]:
    """Za každý tah (index k = tah k, stejně jako ``accuracy.per_move``) vrátí
    RAW název kategorie ("book","best","excellent","good","inaccuracy",
    "mistake","blunder","miss","great","brilliant"). Sdílená logika pro
    ``classify_moves`` (seznam tahů – jen symboly) i ``count_kinds`` (koláčový
    graf – potřebuje i Kniha/Nejlepší/Výborný/Dobrý, co ``classify_moves``
    v seznamu tahů mlčí)."""
    mv = per_move(evals, wdls)
    ws = win_series(evals, wdls)   # POV bílého
    book_plies = _book_plies(moves_uci)
    out: list[str] = []
    for k, m in enumerate(mv):
        if k < book_plies:
            out.append("book")
            continue
        white = m["white"]
        ep_loss = m["ep_loss"]

        if ep_loss <= CCOM_BEST + 1e-9:
            kind = "best"
        elif ep_loss <= CCOM_EXCELLENT:
            kind = "excellent"
        elif ep_loss <= CCOM_GOOD:
            kind = "good"
        elif ep_loss <= CCOM_INACC:
            kind = "inaccuracy"
        elif ep_loss <= CCOM_MIST:
            kind = "mistake"
        else:
            kind = "blunder"

        if kind in ("mistake", "blunder") and k >= 1 and mv[k - 1]["white"] != white \
                and mv[k - 1]["ep_loss"] >= MISS_OPP_DROP:
            kind = "miss"
        elif kind in ("best", "excellent"):
            if crit is not None and k < len(crit) and crit[k] >= GREAT_CRIT:
                kind = "great"
            elif k + 1 < len(boards) and k < len(moves_uci):
                win_before = ws[k] if white else 100.0 - ws[k]
                win_after = ws[k + 1] if white else 100.0 - ws[k + 1]
                if win_before <= BRIL_MAX_WIN_BEFORE and win_after >= BRIL_MIN_WIN_AFTER:
                    move = chess.Move.from_uci(moves_uci[k])
                    hang_val = _hanging_value(boards[k + 1], move.to_square, white)
                    if hang_val is not None:
                        net_loss = hang_val - _captured_value(boards[k], move)
                        if net_loss >= BRIL_MIN_NET_LOSS:
                            kind = "brilliant"
        out.append(kind)
    return out


def classify_moves(evals: list[int], moves_uci: list[str], boards: list[chess.Board],
                   crit: list[float] | None = None, wdls=None) -> dict[int, str]:
    """Vrátí ``{ply (1..n): symbol}`` pro každý tah mimo knihy (Kniha zůstává
    beze značky, zbytek – i Nejlepší/Výborný/Dobrý – dostane symbol, stejně
    jako chess.com). ``boards[k]`` = pozice PŘED tahem k (0-indexed, stejná
    konvence jako ``accuracy_batch.py``). ``crit[k]`` (volitelně, POV hráče na
    tahu, z multipv) – bez něj se Skvělé tahy nikdy nepoznají, zbytek funguje
    i tak."""
    kinds = _classify_kinds(evals, moves_uci, boards, crit, wdls)
    out: dict[int, str] = {}
    for k, kind in enumerate(kinds):
        symbol = _KIND_SYMBOL.get(kind)
        if symbol:
            out[k + 1] = symbol
    return out


def brilliant_plies(evals: list[int], moves_uci: list[str], boards: list[chess.Board],
                    crit: list[float] | None = None, wdls=None) -> list[int]:
    """Indexy tahů (0-based, konvence jako ``per_move`` / ``accuracy_batch``)
    klasifikovaných jako **brilantní** (oběť materiálu ve zdravé pozici)."""
    return [k for k, kind in enumerate(_classify_kinds(evals, moves_uci, boards, crit, wdls))
            if kind == "brilliant"]


def count_kinds(evals: list[int], moves_uci: list[str], boards: list[chess.Board],
                crit: list[float] | None = None, wdls=None) -> dict[str, int]:
    """Počty tahů (obě strany dohromady) po kategoriích – VČETNĚ Kniha/Nejlepší/
    Výborný/Dobrý (na rozdíl od ``classify_moves``, co je v seznamu tahů mlčí).
    Pro koláčový graf rozložení tahů podle kategorie."""
    counts: dict[str, int] = {}
    for kind in _classify_kinds(evals, moves_uci, boards, crit, wdls):
        counts[kind] = counts.get(kind, 0) + 1
    return counts
