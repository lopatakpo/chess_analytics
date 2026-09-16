"""Mapa přesnosti tahu podle čísla tahu partie – vlastní QPainter widget.

Vstup: (číslo tahu, průměrná přesnost tahů hráče při tom čísle tahu napříč
celou databází, počet tahů). Mezi těmito naměřenými body appka hladce
**interpoluje** (Catmull-Rom spline) místo lomené čáry; plocha pod křivkou má
barvu podle výšky (červená = nízká přesnost, přes žlutou, zelená = vysoká) –
vznikne „mapa" toho, jak se přesnost mění v průběhu partie. Legenda dole.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

_BAD = (196, 74, 68)       # červená – nízká přesnost
_MID = (223, 180, 70)      # žlutá – střední
_GOOD = (61, 139, 89)      # zelená – vysoká přesnost


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def acc_color(pct: float) -> QColor:
    """0–100 % -> barva na škále červená → žlutá → zelená."""
    pct = max(0.0, min(100.0, pct))
    if pct <= 50.0:
        t, c0, c1 = pct / 50.0, _BAD, _MID
    else:
        t, c0, c1 = (pct - 50.0) / 50.0, _MID, _GOOD
    return QColor(round(_lerp(c0[0], c1[0], t)), round(_lerp(c0[1], c1[1], t)),
                 round(_lerp(c0[2], c1[2], t)))


def catmull_rom(points: list[tuple[float, float]], samples_per_seg: int = 10):
    """Body ``[(x,y), …]`` (rostoucí x) -> hladce interpolovaná posloupnost
    bodů (Catmull-Rom spline; krajní body appka zdvojí, ať křivka prochází
    i prvním/posledním naměřeným bodem přesně)."""
    if len(points) < 2:
        return list(points)
    pts = [points[0], *points, points[-1]]
    out: list[tuple[float, float]] = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(samples_per_seg):
            t = s / samples_per_seg
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                      + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                      + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                      + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                      + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(points[-1])
    return out


class MoveAccuracyMap(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: list[tuple[int, float, int]] = []
        self._tail_label: str | None = None       # popisek posledního bodu, např. "61+"
        self.setMinimumSize(560, 320)

    def set_data(self, points: list[tuple[int, float, int]],
                tail_label: str | None = None) -> None:
        """``points`` = [(číslo tahu, průměrná přesnost 0–100, počet tahů), …],
        seřazené podle čísla tahu. ``tail_label`` – popisek posledního bodu
        (appka tak sloučí ocas „61 a víc" do jednoho koše)."""
        self._points = list(points)
        self._tail_label = tail_label
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#f7f6f2"))

        title_f = QFont()
        title_f.setPointSizeF(11)
        title_f.setBold(True)
        p.setFont(title_f)
        p.setPen(QColor("#222"))
        p.drawText(QRectF(0, 4, w, 20), Qt.AlignCenter,
                  "Přesnost tahu podle čísla tahu partie")

        if len(self._points) < 2:
            p.setPen(QColor("#666"))
            p.drawText(self.rect(), Qt.AlignCenter, "Málo dat.")
            return

        pad_l, pad_t, pad_r, pad_b = 42, 30, 16, 46
        plot_w = max(10.0, w - pad_l - pad_r)
        plot_h = max(10.0, h - pad_t - pad_b)

        moves = [pt[0] for pt in self._points]
        mn, mx = moves[0], moves[-1]
        span = max(1, mx - mn)

        def px(move: float) -> float:
            return pad_l + (move - mn) / span * plot_w

        def py(acc: float) -> float:
            return pad_t + (1.0 - max(0.0, min(100.0, acc)) / 100.0) * plot_h

        knots = [(px(mv), py(acc)) for mv, acc, _n in self._points]
        curve = catmull_rom(knots, 10)

        grid_pen = QPen(QColor("#e2ded2"))
        axis_font = QFont()
        axis_font.setPointSizeF(7.5)
        p.setFont(axis_font)
        for pct in (0, 25, 50, 75, 100):
            y = py(pct)
            p.setPen(grid_pen)
            p.drawLine(QPointF(pad_l, y), QPointF(pad_l + plot_w, y))
            p.setPen(QColor("#888"))
            p.drawText(QRectF(0, y - 6, pad_l - 6, 12), Qt.AlignRight, f"{pct}")
        step = max(1, span // 10)
        mv = mn
        while mv <= mx:
            x = px(mv)
            p.setPen(grid_pen)
            p.drawLine(QPointF(x, pad_t), QPointF(x, pad_t + plot_h))
            p.setPen(QColor("#888"))
            lbl = self._tail_label if (self._tail_label and mv == mx) else str(mv)
            p.drawText(QRectF(x - 16, pad_t + plot_h + 2, 32, 12), Qt.AlignCenter, lbl)
            mv += step

        # plocha pod křivkou, barvená po úsecích podle výšky (= přesnosti tam)
        base_y = pad_t + plot_h
        p.setPen(Qt.NoPen)
        for i in range(len(curve) - 1):
            x0, y0 = curve[i]
            x1, y1 = curve[i + 1]
            acc_mid = 100.0 * (1.0 - ((y0 + y1) / 2.0 - pad_t) / plot_h)
            path = QPainterPath()
            path.moveTo(x0, base_y)
            path.lineTo(x0, y0)
            path.lineTo(x1, y1)
            path.lineTo(x1, base_y)
            path.closeSubpath()
            c = acc_color(acc_mid)
            c.setAlpha(150)
            p.setBrush(c)
            p.drawPath(path)

        line_path = QPainterPath()
        line_path.moveTo(*curve[0])
        for x, y in curve[1:]:
            line_path.lineTo(x, y)
        p.setPen(QPen(QColor("#2c2c2c"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(line_path)

        p.setPen(QPen(QColor("#2c2c2c"), 1))
        for mvv, acc, _n in self._points:
            p.setBrush(acc_color(acc))
            p.drawEllipse(QPointF(px(mvv), py(acc)), 2.6, 2.6)

        # legenda – barevná škála + vysvětlivka
        ly = h - 14
        lw = 140.0
        lx = pad_l
        p.setPen(Qt.NoPen)
        for i in range(40):
            t = i / 39 * 100.0
            p.setBrush(acc_color(t))
            p.drawRect(QRectF(lx + i * lw / 40, ly, lw / 40 + 1, 8))
        p.setFont(axis_font)
        p.setPen(QColor("#666"))
        p.drawText(QRectF(lx - 10, ly + 8, 40, 11), Qt.AlignLeft, "0 %")
        p.drawText(QRectF(lx + lw - 30, ly + 8, 40, 11), Qt.AlignRight, "100 %")
        p.drawText(QRectF(lx + lw + 14, ly - 2, 380, 12), Qt.AlignLeft,
                  "barva = přesnost tahu · tečky = naměřené hodnoty · křivka vyhlazená (interpolace)")
