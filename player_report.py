"""Uložené reporty hráčů a jejich vzájemné porovnání (karta *Report*).

Report = snímek spočítaných statistik jednoho hráče v jednom okamžiku:
- **Přesnost** – celý výsledek dávkového rozboru z karty *Přesnost*
  (``AccuracyBatch``): přesnost/ACPL/EP/hrubky/T1/IPR/index, charakter partií,
  dotahování, kategorie tahů chess.com, brilantní tahy, kritičnost×přesnost.
- **Vzorce** – serializovaný ``PatternReport`` z karty *Vzorce* (winrate po
  koších + Elo-adjusted převaha a „štěstí").

Snímky se ukládají do ``player_reports.json`` vedle aplikace. Porovnání staví
tabulku metrik vedle sebe (s barevným „kdo je lepší") a srovnávací grafy.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime

_STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_reports.json")

# ---------------------------------------------------------------- serializace

# Klíče z accuracy["overall"] (dict z accuracy_batch._final), co si necháme.
_ACC_OVERALL_KEYS = (
    "games", "moves", "accuracy", "acpl", "ep_per_game", "blund_100", "mist_100",
    "inacc_100", "t1", "tact", "index", "ipr", "conversion", "resourcefulness",
    "ep_wasted", "had_win", "had_loss", "volatility", "reversals", "sharpness",
    "complexity",
)


def _sections_to_jsonable(sections) -> list:
    """[(group, [(label, finaldict), ...]), ...] → čistě listy/dicty."""
    out = []
    for group, rows in sections or []:
        out.append([group, [[label, dict(st)] for label, st in rows]])
    return out


# co si necháme z každého per-partii záznamu (kvůli grafům na kartě Grafy)
_PG_KEYS = ("acc", "acpl", "ipr", "index", "vol", "reversals", "sharp", "cx",
            "ep_wasted", "had_win", "won", "had_loss", "not_lost", "tact_pct",
            "result", "game_len_moves")


def trim_accuracy(res: dict | None) -> dict | None:
    """Z výsledku ``AccuracyBatch`` nechá to, co report a jeho grafy potřebují
    (per-partii seznam ve zkrácené podobě, bez těžkých polí)."""
    if not res:
        return None
    ov = res.get("overall") or {}
    pg = res.get("per_game") or []
    bril = res.get("brilliants") or []
    bril_player = [b for b in bril if b.get("by_player")]
    return {
        "n_games": res.get("n_games"),
        "requested": res.get("requested"),
        "depth": res.get("depth"),
        "thorough": bool(res.get("thorough")),
        "overall": {k: ov.get(k) for k in _ACC_OVERALL_KEYS},
        "sections": _sections_to_jsonable(res.get("sections")),
        "ipr_info": dict(res.get("ipr_info") or {}),
        "character": list(res.get("character") or []),
        "conversion": list(res.get("conversion") or []),
        "cc_counts": dict(res.get("cc_counts") or {}),
        "crit_accuracy": res.get("crit_accuracy"),
        "per_game": [{k: r.get(k) for k in _PG_KEYS} for r in pg],
        "brilliants_player": [
            {"label": b.get("label"), "move_no": b.get("move_no")}
            for b in bril_player
        ],
        "brilliants_total": len(bril),
    }


def serialize_patterns(report) -> dict | None:
    """``PatternReport`` → dict s w/d/l počty místo syrových seznamů výsledků."""
    if report is None or report.n_games == 0:
        return None
    groups = []
    overall_w = overall_d = overall_l = 0
    for gi, group in enumerate(report.groups):
        bds = []
        for bd in group.breakdowns:
            rows = []
            for label, results in bd.rows:
                w = results.count("win")
                d = results.count("draw")
                lo = results.count("loss")
                rows.append({"label": label, "w": w, "d": d, "l": lo,
                             "n": w + d + lo})
                if gi == 0 and bd is group.breakdowns[0]:
                    overall_w += w
                    overall_d += d
                    overall_l += lo
            bds.append({"title": bd.title, "hint": bd.hint, "rows": rows})
        groups.append({"title": group.title, "breakdowns": bds})
    n_res = overall_w + overall_d + overall_l
    return {
        "n_games": report.n_games,
        "summary_lines": list(report.summary_lines),
        "context": dict(report.context or {}),
        "overall_wdl": [overall_w, overall_d, overall_l],
        "overall_winrate": (100.0 * (overall_w + 0.5 * overall_d) / n_res
                            if n_res else None),
        "groups": groups,
    }


def build_snapshot(player: str, accuracy_res: dict | None, patterns_report,
                   filter_desc: str = "", colors: str = "both",
                   openings: list | None = None, distribution: dict | None = None,
                   game_log: list | None = None, rating_pairs: list | None = None) -> dict:
    saved_at = datetime.now().isoformat(timespec="seconds")
    rid = hashlib.sha1(f"{player}|{saved_at}|{time.time()}".encode()).hexdigest()[:12]
    date_h = saved_at[:16].replace("T", " ")
    return {
        "id": rid,
        "player": player,
        "saved_at": saved_at,
        "label": f"{player} · {date_h}",
        "filter_desc": filter_desc,
        "colors": colors,
        "accuracy": trim_accuracy(accuracy_res),
        "patterns": serialize_patterns(patterns_report),
        "openings": openings or [],
        "distribution": distribution or {},
        "game_log": game_log or [],
        "rating_pairs": rating_pairs or [],
    }


# ---------------------------------------------------------------- metriky pro porovnání
#
# (klíč, popisek, „lepší je" – "high" / "low" / None = jen zobrazit, formát)
COMPARE_ROWS = [
    ("acc_games",   "Partií (rozbor přesnosti)", None,   "{:.0f}"),
    ("accuracy",    "Přesnost %",                "high", "{:.1f}"),
    ("acpl",        "ACPL",                      "low",  "{:.0f}"),
    ("ep_per_game", "EP ztráta / partii",        "low",  "{:.2f}"),
    ("blund_100",   "Hrubky / 100 tahů",         "low",  "{:.1f}"),
    ("mist_100",    "Chyby / 100 tahů",          "low",  "{:.1f}"),
    ("t1",          "Shoda s enginem T1 %",      "high", "{:.1f}"),
    ("tact",        "Tactical Awareness %",      "high", "{:.1f}"),
    ("index",       "Index přesnosti (0–100)",   "high", "{:.0f}"),
    ("ipr",         "IPR (odhad výkonnosti)",    "high", "{:.0f}"),
    ("conversion",  "Konverze vyhraných %",      "high", "{:.0f}"),
    ("resourcefulness", "Záchrany prohraných %", "high", "{:.0f}"),
    ("ep_wasted",   "Ztráta bodů z výher",       "low",  "{:.2f}"),
    ("volatility",  "Volatilita (skok/půltah)",  None,   "{:.1f}"),
    ("reversals",   "Obratů / partii",           None,   "{:.1f}"),
    ("sharpness",   "Ostrost pozic (0–1)",       None,   "{:.2f}"),
    ("complexity",  "Komplexita pozic",          None,   "{:.2f}"),
    ("brilliants",  "Brilantních tahů hráče",    "high", "{:.0f}"),
    ("pat_games",   "Partií (rozbor vzorců)",    None,   "{:.0f}"),
    ("pat_winrate", "Winrate % (vzorce)",        "high", "{:.1f}"),
    ("elo_surplus", "Elo-adjusted převaha (b./partii)", "high", "{:+.2f}"),
    ("luck_z",      "„Štěstí“ (z-skóre)",        None,   "{:+.1f}"),
]

# osy radaru (normalizované 0–100)
RADAR_AXES = [
    ("Přesnost", lambda m: m.get("accuracy")),
    ("ACPL (inv.)", lambda m: None if m.get("acpl") is None
     else max(0.0, 100.0 - m["acpl"] / 3.0)),
    ("T1 %", lambda m: m.get("t1")),
    ("Tactical Aw.", lambda m: m.get("tact")),
    ("Konverze", lambda m: m.get("conversion")),
    ("Záchrana", lambda m: m.get("resourcefulness")),
]


def extract_metrics(snapshot: dict) -> dict:
    """Ploché {klíč: hodnota|None} pro tabulku i grafy."""
    m: dict = {}
    acc = snapshot.get("accuracy") or {}
    ov = acc.get("overall") or {}
    m["acc_games"] = acc.get("n_games")
    for k in ("accuracy", "acpl", "ep_per_game", "blund_100", "mist_100", "t1",
              "tact", "index", "ipr", "conversion", "resourcefulness", "ep_wasted",
              "volatility", "reversals", "sharpness", "complexity"):
        m[k] = ov.get(k)
    m["brilliants"] = acc.get("brilliants_total")
    if acc.get("brilliants_player") is not None:
        m["brilliants"] = len(acc["brilliants_player"])

    pat = snapshot.get("patterns") or {}
    m["pat_games"] = pat.get("n_games")
    m["pat_winrate"] = pat.get("overall_winrate")
    ctx = pat.get("context") or {}
    m["elo_surplus"] = ctx.get("elo_surplus")
    m["luck_z"] = ctx.get("luck_z")
    return m


def cc_percentages(snapshot: dict) -> dict:
    """{kind: %podíl} z chess.com kategorií tahů (obě strany)."""
    cc = ((snapshot.get("accuracy") or {}).get("cc_counts")) or {}
    total = sum(cc.values())
    if not total:
        return {}
    return {k: 100.0 * v / total for k, v in cc.items()}


def best_index(key: str, better: str | None, values: list) -> set[int]:
    """Indexy sloupců s „nejlepší" hodnotou (pro zvýraznění). Prázdné, když
    ``better`` je None nebo je málo dat."""
    if not better:
        return set()
    pairs = [(i, v) for i, v in enumerate(values) if isinstance(v, (int, float))]
    if len(pairs) < 2:
        return set()
    tgt = (max if better == "high" else min)(v for _, v in pairs)
    return {i for i, v in pairs if abs(v - tgt) < 1e-9}


# ---------------------------------------------------------------- úložiště
class ReportStore:
    def __init__(self, path: str = _STORE_PATH) -> None:
        self._path = path
        self._data: dict = {}
        try:
            with open(self._path, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except Exception:
            self._data = {}

    def all(self) -> list[dict]:
        """Snímky, nejnovější první."""
        return sorted(self._data.values(),
                      key=lambda s: s.get("saved_at", ""), reverse=True)

    def get(self, rid: str) -> dict | None:
        return self._data.get(rid)

    def save(self, snapshot: dict) -> str:
        self._data[snapshot["id"]] = snapshot
        self._write()
        return snapshot["id"]

    def delete(self, rid: str) -> None:
        self._data.pop(rid, None)
        self._write()

    def rename(self, rid: str, label: str) -> None:
        if rid in self._data:
            self._data[rid]["label"] = label
            self._write()

    def _write(self) -> None:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except Exception:
            pass
