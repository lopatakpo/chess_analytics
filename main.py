"""Spouštěč aplikace.

Použití:
    python main.py [partie.pgn]
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from pgn_game import load_pgn


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    if len(sys.argv) > 1:
        try:
            games = load_pgn(sys.argv[1])
            win._set_games(games, source=sys.argv[1])
        except Exception as exc:  # pragma: no cover
            print(f"Nepodařilo se načíst {sys.argv[1]}: {exc}")

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
