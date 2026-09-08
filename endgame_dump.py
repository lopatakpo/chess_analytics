"""Populační statistika koncovek z měsíčního dumpu lichess (``.pgn.zst``).

Streamovaně (bez držení partií v paměti): projde dump partii po partii, levně
filtruje podle hlaviček (Elo pásmo, tempo, dohraná partie), u zbytku přehraje
tahy a při **prvním vstupu** do každé kategorie koncovky (stejná logika jako
karta *Koncovky*) zapíše výsledek z pohledu **materiálově silnější strany** do
počítadel. Partii pak zahodí – paměť je plochá, limit je jen čas.

Výstup je JSON-serializovatelný slovník (dá se průběžně ukládat a navázat).
"""
from __future__ import annotations

import gzip
import io
import json
import os
import time

import chess
from PySide6.QtCore import QThread, Signal

import endgames as EG
from pgn_game import read_games
from pgn_stream import iter_game_texts, open_text, quick_headers

_DIR = os.path.dirname(os.path.abspath(__file__))
POP_PATH = os.path.join(_DIR, "endgame_population.json.gz")

TIME_CLASSES = ("blitz", "rapid", "classical")
ELO_BUCKETS = (1800, 1900, 2000, 2100, 2200, 2300, 2400)
_RESULT = {"1-0": "w", "0-1": "l", "1/2-1/2": "d"}
_TC_ORDER = ("ultrabullet", "bullet", "blitz", "rapid", "classical", "correspondence")


def _tc_of(event: str) -> str | None:
    e = event.lower()
    return next((tc for tc in _TC_ORDER if tc in e), None)


def _edge_key(diff: int) -> str:
    if diff <= 1:
        return "1"
    if diff == 2:
        return "2"
    return "3+"


def _blank_cat() -> dict:
    return {"n": 0, "w": 0, "d": 0, "l": 0,
            "bal_n": 0, "bal_draw": 0,
            "edge": {},        # "1"/"2"/"3+" -> [n, ahead_win, draw, ahead_loss]
            "pawns": {},       # str(pawns) -> [n, draw]
            "elo": {},         # str(bucket) -> [n, draw, bal_n, bal_draw, e1_n, e1_win]
            "tc": {}}          # tc -> [n, draw, bal_n, bal_draw, e1_n, e1_win]


def _record(cats: dict, label: str, pawns: int, signed: int, res: str,
            eb: str, tc: str) -> None:
    c = cats.setdefault(label, _blank_cat())
    c["n"] += 1
    c[res] += 1
    is_draw = res == "d"
    diff = abs(signed)
    bal = signed == 0
    if bal:
        c["bal_n"] += 1
        c["bal_draw"] += is_draw
        ahead_win = False
    else:
        ahead_white = signed > 0
        ahead_win = (res == "w") if ahead_white else (res == "l")
        ahead_loss = (res == "l") if ahead_white else (res == "w")
        e = c["edge"].setdefault(_edge_key(diff), [0, 0, 0, 0])
        e[0] += 1
        e[1] += ahead_win
        e[2] += is_draw
        e[3] += ahead_loss
    p = c["pawns"].setdefault(str(pawns), [0, 0])
    p[0] += 1
    p[1] += is_draw
    e1 = diff == 1
    e1_win = e1 and ahead_win
    for d, k in ((c["elo"], eb), (c["tc"], tc)):
        row = d.setdefault(k, [0, 0, 0, 0, 0, 0])
        row[0] += 1
        row[1] += is_draw
        row[2] += bal
        row[3] += bal and is_draw
        row[4] += e1
        row[5] += e1_win


def _scan_game(game, res: str, cats: dict, eb: str, tc: str) -> bool:
    board = game.start_board()
    moves = game.moves
    seen: set[str] = set()
    hit = False
    for ply in range(len(moves) + 1):
        if ply:
            try:
                board.push(moves[ply - 1])
            except Exception:
                break
        c = EG._piece_counts(board)
        if not EG._table_ok(c):
            continue
        label = EG.position_category(board, c)
        if label in seen:
            continue
        seen.add(label)
        hit = True
        wv, bv = EG._side_values(c)
        _record(cats, label, c[0] + c[1], wv - bv, res, eb, tc)
    return hit


def new_state(path: str, elo_lo: int, elo_hi: int, time_classes) -> dict:
    return {"meta": {"path": path, "elo_lo": elo_lo, "elo_hi": elo_hi,
                     "time_classes": sorted(time_classes),
                     "scanned": 0, "matched": 0, "with_eg": 0,
                     "done": False, "started": time.time(), "elapsed": 0.0},
            "cats": {}}


def aggregate(path: str, *, elo_lo: int = 1800, elo_hi: int = 2400,
              time_classes=TIME_CLASSES, max_games: int | None = None,
              on_progress=None, should_stop=None, save_state=None,
              save_every: int = 200_000, resume: dict | None = None) -> dict:
    """Projde dump a nasčítá populační statistiku koncovek. ``resume`` = dřív
    uložený stav (stream se přetočit neumí, takže se rychle přeskočí už
    zpracované partie a pokračuje se)."""
    tset = set(time_classes)
    state = resume or new_state(path, elo_lo, elo_hi, tset)
    m = state["meta"]
    cats = state["cats"]
    skip = m["scanned"] if resume else 0
    t0 = time.time() - m.get("elapsed", 0.0)
    scanned, matched, with_eg = m["scanned"], m["matched"], m["with_eg"]
    last_save = matched
    stopped = False

    fh = open_text(path)
    try:
        it = iter_game_texts(fh)
        if skip:
            n = 0
            for _ in it:
                n += 1
                if should_stop and should_stop():
                    return state
                if n >= skip:
                    break
                if on_progress and n % 200_000 == 0:
                    on_progress({"phase": "skip", "scanned": n, "target": skip},
                                time.time() - t0)
        for gtext in it:
            if should_stop and should_stop():
                stopped = True
                break
            scanned += 1
            h = quick_headers(gtext)
            res = _RESULT.get(h.get("Result", ""))
            if res is None:
                continue
            try:
                we, be = int(h["WhiteElo"]), int(h["BlackElo"])
            except (KeyError, ValueError):
                continue
            if not (elo_lo <= we <= elo_hi and elo_lo <= be <= elo_hi):
                continue
            tc = _tc_of(h.get("Event", ""))
            if tc not in tset:
                continue
            matched += 1
            eb = str(max(ELO_BUCKETS[0],
                         min(ELO_BUCKETS[-1], (we + be) // 2 // 100 * 100)))
            game = next(read_games(io.StringIO(gtext)), None)
            if game is not None and _scan_game(game, res, cats, eb, tc):
                with_eg += 1

            if scanned % 20_000 == 0:
                m.update(scanned=scanned, matched=matched, with_eg=with_eg,
                         elapsed=time.time() - t0)
                if on_progress:
                    on_progress(dict(m), time.time() - t0)
            if save_state and matched - last_save >= save_every:
                m.update(scanned=scanned, matched=matched, with_eg=with_eg,
                         elapsed=time.time() - t0)
                save_state(state)
                last_save = matched
            if max_games and matched >= max_games:
                stopped = True
                break
    finally:
        fh.close()

    m.update(scanned=scanned, matched=matched, with_eg=with_eg,
             elapsed=time.time() - t0, done=not stopped)
    if save_state:
        save_state(state)
    return state


# --------------------------------------------------------------- pohledy na výsledek
def _pct(a: int, b: int):
    return (100.0 * a / b) if b else None


def rows(state: dict, min_n: int = 200) -> list[dict]:
    """Kategorie seřazené podle počtu partií, s klíčovými čísly."""
    out = []
    for label, c in state.get("cats", {}).items():
        if c["n"] < min_n:
            continue
        e1 = c["edge"].get("1", [0, 0, 0, 0])
        e2 = c["edge"].get("2", [0, 0, 0, 0])
        e3 = c["edge"].get("3+", [0, 0, 0, 0])
        out.append({
            "label": label, "n": c["n"],
            "draw_pct": _pct(c["d"], c["n"]),
            "bal_n": c["bal_n"], "bal_draw_pct": _pct(c["bal_draw"], c["bal_n"]),
            "e1_n": e1[0], "e1_conv_pct": _pct(e1[1], e1[0]),
            "e1_upset_pct": _pct(e1[3], e1[0]),
            "e2_n": e2[0], "e2_conv_pct": _pct(e2[1], e2[0]),
            "e3_n": e3[0], "e3_conv_pct": _pct(e3[1], e3[0]),
        })
    out.sort(key=lambda r: -r["n"])
    return out


def elo_trend(state: dict, label: str) -> list[dict]:
    c = state.get("cats", {}).get(label)
    if not c:
        return []
    return [{"elo": int(b),
             "n": row[0],
             "bal_draw_pct": _pct(row[3], row[2]),
             "e1_conv_pct": _pct(row[5], row[4])}
            for b, row in sorted(c["elo"].items(), key=lambda kv: int(kv[0]))]


def tc_split(state: dict, label: str) -> list[dict]:
    c = state.get("cats", {}).get(label)
    if not c:
        return []
    return [{"tc": tc, "n": row[0],
             "bal_draw_pct": _pct(row[3], row[2]),
             "e1_conv_pct": _pct(row[5], row[4])}
            for tc, row in sorted(c["tc"].items(),
                                  key=lambda kv: _TC_ORDER.index(kv[0])
                                  if kv[0] in _TC_ORDER else 9)]


# --------------------------------------------------------------- perzistence
def save_population(state: dict, path: str = POP_PATH) -> None:
    try:
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(state, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        pass


def load_population(path: str = POP_PATH) -> dict | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            st = json.load(fh)
        if isinstance(st, dict) and "cats" in st and "meta" in st:
            return st
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return None


def clear_population(path: str = POP_PATH) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------- vlákno
class EndgameDumpWorker(QThread):
    progress = Signal(dict, float)          # meta snapshot, elapsed s
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, path: str, elo_lo: int, elo_hi: int, time_classes,
                 max_games: int | None = None, resume: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._args = dict(path=path, elo_lo=elo_lo, elo_hi=elo_hi,
                          time_classes=tuple(time_classes), max_games=max_games,
                          resume=resume)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            st = aggregate(
                self._args["path"], elo_lo=self._args["elo_lo"],
                elo_hi=self._args["elo_hi"], time_classes=self._args["time_classes"],
                max_games=self._args["max_games"], resume=self._args["resume"],
                on_progress=lambda m, e: self.progress.emit(m, e),
                should_stop=lambda: self._stop,
                save_state=save_population, save_every=200_000)
        except FileNotFoundError:
            self.failed.emit("Soubor s dumpem nebyl nalezen.")
            return
        except RuntimeError as e:                 # chybí zstd podpora apod.
            self.failed.emit(str(e))
            return
        except Exception as e:
            self.failed.emit(f"Chyba při zpracování dumpu: {e}")
            return
        self.finished_ok.emit(st)


def population_lookup(state: dict, label: str, player_elo: float | None) -> dict | None:
    """Populační referenční čísla pro danou kategorii a (přibližné) Elo hráče –
    pro overlay na kartě *Koncovky*."""
    c = state.get("cats", {}).get(label)
    if not c or not c["n"]:
        return None
    bucket = None
    if player_elo:
        b = str(max(ELO_BUCKETS[0], min(ELO_BUCKETS[-1],
                                        int(player_elo) // 100 * 100)))
        if b in c["elo"] and c["elo"][b][0] >= 100:
            bucket = b
    if bucket:
        row = c["elo"][bucket]
        return {"n": row[0], "elo_bucket": int(bucket),
                "bal_draw_pct": _pct(row[3], row[2]),
                "e1_conv_pct": _pct(row[5], row[4])}
    e1 = c["edge"].get("1", [0, 0, 0, 0])
    return {"n": c["n"], "elo_bucket": None,
            "bal_draw_pct": _pct(c["bal_draw"], c["bal_n"]),
            "e1_conv_pct": _pct(e1[1], e1[0])}
