"""Charakter partie a pozice – fázový index, volatilita, dotahování, kritičnost, ostrost.

Vstup je většinou ``evals`` (hodnocení z pohledu bílého, cp) nebo top-K linie
z multipv. Nic z toho nevolá engine.
"""
from __future__ import annotations

import chess

from accuracy import win_series

PHASE_MAX = 24


# --------------------------------------------------------------- fázový index
def phase_value(board: chess.Board) -> float:
    """0–1: 1 = plný materiál (zahájení), 0 = holé koncovky (tapered eval)."""
    minors = chess.popcount(board.knights | board.bishops)
    rooks = chess.popcount(board.rooks)
    queens = chess.popcount(board.queens)
    raw = 4 * queens + 2 * rooks + minors
    return max(0.0, min(1.0, raw / PHASE_MAX))


def phase_name(pv: float) -> str:
    if pv > 0.75:
        return "Zahájení"
    if pv >= 0.25:
        return "Středhra"
    return "Koncovka"


# --------------------------------------------------------------- volatilita
def volatility(evals: list[int], wdls=None) -> dict:
    """Jak moc se hodnocení házelo. Skoky měříme na škále pravděpodobnosti výhry
    (z reálného W/D/L enginu, když je ``wdls`` k dispozici)."""
    if len(evals) < 2:
        return {"mean_swing": 0.0, "median_swing": 0.0, "max_swing": 0.0,
                "max_swing_ply": 0, "reversals": 0, "n": 0}
    w = win_series(evals, wdls)
    swings = [abs(w[k + 1] - w[k]) for k in range(len(w) - 1)]
    srt = sorted(swings)
    mx = max(swings)
    reversals = sum(1 for k in range(len(w) - 1)
                    if (w[k] - 50.0) * (w[k + 1] - 50.0) < 0)
    return {
        "mean_swing": sum(swings) / len(swings),
        "median_swing": srt[len(srt) // 2],
        "max_swing": mx,
        "max_swing_ply": swings.index(mx) + 1,
        "reversals": reversals,
        "n": len(swings),
    }


# --------------------------------------------------------------- dotahování
_SCORE = {"win": 1.0, "draw": 0.5, "loss": 0.0}


def _ep_seq(evals: list[int], player_white: bool, wdls=None) -> list[float]:
    ws = win_series(evals, wdls)          # POV bílého, 0–100
    return [(w if player_white else 100.0 - w) / 100.0 for w in ws]


def _has_run(vals: list[float], pred, need: int = 3) -> bool:
    run = 0
    for v in vals:
        run = run + 1 if pred(v) else 0
        if run >= need:
            return True
    return False


def conversion_record(evals: list[int], player_white: bool, result: str | None,
                      wdls=None) -> dict:
    """Za jednu partii: měl hráč vyhráno / prohráno a jak to dopadlo."""
    ep = _ep_seq(evals, player_white, wdls)
    score = _SCORE.get(result)
    body = ep[:-1] if len(ep) > 1 else ep          # bez úplného závěru
    had_win = _has_run(body, lambda x: x >= 0.75)
    had_loss = _has_run(body, lambda x: x <= 0.25)
    peak = max(ep) if ep else 0.5
    return {
        "result_known": score is not None,
        "had_win": had_win, "had_loss": had_loss,
        "peak_ep": peak, "trough_ep": min(ep) if ep else 0.5,
        "won": result == "win", "not_lost": result in ("win", "draw"),
        "ep_wasted": max(0.0, peak - score) if (had_win and score is not None) else 0.0,
    }


# --------------------------------------------------------------- kritičnost / ostrost
def criticality(top_ep_player_pov: list[float]) -> float:
    """O kolik je nejlepší tah lepší než druhý (EP jednotky, POV hráče na tahu)."""
    if len(top_ep_player_pov) < 2:
        return 0.0
    return max(0.0, top_ep_player_pov[0] - top_ep_player_pov[1])


def complexity(top_ep_player_pov: list[float]) -> float:
    """Rozptyl hodnocení top tahů – velký = snadné se seknout."""
    n = len(top_ep_player_pov)
    if n < 2:
        return 0.0
    m = sum(top_ep_player_pov) / n
    return (sum((e - m) ** 2 for e in top_ep_player_pov) / n) ** 0.5


def sharpness(top_ep_player_pov: list[float], n_legal: int, tol: float = 0.10) -> float:
    """0 = skoro každý tah drží, →1 = drží jen jeden (úzká cesta)."""
    if not top_ep_player_pov or n_legal <= 1:
        return 0.0
    best = top_ep_player_pov[0]
    good = sum(1 for e in top_ep_player_pov if best - e < tol)
    k = min(len(top_ep_player_pov), n_legal)
    return max(0.0, 1.0 - good / k)
