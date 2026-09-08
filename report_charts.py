"""Srovnávací grafy pro kartu *Report* – staví se ze snímků (``player_report``),
ne z živých dat. Každá funkce vrací seznam ``[(popisek, QChart | QWidget), ...]``
(většinou jeden prvek; heatmapa a zahájení mají jeden graf na hráče).

Grafy jsou obdoba karty *Grafy*, ale s jednou sérií / sadou sloupců na hráče:
- spojité rozdělení (délka, volatilita, …) → překryté frekvenční polygony,
- profil → radar přes sebe,
- konverze / kategorie tahů → seskupené sloupce,
- vývoj v čase / štěstí / kritičnost → čáry přes sebe.
"""
from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QCategoryAxis,
    QChart,
    QLineSeries,
    QPolarChart,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen

from heatmap_widget import HeatmapWidget
from move_class import KIND_LABEL, KIND_ORDER
from patterns import to_prague_local
from player_report import RADAR_AXES, cc_percentages, extract_metrics
from stats_util import elo_expected_from_delta
from time_heatmap_widget import TimeHeatmap

_COLORS = ["#1565c0", "#c62828", "#2e7d32", "#f9a825", "#6a1b9a", "#00838f",
          "#5d4037", "#d81b60"]

# (klíč, popisek). „metric" = vyžaduje výběr metriky (jen trend).
COMPARE_CHARTS = [
    ("radar",         "Profil hráče (radar)"),
    ("trend",         "Vývoj v čase"),
    ("luck",          "Kumulativní „štěstí“"),
    ("conversion",    "Dotahování: konverze / záchrana"),
    ("crit",          "Kritičnost × přesnost tahu"),
    ("piece_acc",     "Přesnost podle figury / typu tahu"),
    ("piece_blund",   "Hrubky podle figury / typu tahu"),
    ("moves",         "Kategorie tahů chess.com %"),
    ("opponent",      "Elo hráče × Elo soupeře"),
    ("volatility",    "Divokost partií"),
    ("ep_wasted",     "Ztráta bodů z vyhraných pozic"),
    ("tactical",      "Tactical Awareness"),
    ("length",        "Délka partie"),
    ("first_capture", "První braní"),
    ("endgame_entry", "Vstup do koncovky"),
    ("material",      "Materiálové manko"),
    ("elo_hist",      "Rozdíl Elo soupeře"),
    ("length_result", "Délka partie podle výsledku"),
    ("openings",      "Úspěšnost podle zahájení"),
    ("error_map",     "Chybová heatmapa (odkud táhnu)"),
    ("time_heatmap",  "Heatmapa dne × hodiny"),
]
TREND_METRICS = [("ACPL", "acpl"), ("Přesnost %", "accuracy"), ("IPR", "ipr"),
                 ("Konverze %", "conversion"), ("Záchrana %", "resourcefulness"),
                 ("Tactical Aw. %", "tact")]


def _color(i: int) -> str:
    return _COLORS[i % len(_COLORS)]


def _name(snap: dict) -> str:
    return snap.get("label", snap.get("player", "?"))


def _empty(title: str) -> QChart:
    ch = QChart()
    ch.setTitle(title)
    ch.legend().hide()
    return ch


def _attach(chart: QChart, ax, ay) -> None:
    chart.addAxis(ax, Qt.AlignBottom)
    chart.addAxis(ay, Qt.AlignLeft)
    for s in chart.series():
        s.attachAxis(ax)
        s.attachAxis(ay)


def _fit(axis: QValueAxis, values, pad_frac: float = 0.08,
         floor_zero: bool = False) -> None:
    """Nastaví rozsah osy podle dat s malým okrajem – QtCharts u ručně
    přidaných os spolehlivě neautorozsahuje, takže hodnoty jinak končí
    mimo viditelnou plochu grafu."""
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return
    lo, hi = min(nums), max(nums)
    if floor_zero:
        lo = min(lo, 0.0)
    span = hi - lo
    pad = span * pad_frac if span > 0 else (abs(hi) * pad_frac or 1.0)
    axis.setRange(lo - (0.0 if floor_zero and lo == 0.0 else pad), hi + pad)


# --------------------------------------------------------------- generické
def _freq_lines(title: str, xlabel: str, series_map: dict, bin_width: float) -> QChart:
    allv = [v for vs in series_map.values() for v in vs if v is not None]
    if not allv:
        return _empty(title + " – žádná data")
    lo = math.floor(min(allv) / bin_width) * bin_width
    hi = math.ceil(max(allv) / bin_width) * bin_width
    nb = max(1, int(round((hi - lo) / bin_width)))
    centers = [lo + (i + 0.5) * bin_width for i in range(nb)]
    chart = QChart()
    chart.setTitle(title)
    max_pct = 0.0
    for i, (nm, vs) in enumerate(series_map.items()):
        vs = [v for v in vs if v is not None]
        if not vs:
            continue
        cnt = [0] * nb
        for v in vs:
            idx = min(nb - 1, max(0, int((v - lo) / bin_width)))
            cnt[idx] += 1
        s = QLineSeries()
        s.setName(f"{nm} (n={len(vs)})")
        s.setColor(QColor(_color(i)))
        for c, ct in zip(centers, cnt):
            pct = 100.0 * ct / len(vs)
            s.append(c, pct)
            max_pct = max(max_pct, pct)
        chart.addSeries(s)
    ax = QValueAxis()
    ax.setTitleText(xlabel)
    ax.setRange(lo, hi)
    ay = QValueAxis()
    ay.setTitleText("% partií hráče")
    ay.setRange(0.0, (max_pct or 1.0) * 1.12)
    _attach(chart, ax, ay)
    return chart


def _grouped_bars(title: str, cats: list, series_map: dict, ylabel: str,
                  ymax: float | None = None, ymin: float | None = None) -> QChart:
    chart = QChart()
    chart.setTitle(title)
    series = QBarSeries()
    for i, (nm, vals) in enumerate(series_map.items()):
        bs = QBarSet(nm)
        bs.setColor(QColor(_color(i)))
        for v in vals:
            bs.append(float(v) if isinstance(v, (int, float)) else 0.0)
        series.append(bs)
    chart.addSeries(series)
    ax = QBarCategoryAxis()
    ax.append(cats)
    ay = QValueAxis()
    ay.setTitleText(ylabel)
    allv = [float(v) for vals in series_map.values() for v in vals
            if isinstance(v, (int, float))]
    lo = ymin if ymin is not None else 0.0
    hi = ymax if ymax is not None else (max(allv) * 1.12 if allv else 1.0)
    if ymin is not None and allv:                # dolní mez podle dat, ne pod ně
        lo = max(0.0, min(ymin, min(allv) - 3.0))
    ay.setRange(lo, hi)
    _attach(chart, ax, ay)
    return chart


# --------------------------------------------------------------- jednotlivé grafy
def _radar(snaps):
    metrics = [extract_metrics(s) for s in snaps]
    axes = [(lbl, fn) for lbl, fn in RADAR_AXES
            if any(fn(m) is not None for m in metrics)]
    if len(axes) < 3:
        return [("Profil hráče (radar)",
                 _empty("Málo naplněných os (chce „důkladný rozbor“, konverze/záchrana)."))]
    chart = QPolarChart()
    chart.setTitle("Profil hráče (0–100)")
    step = 360.0 / len(axes)
    for si, (m, s) in enumerate(zip(metrics, snaps)):
        ser = QLineSeries()
        ser.setName(_name(s))
        ser.setColor(QColor(_color(si)))
        for i, (_lbl, fn) in enumerate(axes):
            ser.append(step * (i + 1), fn(m) or 0.0)
        ser.append(step, axes[0][1](m) or 0.0)
        chart.addSeries(ser)
    ax = QCategoryAxis()
    ax.setLabelsPosition(QCategoryAxis.AxisLabelsPositionOnValue)
    for i, (lbl, _fn) in enumerate(axes):
        ax.append(lbl, step * (i + 1))
    ax.setRange(0, 360)
    ay = QValueAxis()
    ay.setRange(0, 100)
    ay.setLabelFormat("%d")
    chart.addAxis(ax, QPolarChart.PolarOrientationAngular)
    chart.addAxis(ay, QPolarChart.PolarOrientationRadial)
    for s in chart.series():
        s.attachAxis(ax)
        s.attachAxis(ay)
    return [("Profil hráče (radar)", chart)]


def _trend_rows(snap) -> list:
    return next((rows for nm, rows in (snap.get("accuracy") or {}).get("sections", [])
                if nm == "Vývoj v čase"), []) or []


def _is_month(period: str) -> bool:
    return len(period) == 7 and period[4] == "-"


def _rows_to_years(rows: list) -> list:
    """[(období, _final_dict)] → [(rok, _final_dict)] – měsíce sloučí do roku,
    metriky zprůměruje váženě počtem partií (``games``)."""
    by: dict = {}
    for period, st in rows:
        by.setdefault(period[:4], []).append(st)
    out = []
    for year in sorted(by):
        sts = by[year]
        agg: dict = {"games": sum((s.get("games") or 0) for s in sts)}
        for key in ("acpl", "accuracy", "ipr", "conversion", "resourcefulness", "tact"):
            wv = [(s[key], (s.get("games") or 1)) for s in sts if s.get(key) is not None]
            if wv:
                agg[key] = sum(v * w for v, w in wv) / sum(w for _, w in wv)
        out.append((year, agg))
    return out


def _trend(snaps, metric):
    label = dict((v, k) for k, v in TREND_METRICS).get(metric, metric)
    raw = [(_name(s), _trend_rows(s)) for s in snaps]
    # různá granularita období (rok vs. měsíc) mezi reporty → sjednotit na roky,
    # jinak by se měsíce jednoho hráče seřadily až za roky ostatních
    grans = {("month" if any(_is_month(p) for p, _ in rows) else "year")
             for _nm, rows in raw if rows}
    unified_note = ""
    if len(grans) > 1:
        raw = [(nm, _rows_to_years(rows)) for nm, rows in raw]
        unified_note = " (sjednoceno na roky – reporty měly různou granularitu)"

    per_player = []
    all_periods = []
    for _nm, rows in raw:
        pts = {period: st.get(metric) for period, st in rows}
        per_player.append(pts)
        for p in pts:
            if p not in all_periods:
                all_periods.append(p)
    all_periods.sort()
    if not all_periods:
        return [("Vývoj v čase", _empty("Reporty nemají časový trend (potřeba 2+ měsíce)."))]
    chart = QChart()
    chart.setTitle(f"Vývoj v čase – {label}{unified_note}")
    plotted = []
    for i, ((nm, _rows), pts) in enumerate(zip(raw, per_player)):
        ser = QLineSeries()
        ser.setName(nm)
        ser.setColor(QColor(_color(i)))
        ser.setPointsVisible(True)
        for xi, period in enumerate(all_periods):
            v = pts.get(period)
            if v is not None:
                ser.append(xi, float(v))
                plotted.append(float(v))
        if ser.count():
            chart.addSeries(ser)
    if not plotted:
        return [("Vývoj v čase – " + label,
                 _empty(f"Pro „{label}“ nejsou v reportech data."))]
    n = len(all_periods)
    # QLineSeries + QBarCategoryAxis usekává krajní body → radši QCategoryAxis
    # (číselná osa s popisky u celých čísel), s okrajem ať se nic neořízne
    ax = QCategoryAxis()
    ax.setLabelsPosition(QCategoryAxis.AxisLabelsPositionOnValue)
    ax.setStartValue(-0.5)
    for i, period in enumerate(all_periods):
        ax.append(str(period), i)
    ax.setRange(-0.5, n - 0.5)
    ay = QValueAxis()
    ay.setTitleText(label)
    _fit(ay, plotted, pad_frac=0.12,
         floor_zero=(metric in ("conversion", "resourcefulness", "tact")))
    _attach(chart, ax, ay)
    return [("Vývoj v čase – " + label, chart)]


def _luck(snaps):
    chart = QChart()
    chart.setTitle("Kumulativní „štěstí“ (skutečné − Elo-očekávané, chronologicky)")
    smap = {"win": 1.0, "draw": 0.5, "loss": 0.0}
    any_pts = False
    max_n = 0
    cum_vals = [0.0]
    for i, s in enumerate(snaps):
        ser = QLineSeries()
        ser.setName(_name(s))
        ser.setColor(QColor(_color(i)))
        cum = 0.0
        n = 0
        for row in s.get("game_log", []):
            _iso, delta, result = row
            if delta is None or result not in smap:
                continue
            n += 1
            cum += smap[result] - elo_expected_from_delta(delta)
            ser.append(n, cum)
            cum_vals.append(cum)
        if ser.count() >= 2:
            chart.addSeries(ser)
            any_pts = True
            max_n = max(max_n, n)
    if not any_pts:
        return [("Kumulativní „štěstí“", _empty("Málo partií se známým Elem soupeře."))]
    ax = QValueAxis()
    ax.setTitleText("partie (chronologicky)")
    ax.setLabelFormat("%d")
    ax.setRange(0, max_n)
    ay = QValueAxis()
    ay.setTitleText("kumulativní body")
    _fit(ay, cum_vals, floor_zero=True)
    _attach(chart, ax, ay)
    return [("Kumulativní „štěstí“", chart)]


def _conversion(snaps):
    smap = {}
    for s in snaps:
        ov = (s.get("accuracy") or {}).get("overall") or {}
        smap[_name(s)] = [ov.get("conversion") or 0.0, ov.get("resourcefulness") or 0.0]
    chart = _grouped_bars(
        "Dotahování: konverze vyhraných / záchrana prohraných pozic",
        ["Konverze vyhraných %", "Záchrany prohraných %"], smap, "%", ymax=100)
    return [("Dotahování", chart)]


def _crit(snaps):
    chart = QChart()
    chart.setTitle("Přesnost tahu podle kritičnosti pozice")
    any_pts = False
    yvals = []
    for i, s in enumerate(snaps):
        ca = (s.get("accuracy") or {}).get("crit_accuracy")
        if not ca:
            continue
        edges, means = ca["edges"], ca["mean_acc"]
        ser = QLineSeries()
        ser.setName(_name(s))
        ser.setColor(QColor(_color(i)))
        for j, m in enumerate(means):
            if m is not None:
                ser.append((edges[j] + edges[j + 1]) / 2, m)
                yvals.append(m)
        if ser.count() >= 2:
            chart.addSeries(ser)
            any_pts = True
    if not any_pts:
        return [("Kritičnost × přesnost", _empty("Potřebuje „důkladný rozbor“ (multipv)."))]
    ax = QValueAxis()
    ax.setRange(0, 1)
    ax.setTitleText("kritičnost pozice (0 = klid, 1 = jasně nejlepší tah)")
    ay = QValueAxis()
    ay.setTitleText("průměrná přesnost tahu (%)")
    _fit(ay, yvals)
    _attach(chart, ax, ay)
    return [("Kritičnost × přesnost tahu", chart)]


def _moves(snaps):
    pcts = [cc_percentages(s) for s in snaps]
    kinds = [k for k in KIND_ORDER if any(p.get(k) for p in pcts)]
    if not kinds:
        return [("Kategorie tahů chess.com", _empty("Žádná data (rozbor na kartě Přesnost)."))]
    smap = {_name(s): [pcts[i].get(k, 0.0) for k in kinds] for i, s in enumerate(snaps)}
    chart = _grouped_bars("Kategorie tahů chess.com (% ze všech tahů, obě strany)",
                          [KIND_LABEL[k] for k in kinds], smap, "% tahů")
    return [("Kategorie tahů chess.com %", chart)]


_PIECE_LABELS = [("1", "pěšec"), ("2", "jezdec"), ("3", "střelec"),
                 ("4", "věž"), ("5", "dáma"), ("6", "král")]
_MTYPE_LABELS = ["braní", "tichý tah", "šach", "rošáda", "proměna"]


def _piece(snaps, blunders: bool):
    field = "movetype_accuracy"
    key = "blund_100" if blunders else "acc"
    rows = []                       # (popisek, klíč, zdroj)
    for pk, lbl in _PIECE_LABELS:
        rows.append((lbl, pk, "piece_accuracy"))
    for mt in _MTYPE_LABELS:
        rows.append((mt, mt, "movetype_accuracy"))
    per = [(s.get("accuracy") or {}) for s in snaps]
    cats, smap = [], {_name(s): [] for s in snaps}
    for lbl, k, src in rows:
        got = [(acc.get(src) or {}).get(k) for acc in per]
        if not any(g and g.get("n") for g in got):
            continue
        cats.append(lbl)
        for s, g in zip(snaps, got):
            smap[_name(s)].append((g or {}).get(key) or 0.0)
    if not cats:
        return [(("Hrubky" if blunders else "Přesnost") + " podle figury",
                 _empty("Reporty nemají rozdělení podle figury – ulož je znovu."))]
    ttl = ("Hrubky na 100 tahů podle tažené figury a typu tahu" if blunders
           else "Přesnost tahu podle tažené figury a typu tahu")
    if blunders:
        chart = _grouped_bars(ttl, cats, smap, "hrubky / 100")
    else:
        chart = _grouped_bars(ttl, cats, smap, "přesnost %", ymax=100.0, ymin=70.0)
    return [(ttl, chart)]


def _opponent(snaps):
    chart = QChart()
    chart.setTitle("Elo hráče × Elo soupeře")
    allv = []
    for i, s in enumerate(snaps):
        pairs = s.get("rating_pairs", [])
        if not pairs:
            continue
        sc = QScatterSeries()
        sc.setName(f"{_name(s)} (n={len(pairs)})")
        col = QColor(_color(i))
        col.setAlpha(max(50, min(200, round(4000 / max(1, len(pairs)) ** 0.5))))
        sc.setColor(col)
        sc.setBorderColor(Qt.transparent)
        sc.setMarkerSize(7)
        for mine, opp, _r in pairs:
            sc.append(opp, mine)
            allv += [mine, opp]
        chart.addSeries(sc)
    if not allv:
        return [("Elo hráče × Elo soupeře", _empty("Žádné partie se známým Elem obou stran."))]
    lo, hi = min(allv), max(allv)
    diag = QLineSeries()
    diag.setName("stejné Elo")
    diag.append(lo, lo)
    diag.append(hi, hi)
    pen = QPen(QColor("#999999"))
    pen.setStyle(Qt.DashLine)
    diag.setPen(pen)
    chart.addSeries(diag)
    ax = QValueAxis()
    ax.setTitleText("Elo soupeře")
    ay = QValueAxis()
    ay.setTitleText("Elo hráče")
    _fit(ax, allv, pad_frac=0.04)
    _fit(ay, allv, pad_frac=0.04)
    _attach(chart, ax, ay)
    return [("Elo hráče × Elo soupeře", chart)]


def _pg_values(snaps, key):
    return {_name(s): [r.get(key) for r in (s.get("accuracy") or {}).get("per_game", [])]
            for s in snaps}


def _ep_wasted(snaps):
    smap = {}
    for s in snaps:
        smap[_name(s)] = [r.get("ep_wasted") for r in (s.get("accuracy") or {}).get("per_game", [])
                          if r.get("had_win") and r.get("ep_wasted") is not None]
    return [("Ztráta bodů z vyhraných pozic",
             _freq_lines("Ztráta bodů z vyhraných pozic",
                         "ztraceno z dosaženého maxima (body 0–1)", smap, 0.05))]


def _dist_values(snaps, key, drop_none=True):
    out = {}
    for s in snaps:
        vs = (s.get("distribution") or {}).get(key, [])
        out[_name(s)] = [v for v in vs if v is not None] if drop_none else list(vs)
    return out


def _length_result(snaps):
    # průměrná délka partie podle výsledku, seskupené sloupce
    cats = ["Výhra", "Remíza", "Prohra"]
    keymap = {"Výhra": "win", "Remíza": "draw", "Prohra": "loss"}
    smap = {}
    for s in snaps:
        dist = s.get("distribution") or {}
        lens = dist.get("length", [])
        res = dist.get("result", [])
        by = {"win": [], "draw": [], "loss": []}
        for ln, r in zip(lens, res):
            if r in by and ln is not None:
                by[r].append(ln)
        smap[_name(s)] = [(sum(by[keymap[c]]) / len(by[keymap[c]]) if by[keymap[c]] else 0.0)
                          for c in cats]
    return [("Délka partie podle výsledku",
             _grouped_bars("Průměrná délka partie podle výsledku", cats, smap, "tahů"))]


def _openings(snaps):
    out = []
    for i, s in enumerate(snaps):
        rows = []
        for o in s.get("openings", []):
            dec = o["w"] + o["d"] + o["l"]
            if dec:
                rows.append((o["label"], o["n"], 100.0 * o["w"] / dec))
        if not rows:
            out.append((f"Zahájení – {_name(s)}", _empty("Žádná data.")))
            continue
        rows.sort(key=lambda r: -r[1])
        rows = rows[:12]
        rows.sort(key=lambda r: r[2])
        chart = QChart()
        chart.setTitle(f"Úspěšnost podle zahájení – {_name(s)} (top 12 podle počtu)")
        chart.legend().hide()
        from PySide6.QtCharts import QHorizontalBarSeries
        bs = QBarSet("Úspěšnost %")
        bs.setColor(QColor(_color(i)))
        for _lbl, _n, wr in rows:
            bs.append(round(wr, 1))
        ser = QHorizontalBarSeries()
        ser.append(bs)
        chart.addSeries(ser)
        ax = QBarCategoryAxis()
        ax.append([f"{lbl[:24]} (n={n})" for lbl, n, _ in rows])
        ay = QValueAxis()
        ay.setRange(0, 100)
        ay.setTitleText("úspěšnost %")
        chart.addAxis(ax, Qt.AlignLeft)
        chart.addAxis(ay, Qt.AlignBottom)
        ser.attachAxis(ax)
        ser.attachAxis(ay)
        out.append((f"Zahájení – {_name(s)}", chart))
    return out


def _time_heatmap(snaps):
    out = []
    for s in snaps:
        wins = [[0] * 24 for _ in range(7)]
        totals = [[0] * 24 for _ in range(7)]
        for row in s.get("game_log", []):
            iso, _delta, result = row
            if not iso or result not in ("win", "draw", "loss"):
                continue
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError:
                continue
            loc = to_prague_local(dt)
            totals[loc.weekday()][loc.hour] += 1
            if result == "win":
                wins[loc.weekday()][loc.hour] += 1
        if not any(any(r) for r in totals):
            out.append((f"Heatmapa dne × hodiny – {_name(s)}", _empty("Žádné partie se známým časem.")))
            continue
        wr = [[(wins[d][h] / totals[d][h] if totals[d][h] else None) for h in range(24)]
              for d in range(7)]
        w = TimeHeatmap()
        w.set_data(wr, totals)
        out.append((f"Heatmapa dne × hodiny – {_name(s)}", w))
    return out


def _error_map(snaps):
    out = []
    for s in snaps:
        em = (s.get("accuracy") or {}).get("error_map") or {}
        mf = em.get("mf") or []
        if not any(mf):
            out.append((f"Chybová heatmapa – {_name(s)}",
                        _empty("Report nemá chybovou heatmapu – ulož ho znovu.")))
            continue
        wdg = HeatmapWidget()
        wdg.set_data([int(x) for x in mf[:64]])   # už sjednoceno na perspektivu hráče
        out.append((f"Chybová heatmapa (odkud táhnu) – {_name(s)}", wdg))
    return out


_BUILDERS = {
    "radar": _radar,
    "luck": _luck,
    "conversion": _conversion,
    "crit": _crit,
    "piece_acc": lambda snaps: _piece(snaps, False),
    "piece_blund": lambda snaps: _piece(snaps, True),
    "moves": _moves,
    "opponent": _opponent,
    "ep_wasted": _ep_wasted,
    "length_result": _length_result,
    "openings": _openings,
    "error_map": _error_map,
    "time_heatmap": _time_heatmap,
    "volatility": lambda snaps: [("Divokost partií", _freq_lines(
        "Divokost partií (průměrný skok šance na výhru / půltah, %)",
        "volatilita (%)", _pg_values(snaps, "vol"), 1.0))],
    "tactical": lambda snaps: [("Tactical Awareness", _freq_lines(
        "Tactical Awareness (% shody s enginem v kritických pozicích)",
        "Tactical Awareness %", _pg_values(snaps, "tact_pct"), 10.0))],
    "length": lambda snaps: [("Délka partie", _freq_lines(
        "Délka partie", "délka partie (tahy)", _dist_values(snaps, "length"), 5.0))],
    "first_capture": lambda snaps: [("První braní", _freq_lines(
        "První braní", "tah prvního braní", _dist_values(snaps, "first_capture"), 2.0))],
    "endgame_entry": lambda snaps: [("Vstup do koncovky", _freq_lines(
        "Vstup do koncovky", "tah vstupu do koncovky",
        _dist_values(snaps, "endgame_entry"), 5.0))],
    "material": lambda snaps: [("Materiálové manko", _freq_lines(
        "Největší usazené materiálové manko", "manko (body)",
        _dist_values(snaps, "deficit"), 1.0))],
    "elo_hist": lambda snaps: [("Rozdíl Elo soupeře", _freq_lines(
        "Rozdíl Elo (hráč − soupeř)", "Elo hráč − soupeř",
        _dist_values(snaps, "elo_delta"), 50.0))],
}


def build(kind: str, snaps: list, metric: str = "acpl") -> list:
    """→ [(popisek, QChart | QWidget), ...]."""
    if kind == "trend":
        return _trend(snaps, metric)
    fn = _BUILDERS.get(kind)
    if fn is None:
        return [(kind, _empty(f"Neznámý graf: {kind}"))]
    try:
        return fn(snaps)
    except Exception as exc:  # ať jeden rozbitý graf neshodí export
        return [(kind, _empty(f"Graf se nepodařilo sestavit: {exc}"))]
