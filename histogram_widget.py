"""Histogram s vyrovnávací normální křivkou a stat-boxem (styl Minitab).

Vlastní QPainter widget – stejný duch jako heatmap_widget.py. Sloupce = četnosti
v koších pevné šířky; přes ně vyrovnávací normální křivka škálovaná na stejné
měřítko (N · šířka koše · hustota); vpravo nahoře box s Průměr / Sm. odchylka / N.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class HistogramChart(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._bin_width = 1.0
        self._title = ""
        self._subtitle = "Normální rozdělení"
        self._xlabel = ""
        self.setMinimumSize(360, 260)

    def set_data(self, values: list[float], bin_width: float, title: str,
                xlabel: str, subtitle: str = "Normální rozdělení",
                max_bins: int = 40) -> None:
        self._values = [v for v in values if v is not None]
        bw = max(1e-9, bin_width)
        if self._values:
            rng = max(self._values) - min(self._values)
            while rng > 0 and rng / bw > max_bins:
                bw *= 2
        self._bin_width = bw
        self._title = title
        self._subtitle = subtitle
        self._xlabel = xlabel
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#f2f2f0"))
        w, h = self.width(), self.height()

        f = QFont()
        f.setPointSizeF(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#222"))
        p.drawText(QRectF(0, 4, w, 20), Qt.AlignCenter, self._title)
        f2 = QFont()
        f2.setPointSizeF(9)
        p.setFont(f2)
        p.setPen(QColor("#555"))
        p.drawText(QRectF(0, 23, w, 16), Qt.AlignCenter, self._subtitle)

        if not self._values:
            p.setPen(QColor("#666"))
            p.drawText(self.rect(), Qt.AlignCenter, "Žádná data.")
            return

        n = len(self._values)
        mean = sum(self._values) / n
        var = sum((x - mean) ** 2 for x in self._values) / n if n > 1 else 0.0
        std = math.sqrt(var)

        bw = self._bin_width
        lo = math.floor(min(self._values) / bw) * bw
        hi = math.ceil(max(self._values) / bw) * bw
        if hi <= lo:
            hi = lo + bw
        n_bins = max(1, round((hi - lo) / bw))
        counts = [0] * n_bins
        for x in self._values:
            idx = min(n_bins - 1, max(0, int((x - lo) / bw)))
            counts[idx] += 1
        max_count = max(counts) if counts else 1

        box_w = 96
        pad_l, pad_r, pad_t, pad_b = 50, 16 + box_w, 46, 42
        plot_w = max(10.0, w - pad_l - pad_r)
        plot_h = max(10.0, h - pad_t - pad_b)

        # vyrovnávací křivka může přesáhnout nejvyšší sloupec – trochu rezervy nahoře
        peak_density = (1.0 / (std * math.sqrt(2 * math.pi))) if std > 1e-9 else 0.0
        peak_curve = n * bw * peak_density
        top_val = max(max_count, peak_curve) * 1.15 or 1.0

        def px(x: float) -> float:
            return pad_l + (x - lo) / (hi - lo) * plot_w

        def pyv(count: float) -> float:
            return pad_t + plot_h - (count / top_val) * plot_h

        # mřížka + popisky osy Y (četnost)
        n_grid = max(2, min(6, max_count))
        p.setPen(QPen(QColor("#ddd8d0"), 1))
        for i in range(n_grid + 1):
            gy = pad_t + plot_h - plot_h * i / n_grid
            p.drawLine(int(pad_l), int(gy), int(pad_l + plot_w), int(gy))
        small = QFont()
        small.setPointSizeF(7.5)
        p.setFont(small)
        for i in range(n_grid + 1):
            gy = pad_t + plot_h - plot_h * i / n_grid
            val = top_val * i / n_grid
            p.setPen(QColor("#777"))
            p.drawText(QRectF(2, gy - 8, pad_l - 8, 16), Qt.AlignRight | Qt.AlignVCenter,
                      f"{val:.0f}")

        # sloupce
        p.setPen(QPen(QColor("#595959"), 1))
        p.setBrush(QColor("#bfbfbf"))
        for i, c in enumerate(counts):
            x0, x1 = px(lo + i * bw), px(lo + (i + 1) * bw)
            y0, y1 = pyv(0), pyv(c)
            p.drawRect(QRectF(x0, y1, x1 - x0, y0 - y1))

        # vyrovnávací normální křivka: N × šířka koše × hustota, na stejné škále
        if std > 1e-9:
            path = QPainterPath()
            steps = 200
            for k in range(steps + 1):
                x = lo + (hi - lo) * k / steps
                dens = peak_density * math.exp(-0.5 * ((x - mean) / std) ** 2)
                yv = n * bw * dens
                pt = (px(x), pyv(yv))
                path.moveTo(*pt) if k == 0 else path.lineTo(*pt)
            p.setPen(QPen(QColor("#2255aa"), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        # rámeček + popisky os
        p.setPen(QPen(QColor("#888"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(pad_l, pad_t, plot_w, plot_h))

        f3 = QFont()
        f3.setPointSizeF(8.5)
        p.setFont(f3)
        p.setPen(QColor("#333"))
        p.drawText(QRectF(pad_l, h - 18, plot_w, 16), Qt.AlignCenter, self._xlabel)
        p.save()
        p.translate(12, pad_t + plot_h / 2)
        p.rotate(-90)
        p.drawText(QRectF(-plot_h / 2, -10, plot_h, 16), Qt.AlignCenter, "Četnost")
        p.restore()

        small2 = QFont()
        small2.setPointSizeF(7.5)
        p.setFont(small2)
        p.setPen(QColor("#777"))
        n_xticks = min(8, max(1, n_bins))
        for i in range(n_xticks + 1):
            val = lo + (hi - lo) * i / n_xticks
            gx = px(val)
            p.drawText(QRectF(gx - 22, pad_t + plot_h + 2, 44, 14), Qt.AlignCenter, f"{val:.0f}")

        # stat-box (Průměr / Sm. odchylka / N)
        box_x, box_y, box_h = w - box_w - 8, pad_t, 66
        p.setPen(QPen(QColor("#999"), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawRect(QRectF(box_x, box_y, box_w, box_h))
        f4 = QFont()
        f4.setPointSizeF(8)
        p.setFont(f4)
        p.setPen(QColor("#222"))
        for i, (label, val) in enumerate((
                ("Průměr", f"{mean:.3g}"), ("Sm. odch.", f"{std:.3g}"), ("N", str(n)))):
            ry = box_y + 6 + i * 19
            p.drawText(QRectF(box_x + 6, ry, box_w * 0.6, 16), Qt.AlignLeft | Qt.AlignVCenter,
                      label)
            p.drawText(QRectF(box_x + box_w * 0.55, ry, box_w * 0.42, 16),
                      Qt.AlignRight | Qt.AlignVCenter, val)
