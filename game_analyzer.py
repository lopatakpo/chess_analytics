"""Rozbor jedné partie enginem – hodnocení pozic, nejlepší tahy, značky chyb, ACPL."""
from __future__ import annotations

import queue
import threading
import time

import chess
import chess.engine
from PySide6.QtCore import QThread, Signal

from accuracy import CP_CLAMP as _CLAMP  # stejný ořez jako accuracy.py (Přesnost tab) –
                                          # jinak by ACPL téže partie na kartě Partie
                                          # a na kartě Přesnost nesedělo (byl tu bug: 1500 vs 1000)
from accuracy import classify as _classify_drop
from accuracy import win_pct as _win_pct
from accuracy import win_series as _win_series
from charakter import criticality as _criticality
from engine_perf import apply_engine_options
from move_class import classify_moves as _classify_moves_ccom
from move_class import count_kinds as _count_kinds_ccom

_MATE_CP = 100000

# druhy značek, které rozbor najde (od nejhoršího) – label + barvy pro GUI
MARK_KINDS = [
    ("??", "hrubka", "#c62828", "#f2c6c6"),
    ("?", "chyba", "#e65100", "#f7d9b0"),
    ("?!", "nepřesnost", "#f9a825", "#f4eebb"),
]
MARK_TEXT = {k: lbl for k, lbl, _, _ in MARK_KINDS}
MARK_FG = {k: fg for k, _, fg, _ in MARK_KINDS}
MARK_BG = {k: bg for k, _, _, bg in MARK_KINDS}


def _white_cp(score: chess.engine.PovScore) -> int:
    pov = score.white()
    if pov.is_mate():
        m = pov.mate() or 0
        return _MATE_CP if m > 0 else -_MATE_CP
    return pov.score() or 0


def _mate_eval(board: chess.Board) -> int:
    if board.is_checkmate():
        return -_MATE_CP if board.turn == chess.WHITE else _MATE_CP
    return 0


def analyse_position(engine, board, limit, should_stop, multipv: int = 1):
    """Analýza jedné pozice – jedno blokující volání (``SimpleEngine.analyse``),
    ne streamování dílčích UCI zpráv. Dřívější streamovací varianta
    (``with engine.analysis(...) as analysis: for _ in analysis: ...``)
    procházela v Pythonu KAŽDOU průběžnou ``info`` zprávu enginu, což se při
    krátkých hloubkách (appka volá engine zvlášť na každou pozici, ne jeden
    dlouhý běh) ukázalo jako skutečné úzké hrdlo, ne samotné hledání – viz
    ``engine_perf.py`` a benchmark v README. ``should_stop()`` se teď
    kontroluje jen MEZI pozicemi, ne uprostřed jedné – zastavení rozboru tak
    může zabrat o jednu (probíhající) pozici víc, řádově do několika vteřin
    podle hloubky, což je přijatelná cena za odstranění té režie.

    Vrátí (cp z pohledu bílého, nejlepší tah UCI nebo None, wdl (w,d,l) promile z
    pohledu bílého nebo None, top-K linie [[cp_bílého, uci], ...] nebo None).
    Top-K a WDL se doplní jen když je zapotřebí (``multipv`` > 1 pro top-K; WDL
    posílá Stockfish defaultně).
    """
    if should_stop():
        return 0, None, None, None
    info = engine.analyse(board, limit, multipv=multipv if multipv > 1 else None)
    lines = info if isinstance(info, list) else [info]
    if not lines:
        return 0, None, None, None
    lines.sort(key=lambda d: d.get("multipv", 1))
    top = lines[0]
    score = top.get("score")
    cp = _white_cp(score) if score is not None else 0
    pv0 = top.get("pv")
    best = pv0[0].uci() if pv0 else None
    wdl = None
    pov_wdl = top.get("wdl")
    if pov_wdl is not None:
        try:
            ww = pov_wdl.white()
            wdl = [ww.wins, ww.draws, ww.losses]
        except Exception:
            wdl = None
    topk = None
    if multipv > 1:
        topk = []
        for ln in lines:
            sc, pv = ln.get("score"), ln.get("pv")
            if sc is not None and pv:
                topk.append([_white_cp(sc), pv[0].uci()])
    return cp, best, wdl, topk


def analyse_boards(engine, boards, depth, should_stop=lambda: False, on_progress=None,
                   multipv: int = 1):
    """Projede seznam pozic. Vrátí (evals, bestmoves, wdls, topks) – vše len(boards)."""
    evals: list[int] = []
    bests: list[str | None] = []
    wdls: list = []
    topks: list = []
    limit = chess.engine.Limit(depth=depth)
    total = len(boards)
    for i, b in enumerate(boards):
        if should_stop():
            break
        if b.is_game_over():
            evals.append(_mate_eval(b))
            bests.append(None)
            wdls.append(None)
            topks.append(None)
        else:
            cp, best, wdl, topk = analyse_position(engine, b, limit, should_stop, multipv)
            evals.append(cp)
            bests.append(best)
            wdls.append(wdl)
            topks.append(topk)
        if on_progress is not None:
            on_progress(i + 1, total)
    return evals, bests, wdls, topks


def analyse_boards_parallel(engine_path: str, boards, depth: int, n_workers: int,
                            should_stop=lambda: False, on_progress=None,
                            multipv: int = 1, threads=None, hash_mb=None):
    """Jako :func:`analyse_boards`, ale pozice rozdělí mezi ``n_workers`` enginů
    (vzor jako ``accuracy_batch``, jen v rámci jedné partie). Pozice jsou
    nezávislé, takže to škáluje čistě; strop je stejný jako u dávky (~30 % je
    parsování UCI v Pythonu pod GIL). Vrátí (evals, bestmoves, wdls, topks),
    vše ``len(boards)`` a ve správném pořadí."""
    n = len(boards)
    n_workers = max(1, min(int(n_workers), n or 1))
    limit = chess.engine.Limit(depth=depth)

    if n_workers == 1:
        eng = chess.engine.SimpleEngine.popen_uci(engine_path)
        apply_engine_options(eng, threads, hash_mb)
        try:
            return analyse_boards(eng, boards, depth, should_stop, on_progress, multipv)
        finally:
            try:
                eng.quit()
            except Exception:
                pass

    jobs: queue.Queue = queue.Queue()
    for i, b in enumerate(boards):
        jobs.put((i, b))
    results: dict = {}
    rlock = threading.Lock()
    done = [0]
    dlock = threading.Lock()
    engines: list = []
    elock = threading.Lock()
    errors: list = []

    def worker() -> None:
        try:
            eng = chess.engine.SimpleEngine.popen_uci(engine_path)
            apply_engine_options(eng, threads, hash_mb)
            with elock:
                engines.append(eng)
            while not should_stop():
                try:
                    i, b = jobs.get_nowait()
                except queue.Empty:
                    break
                if b.is_game_over():
                    r = (_mate_eval(b), None, None, None)
                else:
                    r = analyse_position(eng, b, limit, should_stop, multipv)
                with rlock:
                    results[i] = r
                with dlock:
                    done[0] += 1
        except chess.engine.EngineTerminatedError:
            errors.append("Engine během rozboru skončil.")
        except Exception as exc:  # pragma: no cover
            errors.append(f"Chyba rozboru: {exc}")

    pool = [threading.Thread(target=worker, daemon=True, name=f"ga-par-{i}")
            for i in range(n_workers)]
    for t in pool:
        t.start()
    while any(t.is_alive() for t in pool):
        if on_progress is not None:
            with dlock:
                d = done[0]
            on_progress(d, n)
        time.sleep(0.2)
    for t in pool:
        t.join()
    for eng in engines:
        try:
            eng.quit()
        except Exception:
            pass
    if on_progress is not None:
        on_progress(done[0], n)
    if errors and not results:
        raise RuntimeError(errors[0])

    evals: list = []
    bests: list = []
    wdls: list = []
    topks: list = []
    for i in range(n):
        r = results.get(i)
        if r is None:                     # přerušeno / chyba u téhle pozice
            evals.append(0)
            bests.append(None)
            wdls.append(None)
            topks.append(None)
        else:
            cp, best, wdl, topk = r
            evals.append(cp)
            bests.append(best)
            wdls.append(wdl)
            topks.append(topk)
    return evals, bests, wdls, topks


class GameAnalyzer(QThread):
    progress = Signal(int, int)      # hotovo, celkem
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, engine_path: str, boards: list[chess.Board], moves_uci: list[str],
                 depth: int = 12, parent=None,
                 threads: int | None = None, hash_mb: int | None = None,
                 parallel: int = 1, use_wdl: bool = True) -> None:
        super().__init__(parent)
        self._path = engine_path
        self._boards = boards
        self._moves_uci = moves_uci
        self._depth = depth
        self._threads = threads
        self._hash_mb = hash_mb
        self._parallel = max(1, int(parallel))
        self._use_wdl = bool(use_wdl)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: C901
        try:
            # multipv=3 vždy – jedna partie je levná a chess.com styl (Skvělý
            # tah) potřebuje vidět i druhý nejlepší tah (kritičnost)
            evals, bests, wdls, topks = analyse_boards_parallel(
                self._path, self._boards, self._depth, self._parallel,
                should_stop=lambda: self._stop,
                on_progress=lambda d, t: self.progress.emit(d, t), multipv=3,
                threads=self._threads, hash_mb=self._hash_mb)
        except chess.engine.EngineTerminatedError:
            self.failed.emit("Engine během rozboru skončil.")
            return
        except FileNotFoundError as exc:
            self.failed.emit(f"Engine se nepodařilo spustit:\n{exc}")
            return
        except Exception as exc:
            self.failed.emit(f"Chyba rozboru: {exc}")
            return

        if self._stop or len(evals) < 2:
            self.failed.emit("Rozbor přerušen.")
            return

        wdl_use = wdls if (self._use_wdl and wdls
                           and any(x is not None for x in wdls)) else None
        try:
            summary = self._summarize(evals, topks, wdl_use)
            summary["bestmoves"] = bests
        except Exception as exc:  # ať výjimka nikdy neuteče z vlákna
            self.failed.emit(f"Chyba vyhodnocení rozboru: {exc}")
            return
        self.finished_ok.emit(summary)

    def _criticality_series(self, evals: list[int], topks: list) -> list[float] | None:
        if not topks:
            return None
        out = []
        for k in range(len(evals) - 1):
            tk = topks[k] if k < len(topks) else None
            if not tk:
                out.append(0.0)
                continue
            mover_white = (k % 2 == 0)
            eps = [_win_pct(c if mover_white else -c) / 100.0 for c, _ in tk]
            out.append(_criticality(eps))
        return out

    def _summarize(self, evals: list[int], topks: list | None = None,
                   wdls: list | None = None) -> dict:
        marks: dict[int, str] = {}          # ply (1..n) -> "?!" / "?" / "??"
        losses = {chess.WHITE: [], chess.BLACK: []}
        counts = {chess.WHITE: [0, 0, 0], chess.BLACK: [0, 0, 0]}  # inacc, mist, blund
        idx = {"?!": 0, "?": 1, "??": 2}
        ws = _win_series(evals, wdls)       # POV bílého (reálné W/D/L, když je)
        for k in range(len(evals) - 1):
            mover = chess.WHITE if k % 2 == 0 else chess.BLACK
            # ACPL: v setinách pěšce (ořez _CLAMP)
            e0 = max(-_CLAMP, min(_CLAMP, evals[k]))
            e1 = max(-_CLAMP, min(_CLAMP, evals[k + 1]))
            losses[mover].append(max(0, (e0 - e1) if mover == chess.WHITE else (e1 - e0)))
            # značky: podle poklesu pravděpodobnosti výhry (jako přesnost / lichess)
            w0 = ws[k] if mover == chess.WHITE else 100.0 - ws[k]
            w1 = ws[k + 1] if mover == chess.WHITE else 100.0 - ws[k + 1]
            kind = _classify_drop(max(0.0, w0 - w1))
            if kind is not None:
                marks[k + 1] = kind
                counts[mover][idx[kind]] += 1

        def acpl(side):
            xs = losses[side]
            return round(sum(xs) / len(xs)) if xs else 0

        crit = self._criticality_series(evals, topks)
        try:
            cc_marks = _classify_moves_ccom(evals, self._moves_uci, self._boards, crit, wdls)
            cc_counts = _count_kinds_ccom(evals, self._moves_uci, self._boards, crit, wdls)
        except Exception:
            cc_marks, cc_counts = {}, {}

        return {
            "evals": evals,          # index = ply (0 = výchozí pozice)
            "marks": marks,
            "cc_marks": cc_marks,    # chess.com styl (viz move_class.py) – jen "zajímavé"
            "cc_counts": cc_counts,  # chess.com styl – počty VŠECH kategorií (koláč)
            "topks": topks or [],    # top-3 linie na půltah (pro „Hádej tah")
            "wdls": wdls or [],      # reálné W/D/L na půltah (šance na výhru, EP)
            "acpl": {"white": acpl(chess.WHITE), "black": acpl(chess.BLACK)},
            "counts": {"white": counts[chess.WHITE], "black": counts[chess.BLACK]},
        }
