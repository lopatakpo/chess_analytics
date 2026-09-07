"""Taktické úlohy z už rozebraných partií hráče.

Úloha = pozice, kde strana na tahu měla k dispozici silný **forsírující** tah
(braní / šach / proměna) s velkým dopadem na šanci na výhru, a hráč / soupeř ho
buď **přehlédl** (zahrál něco výrazně horšího), nebo **našel** (zahrál ten
nejlepší a hodně tím získal / byla to jediná dobrá volba).

Nepotřebuje engine navíc – jen čte, co je v ``AnalysisCache`` (ideálně
„důkladný rozbor" kvůli top-3 tahům). Postup řešení a hodnocení užitečnosti se
ukládá do ``tactics_progress.json`` vedle aplikace.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date

import chess

from accuracy import win_prob, win_series
from analysis_cache import game_key
from motifs import detect_motifs
from move_class import _hanging_value as _hang, _captured_value as _capval
from player_analysis import game_matches

_STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tactics_progress.json")

MISS_SWING_MIN = 20.0   # p.b. šance na výhru – jak moc musí přehlédnutí stát
FOUND_GAIN_MIN = 15.0   # p.b. – jak moc musí „nalezený" tah sám o sobě získat
CRIT_MIN = 0.12         # rozdíl nejlepšího a 2. nejlepšího (0..1), když jsou top-K
OPENING_SKIP = 8        # nezkoumat úvodní půltahy (kniha)
TOL_WINPCT = 3.0        # p.b. – jiný (taky forsírující) top tah do téhle tolerance se uzná
SAC_MIN = 2.0           # pěšce – od jaké čisté ztráty je tah „oběť"

# stavy: unseen | seen | solved_ok | solved_fail
STATUS_LABEL = {
    "unseen": "neviděno", "seen": "viděno (nevyřešeno)",
    "solved_ok": "vyřešeno správně", "solved_fail": "vyřešeno špatně",
}


@dataclass
class Puzzle:
    pid: str
    gi: int                  # index partie v seznamu (pro otevření)
    label: str
    ply: int                 # 1-based půltah
    move_no: int             # číslo tahu (pro zobrazení)
    fen: str                 # pozice PŘED rozhodujícím tahem
    solution: str            # UCI nejlepšího tahu
    played: str              # UCI skutečně zahraného tahu
    mover_is_player: bool    # čí byl tah (True = hráč, False = soupeř)
    found: bool              # tah nalezen (True) nebo přehlédnut (False)
    swing: float             # p.b. – velikost efektu (obtížnost ~ velikost)
    motifs: list = field(default_factory=list)   # taktické motivy (viz motifs.py)
    difficulty: int = 1500   # odhad obtížnosti (Elo)
    accept: list = field(default_factory=list)   # UCI tahů uznaných jako řešení


def _pid(gk: str, ply: int) -> str:
    return hashlib.sha1(f"{gk}:{ply}".encode()).hexdigest()[:16]


def _is_forcing(board: chess.Board, move: chess.Move) -> bool:
    if board.is_capture(move) or move.promotion:
        return True
    board.push(move)
    chk = board.is_check()
    board.pop()
    return chk


def _is_sacrifice(board: chess.Board, move: chess.Move) -> bool:
    """Tah nechá vlastní figuru braní (soupeř ji může vzít se ziskem ≥ SAC_MIN)."""
    b2 = board.copy(stack=False)
    b2.push(move)
    hv = _hang(b2, move.to_square, board.turn)
    if hv is None:
        return False
    return hv - _capval(board, move) >= SAC_MIN


def _difficulty(board: chess.Board, move: chess.Move, swing: float, crit: float,
                is_sac: bool, player_elo: int | None, found: bool) -> int:
    base = float(player_elo) if player_elo else 1500.0
    d = base
    gives_check = board.gives_check(move)
    quiet = not board.is_capture(move) and not gives_check and not move.promotion
    if quiet:
        d += 230
    if is_sac:
        d += 260
    n_legal = board.legal_moves.count()
    d += max(-40, min(200, (n_legal - 22) * 7))     # víc možností = těžší
    d += max(-220, min(140, (26.0 - swing) * 8.0))  # malý dopad = jemné = těžší
    if crit >= 0.5:
        d -= 130                                     # jediná cesta = důvěřuj jí
    if not found:
        d += 60                                       # přehlédnuté bývají zákeřnější
    return int(max(600, min(2900, round(d / 10.0) * 10)))


def extract_puzzles(games, player: str, colors: str, keep, cache, min_depth: int,
                    use_wdl: bool = True) -> list[Puzzle]:
    """Projde partie hráče (podle barvy) a z jejich rozboru v cache vytáhne úlohy –
    VŠECHNY (přehlédnuté i nalezené, hráčovy i soupeřovy). Filtruje se až v UI."""
    out: list[Puzzle] = []
    for gi, g in enumerate(games):
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        e = cache.get(game_key(g), min_depth, need_topk=False)
        if e is None:
            continue
        evals = e.get("evals") or []
        bests = e.get("bestmoves") or []
        topk = e.get("topk")
        wdls = e.get("wdl")
        moves = g.moves
        boards = g.boards
        if len(evals) < 2 or len(evals) != len(boards) or len(bests) < len(moves):
            continue
        ws = win_series(evals, wdls if (use_wdl and wdls
                                        and any(x is not None for x in wdls)) else None)
        player_is_white = (g.headers.get("White", "").strip() == player)
        try:
            my_elo = int(g.headers.get("WhiteElo" if player_is_white else "BlackElo"))
        except (TypeError, ValueError):
            my_elo = None

        for k in range(len(moves)):
            if k < OPENING_SKIP:
                continue
            best_uci = bests[k]
            if not best_uci:
                continue
            board = boards[k]
            mover_white = board.turn
            played_uci = moves[k].uci()

            # šance na výhru z pohledu strany na tahu (reálné W/D/L, když je)
            w_before = ws[k] if mover_white else 100.0 - ws[k]
            w_after = ws[k + 1] if mover_white else 100.0 - ws[k + 1]
            w_best = w_before
            crit = 0.0
            if topk and k < len(topk) and topk[k]:
                eps = [win_prob(c if mover_white else -c) for c, _ in topk[k]]
                w_best = eps[0]
                if len(eps) >= 2:
                    crit = (eps[0] - eps[1]) / 100.0

            try:
                bmove = chess.Move.from_uci(best_uci)
            except ValueError:
                continue
            if bmove not in board.legal_moves:
                continue
            if not _is_forcing(board, bmove):
                continue

            played_is_best = (played_uci == best_uci)
            miss = w_best - w_after           # o kolik horší než nejlepší
            gain = w_after - w_before         # kolik nejlepší/zahraný tah získal

            if played_is_best:
                # NALEZENÝ – musela to být opravdová taktika, ne „nejlepší v klidu"
                if gain < FOUND_GAIN_MIN and crit < CRIT_MIN:
                    continue
                found, swing = True, max(gain, crit * 100.0)
            else:
                # PŘEHLÉDNUTÝ – nejlepší byl výrazně lepší než zahraný
                if miss < MISS_SWING_MIN:
                    continue
                found, swing = False, miss

            # tolerované řešení: jiný top tah do TOL_WINPCT od nejlepšího, který
            # je taky forsírující (ať úloha zůstane o taktice, ne o přešlapování)
            accept = [best_uci]
            if topk and k < len(topk) and topk[k] and len(topk[k]) > 1:
                w0 = win_prob(topk[k][0][0] if mover_white else -topk[k][0][0])
                for cp, uci in topk[k][1:]:
                    w = win_prob(cp if mover_white else -cp)
                    if w0 - w > TOL_WINPCT:
                        continue
                    try:
                        alt = chess.Move.from_uci(uci)
                    except ValueError:
                        continue
                    if alt in board.legal_moves and _is_forcing(board, alt):
                        accept.append(uci)

            is_sac = _is_sacrifice(board, bmove)
            try:
                mts = detect_motifs(board, bmove, is_sac)
            except Exception:
                mts = []
            diff = _difficulty(board, bmove, swing, crit, is_sac, my_elo, found)

            out.append(Puzzle(
                pid=_pid(game_key(g), k + 1), gi=gi, label=g.label(),
                ply=k + 1, move_no=(k // 2) + 1, fen=board.fen(),
                solution=best_uci, played=played_uci,
                mover_is_player=(mover_white == player_is_white),
                found=found, swing=round(swing, 1),
                motifs=mts, difficulty=diff, accept=accept,
            ))
    out.sort(key=lambda p: -p.swing)
    return out


class TacticsStore:
    """Trvalý stav úloh (viděno / vyřešeno / hodnocení užitečnosti)."""

    def __init__(self) -> None:
        self._data: dict = {}
        try:
            with open(_STORE_PATH, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except Exception:
            self._data = {}

    def rec(self, pid: str) -> dict:
        return self._data.get(pid, {})

    def status(self, pid: str) -> str:
        return self.rec(pid).get("status", "unseen")

    def useful(self, pid: str):
        return self.rec(pid).get("useful")

    def starred(self, pid: str) -> bool:
        return bool(self.rec(pid).get("starred"))

    def attempts(self, pid: str) -> int:
        return int(self.rec(pid).get("attempts", 0))

    # ---------------------------------------------------- opakování (SM-2 lite)
    def due_date(self, pid: str) -> "date | None":
        d = self.rec(pid).get("due")
        try:
            return date.fromisoformat(d) if d else None
        except ValueError:
            return None

    def is_due(self, pid: str, today: "date | None" = None) -> bool:
        dd = self.due_date(pid)          # None dokud úloha nebyla naplánovaná
        return dd is not None and dd <= (today or date.today())

    def in_rotation(self, pid: str) -> bool:
        return "due" in self.rec(pid)

    def schedule(self, pid: str, quality: int, pz: "Puzzle | None" = None) -> None:
        """quality 0–5 (0 = úplně mimo, 5 = hned správně). SM-2 v jednoduché
        podobě: špatně → zpět na 1 den, jinak interval roste podle „ease"."""
        r = self._touch(pid, pz)
        ease = float(r.get("ease", 2.5))
        reps = int(r.get("reps", 0))
        interval = int(r.get("interval", 0))
        if quality < 3:
            reps, interval = 0, 0
            due = date.today()                       # špatně → zpět do dnešní fronty
        else:
            interval = 1 if reps == 0 else 3 if reps == 1 else max(1, round(interval * ease))
            reps += 1
            due = date.fromordinal(date.today().toordinal() + interval)
        ease = max(1.3, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        r["ease"] = round(ease, 3)
        r["reps"] = reps
        r["interval"] = interval
        r["due"] = due.isoformat()
        self._save()

    def _touch(self, pid: str, pz: "Puzzle | None") -> dict:
        r = self._data.setdefault(pid, {})
        if pz is not None:
            r.setdefault("label", pz.label)
            r.setdefault("fen", pz.fen)
            r.setdefault("solution", pz.solution)
            r.setdefault("found", pz.found)
            if getattr(pz, "motifs", None):
                r.setdefault("motifs", list(pz.motifs))
            if getattr(pz, "difficulty", None):
                r.setdefault("difficulty", int(pz.difficulty))
        r["ts"] = int(time.time())
        return r

    def mark_seen(self, pid: str, pz: "Puzzle | None" = None) -> None:
        r = self._touch(pid, pz)
        if r.get("status", "unseen") == "unseen":
            r["status"] = "seen"
        self._save()

    def mark_solved(self, pid: str, ok: bool, pz: "Puzzle | None" = None,
                    quality: int | None = None) -> None:
        r = self._touch(pid, pz)
        r["attempts"] = int(r.get("attempts", 0)) + 1
        # jednou správně vyřešeno zůstává „ok" (nezhoršuje se dalším omylem)
        if ok or r.get("status") != "solved_ok":
            r["status"] = "solved_ok" if ok else "solved_fail"
        self._save()
        if quality is not None:
            self.schedule(pid, quality, pz)

    def set_useful(self, pid: str, val, pz: "Puzzle | None" = None) -> None:
        self._touch(pid, pz)["useful"] = val
        self._save()

    def set_starred(self, pid: str, val: bool, pz: "Puzzle | None" = None) -> None:
        self._touch(pid, pz)["starred"] = bool(val)
        self._save()

    def toggle_starred(self, pid: str, pz: "Puzzle | None" = None) -> bool:
        new = not self.starred(pid)
        self.set_starred(pid, new, pz)
        return new

    def summary(self, pids) -> dict:
        pids = list(pids)
        st = [self.status(p) for p in pids]
        us = [self.useful(p) for p in pids]
        today = date.today()
        return {
            "total": len(pids),
            "unseen": st.count("unseen"),
            "seen": st.count("seen"),
            "ok": st.count("solved_ok"),
            "fail": st.count("solved_fail"),
            "useful_yes": us.count(True),
            "useful_no": us.count(False),
            "starred": sum(1 for p in pids if self.starred(p)),
            "due": sum(1 for p in pids if self.is_due(p, today)),
        }

    def clear(self) -> int:
        """Zahodí celý postup (viděno/vyřešeno/hodnocení/hvězdičky/opakování) a
        smaže soubor. Vrátí počet smazaných záznamů."""
        n = len(self._data)
        self._data = {}
        try:
            os.remove(_STORE_PATH)
        except OSError:
            pass
        return n

    def _save(self) -> None:
        try:
            tmp = _STORE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, _STORE_PATH)
        except Exception:
            pass
