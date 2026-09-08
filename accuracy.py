"""Metriky přesnosti a chybovosti z hodnocení partie enginem (čistá matematika).

Vstup je vždy ``evals`` – seznam hodnocení z pohledu bílého (cp) po každém
půltahu, ``evals[0]`` je výchozí pozice. Nic z toho nevolá engine.
"""
from __future__ import annotations

import math

CP_CLAMP = 1000
# prahy poklesu pravděpodobnosti výhry (v procentních bodech, škála 0–100) – jako lichess:
# lichess počítá na škále „winning chances" (−1..1) s prahy 0.1 / 0.2 / 0.3,
# což po převodu na win% (0..100) odpovídá 5 / 10 / 15 bodům.
WIN_INACC, WIN_MIST, WIN_BLUND = 5.0, 10.0, 15.0
# práh kritičnosti (EP jednotky, 0–1) nad kterým je pozice „taktická" – jasně nejlepší
# tah citelně mění šanci na výhru; použito pro Tactical Awareness (viz analyze_game)
TACT_CRIT_THRESHOLD = 0.15


def win_pct(cp: int) -> float:
    """cp (z pohledu strany na tahu) -> pravděpodobnost výhry v % (0–100)."""
    cp = max(-CP_CLAMP, min(CP_CLAMP, cp))
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0)


def win_prob(cp: int, wdl=None) -> float:
    """Šance na výhru (0–100). Z reálného W/D/L enginu (``[w, d, l]`` promile ze
    STEJNÉHO POV jako ``cp``), když je k dispozici – to zná contempt, pravidlo
    50 tahů i typ pozice; jinak fallback na sigmoidu z centipawnů. ``w + d/2``."""
    if wdl:
        tot = wdl[0] + wdl[1] + wdl[2]
        if tot > 0:
            return 100.0 * (wdl[0] + 0.5 * wdl[1]) / tot
    return win_pct(cp)


def win_series(evals: list[int], wdls=None) -> list[float]:
    """win% z pohledu BÍLÉHO pro každou pozici (index = půltah, jako ``evals``)."""
    if not wdls:
        return [win_pct(e) for e in evals]
    return [win_prob(e, wdls[k] if k < len(wdls) else None)
            for k, e in enumerate(evals)]


def expected_points(cp: int) -> float:
    """Očekávané skóre (0–1). Zjednodušeně = win% / 100 (křivka je symetrická)."""
    return win_pct(cp) / 100.0


def move_accuracy(win_before: float, win_after: float) -> float:
    """Přesnost jednoho tahu (0–100) z poklesu win% z pohledu hráče (lichess vzorec)."""
    drop = max(0.0, win_before - win_after)
    acc = 103.1668 * math.exp(-0.04354 * drop) - 3.1669 + 1.0
    return max(0.0, min(100.0, acc))


def classify(win_drop: float) -> str | None:
    if win_drop >= WIN_BLUND:
        return "??"
    if win_drop >= WIN_MIST:
        return "?"
    if win_drop >= WIN_INACC:
        return "?!"
    return None


def per_move(evals: list[int], wdls=None) -> list[dict]:
    """Za každý tah dict: white, cp_loss, win_before/after/drop, acc, ep_loss (vše
    POV hráče). ``wdls`` (volitelně, ``[w,d,l]`` bílého na pozici) → šance na výhru
    a očekávané body z reálného W/D/L místo ze sigmoidy; ACPL zůstává v cp."""
    ws = win_series(evals, wdls)         # POV bílého
    out = []
    for k in range(len(evals) - 1):
        white = (k % 2 == 0)
        e0 = evals[k] if white else -evals[k]
        e1 = evals[k + 1] if white else -evals[k + 1]
        e0c = max(-CP_CLAMP, min(CP_CLAMP, e0))
        e1c = max(-CP_CLAMP, min(CP_CLAMP, e1))
        wb = ws[k] if white else 100.0 - ws[k]
        wa = ws[k + 1] if white else 100.0 - ws[k + 1]
        out.append({
            "white": white,
            "cp_loss": max(0, e0c - e1c),
            "win_before": wb,
            "win_after": wa,
            "win_drop": max(0.0, wb - wa),
            "acc": move_accuracy(wb, wa),
            "ep_loss": max(0.0, wb / 100.0 - wa / 100.0),
        })
    return out


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def game_accuracy(evals: list[int], wdls=None, weights=None) -> dict:
    """{'white': %, 'black': %} – lichess styl: průměr váženého a harmonického průměru.

    ``weights`` (volitelně, per půltah k) přebije standardní váhu (lokální rozptyl
    win% – proxy „ostrá fáze partie") vlastní vahou obtížnosti pozice, např.
    komplexitou z multipv (rozptyl top tahů enginu). Pak jde o *counterfactual*
    obtížnost – jak zlé byly alternativy –, ne jen o to, co se v partii semlelo;
    číslo zůstává na stejné škále (harmonická půlka se nemění)."""
    mv = per_move(evals, wdls)
    if not mv:
        return {"white": None, "black": None}
    win_seq = win_series(evals, wdls)               # z pohledu bílého
    window = max(2, min(8, len(mv) // 10))
    res: dict = {}
    for white in (True, False):
        accs, wts = [], []
        for k, m in enumerate(mv):
            if m["white"] != white:
                continue
            if weights is not None and k < len(weights):
                wts.append(max(0.03, float(weights[k])))
            else:
                lo, hi = max(0, k - window), min(len(win_seq), k + window + 1)
                wts.append(max(0.5, _std(win_seq[lo:hi])))
            accs.append(m["acc"])
        key = "white" if white else "black"
        if not accs:
            res[key] = None
            continue
        wmean = sum(a * w for a, w in zip(accs, wts)) / sum(wts)
        harm = len(accs) / sum(1.0 / max(a, 1e-6) for a in accs)
        res[key] = round((wmean + harm) / 2.0, 1)
    return res


# --------------------------------------------------------------- rozbor jedné partie
def _blank() -> dict:
    return {"moves": 0, "cp_loss": 0.0, "ep_loss": 0.0,
            "inacc": 0, "mist": 0, "blund": 0, "t1": 0, "t1_den": 0,
            "cwl_num": 0.0, "cwl_den": 0.0, "tact": 0, "tact_den": 0}


def analyze_game(evals: list[int], moves_uci: list[str], bestmoves: list[str | None],
                 phases: list[str], forced: list[bool], book_plies: int,
                 crit: list[float] | None = None, wdls=None) -> dict:
    """Rozklad partie na stranu a fázi. ``phases[k]``/``forced[k]`` platí pro pozici
    PŘED tahem k+1. ``crit[k]`` (volitelně) je kritičnost tahu k (0..1) – použije se
    pro kritičností vážený ACPL (``cwl_num``/``cwl_den``) a pro Tactical Awareness
    (``tact``/``tact_den`` – shoda s enginem jen v pozicích s ``crit >= TACT_CRIT_THRESHOLD``,
    tj. tam, kde byl jasně nejlepší tah).
    Vrací {'acc': {...}, 'sides': {...}, 'phase': {(side,phase): {...}}}.
    """
    mv = per_move(evals, wdls)
    sides = {"white": _blank(), "black": _blank()}
    phase: dict = {}
    for k, m in enumerate(mv):
        side = "white" if m["white"] else "black"
        kind = classify(m["win_drop"])
        counts_t1 = k >= book_plies and not forced[k] and bool(bestmoves[k])
        hit_t1 = counts_t1 and moves_uci[k] == bestmoves[k]
        c = crit[k] if crit is not None and k < len(crit) else None
        is_tact = counts_t1 and c is not None and c >= TACT_CRIT_THRESHOLD
        for bucket in (sides[side], phase.setdefault((side, phases[k]), _blank())):
            bucket["moves"] += 1
            bucket["cp_loss"] += m["cp_loss"]
            bucket["ep_loss"] += m["ep_loss"]
            if kind == "??":
                bucket["blund"] += 1
            elif kind == "?":
                bucket["mist"] += 1
            elif kind == "?!":
                bucket["inacc"] += 1
            if counts_t1:
                bucket["t1_den"] += 1
                if hit_t1:
                    bucket["t1"] += 1
            if is_tact:
                bucket["tact_den"] += 1
                if hit_t1:
                    bucket["tact"] += 1
            if c is not None:
                bucket["cwl_num"] += m["cp_loss"] * c
                bucket["cwl_den"] += c
    return {"acc": game_accuracy(evals, wdls), "sides": sides, "phase": phase}


# --------------------------------------------------------------- rychlé shrnutí jedné partie
def quick_summary(evals: list[int], wdls=None) -> dict:
    """Jen z evalů (bez tahů) – pro kartu Partie: přesnost a ztráta v očekávaných bodech."""
    acc = game_accuracy(evals, wdls)
    mv = per_move(evals, wdls)
    ep = {"white": 0.0, "black": 0.0}
    for m in mv:
        ep["white" if m["white"] else "black"] += m["ep_loss"]
    return {"accuracy": acc,
            "ep_lost": {"white": round(ep["white"], 2), "black": round(ep["black"], 2)}}


# --------------------------------------------------------------- kompozitní index přesnosti
# váhy jednotlivých složek (0–100 každá); dají se ladit na jednom místě
COMPOSITE_WEIGHTS = {"accuracy": 0.45, "acpl": 0.30, "blunders": 0.15, "t1": 0.10}


def _acpl_score(acpl: float) -> float:
    return 100.0 * math.exp(-max(0.0, acpl) / 90.0)


def _blunder_score(blund_per_100: float) -> float:
    return 100.0 * math.exp(-max(0.0, blund_per_100) / 2.0)


def composite_index(accuracy, acpl, blund_per_100, t1) -> float | None:
    """Jedno číslo 0–100: kombinace přesnosti, ACPL a chybovosti (vlastní, ne CAPS)."""
    if accuracy is None:
        return None
    w = COMPOSITE_WEIGHTS
    parts = [
        (w["accuracy"], float(accuracy)),
        (w["acpl"], _acpl_score(acpl)),
        (w["blunders"], _blunder_score(blund_per_100)),
    ]
    if t1 is not None:
        parts.append((w["t1"], float(t1)))
    tot = sum(wt for wt, _ in parts)
    return round(sum(wt * v for wt, v in parts) / tot, 1)


# --------------------------------------------------------------- odhad výkonnosti (IPR)
def ipr_from_acpl(acpl: float) -> float:
    """Hrubý odhad Ela z ACPL: Elo ≈ 3940 − 580·ln(ACPL). Orientační (spíš blitz/rapid)."""
    a = max(3.0, min(300.0, acpl))
    return max(400.0, min(2900.0, 3940.0 - 580.0 * math.log(a)))


def ipr_gap_significant(ipr_a: float, se_a: float, ipr_b: float, se_b: float,
                        k: float = 2.0) -> bool:
    """Liší se dva odhady IPR (každý s vlastní směrodatnou chybou) o víc než
    ``k`` směrodatných chyb rozdílu? Pro „je forma / rozdíl po barvě reálný, nebo
    je to šum z malého vzorku"."""
    if se_a is None or se_b is None:
        return False
    se = math.sqrt(se_a * se_a + se_b * se_b)
    return se > 0 and abs(ipr_a - ipr_b) > k * se


def _solve3(mat: list[list[float]], rhs: list[float]):
    """Gaussova eliminace pro 3×3. Vrátí [x0,x1,x2] nebo None."""
    m = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(m[r][i]))
        if abs(m[p][i]) < 1e-12:
            return None
        m[i], m[p] = m[p], m[i]
        for r in range(3):
            if r == i:
                continue
            f = m[r][i] / m[i][i]
            for c in range(i, 4):
                m[r][c] -= f * m[i][c]
    return [m[i][3] / m[i][i] for i in range(3)]


def fit_linear(rows: list[tuple]):
    """OLS ``y ≈ b0 + b1·x1 + b2·x2`` bez numpy. rows = [(x1, x2, y), ...].

    Vrátí {'beta': [b0,b1,b2], 'r2': float, 'n': int} nebo None.
    """
    n = len(rows)
    if n < 3:
        return None
    s = [[0.0] * 3 for _ in range(3)]
    t = [0.0] * 3
    for x1, x2, y in rows:
        v = (1.0, x1, x2)
        for i in range(3):
            t[i] += v[i] * y
            for j in range(3):
                s[i][j] += v[i] * v[j]
    beta = _solve3(s, t)
    if beta is None:
        return None
    ymean = sum(r[2] for r in rows) / n
    ss_tot = sum((r[2] - ymean) ** 2 for r in rows) or 1.0
    ss_res = sum((r[2] - (beta[0] + beta[1] * r[0] + beta[2] * r[1])) ** 2 for r in rows)
    return {"beta": beta, "r2": 1.0 - ss_res / ss_tot, "n": n}
