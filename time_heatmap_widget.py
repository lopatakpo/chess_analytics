"""Heatmapa úspěšnosti podle dne v týdnu a (2h) bloku hodin – vlastní QPainter widget.

Čitelnost: úspěšnost se do každé buňky s dost partiemi vypíše jako **číslo**
(barva je jen doplněk), sloupce jsou 2h bloky (12 místo 24 – větší buňky, míň
šumu), barevná škála je **diverging kolem hráčovy celkové úspěšnosti** (ne kolem
50 %) a plné barvy dosáhne už při ±15 p.b., takže i běžné hodnoty (45–55 %) mají
vidět barvu. Vpravo/dole jsou okrajové součty (úspěšnost za celý den / za blok) –
ty jsou nejčitelnější a rovnou odpoví „kdy hraju nejlíp".
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

_DAY_LABELS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
_NEUTRAL = (242, 242, 238)
_WORSE = (198, 72, 66)     # červená – pod hráčovým průměrem
_BETTER = (52, 132, 74)    # zelená – nad průměrem
_SPAN = 0.15               # ±15 p.b. od průměru = plná barva
_MIN_LABEL_N = 3           # od kolika partií v buňce vypsat úspěšnost číslem


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _div_color(wr: float, mid: float) -> QColor:
    t = max(-1.0, min(1.0, (wr - mid) / _SPAN))
    end = _BETTER if t >= 0 else _WORSE
    f = abs(t)
    return QColor(round(_lerp(_NEUTRAL[0], end[0], f)),
                 round(_lerp(_NEUTRAL[1], end[1], f)),
                 round(_lerp(_NEUTRAL[2], end[2], f)))


def _text_color(bg: QColor) -> QColor:
    lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QColor("#ffffff") if lum < 128 else QColor("#222222")


class TimeHeatmap(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._wr: list[list[float | None]] = [[None] * 24 for _ in range(7)]
        self._n: list[list[int]] = [[0] * 24 for _ in range(7)]
        self._overall = 0.5
        self.setMinimumSize(560, 340)

    def set_data(self, winrate: list[list[float | None]], counts: list[list[int]],
                overall_wr: float | None = None) -> None:
        """``winrate``/``counts`` jsou 7×24 (den × hodina; ``winrate`` = podíl výher).
        Widget si sám složí 24 hodin do 12 dvouhodinových bloků. ``overall_wr`` =
        kotva barevné škály (hráčova celková úspěšnost); None → dopočítá se z mřížky."""
        self._wr = winrate
        self._n = counts
        tot_w = sum(winrate[d][h] * counts[d][h] for d in range(7) for h in range(24)
                   if winrate[d][h] is not None)
        tot_n = sum(counts[d][h] for d in range(7) for h in range(24))
        self._overall = overall_wr if overall_wr is not None else (
            tot_w / tot_n if tot_n else 0.5)
        self.update()

    # ---- složení do 2h bloků ---------------------------------------------------
    def _binned(self):
        wr = [[None] * 12 for _ in range(7)]
        n = [[0] * 12 for _ in range(7)]
        for d in range(7):
            for b in range(12):
                w_sum = 0.0
                c_sum = 0
                for h in (2 * b, 2 * b + 1):
                    if self._wr[d][h] is not None and self._n[d][h]:
                        w_sum += self._wr[d][h] * self._n[d][h]
                        c_sum += self._n[d][h]
                n[d][b] = c_sum
                wr[d][b] = (w_sum / c_sum) if c_sum else None
        return wr, n

    def paintEvent(self, event) -> None:  # noqa: N802, C901
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#f2f2f0"))
        w, h = self.width(), self.height()

        f = QFont()
        f.setPointSizeF(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#222"))
        p.drawText(QRectF(0, 4, w, 20), Qt.AlignCenter,
                  "Úspěšnost podle dne v týdnu a hodiny (místní čas)")

        wr, n = self._binned()
        n_cols, n_rows = 12, 7

        if not any(any(row) for row in n):
            p.setPen(QColor("#666"))
            p.drawText(self.rect(), Qt.AlignCenter, "Žádná data.")
            return

        pad_l, pad_t = 34, 30
        pad_r, pad_b = 68, 58        # r: pruh „den celkem"; b: řádek „blok celkem" + legenda
        plot_w = max(10.0, w - pad_l - pad_r)
        plot_h = max(10.0, h - pad_t - pad_b)
        cw = plot_w / n_cols
        ch = plot_h / n_rows

        cell_font = QFont()
        cell_font.setPointSizeF(8.5)
        cell_font.setBold(True)
        sub_font = QFont()
        sub_font.setPointSizeF(6.5)

        def draw_cell(x, y, cw_, ch_, wr_, n_, strong_border=False):
            rect = QRectF(x + 0.5, y + 0.5, cw_ - 1, ch_ - 1)
            if not n_:
                p.setPen(QPen(QColor("#ddd8d0"), 1))
                p.setBrush(QColor("#edeae4"))
                p.drawRect(rect)
                return
            enough = n_ >= _MIN_LABEL_N
            bg = _div_color(wr_, self._overall) if enough else QColor("#e9e7e0")
            p.setPen(QPen(QColor("#5f6b70") if strong_border else QColor("#ffffff"),
                         2 if strong_border else 1))
            p.setBrush(bg)
            p.drawRect(rect)
            if enough:
                p.setPen(_text_color(bg))
                p.setFont(cell_font)
                p.drawText(QRectF(x, y + 1, cw_, ch_ - 8), Qt.AlignCenter,
                          f"{wr_ * 100:.0f}%")
                p.setFont(sub_font)
                p.setPen(QColor(_text_color(bg)))
                p.drawText(QRectF(x, y + ch_ - 11, cw_, 10), Qt.AlignCenter, f"{n_}")
            else:
                p.setPen(QColor("#999"))
                p.setFont(sub_font)
                p.drawText(rect, Qt.AlignCenter, f"·{n_}")

        # mřížka
        for d in range(n_rows):
            for b in range(n_cols):
                draw_cell(pad_l + b * cw, pad_t + d * ch, cw, ch, wr[d][b], n[d][b])

        # okrajový sloupec – „den celkem"
        for d in range(n_rows):
            cn = sum(n[d])
            cw_r = 0.0
            if cn:
                cw_r = sum(wr[d][b] * n[d][b] for b in range(n_cols) if wr[d][b] is not None) / cn
            draw_cell(pad_l + plot_w + 4, pad_t + d * ch, pad_r - 8, ch,
                      cw_r if cn else None, cn, strong_border=True)

        # okrajový řádek – „blok celkem"
        for b in range(n_cols):
            cn = sum(n[d][b] for d in range(n_rows))
            cw_b = 0.0
            if cn:
                cw_b = sum(wr[d][b] * n[d][b] for d in range(n_rows)
                          if wr[d][b] is not None) / cn
            draw_cell(pad_l + b * cw, pad_t + plot_h + 4, cw, pad_b - 30,
                      cw_b if cn else None, cn, strong_border=True)

        # popisky řádků (dny) + osy
        lbl = QFont()
        lbl.setPointSizeF(8)
        p.setFont(lbl)
        p.setPen(QColor("#444"))
        for d in range(n_rows):
            p.drawText(QRectF(0, pad_t + d * ch, pad_l - 4, ch),
                      Qt.AlignRight | Qt.AlignVCenter, _DAY_LABELS[d])
        p.drawText(QRectF(pad_l + plot_w + 4, pad_t - 14, pad_r - 8, 13),
                  Qt.AlignCenter, "den")
        small = QFont()
        small.setPointSizeF(7)
        p.setFont(small)
        p.setPen(QColor("#666"))
        for b in range(0, n_cols, 2):
            p.drawText(QRectF(pad_l + b * cw - 4, pad_t + plot_h + 4 + (pad_b - 30) + 1,
                             cw * 2, 12), Qt.AlignLeft, f"{2 * b}h")

        # legenda
        ly = h - 16
        lw = 150.0
        lx = pad_l
        p.setPen(Qt.NoPen)
        for i in range(40):
            t = -1.0 + 2.0 * i / 39
            p.setBrush(_div_color(self._overall + t * _SPAN, self._overall))
            p.drawRect(QRectF(lx + i * lw / 40, ly, lw / 40 + 1, 9))
        p.setFont(small)
        p.setPen(QColor("#666"))
        p.drawText(QRectF(lx - 18, ly + 9, 60, 11), Qt.AlignLeft,
                  f"−{_SPAN * 100:.0f} p.b.")
        p.drawText(QRectF(lx + lw / 2 - 40, ly + 9, 80, 11), Qt.AlignCenter,
                  f"tvůj průměr {self._overall * 100:.0f} %")
        p.drawText(QRectF(lx + lw - 42, ly + 9, 60, 11), Qt.AlignRight,
                  f"+{_SPAN * 100:.0f} p.b.")
        p.drawText(QRectF(lx + lw + 24, ly - 1, 340, 12), Qt.AlignLeft,
                  f"velké číslo = úspěšnost, malé = počet partií; „·N“ = méně než "
                  f"{_MIN_LABEL_N} partií")
