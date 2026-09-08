"""Obecné statistické nástroje (bod 7 rozboru dat) – nezávislé na šachu.

Wilsonův interval, empirical-Bayes shrinkage, Elo-očekávané skóre, Kaplan–Meierův
odhad (cenzurovaná data), „štěstí" (z-skóre) a jednoduchá logistická regrese
(vlastní IRLS, bez numpy). Nic z toho nevolá engine.
"""
from __future__ import annotations

import math

Z95 = 1.959963985


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# --------------------------------------------------------------- test dvou podílů
def two_prop_p(k1: int, n1: int, k2: int, n2: int) -> float | None:
    """Oboustranná p-hodnota dvouvýběrového z-testu podílů (poolovaná varianta).
    ``None`` když je vzorek prázdný."""
    if n1 <= 0 or n2 <= 0:
        return None
    p_pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n2))
    if se == 0.0:
        return 1.0
    z = (k1 / n1 - k2 / n2) / se
    return max(0.0, min(1.0, 2.0 * (1.0 - norm_cdf(abs(z)))))


def one_prop_p(k: int, n: int, p0: float) -> float | None:
    """Oboustranná p-hodnota jednovýběrového z-testu: liší se podíl k/n od *známé*
    referenční hodnoty ``p0`` (např. winrate populace z Opening Exploreru, kde je
    vzorek tak velký, že se bere jako pevný)? ``None`` když n ≤ 0."""
    if n <= 0 or not (0.0 < p0 < 1.0):
        return None
    se = math.sqrt(p0 * (1.0 - p0) / n)
    if se == 0.0:
        return 1.0
    z = (k / n - p0) / se
    return max(0.0, min(1.0, 2.0 * (1.0 - norm_cdf(abs(z)))))


def bh_reject(pvals: list, alpha: float = 0.05) -> list[bool]:
    """Benjamini–Hochberg (kontrola FDR): pro každou p-hodnotu vrátí True, když
    se zamítá H0 při FDR ``alpha``. ``None`` p-hodnoty → False."""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    m = len(idx)
    out = [False] * len(pvals)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvals[i])
    kmax = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= alpha * rank / m:
            kmax = rank
    for rank, i in enumerate(order, start=1):
        if rank <= kmax:
            out[i] = True
    return out


def winrate_outliers(buckets: list, w0: int, n0: int, alpha: float = 0.05,
                     min_effect: float = 0.025):
    """``buckets`` = [(výhry, rozhodnuté), …]. Každý koš se testuje proti
    **zbytku** souboru (celek bez toho koše), pak Benjamini–Hochberg. Aby se
    u obřích vzorků neoznačovaly triviální rozdíly, koš se označí, jen když je
    i **věcný rozdíl** aspoň ``min_effect`` (v podílu) od celkového winrate.
    Vrátí (flags: list[bool], pvals: list[float|None])."""
    p0 = (w0 / n0) if n0 else 0.5
    pvals = []
    for w, n in buckets:
        wr, nr = w0 - w, n0 - n
        pvals.append(two_prop_p(w, n, wr, nr) if (n >= 1 and nr >= 1) else None)
    flags = bh_reject(pvals, alpha)
    for i, (w, n) in enumerate(buckets):
        if flags[i] and n > 0 and abs(w / n - p0) < min_effect:
            flags[i] = False
    return flags, pvals


# --------------------------------------------------------------- Wilsonův interval
def wilson_interval(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """95% interval spolehlivosti pro podíl k/n. Vrátí (lo, hi) v 0..1, nebo None."""
    if n <= 0:
        return None
    p = k / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (center - half) / denom), min(1.0, (center + half) / denom))


# --------------------------------------------------------------- empirical-Bayes shrinkage
def estimate_shrink_m(buckets: list[tuple[int, int]]) -> float:
    """Odhadne sílu priorku (pseudopočet partií) pro shrinkage – DerSimonian–Laird
    styl, inverse-variance vážený přes koše (k_i, n_i), aby jeden malý/šumivý koš
    nerozhodl o síle pro všechny ostatní. Malé skutečné rozdíly mezi koši → velké m
    (koším se moc nevěří); velké skutečné rozdíly → malé m (koším se věří)."""
    pts = [(k / n, n) for k, n in buckets if n > 0]
    if len(pts) < 2:
        return 10.0
    total_n = sum(n for _, n in pts)
    p0 = sum(p * n for p, n in pts) / total_n
    p0 = min(0.999, max(0.001, p0))
    weights = [n / (p0 * (1 - p0)) for _, n in pts]   # 1 / (dovnitř-koše rozptyl)
    sw = sum(weights)
    if sw <= 0:
        return 10.0
    pw = sum(w * p for w, (p, _) in zip(weights, pts)) / sw
    q_stat = sum(w * (p - pw) ** 2 for w, (p, _) in zip(weights, pts))
    k = len(pts)
    sw2 = sum(w * w for w in weights)
    c = sw - sw2 / sw
    tau2 = max(0.0, (q_stat - (k - 1)) / c) if c > 0 else 0.0
    if tau2 <= 1e-9:
        return 200.0                                  # koše jsou v podstatě homogenní
    m = p0 * (1 - p0) / tau2 - 1.0
    return max(2.0, min(200.0, m))


def shrink_rate(k: int, n: int, p0: float, m: float) -> float:
    """Stažený odhad podílu k/n směrem k celkovému průměru p0 (síla m partií)."""
    return (k + m * p0) / (n + m)


# --------------------------------------------------------------- Elo
def elo_expected(rating: float, opp_rating: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((opp_rating - rating) / 400.0))


def elo_expected_from_delta(delta: float) -> float:
    """Stejné jako :func:`elo_expected`, jen z rozdílu (hráč − soupeř) přímo."""
    return 1.0 / (1.0 + 10.0 ** (-delta / 400.0))


def luck_z(pairs: list[tuple[float, float]]) -> float | None:
    """Pairs (skutečné_skóre, očekávané_skóre dle Elo). z = kolik směr. odchylek
    se skutečný součet liší od očekávaného (binomický rozptyl); ~0 = odpovídá síle
    soupeřů, vysoké |z| = neobvykle šťastná/nešťastná série."""
    if not pairs:
        return None
    actual = sum(a for a, _ in pairs)
    expected = sum(e for _, e in pairs)
    var = sum(e * (1 - e) for _, e in pairs)
    if var <= 0:
        return None
    return (actual - expected) / math.sqrt(var)


# --------------------------------------------------------------- Kaplan–Meier
def kaplan_meier(records: list[tuple[int, bool]]) -> dict:
    """``records`` = [(čas, událost_nastala?)]. Necenzurované (event=False) = čas
    doběhl partie, aniž událost nastala. Vrátí {'median','curve','n','n_events'}.
    """
    n_total = len(records)
    n_events = sum(1 for _, e in records if e)
    event_times = sorted({t for t, e in records if e})
    surv = 1.0
    curve = [(0, 1.0)]
    for t in event_times:
        at_risk = sum(1 for tt, _ in records if tt >= t)
        d = sum(1 for tt, e in records if tt == t and e)
        if at_risk > 0:
            surv *= (1.0 - d / at_risk)
        curve.append((t, surv))
    median = next((t for t, s in curve if s <= 0.5), None)
    return {"median": median, "curve": curve, "n": n_total, "n_events": n_events}


# --------------------------------------------------------------- lineární algebra (obecná)
def _solve(mat: list[list[float]], rhs: list[float]):
    n = len(mat)
    m = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(m[r][i]))
        if abs(m[p][i]) < 1e-10:
            return None
        m[i], m[p] = m[p], m[i]
        piv = m[i][i]
        for c in range(i, n + 1):
            m[i][c] /= piv
        for r in range(n):
            if r == i:
                continue
            f = m[r][i]
            if f == 0:
                continue
            for c in range(i, n + 1):
                m[r][c] -= f * m[i][c]
    return [m[i][n] for i in range(n)]


# --------------------------------------------------------------- logistická regrese
def logistic_fit(X: list[list[float]], y: list[float], ridge: float = 1.0,
                 iters: int = 25) -> dict | None:
    """IRLS/Newton fit ``logit(P) = b0 + b·x`` s ridge regularizací (stabilita na
    malých vzorcích). ``X`` bez sloupce jedniček (přidá se sám). Vrátí
    {'beta':[b0,b1,...], 'n':N} nebo None při selhání.
    """
    n = len(X)
    if n < 8 or not X or len(X) != len(y):
        return None
    p = len(X[0]) + 1
    beta = [0.0] * p

    def row(x):
        return [1.0] + list(x)

    for _ in range(iters):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        for xi, yi in zip(X, y):
            r = row(xi)
            z = sum(b * v for b, v in zip(beta, r))
            z = max(-30.0, min(30.0, z))
            pi = 1.0 / (1.0 + math.exp(-z))
            w = max(1e-6, pi * (1.0 - pi))
            err = yi - pi
            for a in range(p):
                grad[a] += r[a] * err
                for b in range(p):
                    hess[a][b] += w * r[a] * r[b]
        for a in range(p):
            hess[a][a] += ridge
            grad[a] -= ridge * beta[a]
        delta = _solve(hess, grad)
        if delta is None:
            return None
        beta = [b + d for b, d in zip(beta, delta)]
        if max(abs(d) for d in delta) < 1e-6:
            break
    return {"beta": beta, "n": n}


def logistic_predict(beta: list[float], x: list[float]) -> float:
    z = beta[0] + sum(b * v for b, v in zip(beta[1:], x))
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))
