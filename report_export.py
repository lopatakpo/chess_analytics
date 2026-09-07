"""Export porovnání hráčů (karta *Report*) do PDF.

Staví HTML dokument (tabulka metrik + grafy jako obrázky) a vytiskne ho přes
``QPrinter`` do PDF. ``QtPrintSupport`` je součást PySide6 (Essentials)."""
from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import QMarginsF, QUrl
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter

_IMG_W = 640   # px v HTML (QTextDocument ~96 DPI) – vejde se na A4 s okraji

from player_report import COMPARE_ROWS, best_index, extract_metrics

_CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 11px; color: #222; }
h1 { font-size: 18px; margin: 0 0 2px 0; }
h2 { font-size: 13px; margin: 16px 0 4px 0; border-bottom: 1px solid #ccc; }
.sub { color: #666; font-size: 10px; margin-bottom: 10px; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ccc; padding: 3px 6px; text-align: right; }
th { background: #f0f0f0; }
td.metric, th.metric { text-align: left; }
td.best { background: #d8efd8; font-weight: bold; }
img { margin: 6px 0; }
"""


def _fmt(value, spec: str) -> str:
    if value is None:
        return "–"
    try:
        return spec.format(value)
    except (ValueError, TypeError):
        return str(value)


def _table_html(snapshots: list[dict]) -> str:
    metrics = [extract_metrics(s) for s in snapshots]
    head = "".join(f"<th>{html.escape(s.get('label', s.get('player', '?')))}</th>"
                   for s in snapshots)
    rows_html = [f"<tr><th class='metric'>Ukazatel</th>{head}</tr>"]
    for key, label, better, spec in COMPARE_ROWS:
        vals = [m.get(key) for m in metrics]
        if all(v is None for v in vals):
            continue
        best = best_index(key, better, vals)
        cells = "".join(
            f"<td class='{'best' if i in best else ''}'>{_fmt(v, spec)}</td>"
            for i, v in enumerate(vals))
        rows_html.append(f"<tr><td class='metric'>{html.escape(label)}</td>{cells}</tr>")
    return "<table>" + "".join(rows_html) + "</table>"


def export_pdf(path: str, snapshots: list[dict], charts: list[tuple[str, object]]) -> None:
    """``charts`` = [(popisek, QImage), ...]."""
    doc = QTextDocument()
    doc.setDefaultStyleSheet(_CSS)

    parts = [f"<h1>Porovnání hráčů</h1>",
             f"<div class='sub'>Vytvořeno {datetime.now():%d.%m.%Y %H:%M} · "
             f"{len(snapshots)} reportů: "
             + html.escape(", ".join(s.get("player", "?") for s in snapshots))
             + "</div>"]

    parts.append("<h2>Přehled sazeb a odhadů</h2>")
    parts.append(_table_html(snapshots))
    parts.append("<div class='sub'>Zeleně zvýrazněná buňka = nejlepší hodnota "
                 "v řádku (volatilita, ostrost, komplexita a „štěstí“ se "
                 "nehodnotí, jen zobrazují).</div>")

    for i, (caption, image) in enumerate(charts):
        url = QUrl(f"mem://chart{i}.png")
        doc.addResource(QTextDocument.ImageResource, url, image)
        w = min(_IMG_W, image.width())
        parts.append(f"<h2>{html.escape(caption)}</h2>")
        parts.append(f"<img src='{url.toString()}' width='{w}'>")

    per_player_notes = []
    for s in snapshots:
        acc = s.get("accuracy") or {}
        lines = list(acc.get("character") or []) + list(acc.get("conversion") or [])
        pat = s.get("patterns") or {}
        lines += list(pat.get("summary_lines") or [])
        if not lines:
            continue
        li = "".join(f"<li>{html.escape(x)}</li>" for x in lines)
        per_player_notes.append(
            f"<h2>{html.escape(s.get('label', s.get('player', '?')))} – textové shrnutí</h2>"
            f"<ul>{li}</ul>")
    parts.extend(per_player_notes)

    doc.setHtml("".join(parts))

    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    printer.setPageLayout(QPageLayout(
        QPageSize(QPageSize.A4), QPageLayout.Portrait,
        QMarginsF(12, 12, 12, 12), QPageLayout.Millimeter))
    # necháme QTextDocument.print_ stránkovat samo podle tiskárny (nenastavovat
    # doc.setPageSize v device-pixelech – to shodí celý dokument na 1 obří stranu)
    doc.print_(printer)
