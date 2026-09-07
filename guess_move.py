"""„Hádej tah" – přehraješ rozebranou partii se skrytým enginem a u každého
svého tahu tipuješ; ohodnotí se přesnost tvého tipu (lichess vzorec) proti
nejlepšímu tahu enginu.

Vstup = výsledek rozboru partie (``_game_eval``: ``evals`` / ``bestmoves`` /
``topks``). Tip, který je v top-3 rozboru, se ohodnotí okamžitě z cache; jinak
si dialog nechá pozici po tvém tahu dopočítat enginem (jedno volání).
"""
from __future__ import annotations

import chess
import chess.engine
import chess.svg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from accuracy import move_accuracy, per_move, win_prob
from board_widget import BoardWidget
from engine_perf import apply_engine_options
from game_analyzer import _white_cp

_MATE_CP = 100000


class GuessMoveDialog(QDialog):
    def __init__(self, game, ev: dict, engine_path: str, depth: int,
                 side: chess.Color, start_move: int = 1, parent=None,
                 use_wdl: bool = True) -> None:
        super().__init__(parent)
        self._game = game
        self._use_wdl = bool(use_wdl)
        self._boards = list(game.boards)
        self._moves = list(game.moves)
        self._evals = ev.get("evals") or []
        self._bests = ev.get("bestmoves") or []
        self._topks = ev.get("topks") or ev.get("topk") or []
        self._wdls = (ev.get("wdls") or ev.get("wdl") or []) if use_wdl else []
        self._path = engine_path
        self._depth = max(8, min(int(depth), 20))
        self._side = side
        self._engine = None

        self._plies = [k for k in range(min(len(self._moves), len(self._evals) - 1))
                       if (k % 2 == 0) == (side == chess.WHITE)
                       and (k // 2) + 1 >= start_move]
        self._i = 0
        self._scores: list[float] = []       # přesnost tvých tipů
        self._top1 = 0                        # kolikrát shoda s enginem

        self.setWindowTitle("Hádej tah")
        self.setModal(True)
        lay = QVBoxLayout(self)
        self.board = BoardWidget(interactive=True, min_px=360)
        if self.board.orientation != side:
            self.board.flip()
        self.board.user_move.connect(self._on_move)
        lay.addWidget(self.board, stretch=1)
        self.prompt = QLabel("")
        self.prompt.setStyleSheet("font-weight:bold;")
        self.prompt.setWordWrap(True)
        lay.addWidget(self.prompt)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        lay.addWidget(self.feedback)
        self.running = QLabel("")
        self.running.setStyleSheet("color:#555;")
        lay.addWidget(self.running)

        brow = QHBoxLayout()
        self.btn_next = QPushButton("Další ▶")
        self.btn_next.clicked.connect(self._advance)
        self.btn_next.setEnabled(False)
        brow.addWidget(self.btn_next)
        self.btn_skip = QPushButton("Přeskočit (ukázat)")
        self.btn_skip.clicked.connect(self._reveal)
        brow.addWidget(self.btn_skip)
        brow.addStretch(1)
        self.btn_close = QPushButton("Konec")
        self.btn_close.clicked.connect(self.accept)
        brow.addWidget(self.btn_close)
        lay.addLayout(brow)

        if not self._plies:
            self.prompt.setText("Za tuhle barvu tu není co hádat (partie je moc krátká "
                                "nebo je start moc pozdě).")
            self.board.set_interactive(False)
        else:
            self._show()

    # ---------------------------------------------------------------- engine
    def _get_engine(self):
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci(self._path)
            apply_engine_options(self._engine, 1, None)
        return self._engine

    def closeEvent(self, e):  # noqa: N802
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass
            self._engine = None
        super().closeEvent(e)

    # ---------------------------------------------------------------- flow
    def _cur_ply(self) -> int:
        return self._plies[self._i]

    def _show(self) -> None:
        k = self._cur_ply()
        b = self._boards[k]
        self.board.set_position(b, None)
        self.board.set_arrows([])
        self.board.set_interactive(True)
        self.feedback.setText("")
        self.btn_next.setEnabled(False)
        self.btn_skip.setEnabled(True)
        self.prompt.setText(f"Tah {(k // 2) + 1}{'.' if self._side == chess.WHITE else '…'}"
                            f"  – na tahu jsi ty. Zahraj tah, který bys volil.")
        self.running.setText(self._running_text())

    def _running_text(self) -> str:
        if not self._scores:
            return f"Tip {self._i + 1} z {len(self._plies)}."
        avg = sum(self._scores) / len(self._scores)
        return (f"Tip {self._i + 1} z {len(self._plies)} · průměrná přesnost tvých "
                f"tipů {avg:.1f} % · shoda s enginem {self._top1}/{len(self._scores)}.")

    def _eval_after(self, k: int, board: chess.Board, guess: chess.Move):
        """(white-cp, wdl bílého | None) po tahu ``guess`` z pozice boards[k].
        Když počítáme s W/D/L, pozici po tahu vždy dopočítáme enginem (top-K
        nemá W/D/L, takže by se „před" a „po" míchaly dvě různé škály)."""
        guci = guess.uci()
        # skutečně zahraný tah: „po" bereme rovnou z rozboru partie (stejná škála)
        if (k < len(self._moves) and guci == self._moves[k].uci()
                and k + 1 < len(self._evals)):
            wl = self._wdls[k + 1] if (self._use_wdl and k + 1 < len(self._wdls)) else None
            return self._evals[k + 1], wl
        tk = self._topks[k] if k < len(self._topks) and self._topks[k] else None
        if tk and not self._use_wdl:
            for cp, uci in tk:
                if uci == guci:
                    return cp, None
        b2 = board.copy(stack=False)
        b2.push(guess)
        if b2.is_checkmate():
            return (_MATE_CP if board.turn == chess.WHITE else -_MATE_CP), None
        if b2.is_game_over():
            return 0, [0, 1000, 0]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            info = self._get_engine().analyse(b2, chess.engine.Limit(depth=self._depth))
        finally:
            QApplication.restoreOverrideCursor()
        sc = info.get("score")
        cp = _white_cp(sc) if sc is not None else 0
        wdl = None
        pw = info.get("wdl") if self._use_wdl else None
        if pw is not None:
            try:
                ww = pw.white()
                wdl = [ww.wins, ww.draws, ww.losses]
            except Exception:
                wdl = None
        return cp, wdl

    def _wdl_pov(self, wdl, white_pov: bool):
        if not wdl:
            return None
        return wdl if white_pov else [wdl[2], wdl[1], wdl[0]]

    def _on_move(self, guess: chess.Move) -> None:
        if not self.btn_skip.isEnabled():        # tah už padl
            return
        k = self._cur_ply()
        board = self._boards[k]
        white_side = (self._side == chess.WHITE)
        pov = 1 if white_side else -1
        wdl_before = self._wdls[k] if k < len(self._wdls) else None
        w_before = win_prob(self._evals[k] * pov, self._wdl_pov(wdl_before, white_side))
        cp_after, wdl_after = self._eval_after(k, board, guess)
        w_after = win_prob(cp_after * pov, self._wdl_pov(wdl_after, white_side))
        acc = move_accuracy(w_before, w_after)
        self._scores.append(acc)

        best_uci = self._bests[k] if k < len(self._bests) else None
        is_top1 = (guess.uci() == best_uci)
        if is_top1:
            self._top1 += 1

        played = self._moves[k]
        best_san = self._san(board, best_uci) if best_uci else "?"
        parts = [f"Tvůj tah <b>{self._san(board, guess.uci())}</b> – přesnost "
                 f"<b>{acc:.0f} %</b>."]
        if is_top1:
            parts.append("Přesně tah enginu. ✓")
        else:
            parts.append(f"Engine: <b>{best_san}</b>.")
        if played != guess:
            parts.append(f"V partii jsi zahrál {self._san(board, played.uci())}.")
        else:
            parts.append("Přesně jak v partii.")
        self.feedback.setTextFormat(Qt.RichText)
        self.feedback.setText("  ".join(parts))

        col = "#2e7d32" if acc >= 80 else "#e65100" if acc >= 55 else "#c62828"
        self.board.set_arrows([
            chess.svg.Arrow(guess.from_square, guess.to_square, color=col),
        ] + ([chess.svg.Arrow(chess.Move.from_uci(best_uci).from_square,
                              chess.Move.from_uci(best_uci).to_square, color="#1565c0")]
             if best_uci and not is_top1 else []))
        self.board.set_interactive(False)
        self.btn_skip.setEnabled(False)
        self.btn_next.setEnabled(True)
        self.running.setText(self._running_text())

    def _reveal(self) -> None:
        k = self._cur_ply()
        board = self._boards[k]
        best_uci = self._bests[k] if k < len(self._bests) else None
        self._scores.append(0.0)
        if best_uci:
            mv = chess.Move.from_uci(best_uci)
            self.board.set_arrows([chess.svg.Arrow(mv.from_square, mv.to_square, color="#1565c0")])
            self.feedback.setText(f"Řešení: {self._san(board, best_uci)} "
                                  f"(v partii {self._san(board, self._moves[k].uci())}).")
        self.board.set_interactive(False)
        self.btn_skip.setEnabled(False)
        self.btn_next.setEnabled(True)

    def _advance(self) -> None:
        self._i += 1
        if self._i >= len(self._plies):
            self._finish()
            return
        self._show()

    def _finish(self) -> None:
        self.board.set_interactive(False)
        self.btn_next.setEnabled(False)
        self.btn_skip.setEnabled(False)
        n = len(self._scores)
        avg = (sum(self._scores) / n) if n else 0.0
        # tvá skutečná přesnost v partii za tuhle barvu (z rozboru)
        pm = per_move(self._evals, self._wdls or None)
        real = [m["acc"] for k, m in enumerate(pm)
                if (k % 2 == 0) == (self._side == chess.WHITE)]
        real_avg = (sum(real) / len(real)) if real else None
        lines = [
            f"<b>Hotovo.</b> {n} tipů, průměrná přesnost <b>{avg:.1f} %</b>, "
            f"shoda s enginem {self._top1}/{n} ({100 * self._top1 / n:.0f} %)."
        ]
        if real_avg is not None:
            d = avg - real_avg
            lines.append(f"Tvá skutečná přesnost v téhle partii za tuhle barvu byla "
                         f"{real_avg:.1f} % (rozdíl {'+' if d >= 0 else ''}{d:.1f}).")
        self.prompt.setTextFormat(Qt.RichText)
        self.prompt.setText("  ".join(lines))
        self.feedback.setText("")
        self.running.setText("")

    def _san(self, board: chess.Board, uci: str | None) -> str:
        if not uci:
            return "?"
        try:
            return board.san(chess.Move.from_uci(uci))
        except Exception:
            return uci
