"""Rozbor jednoho hráče napříč načtenými partiemi.

- heatmapa polí, kam (nebo odkud) táhnou figury vybraného hráče,
- strom tahů od počáteční pozice s úspěšností podle výsledku partie.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import chess

from stats_util import shrink_rate

Colors = str  # "white" | "black" | "both"
Mode = str    # "to" | "from"


# --------------------------------------------------------------------- pomocné
def collect_players(games) -> Counter:
    """Jména hráčů -> počet partií, ve kterých se objevují."""
    c: Counter = Counter()
    for g in games:
        for key in ("White", "Black"):
            name = (g.headers.get(key) or "").strip()
            if name and name != "?":
                c[name] += 1
    return c


def _is_white(game, player: str) -> bool:
    return (game.headers.get("White") or "").strip() == player


def _is_black(game, player: str) -> bool:
    return (game.headers.get("Black") or "").strip() == player


def game_matches(game, player: str, colors: Colors) -> bool:
    if colors == "white":
        return _is_white(game, player)
    if colors == "black":
        return _is_black(game, player)
    return _is_white(game, player) or _is_black(game, player)


def player_result(game, player: str) -> str | None:
    """'win' / 'draw' / 'loss' z pohledu hráče, nebo None (nerozhodnuto / *)."""
    res = (game.headers.get("Result") or "*").strip()
    white = _is_white(game, player)
    if res == "1-0":
        return "win" if white else "loss"
    if res == "0-1":
        return "loss" if white else "win"
    if res in ("1/2-1/2", "1/2", "½-½"):
        return "draw"
    return None


# --------------------------------------------------------------------- heatmapa
def heatmap_counts(games, player: str, colors: Colors,
                   piece_type: int | None = None, mode: Mode = "to",
                   keep=None, who: str = "player", phase: str = "all",
                   outcome: str = "all") -> tuple[list[int], int]:
    """Vrátí (pole[64] s počty, celkový počet započítaných tahů).

    - ``who``: ``"player"`` počítá tahy hráče, ``"opponent"`` tahy soupeře,
    - ``mode``: ``"to"`` cílové pole, ``"from"`` výchozí pole, ``"capture"`` jen
      braní (cílové pole),
    - ``phase``: ``"all"`` / ``"opening"`` (tah ≤ 15) / ``"middle"`` (16–40) /
      ``"endgame"`` (41+),
    - ``outcome``: ``"all"`` / ``"win"`` / ``"loss"`` – filtr celých partií
      podle výsledku hráče.
    """
    counts = [0] * 64
    total = 0
    need_board = piece_type is not None or mode == "capture"
    for g in games:
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        if outcome in ("win", "loss") and player_result(g, player) != outcome:
            continue
        player_parity = 0 if _is_white(g, player) else 1
        parity = player_parity if who == "player" else 1 - player_parity
        board = g.start_board() if need_board else None
        for k, mv in enumerate(g.moves):
            if k % 2 == parity:
                move_no = k // 2 + 1
                in_phase = (phase == "all"
                            or (phase == "opening" and move_no <= 15)
                            or (phase == "middle" and 16 <= move_no <= 40)
                            or (phase == "endgame" and move_no >= 41))
                ok = in_phase
                if ok and board is not None:
                    if mode == "capture" and not board.is_capture(mv):
                        ok = False
                    if ok and piece_type is not None:
                        pc = board.piece_at(mv.from_square)
                        ok = pc is not None and pc.piece_type == piece_type
                if ok:
                    counts[mv.from_square if mode == "from" else mv.to_square] += 1
                    total += 1
            if board is not None:
                board.push(mv)
    return counts, total


# ------------------------------------------------------------------ strom tahů
@dataclass
class TreeNode:
    move_uci: str = ""
    move_san: str = ""
    games: int = 0
    win: int = 0
    draw: int = 0
    loss: int = 0
    unknown: int = 0
    children: dict[str, "TreeNode"] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        return self.win + self.draw + self.loss

    @property
    def score(self) -> float | None:
        d = self.decided
        return (self.win + 0.5 * self.draw) / d if d else None

    @property
    def winrate(self) -> float | None:
        d = self.decided
        return self.win / d if d else None

    def sorted_children(self) -> list["TreeNode"]:
        return sorted(self.children.values(), key=lambda n: (-n.games, -(n.score or 0)))

    def predicted_score(self, p0: float | None = None, shrink_m: float = 10.0) -> float | None:
        """Odhad skóre rekurzivně přes strom (empirický Markovův řetězec pozic):
        u listu / řídkého uzlu stažené (empirical-Bayes) skóre k celkovému
        průměru ``p0``, jinak partiemi vážený průměr predikcí dětí. Robustnější
        na řídce navštívených uzlech než syrový ``score``."""
        if p0 is None:
            p0 = self.score if self.score is not None else 0.5
        if not self.children:
            d = self.decided
            return shrink_rate(self.win + 0.5 * self.draw, d, p0, shrink_m) if d else p0
        total = sum(c.games for c in self.children.values())
        if total == 0:
            return p0
        agg = sum(c.games * (c.predicted_score(p0, shrink_m) or p0)
                 for c in self.children.values())
        return agg / total


def _apply(node: TreeNode, result: str | None) -> None:
    if result == "win":
        node.win += 1
    elif result == "draw":
        node.draw += 1
    elif result == "loss":
        node.loss += 1
    else:
        node.unknown += 1


def board_key(board: chess.Board):
    """Klíč pozice (rozestavění + strana + rošády + e.p.) – pro hledání transpozic."""
    return board._transposition_key()


def build_position_tree(games, player: str, colors: Colors,
                        target: chess.Board, max_depth: int = 8, keep=None) -> TreeNode:
    """Strom pokračování z pozice ``target`` napříč partiemi vybraného hráče.

    U každého tahu je počet partií a výsledková bilance z pohledu hráče.
    """
    key = board_key(target)
    root = TreeNode(move_san="(pozice na šachovnici)")
    for g in games:
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        try:
            start = g.keys.index(key)
        except ValueError:
            continue
        result = player_result(g, player)
        root.games += 1
        _apply(root, result)
        node = root
        end = min(start + max_depth, len(g.moves))
        sans = g.san_moves(end)
        for k in range(start, end):
            uci = g.moves[k].uci()
            child = node.children.get(uci)
            if child is None:
                child = TreeNode(move_uci=uci, move_san=sans[k])
                node.children[uci] = child
            child.games += 1
            _apply(child, result)
            node = child
    return root
