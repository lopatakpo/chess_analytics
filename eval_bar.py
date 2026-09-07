"""Svislý ukazatel hodnocení pozice (bílá zdola, černá shora)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


def _white_fraction(cp: float) -> float:
    """Centipěšci (z pohledu bílého) -> podíl výšky pro bílou (0..1)."""
    # logistická křivka ~ pravděpodobnost výhry bílého
    import math
    return 1.0 / (1.0 + math.exp(-cp / 350.0))


class EvalBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cp: float | None = None
        self._mate: int | None = None
        self._text = ""
        self.setFixedWidth(26)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setToolTip("Hodnocení pozice enginem (z pohledu bílého)")

    def clear(self) -> None:
        self._cp = self._mate = None
        self._text = ""
        self.update()

    def set_eval(self, *, cp: float | None = None, mate: int | None = None) -> None:
        self._cp, self._mate = cp, mate
        if mate is not None:
            self._text = f"M{abs(mate)}"
            self._cp = 100000 if mate > 0 else -100000
        elif cp is not None:
            self._text = f"{cp / 100:+.1f}"
        else:
            self._text = ""
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        h = self.height()
        w = self.width()
        p.fillRect(self.rect(), QColor("#2b2b2b"))          # černá část
        if self._cp is None and self._mate is None:
            p.setPen(QColor("#888"))
            p.setBrush(Qt.NoBrush)
            p.drawRect(0, 0, w - 1, h - 1)
            return
        frac = _white_fraction(float(self._cp if self._cp is not None else 0.0))
        wh = int(round(h * frac))
        p.fillRect(QRectF(0, h - wh, w, wh), QColor("#f2f2f2"))   # bílá zdola
        # střední čára
        p.setPen(QColor("#c62828"))
        p.drawLine(0, h - wh, w, h - wh)
        # text
        f = QFont()
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        white_ahead = frac >= 0.5
        if white_ahead:
            p.setPen(QColor("#222"))
            p.drawText(QRectF(0, h - 16, w, 15), Qt.AlignCenter, self._text)
        else:
            p.setPen(QColor("#eee"))
            p.drawText(QRectF(0, 1, w, 15), Qt.AlignCenter, self._text.lstrip("+"))
