"""Načtení PGN (jen hlavní linie) s líným dopočítáváním pozic, SAN a klíčů.

Při načtení se z každé partie uloží jen hlavičky a seznam tahů – šachovnice pro
každý půltah, texty SAN a klíče pozic se dopočítají teprve když je někdo potřebuje
(zobrazení seznamu tahů, navigace, rozbory). To výrazně zrychluje otevření
velkých databází.
"""
from __future__ import annotations

import io
import re

import chess
import chess.pgn

_ANNOT_RE = re.compile(r"\[%[^\]]*\]")  # {[%clk ...]}, {[%eval ...]} apod.


class LoadedGame:
    __slots__ = ("headers", "moves", "_comment_map", "_boards", "_keys", "_sans")

    def __init__(self, headers: dict, moves: list[chess.Move],
                 comment_map: dict[int, str] | None = None) -> None:
        self.headers = dict(headers)
        self.moves: list[chess.Move] = list(moves)
        self._comment_map: dict[int, str] = dict(comment_map or {})
        self._boards: list[chess.Board] | None = None
        self._keys: list | None = None
        self._sans: list[str] | None = None

    # ---------------------------------------------------------------- základ
    @property
    def ply_count(self) -> int:
        return len(self.moves)

    def start_board(self) -> chess.Board:
        fen = self.headers.get("FEN")
        if fen and self.headers.get("SetUp", "1") != "0":
            try:
                return chess.Board(fen)
            except ValueError:
                pass
        return chess.Board()

    # ------------------------------------------------------ líné dopočítání
    def _ensure_boards(self) -> None:
        if self._boards is not None:
            return
        b = self.start_board()
        boards = [b.copy(stack=False)]
        for m in self.moves:
            b.push(m)
            boards.append(b.copy(stack=False))
        self._boards = boards

    def _ensure_keys(self) -> None:
        if self._keys is not None:
            return
        b = self.start_board()
        keys = [b._transposition_key()]
        for m in self.moves:
            b.push(m)
            keys.append(b._transposition_key())
        self._keys = keys

    @property
    def boards(self) -> list[chess.Board]:
        self._ensure_boards()
        return self._boards

    @property
    def keys(self) -> list:
        self._ensure_keys()
        return self._keys

    @property
    def moves_san(self) -> list[str]:
        return self.san_moves()

    def san_moves(self, limit: int | None = None) -> list[str]:
        """SAN tahů; s ``limit`` spočítá jen prvních N (a necachuje celek)."""
        if self._sans is not None:
            return self._sans if limit is None else self._sans[:limit]
        b = self.start_board()
        want = len(self.moves) if limit is None else min(limit, len(self.moves))
        sans = []
        for m in self.moves[:want]:
            sans.append(b.san(m))
            b.push(m)
        if limit is None:
            self._sans = sans
        return sans

    # ---------------------------------------------------------------- API
    def board_at(self, ply: int) -> chess.Board:
        ply = max(0, min(ply, len(self.moves)))
        return self.boards[ply]

    def lastmove_at(self, ply: int) -> chess.Move | None:
        if ply <= 0 or ply > len(self.moves):
            return None
        return self.moves[ply - 1]

    def comment_at(self, ply: int) -> str:
        """Textový komentář za `ply`-tým půltahem (ply od 1)."""
        return self._comment_map.get(ply - 1, "")

    def label(self) -> str:
        h = self.headers
        white = h.get("White", "?")
        black = h.get("Black", "?")
        result = h.get("Result", "*")
        event = h.get("Event", "")
        date = h.get("Date", "")
        extra = " – ".join(x for x in (event, date) if x and x not in ("?", "????.??.??"))
        return f"{white} – {black}  ({result})   {extra}".strip()


def position_key(board: chess.Board):
    """Klíč pozice pro hledání transpozic (rozestavění + strana + rošády + e.p.)."""
    return board._transposition_key()


class _MainlineVisitor(chess.pgn.BaseVisitor):
    """Načte jen hlavičky, tahy hlavní linie a textové komentáře."""

    def begin_game(self) -> None:
        self._headers: dict = {}
        self._moves: list[chess.Move] = []
        self._comments: dict[int, str] = {}

    def visit_header(self, name, value) -> None:
        self._headers[name] = value

    def visit_move(self, board, move) -> None:
        self._moves.append(move)

    def visit_comment(self, comment) -> None:
        txt = _ANNOT_RE.sub("", comment).strip()
        if txt:
            i = len(self._moves) - 1  # -1 = komentář před 1. tahem
            self._comments[i] = (self._comments.get(i, "") + " " + txt).strip()

    def begin_variation(self):
        return chess.pgn.SKIP

    def result(self) -> LoadedGame:
        return LoadedGame(self._headers, self._moves, self._comments)


def read_games(fh):
    """Generátor přes partie v otevřeném souboru/streamu."""
    while True:
        game = chess.pgn.read_game(fh, Visitor=_MainlineVisitor)
        if game is None:
            break
        yield game


def load_pgn(path: str) -> list[LoadedGame]:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return list(read_games(fh))


def load_pgn_text(text: str) -> list[LoadedGame]:
    return list(read_games(io.StringIO(text)))
