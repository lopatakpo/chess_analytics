"""Vizuálně atraktivní heatmapa polí na šachovnici (kreslená QPainterem)."""
from __future__ import annotations

import chess
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

# barevná škála: krémová -> zlatá -> oranžová -> cihlová
_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.00, (248, 248, 240)),
    (0.18, (255, 226, 140)),
    (0.42, (255, 168, 60)),
    (0.70, (233, 95, 46)),
    (1.00, (176, 24, 43)),
]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def heat_color(t: float) -> QColor:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    for i in range(len(_STOPS) - 1):
        t0, c0 = _STOPS[i]
        t1, c1 = _STOPS[i + 1]
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return QColor(
                round(_lerp(c0[0], c1[0], f)),
                round(_lerp(c0[1], c1[1], f)),
                round(_lerp(c0[2], c1[2], f)),
            )
    return QColor(*_STOPS[-1][1])


class HeatmapWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._counts = [0] * 64
        self._orientation = chess.WHITE
        self._max = 1
        self.setMinimumSize(340, 370)

    def set_data(self, counts: list[int], orientation: bool = chess.WHITE) -> None:
        self._counts = list(counts)
        self._orientation = orientation
        self._max = max(self._counts) or 1
        self.update()

    def paintEvent(self, event):  # noqa: N802, C901
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), self.palette().window())

        pad = 12
        rank_w = 22          # levý pruh pro čísla řad
        file_h = 18          # spodní pruh pro písmena sloupců
        gap1 = 6             # šachovnice -> písmena
        gap2 = 14            # písmena -> legenda
        legend_block = 40    # pruh legendy + popisky pod ním

        avail_w = self.width() - 2 * pad - rank_w
        avail_h = self.height() - 2 * pad - gap1 - file_h - gap2 - legend_block
        side = min(avail_w, avail_h)
        if side < 48:
            p.end()
            return
        ox = pad + rank_w + (avail_w - side) / 2
        oy = pad + max(0.0, (avail_h - side) / 2)
        cell = side / 8
        gap = max(1.0, cell * 0.06)

        # podklad šachovnice
        p.setPen(QPen(QColor("#c9c9be"), 1))
        p.setBrush(QColor("#ecebdf"))
        p.drawRoundedRect(QRectF(ox - 4, oy - 4, side + 8, side + 8), 9, 9)

        num_font = QFont()
        num_font.setPointSizeF(max(7.5, cell * 0.30))
        num_font.setBold(True)

        for rank in range(8):
            for file in range(8):
                sq = chess.square(file, rank)
                c = self._counts[sq]
                t = c / self._max
                if self._orientation == chess.WHITE:
                    cx, cy = file, 7 - rank
                else:
                    cx, cy = 7 - file, rank
                r = QRectF(ox + cx * cell + gap / 2, oy + cy * cell + gap / 2,
                          cell - gap, cell - gap)
                base = QColor("#e6e6d6") if (file + rank) % 2 == 0 else QColor("#f3f3e9")
                p.setPen(Qt.NoPen)
                p.setBrush(base)
                p.drawRoundedRect(r, 4, 4)
                if c:
                    col = heat_color(t)
                    p.setBrush(col)
                    p.drawRoundedRect(r, 4, 4)
                    lum = 0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()
                    p.setPen(QColor("#ffffff") if lum < 140 else QColor("#3a2a12"))
                    p.setFont(num_font)
                    p.drawText(r, Qt.AlignCenter, str(c))

        # souřadnice
        coord_font = QFont()
        coord_font.setPointSizeF(max(7.0, cell * 0.22))
        p.setFont(coord_font)
        p.setPen(QColor("#7a7a70"))
        files = "abcdefgh" if self._orientation == chess.WHITE else "hgfedcba"
        for i in range(8):
            rlbl = str(8 - i) if self._orientation == chess.WHITE else str(i + 1)
            p.drawText(QRectF(ox + i * cell, oy + side + gap1, cell, file_h),
                       Qt.AlignHCenter | Qt.AlignVCenter, files[i])
            p.drawText(QRectF(pad, oy + i * cell, rank_w - 5, cell),
                       Qt.AlignRight | Qt.AlignVCenter, rlbl)

        # legenda
        ly = oy + side + gap1 + file_h + gap2
        lw = min(side * 0.66, 200.0)
        lrect = QRectF(ox, ly, lw, 11)
        grad = QLinearGradient(lrect.left(), 0.0, lrect.right(), 0.0)
        for stop, cc in _STOPS:
            grad.setColorAt(stop, QColor(*cc))
        p.setPen(QPen(QColor("#c9c9be"), 1))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(lrect, 3, 3)
        p.setFont(coord_font)
        p.setPen(QColor("#7a7a70"))
        p.drawText(QRectF(ox, ly + 12, lw, 13), Qt.AlignLeft | Qt.AlignTop, "méně")
        p.drawText(QRectF(ox, ly + 12, lw, 13), Qt.AlignRight | Qt.AlignTop, "více")
        p.drawText(QRectF(lrect.right() + 10, ly - 3, 120, 17),
                   Qt.AlignLeft | Qt.AlignVCenter, f"nejvíc: {self._max}")
        p.end()
