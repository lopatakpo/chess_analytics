"""Sidecar cache rozborů partií enginem – ať se hloubková analýza nepočítá pořád dokola.

Ukládá se **gzipovaně** do ``analysis_cache.json.gz`` vedle aplikace (JSON uvnitr
je ~5× menší): klíč partie -> {depth, evals, bestmoves, wdl, topk}. ``wdl``/``topk``
jsou volitelné (potřeba jen pro „důkladný rozbor" – kritičnost/ostrost). Starý
nezabalený ``analysis_cache.json`` se při prvním načtení převezme a po prvním
uložení smaže.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading

_DIR = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_DIR, "analysis_cache.json.gz")
_LEGACY_PATH = os.path.join(_DIR, "analysis_cache.json")
_HEADER_KEYS = ("White", "Black", "Date", "UTCDate", "Event", "Round", "Result", "Site")


def game_key(game) -> str:
    h = hashlib.sha1()
    for k in _HEADER_KEYS:
        h.update((game.headers.get(k, "") or "").encode("utf-8", "replace"))
        h.update(b"\x00")
    h.update(b"|")
    h.update(b" ".join(m.uci().encode() for m in game.moves))
    return h.hexdigest()[:16]


def _load_any() -> dict:
    """Načte z ``.json.gz``; když není, zkusí starý nezabalený ``.json``."""
    for path, opener in ((_PATH, gzip.open), (_LEGACY_PATH, open)):
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            continue
        except Exception:
            return {}
    return {}


class AnalysisCache:
    """Thread-safe: ``get``/``put``/``save`` drží zámek, aby to sneslo souběžný
    dávkový rozbor s víc enginy (viz ``AccuracyBatch`` s ``parallel`` > 1)."""

    def __init__(self) -> None:
        self._dirty = False
        self._lock = threading.Lock()
        self._data: dict = _load_any()

    @staticmethod
    def _has_data(lst) -> bool:
        """``analyse_boards`` always returns a full-length list, filled with None
        per position when there's nothing (e.g. topk at multipv=1) – that list is
        truthy but carries no real data, so check its contents, not just presence.
        """
        return bool(lst) and any(x is not None for x in lst)

    def get(self, key: str, min_depth: int, need_topk: bool = False):
        with self._lock:
            e = self._data.get(key)
            if not e or int(e.get("depth", 0)) < min_depth:
                return None
            if need_topk and not self._has_data(e.get("topk")):
                return None
            return e

    def put(self, key: str, depth: int, evals: list[int], bestmoves: list,
           wdl: list | None = None, topk: list | None = None) -> None:
        with self._lock:
            old = self._data.get(key) or {}
            same_depth = int(old.get("depth", -1)) == int(depth)
            entry = {"depth": int(depth), "evals": list(evals), "bestmoves": list(bestmoves)}
            new_wdl = wdl if self._has_data(wdl) else None
            new_topk = topk if self._has_data(topk) else None
            entry["wdl"] = new_wdl if new_wdl is not None else (old.get("wdl") if same_depth else None)
            entry["topk"] = new_topk if new_topk is not None else (old.get("topk") if same_depth else None)
            self._data[key] = entry
            self._dirty = True

    def clear(self) -> int:
        """Zahodí všechna data a smaže soubor(y). Vrátí počet smazaných partií."""
        with self._lock:
            n = len(self._data)
            self._data = {}
            self._dirty = False
        for path in (_PATH, _LEGACY_PATH):
            try:
                os.remove(path)
            except OSError:
                pass
        return n

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data_snapshot = dict(self._data)
            self._dirty = False
        try:
            tmp = _PATH + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
                json.dump(data_snapshot, fh, separators=(",", ":"))
            os.replace(tmp, _PATH)
            try:                                   # starý nezabalený už není potřeba
                os.remove(_LEGACY_PATH)
            except OSError:
                pass
        except Exception:
            with self._lock:
                self._dirty = True
