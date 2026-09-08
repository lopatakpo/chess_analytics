"""Porovnání zahájení hráče s populací přes lichess Opening Explorer.

Pro reprezentativní pozici každé varianty se z veřejného API
``explorer.lichess.org/lichess`` stáhne winrate populace ve srovnatelném Elo
pásmu a tempu; porovná se s hráčovým winrate (jednovýběrový z-test proti
populaci jako známé referenci + Benjamini–Hochberg). Výsledky se cachují
gzipovaně vedle aplikace, ať se API nezatěžuje opakovaně.

Jediné místo v aplikaci, které chodí na síť – spouští se ručně tlačítkem.
"""
from __future__ import annotations

import gzip
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import chess
from PySide6.QtCore import QThread, Signal

from stats_util import bh_reject, one_prop_p

_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_PATH = os.path.join(_DIR, "opening_explorer_cache.json.gz")

_API = "https://explorer.lichess.org/lichess"
_UA = "chess_analytics (osobní rozbor partií)"

# spodní hranice Elo pásem, jak je bere Opening Explorer
_RATING_BANDS = [0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]
_SPEEDS_ALL = ["ultraBullet", "bullet", "blitz", "rapid", "classical", "correspondence"]

MAX_PLY = 14            # hlouběji už má explorer pro pásmo řídká data
MIN_PLAYER_GAMES = 8    # míň partií hráče v lince nemá smysl porovnávat
MIN_POP_GAMES = 50      # míň partií populace = nespolehlivá reference
TOP_VARIANTS = 30       # kolik nejhranějších variant porovnat
_MIN_EFFECT = 0.05      # min. věcný rozdíl winrate (jinak neoznačovat)

try:                                   # requests s sebou nese certifi; když je
    import certifi                     # k dispozici, použij jeho CA balík
    _SSL_CTX: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = None


# --------------------------------------------------------------- Elo pásma / tempa
def bands_for(elo: float | None) -> str:
    """CSV Elo pásem kolem hráčova Ela (pásmo, ve kterém leží, + sousední)."""
    if not elo:
        return "1600,1800,2000"
    i = 0
    for j, lo in enumerate(_RATING_BANDS):
        if elo >= lo:
            i = j
    picks = {_RATING_BANDS[max(0, i - 1)], _RATING_BANDS[i],
             _RATING_BANDS[min(len(_RATING_BANDS) - 1, i + 1)]}
    return ",".join(str(x) for x in sorted(picks))


def speeds_csv(selected: str | None = None) -> str:
    if selected and selected in _SPEEDS_ALL:
        return selected
    return "bullet,blitz,rapid"


# --------------------------------------------------------------- cache
def _load_cache() -> dict:
    try:
        with gzip.open(_CACHE_PATH, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        tmp = _CACHE_PATH + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        pass


def clear_cache() -> None:
    try:
        os.remove(_CACHE_PATH)
    except OSError:
        pass


# --------------------------------------------------------------- API
def _fetch(fen: str, ratings: str, speeds: str, timeout: float = 12.0) -> dict | None:
    q = urllib.parse.urlencode({
        "variant": "standard", "fen": fen, "ratings": ratings, "speeds": speeds,
        "moves": 0, "topGames": 0, "recentGames": 0,
    })
    req = urllib.request.Request(f"{_API}?{q}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        d = json.load(r)
    w, dr, bl = int(d.get("white", 0)), int(d.get("draws", 0)), int(d.get("black", 0))
    tot = w + dr + bl
    if tot == 0:
        return {"white": 0, "draws": 0, "black": 0, "total": 0, "opening": None}
    op = d.get("opening") or {}
    return {"white": w, "draws": dr, "black": bl, "total": tot,
            "opening": op.get("name")}


def fen_after(game, ply: int) -> str | None:
    """FEN pozice po ``ply`` půltazích partie (levné – tahy jsou už načtené)."""
    try:
        b = game.start_board()
        for m in game.moves[:ply]:
            b.push(m)
        return b.fen()
    except Exception:
        return None


# --------------------------------------------------------------- worker
class ExplorerWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, tasks: list, ratings: str, speeds: str, parent=None) -> None:
        """``tasks`` = [{name, fen, is_white, results, n_repr, n_total}, …]."""
        super().__init__(parent)
        self._tasks = tasks
        self._ratings = ratings
        self._speeds = speeds
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: C901
        cache = _load_cache()
        rows: list = []
        net_err = 0
        total = len(self._tasks)
        for i, t in enumerate(self._tasks):
            if self._stop:
                break
            self.progress.emit(i, total)
            ck = f"{t['fen']}|{self._ratings}|{self._speeds}"
            pop = cache.get(ck)
            if pop is None:
                for attempt in range(2):
                    try:
                        pop = _fetch(t["fen"], self._ratings, self._speeds)
                        cache[ck] = pop
                        time.sleep(0.35)
                        break
                    except urllib.error.HTTPError as e:
                        if e.code == 429 and attempt == 0:
                            time.sleep(60.0)
                            continue
                        net_err += 1
                        pop = None
                        break
                    except Exception:
                        net_err += 1
                        pop = None
                        break
            if not pop or pop.get("total", 0) < MIN_POP_GAMES:
                continue
            res = t["results"]
            w = res.count("win")
            n = w + res.count("draw") + res.count("loss")
            if n < 1:
                continue
            wr_player = w / n
            side_wins = pop["white"] if t["is_white"] else pop["black"]
            wr_pop = side_wins / pop["total"]
            rows.append({
                "name": t["name"], "fen": t["fen"], "is_white": t["is_white"],
                "n_player": n, "wr_player": wr_player,
                "n_pop": pop["total"], "wr_pop": wr_pop,
                "delta": wr_player - wr_pop,
                "p": one_prop_p(w, n, wr_pop),
                "n_repr": t.get("n_repr"), "n_total": t.get("n_total"),
                "pop_name": pop.get("opening"),
            })
        _save_cache(cache)

        if not rows:
            self.failed.emit(
                "Nic se neporovnalo – buď populace nemá pro dané linky dost partií, "
                "nebo se nepodařilo spojit s explorer.lichess.org"
                + (f" ({net_err}× chyba sítě)." if net_err else "."))
            return

        flags = bh_reject([r["p"] for r in rows], alpha=0.05)
        for r, fl in zip(rows, flags):
            r["sig"] = bool(fl) and abs(r["delta"]) >= _MIN_EFFECT
        rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
        self.progress.emit(total, total)
        self.finished_ok.emit(rows)


# --------------------------------------------------------------- příprava úloh
def build_tasks(groups, games, colors: str) -> list:
    """Z ECO stromu (``analyze_openings`` výstup) sestaví úlohy pro worker:
    reprezentativní (modální) pozici každé z nejhranějších variant."""
    from collections import Counter

    is_white = colors == "white"
    variants = [v for g in groups for v in g.variations]
    variants.sort(key=lambda v: -len(v.entries))
    tasks: list = []
    for v in variants:
        if len(v.entries) < MIN_PLAYER_GAMES:
            continue
        # jeden pevný půltah pro celou variantu (modální konec teorie) – ať se
        # pozice co nejmíň tříští; pak modální FEN v tom půltahu. Winrate hráče
        # se počítá jen z partií, které tou pozicí prošly – aby to bylo srovnání
        # like-for-like s populací (ne winrate přes celou variantu vs. jedna pozice)
        plies = Counter(min(max(int(e.enter_ply or 0), 6), MAX_PLY) for e in v.entries)
        ply = plies.most_common(1)[0][0]
        by_fen: dict = {}
        for e in v.entries:
            f = fen_after(games[e.game_index], ply)
            if f:
                by_fen.setdefault(f, []).append(e.result)
        if not by_fen:
            continue
        fen, results = max(by_fen.items(), key=lambda kv: len(kv[1]))
        if len(results) < MIN_PLAYER_GAMES:
            continue
        tasks.append({
            "name": v.name, "fen": fen, "is_white": is_white,
            "results": results,
            "n_repr": len(results), "n_total": len(v.entries),
        })
        if len(tasks) >= TOP_VARIANTS:
            break
    return tasks
