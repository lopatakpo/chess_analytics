"""Filtry nad databází partií pro rozbory (rok, tempo, síla soupeře)."""
from __future__ import annotations

import re

_TC_RE = re.compile(r"^(\d+)(?:\+(\d+))?")

TIME_CLASSES = ["vše", "bullet", "blitz", "rapid", "vážná", "korespondenční"]
ELO_CHOICES = ["vše", "jen silnější soupeř", "jen slabší soupeř", "vyrovnaní (±100)"]


def game_year(g) -> int | None:
    d = (g.headers.get("Date") or g.headers.get("UTCDate") or "").strip()
    m = re.match(r"(\d{4})", d)
    if m and m.group(1) != "0000":
        return int(m.group(1))
    return None


def game_year_month(g) -> str | None:
    """"YYYY-MM" – jemnější granularita než ``game_year`` pro trendy, když
    partie nepokrývají aspoň 2 kalendářní roky."""
    d = (g.headers.get("Date") or g.headers.get("UTCDate") or "").strip()
    m = re.match(r"(\d{4})\.(\d{2})", d)
    if m and m.group(1) != "0000" and m.group(2) != "00":
        return f"{m.group(1)}-{m.group(2)}"
    return None


def time_class(g) -> str:
    tc = (g.headers.get("TimeControl") or "").strip()
    if not tc or tc == "-":
        return "neurčeno"
    if "/" in tc:
        return "korespondenční"
    m = _TC_RE.match(tc)
    if not m:
        return "neurčeno"
    est = int(m.group(1)) + 40 * int(m.group(2) or 0)
    if est < 179:
        return "bullet"
    if est < 479:
        return "blitz"
    if est < 1499:
        return "rapid"
    return "vážná"


def opponent_elo_delta(g, player: str) -> int | None:
    """Elo hráče − Elo soupeře (kladné = hráč má víc)."""
    white = (g.headers.get("White") or "").strip() == player
    try:
        mine = int(g.headers["WhiteElo" if white else "BlackElo"])
        opp = int(g.headers["BlackElo" if white else "WhiteElo"])
    except (KeyError, ValueError, TypeError):
        return None
    return mine - opp


def player_opponent_elo(g, player: str) -> tuple[int | None, int | None]:
    """(Elo hráče, Elo soupeře) – syrové hodnoty, na rozdíl od ``opponent_elo_delta``."""
    white = (g.headers.get("White") or "").strip() == player
    try:
        mine = int(g.headers["WhiteElo" if white else "BlackElo"])
        opp = int(g.headers["BlackElo" if white else "WhiteElo"])
    except (KeyError, ValueError, TypeError):
        return None, None
    return mine, opp


def years_range(games) -> tuple[int, int] | None:
    ys = [y for y in (game_year(g) for g in games) if y]
    return (min(ys), max(ys)) if ys else None


def make_filter(player: str, *, year_from: int | None = None, year_to: int | None = None,
                tc: str = "vše", elo: str = "vše"):
    """Vrátí funkci partie -> bool, nebo None když se nefiltruje."""
    active = ((year_from is not None) or (year_to is not None)
              or (tc not in ("", "vše")) or (elo not in ("", "vše")))
    if not active:
        return None

    def keep(g) -> bool:
        if year_from is not None or year_to is not None:
            y = game_year(g)
            if y is None:
                return False
            if year_from is not None and y < year_from:
                return False
            if year_to is not None and y > year_to:
                return False
        if tc not in ("", "vše") and time_class(g) != tc:
            return False
        if elo not in ("", "vše"):
            d = opponent_elo_delta(g, player)
            if d is None:
                return False
            if elo.startswith("jen silnější") and d >= 0:
                return False
            if elo.startswith("jen slabší") and d <= 0:
                return False
            if elo.startswith("vyrovnaní") and abs(d) > 100:
                return False
        return True

    return keep
