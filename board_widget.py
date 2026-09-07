"""Vykreslení šachovnice pomocí chess.svg do QSvgWidget.

Kromě zobrazení umožňuje i interakci – uživatel může klikáním zahrát vlastní
tah (signál :data:`user_move`) a na šachovnici lze vykreslit šipky (např.
nejlepší tah z enginu).
"""
from __future__ import annotations

import chess
import chess.svg
from PySide6.QtCore import QByteArray, QSize, Qt, Signal
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QInputDialog, QSizePolicy

# rozměry odpovídají chess.svg (SQUARE_SIZE=45, coordinates -> margin 15)
_SQ = 45
_OFFSET = 15
_FULL = 2 * _OFFSET + 8 * _SQ  # 390

_SEL_COLOR = "#2e7d3288"       # zvýraznění vybraného pole
_PROMO = {
    "Dáma": chess.QUEEN,
    "Věž": chess.ROOK,
    "Střelec": chess.BISHOP,
    "Jezdec": chess.KNIGHT,
}


class BoardWidget(QSvgWidget):
    user_move = Signal(chess.Move)

    def __init__(self, parent=None, *, interactive: bool = True, min_px: int = 320) -> None:
        super().__init__(parent)
        self._board = chess.Board()
        self._lastmove: chess.Move | None = None
        self._orientation = chess.WHITE
        self._check_square: int | None = None
        self._arrows: list = []
        self._selected: int | None = None
        self._interactive = interactive
        self.setMinimumSize(min_px, min_px)
        sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        try:
            self.renderer().setAspectRatioMode(Qt.KeepAspectRatio)
        except Exception:
            pass
        self._render()

    # ------------------------------------------------------------ sizing
    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(600, 600)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return w

    # --------------------------------------------------------------- API
    def set_position(self, board: chess.Board, lastmove: chess.Move | None) -> None:
        self._board = board
        self._lastmove = lastmove
        self._check_square = board.king(board.turn) if board.is_check() else None
        self._selected = None
        self._render()

    def set_arrows(self, arrows: list) -> None:
        self._arrows = list(arrows or [])
        self._render()

    def set_interactive(self, on: bool) -> None:
        self._interactive = bool(on)
        if not on:
            self._selected = None
            self._render()

    def flip(self) -> None:
        self._orientation = not self._orientation
        self._selected = None
        self._render()

    @property
    def orientation(self) -> chess.Color:
        return self._orientation

    # ----------------------------------------------------------- render
    def _render(self) -> None:
        fill = {self._selected: _SEL_COLOR} if self._selected is not None else {}
        targets = None
        if self._selected is not None:
            targets = chess.SquareSet(
                m.to_square for m in self._board.legal_moves
                if m.from_square == self._selected
            )
        svg = chess.svg.board(
            self._board,
            orientation=self._orientation,
            lastmove=self._lastmove,
            check=self._check_square,
            arrows=self._arrows,
            fill=fill,
            squares=targets,
            coordinates=True,
        )
        self.load(QByteArray(svg.encode("utf-8")))
        try:
            self.renderer().setAspectRatioMode(Qt.KeepAspectRatio)
        except Exception:
            pass

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._render()

    # ------------------------------------------------------- interakce
    def _square_at(self, x: float, y: float) -> int | None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        # šachovnice je vykreslena čtvercově a vycentrovaně (KeepAspectRatio)
        scale = min(w, h) / _FULL
        off_x = (w - _FULL * scale) / 2
        off_y = (h - _FULL * scale) / 2
        sx = (x - off_x) / scale
        sy = (y - off_y) / scale
        bx = (sx - _OFFSET) / _SQ
        by = (sy - _OFFSET) / _SQ
        if not (0 <= bx < 8 and 0 <= by < 8):
            return None
        file_idx, top_idx = int(bx), int(by)
        if self._orientation == chess.WHITE:
            file_index, rank_index = file_idx, 7 - top_idx
        else:
            file_index, rank_index = 7 - file_idx, top_idx
        return chess.square(file_index, rank_index)

    def mousePressEvent(self, event):  # noqa: N802
        if not self._interactive or event.button() != Qt.LeftButton:
            return
        pos = event.position()
        sq = self._square_at(pos.x(), pos.y())
        if sq is None:
            self._selected = None
            self._render()
            return

        if self._selected is None:
            self._select_if_own(sq)
            return

        if sq == self._selected:
            self._selected = None
            self._render()
            return

        move = self._build_move(self._selected, sq)
        if move is not None and move in self._board.legal_moves:
            self._selected = None
            self._render()
            self.user_move.emit(move)
        else:
            # klik na jinou vlastní figuru = přehození výběru
            self._select_if_own(sq)

    def _select_if_own(self, sq: int) -> None:
        piece = self._board.piece_at(sq)
        if piece is not None and piece.color == self._board.turn:
            self._selected = sq
        else:
            self._selected = None
        self._render()

    def _build_move(self, from_sq: int, to_sq: int) -> chess.Move | None:
        piece = self._board.piece_at(from_sq)
        promotion = None
        if piece is not None and piece.piece_type == chess.PAWN and chess.square_rank(to_sq) in (0, 7):
            promotion = self._ask_promotion()
            if promotion is None:
                return None
        return chess.Move(from_sq, to_sq, promotion=promotion)

    def _ask_promotion(self) -> int | None:
        label, ok = QInputDialog.getItem(
            self, "Proměna pěšce", "Proměnit na:", list(_PROMO.keys()), 0, False)
        if not ok:
            return None
        return _PROMO[label]
