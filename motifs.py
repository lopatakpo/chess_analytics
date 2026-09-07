"""Rozpoznání taktických motivů jednoho tahu – čistě z pozice, bez enginu.

Spolehlivě jde poznat: mat, vidlička, vazba, napíchnutí, objevený útok/šach,
dvojšach, proměna, oběť, mat na první řadě. Motivy, co potřebují hledání do
hloubky (odlákání, blokáda, mezitah), se sem nedávají.
"""
from __future__ import annotations

import chess

_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}

MOTIF_LABEL = {
    "mate": "mat",
    "back_rank": "mat na první řadě",
    "fork": "vidlička",
    "double_attack": "dvojitý útok",
    "pin": "vazba",
    "skewer": "napíchnutí",
    "discovered": "objevený útok",
    "double_check": "dvojšach",
    "promotion": "proměna",
    "sacrifice": "oběť",
}
# pořadí pro zobrazení (od „nej" motivu)
MOTIF_ORDER = ["mate", "back_rank", "sacrifice", "fork", "double_attack",
               "discovered", "double_check", "pin", "skewer", "promotion"]


def _slider_dirs(pt: int):
    if pt == chess.BISHOP:
        return [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    if pt == chess.ROOK:
        return [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if pt == chess.QUEEN:
        return [(1, 1), (1, -1), (-1, 1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)]
    return []


def _ray_pieces(board: chess.Board, frm: int, df: int, dr: int):
    """Figury v cestě paprsku z ``frm`` směrem (df,dr), v pořadí od frm."""
    f, r = chess.square_file(frm), chess.square_rank(frm)
    out = []
    while True:
        f += df
        r += dr
        if not (0 <= f < 8 and 0 <= r < 8):
            break
        sq = chess.square(f, r)
        pc = board.piece_at(sq)
        if pc is not None:
            out.append((sq, pc))
    return out


def _pin_or_skewer(board: chess.Board, slider_sq: int, mover: chess.Color):
    """Slider ``mover`` na ``slider_sq``. Na jeho paprscích: první soupeřova
    figura A, za ní druhá soupeřova figura B (nic našeho mezi).
    A levnější / B je král → 'pin'; A dražší → 'skewer'."""
    pt = board.piece_type_at(slider_sq)
    result = None
    for df, dr in _slider_dirs(pt):
        chain = _ray_pieces(board, slider_sq, df, dr)
        if len(chain) < 2:
            continue
        (_sqa, pa), (_sqb, pb) = chain[0], chain[1]
        if pa.color == mover or pb.color == mover:
            continue
        va, vb = _VALUE[pa.piece_type], _VALUE[pb.piece_type]
        if pb.piece_type == chess.KING or va < vb:
            return "pin"
        if va > vb:
            result = "skewer"
    return result


def _attacked_targets(board: chess.Board, frm: int, mover: chess.Color):
    """Soupeřovy figury (mimo pěšce), na které z ``frm`` útočí figura ``mover``."""
    out = []
    for sq in board.attacks(frm):
        pc = board.piece_at(sq)
        if pc is not None and pc.color != mover and pc.piece_type != chess.PAWN:
            out.append(pc.piece_type)
    return out


def detect_motifs(board_before: chess.Board, move: chess.Move,
                  is_sacrifice: bool = False) -> list[str]:
    mover = board_before.turn
    opp = not mover
    b = board_before.copy(stack=False)
    b.push(move)
    found: set[str] = set()

    if move.promotion:
        found.add("promotion")
    if is_sacrifice:
        found.add("sacrifice")

    if b.is_checkmate():
        found.add("mate")
        ksq = b.king(opp)
        if ksq is not None:
            back = 0 if opp == chess.WHITE else 7
            if chess.square_rank(ksq) == back:
                # král zabarikádovaný – bez únikového pole na sousední řadě
                found.add("back_rank")

    if b.is_check():
        checkers = b.checkers()
        if len(checkers) >= 2:
            found.add("double_check")
        # objevený šach: šachuje figura, kterou jsme netáhli
        if any(sq != move.to_square for sq in checkers):
            found.add("discovered")

    # vidlička / dvojitý útok táhnutou figurou
    moved_pt = b.piece_type_at(move.to_square)
    if moved_pt:
        tgts = _attacked_targets(b, move.to_square, mover)
        # cíle bránící se → počítáme jen ty, co po braní nejsou hned zpět sebratelné
        strong = [t for t in tgts if t in (chess.ROOK, chess.QUEEN)] or tgts
        if len(tgts) >= 2:
            found.add("fork" if moved_pt in (chess.KNIGHT, chess.PAWN, chess.KING)
                      else "double_attack")
        elif len(tgts) == 1 and b.is_check() and move.to_square not in b.checkers():
            found.add("fork")   # šach + útok na figuru jinou figurou

    # vazba / napíchnutí táhnutou figurou (slider)
    if moved_pt in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        ps = _pin_or_skewer(b, move.to_square, mover)
        if ps:
            found.add(ps)

    # objevený útok (ne nutně šach): za výchozím polem stál náš slider, který
    # po odtažení figury „prohlédl" na soupeřovu figuru na opačném paprsku
    if "discovered" not in found:
        frm = move.from_square
        for df, dr in ((1, 1), (1, -1), (-1, 1), (0, 1), (1, 0)):
            fwd = _ray_pieces(b, frm, df, dr)
            bwd = _ray_pieces(b, frm, -df, -dr)
            if not fwd or not bwd:
                continue
            need = ({chess.BISHOP, chess.QUEEN} if df != 0 and dr != 0
                    else {chess.ROOK, chess.QUEEN})
            for near, far in ((fwd[0], bwd[0]), (bwd[0], fwd[0])):
                (sq_n, pn), (sq_f, pf) = near, far
                if (pn.color == mover and pn.piece_type in need
                        and pf.color == opp and pf.piece_type not in (chess.PAWN, chess.KING)
                        and not board_before.is_attacked_by(mover, sq_f)):
                    found.add("discovered")
                    break
            if "discovered" in found:
                break

    return [m for m in MOTIF_ORDER if m in found]


def motif_labels(keys) -> str:
    return ", ".join(MOTIF_LABEL.get(k, k) for k in keys)
