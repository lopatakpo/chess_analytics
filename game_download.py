"""Stažení všech veřejných partií hráče z lichess.org a chess.com.

Bez přihlášení (jen veřejné partie).
- **lichess**: jeden streamovaný PGN endpoint ``/api/games/user/{jméno}``.
- **chess.com**: seznam měsíčních archivů + PGN po měsících (Published-Data API).

Výsledek je jeden PGN text – uloží a načte se jako každý jiný.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse

from PySide6.QtCore import QThread, Signal

from net_util import CertVerifyError, open_url

_CHUNK = 65536
_LICHESS = "https://lichess.org/api/games/user/"
_CHESSCOM = "https://api.chess.com/pub/player/"


class DownloadCancelled(Exception):
    pass


def _count(text: str) -> int:
    return text.count("[Event ")


def _http_message(e: urllib.error.HTTPError, who: str) -> str:
    if e.code == 404:
        return f"{who}: uživatel nenalezen (zkontroluj přesné jméno)."
    if e.code == 429:
        ra = e.headers.get("Retry-After", "")
        return (f"{who}: server tě dočasně omezil (429){f', zkus za {ra} s' if ra else ''}. "
                f"Počkej chvíli a spusť znovu.")
    return f"{who}: HTTP {e.code} {e.reason}."


# --------------------------------------------------------------- lichess
def lichess_games(username: str, *, rated_only: bool = False,
                  on_progress=None, should_stop=None) -> str:
    """Stáhne všechny veřejné partie hráče z lichess jako jeden PGN text."""
    params = {"moves": "true", "tags": "true", "clocks": "false",
              "evals": "false", "opening": "true"}
    if rated_only:
        params["rated"] = "true"
    url = f"{_LICHESS}{urllib.parse.quote(username.strip())}?{urllib.parse.urlencode(params)}"
    buf: list[str] = []
    n = 0
    with open_url(url, headers={"Accept": "application/x-chess-pgn"}, timeout=60) as r:
        while True:
            if should_stop and should_stop():
                raise DownloadCancelled()
            chunk = r.read(_CHUNK)
            if not chunk:
                break
            piece = chunk.decode("utf-8", "replace")
            buf.append(piece)
            n += piece.count("[Event ")
            if on_progress:
                on_progress(n)
    return "".join(buf)


# --------------------------------------------------------------- chess.com
def _month_in_range(url: str, since: tuple | None, until: tuple | None) -> bool:
    try:
        y, m = int(url.rsplit("/", 2)[-2]), int(url.rsplit("/", 2)[-1])
    except (ValueError, IndexError):
        return True
    if since and (y, m) < since:
        return False
    if until and (y, m) > until:
        return False
    return True


def chesscom_games(username: str, *, since: tuple | None = None,
                   until: tuple | None = None, on_progress=None,
                   should_stop=None) -> str:
    """Stáhne všechny veřejné partie hráče z chess.com (po měsících) jako PGN."""
    u = urllib.parse.quote(username.strip().lower())
    with open_url(f"{_CHESSCOM}{u}/games/archives", timeout=30) as r:
        archives = json.load(r).get("archives", [])
    archives = [a for a in archives if _month_in_range(a, since, until)]
    out: list[str] = []
    n = 0
    errors = 0
    for a in archives:
        if should_stop and should_stop():
            raise DownloadCancelled()
        try:
            with open_url(f"{a}/pgn", timeout=45) as r:
                txt = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            errors += 1
            continue
        if txt.strip():
            out.append(txt if txt.endswith("\n") else txt + "\n")
            n += txt.count("[Event ")
            if on_progress:
                on_progress(n)
        time.sleep(0.25)                    # chess.com chce sériové požadavky
    if errors and not out:
        raise RuntimeError("chess.com: žádný měsíční archiv se nepodařilo stáhnout.")
    return "\n".join(out)


# --------------------------------------------------------------- worker
class GameDownloadWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, int)         # PGN text, počet partií
    failed = Signal(str)

    def __init__(self, lichess_user: str = "", chesscom_user: str = "",
                 rated_only: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._li = (lichess_user or "").strip()
        self._cc = (chesscom_user or "").strip()
        self._rated_only = rated_only
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        parts: list[str] = []
        try:
            if self._li:
                self.progress.emit(f"lichess ({self._li}): navazuji spojení…")
                parts.append(lichess_games(
                    self._li, rated_only=self._rated_only,
                    on_progress=lambda k: self.progress.emit(
                        f"lichess ({self._li}): {k} partií…"),
                    should_stop=lambda: self._stop))
            if self._cc:
                self.progress.emit(f"chess.com ({self._cc}): načítám seznam archivů…")
                parts.append(chesscom_games(
                    self._cc,
                    on_progress=lambda k: self.progress.emit(
                        f"chess.com ({self._cc}): {k} partií…"),
                    should_stop=lambda: self._stop))
        except DownloadCancelled:
            self.failed.emit("Stahování zrušeno.")
            return
        except CertVerifyError:
            self.failed.emit(
                "Ověření HTTPS certifikátu selhalo (nejspíš kvůli antiviru / "
                "firewallu). Zkus to znovu – aplikace nabídne spojení bez "
                "ověření certifikátu.")
            return
        except urllib.error.HTTPError as e:
            who = "chess.com" if (self._cc and not parts) else "lichess"
            self.failed.emit(_http_message(e, who))
            return
        except urllib.error.URLError as e:
            self.failed.emit(f"Chyba sítě: {e.reason}. Zkontroluj připojení.")
            return
        except Exception as e:
            self.failed.emit(f"Chyba stahování: {e}")
            return

        text = "\n\n".join(p for p in parts if p and p.strip())
        n = _count(text)
        if n == 0:
            self.failed.emit(
                "Nestáhly se žádné partie – buď je jméno špatně, nebo hráč nemá "
                "žádné veřejné partie.")
            return
        self.finished_ok.emit(text, n)
