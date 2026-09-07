"""Analýza pozice pomocí UCI enginu (např. Stockfish) v samostatném vlákně."""
from __future__ import annotations

import threading

import chess
import chess.engine
from PySide6.QtCore import QThread, Signal

from engine_perf import apply_engine_options


class EngineWorker(QThread):
    """Vlákno, které drží spuštěný UCI engine a průběžně analyzuje zadanou pozici.

    Veškerá komunikace s enginem probíhá pouze uvnitř tohoto vlákna, protože
    ``chess.engine.SimpleEngine`` není thread-safe.
    """

    info_ready = Signal(dict)   # {"lines": [...], "depth": int, "nps": int, "fen": str}
    engine_error = Signal(str)
    engine_started = Signal(str)  # jméno enginu

    def __init__(self, engine_path: str, multipv: int = 1, parent=None,
                 threads: int | None = None, hash_mb: int | None = None) -> None:
        super().__init__(parent)
        self._engine_path = engine_path
        self._multipv = max(1, int(multipv))
        self._threads = threads
        self._hash_mb = hash_mb
        self._pending_board: chess.Board | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False

    # ---- veřejné API (voláno z GUI vlákna) -------------------------------
    def set_position(self, board: chess.Board) -> None:
        with self._lock:
            self._pending_board = board.copy(stack=False)
        self._wake.set()

    def set_multipv(self, multipv: int) -> None:
        with self._lock:
            self._multipv = max(1, int(multipv))
            # vynutí restart analýzy s novým nastavením
            if self._pending_board is None:
                self._pending_board = self._last_board.copy(stack=False) if self._last_board else None
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    # ---- vnitřní ------------------------------------------------------------
    _last_board: chess.Board | None = None

    def _take_pending(self):
        with self._lock:
            board = self._pending_board
            self._pending_board = None
            mpv = self._multipv
            if board is not None:
                self._last_board = board
            return board, mpv

    def run(self) -> None:  # noqa: C901 - hlavní smyčka
        try:
            engine = chess.engine.SimpleEngine.popen_uci(self._engine_path)
        except Exception as exc:  # pragma: no cover - závisí na prostředí
            self.engine_error.emit(f"Nepodařilo se spustit engine:\n{exc}")
            return

        try:
            apply_engine_options(engine, self._threads, self._hash_mb)
        except Exception:
            pass
        name = engine.id.get("name", "UCI engine")
        self.engine_started.emit(name)

        try:
            while not self._stop:
                self._wake.wait()
                self._wake.clear()
                if self._stop:
                    break

                board, multipv = self._take_pending()
                if board is None:
                    continue
                if board.is_game_over():
                    self.info_ready.emit({
                        "lines": [], "depth": 0, "nps": 0,
                        "fen": board.fen(), "gameover": True,
                    })
                    continue

                try:
                    with engine.analysis(board, multipv=multipv) as analysis:
                        for _ in analysis:
                            if self._wake.is_set() or self._stop:
                                break
                            self._emit_state(analysis, board)
                        # po ukončení iterace ještě jednou pošli finální stav
                        if not self._wake.is_set() and not self._stop:
                            self._emit_state(analysis, board)
                except chess.engine.EngineTerminatedError:
                    self.engine_error.emit("Engine neočekávaně skončil.")
                    return
                except Exception as exc:  # pragma: no cover
                    self.engine_error.emit(f"Chyba analýzy: {exc}")
        finally:
            try:
                engine.quit()
            except Exception:
                pass

    def _emit_state(self, analysis, board: chess.Board) -> None:
        multipv_info = analysis.multipv or []
        lines = []
        depth = 0
        nps = 0
        for info in multipv_info:
            score = info.get("score")
            pv = info.get("pv") or []
            if score is None or not pv:
                continue
            depth = max(depth, info.get("depth", 0) or 0)
            nps = info.get("nps", nps) or nps
            san = self._pv_to_san(board, pv)
            lines.append({
                "score": self._format_score(score, board.turn),
                "score_cp": self._score_sort_key(score, board.turn),
                "score_mate": score.pov(chess.WHITE).mate(),
                "pv": san,
                "move": pv[0].uci(),
                "pv_uci": [m.uci() for m in pv[:24]],
                "depth": info.get("depth", 0) or 0,
            })
        if not lines:
            return
        self.info_ready.emit({
            "lines": lines,
            "best_move": lines[0]["move"],
            "depth": depth,
            "nps": nps,
            "fen": board.fen(),
        })

    @staticmethod
    def _pv_to_san(board: chess.Board, pv: list[chess.Move], limit: int = 12) -> str:
        tmp = board.copy(stack=False)
        parts: list[str] = []
        for i, move in enumerate(pv[:limit]):
            if tmp.turn == chess.WHITE:
                parts.append(f"{tmp.fullmove_number}.")
            elif i == 0:
                parts.append(f"{tmp.fullmove_number}...")
            try:
                parts.append(tmp.san(move))
            except Exception:
                break
            tmp.push(move)
        return " ".join(parts)

    @staticmethod
    def _format_score(score: chess.engine.PovScore, turn: chess.Color) -> str:
        pov = score.pov(chess.WHITE)
        if pov.is_mate():
            m = pov.mate()
            return f"#{'+' if m and m > 0 else '-'}{abs(m)}" if m is not None else "#"
        cp = pov.score()
        if cp is None:
            return "0.00"
        return f"{cp / 100:+.2f}"

    @staticmethod
    def _score_sort_key(score: chess.engine.PovScore, turn: chess.Color) -> int:
        pov = score.pov(chess.WHITE)
        if pov.is_mate():
            m = pov.mate() or 0
            return 100000 - m if m > 0 else -100000 - m
        return pov.score() or 0
