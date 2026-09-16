"""Časový management – čas na tah (z PGN anotace ``[%clk]``) vs. kvalita tahu.

Nevolá engine – čte evaly z už existující cache rozborů (stejná jako na kartě
*Přesnost*), takže partie musí být napřed rozebraná (libovolnou hloubkou).
Partie bez časových razítek nebo bez rozpoznatelného tempa (``TimeControl``)
se přeskočí a appka to poctivě přizná v souhrnu.
"""
from __future__ import annotations

import chess
from PySide6.QtCore import QThread, Signal

from accuracy import classify, per_move
from accuracy_batch import _phase_list
from analysis_cache import game_key
from clock_util import PIECE_LABEL, PIECE_ORDER, bin_label, move_seconds, parse_time_control, time_bin
from filters import time_class
from openings import classify_by_moves
from player_analysis import _is_white, game_matches

_PHASE_ORDER = ("Zahájení", "Středhra", "Koncovka")


def _bkt0() -> dict:
    return {"n": 0, "time_sum": 0.0, "times": [], "acc_sum": 0.0,
            "cp_sum": 0.0, "blund": 0, "mist": 0, "inacc": 0}


def _bump(b: dict, t: float, acc: float, cpl: float, kind: str | None) -> None:
    b["n"] += 1
    b["time_sum"] += t
    b["times"].append(t)
    b["acc_sum"] += acc
    b["cp_sum"] += cpl
    if kind == "??":
        b["blund"] += 1
    elif kind == "?":
        b["mist"] += 1
    elif kind == "?!":
        b["inacc"] += 1


def _final(b: dict) -> dict:
    n = b["n"]
    if not n:
        return {"n": 0, "mean_time": None, "median_time": None, "accuracy": None,
                "acpl": None, "blund_100": None, "mist_100": None, "inacc_100": None}
    times = sorted(b["times"])
    median = times[len(times) // 2]
    return {
        "n": n,
        "mean_time": round(b["time_sum"] / n, 1),
        "median_time": round(median, 1),
        "accuracy": round(b["acc_sum"] / n, 1),
        "acpl": round(b["cp_sum"] / n),
        "blund_100": round(b["blund"] * 100 / n, 1),
        "mist_100": round(b["mist"] * 100 / n, 1),
        "inacc_100": round(b["inacc"] * 100 / n, 1),
    }


def analyze_time_management(games, player: str, cache, colors: str = "both",
                            keep=None, use_wdl: bool = True,
                            on_progress=None, should_stop=None) -> dict:
    """Projde partie hráče s rozborem v cache a časovými razítky; vrátí
    rozklady podle fáze, tempa, tažené figury a délky přemýšlení."""
    overall = _bkt0()
    by_phase = {ph: _bkt0() for ph in _PHASE_ORDER}
    by_tc: dict = {}
    by_piece = {pt: _bkt0() for pt in PIECE_ORDER}
    by_bin = {}

    total = len(games)
    n_games_ok = n_no_clock = n_no_tc = n_no_cache = 0

    for gi, g in enumerate(games):
        if should_stop and should_stop():
            break
        if on_progress and gi % 200 == 0:
            on_progress(gi, total)
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        if not g.has_clocks():
            n_no_clock += 1
            continue
        tc_parsed = parse_time_control(g.headers.get("TimeControl"))
        if tc_parsed is None:
            n_no_tc += 1
            continue
        key = game_key(g)
        cached = cache.get(key, 1)                # jakákoli hloubka stačí
        if cached is None:
            n_no_cache += 1
            continue
        evals, bests = cached["evals"], cached["bestmoves"]
        moves = g.moves
        if len(evals) < 2 or len(evals) != len(moves) + 1:
            continue
        wdl = cached.get("wdl")
        wdls = wdl if (use_wdl and wdl and any(x is not None for x in wdl)) else None

        is_white = _is_white(g, player)
        initial, increment = tc_parsed
        secs = move_seconds(g.clocks, initial, increment)
        mv = per_move(evals, wdls)
        boards = g.boards
        opening_plies = 12
        try:
            _, _, d = classify_by_moves([m.uci() for m in moves], pos_keys=g.keys)
            opening_plies = min(max(int(d), 8), 24)
        except Exception:
            pass
        phases = _phase_list(boards, opening_plies)
        tcls = time_class(g)
        bkt_tc = by_tc.setdefault(tcls, _bkt0())
        touched = False

        for k in range(len(mv)):
            white = (k % 2 == 0)
            if white != is_white:
                continue
            t = secs[k] if k < len(secs) else None
            if t is None:
                continue
            touched = True
            acc, cpl = mv[k]["acc"], mv[k]["cp_loss"]
            kind = classify(mv[k]["win_drop"])
            piece = boards[k].piece_type_at(moves[k].from_square)
            _bump(overall, t, acc, cpl, kind)
            ph = phases[k] if k < len(phases) else None
            if ph in by_phase:
                _bump(by_phase[ph], t, acc, cpl, kind)
            _bump(bkt_tc, t, acc, cpl, kind)
            if piece in by_piece:
                _bump(by_piece[piece], t, acc, cpl, kind)
            bi = time_bin(t)
            _bump(by_bin.setdefault(bi, _bkt0()), t, acc, cpl, kind)

        if touched:
            n_games_ok += 1

    return {
        "n_games": n_games_ok, "requested": total,
        "n_no_clock": n_no_clock, "n_no_tc": n_no_tc, "n_no_cache": n_no_cache,
        "overall": _final(overall),
        "by_phase": [(ph, _final(by_phase[ph])) for ph in _PHASE_ORDER if by_phase[ph]["n"]],
        "by_tc": [(tc, _final(b)) for tc, b in sorted(by_tc.items()) if b["n"]],
        "by_piece": [(PIECE_LABEL[pt], _final(by_piece[pt])) for pt in PIECE_ORDER
                    if by_piece[pt]["n"]],
        "by_bin": [(bin_label(i), _final(by_bin[i])) for i in sorted(by_bin) if by_bin[i]["n"]],
    }


class TimeManagementWorker(QThread):
    """Rozbor na pozadí – u tisíců partií (přehrání tahů kvůli figuře a fázi)
    to je práce na desítky sekund, nesmí zaseknout GUI. Nevolá engine."""

    progress = Signal(int, int)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, games, player: str, cache, colors: str = "both",
                keep=None, use_wdl: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._games = games
        self._player = player
        self._cache = cache
        self._colors = colors
        self._keep = keep
        self._use_wdl = use_wdl
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            res = analyze_time_management(
                self._games, self._player, self._cache, self._colors, self._keep,
                self._use_wdl,
                on_progress=lambda d, t: self.progress.emit(d, t),
                should_stop=lambda: self._stop)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
            return
        self.done.emit(res)
