"""Detekce anomálních partií hráče – Mahalanobisova vzdálenost ve feature space.

Každá partie se popíše číselným vektorem (jen z hlaviček + jednoho průchodu tahy,
bez enginu – délka, načasování rošády / výměny dam / prvního braní, materiálové
výkyvy, pěšcová struktura, typ centra, vztah rošád, Elo rozdíl, typ konce). Pak:

1. robustní standardizace každého sloupce (medián a MAD místo průměru a σ),
2. kovarianční matice se shrinkage k jednotkové (aby šla invertovat i při
   kolinearitě, např. „IQP" implikuje „izolák"),
3. Mahalanobis d² = zᵀ Σ⁻¹ z pro každou partii,
4. **reweighting** – d² se přepočítá po odříznutí 10 % nejextrémnějších partií,
   ať samotné odlehlé partie nenafouknou Σ a „neschovají se",
5. d² → percentil „divnosti" přes χ²(D) (Wilsonova–Hilfertyho aproximace),
6. u každé odlehlé partie **rozklad d² na příspěvky jednotlivých featur**
   (zᵢ·(Σ⁻¹z)ᵢ) → které vlastnosti ji dělají divnou.

Nic z toho nevolá engine ani numpy.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from patterns import _features
from player_analysis import game_matches

_CENTER_ORD = {"otevřené": 0.0, "pootevřené": 1.0, "zavřené": 2.0}

# (klíč, popisek, funkce Features -> float | None).  None = chybí, doplní se medián.
_FEATURES: list[tuple[str, str, "callable"]] = [
    ("moves", "délka partie (tahy)", lambda f: float(f.moves)),
    ("first_cap", "tah prvního braní", lambda f: _opt(f.first_capture_move)),
    ("queens_traded", "výměna dam (0/1)", lambda f: 1.0 if f.queens_off_move is not None else 0.0),
    ("queens_off_move", "kdy zmizely dámy", lambda f: _opt(f.queens_off_move)),
    ("castled", "hráč rošoval (0/1)", lambda f: 0.0 if f.my_castle == "-" else 1.0),
    ("castle_move", "kdy hráč rošoval", lambda f: _opt(f.my_castle_move)),
    ("opp_castled", "soupeř rošoval (0/1)", lambda f: 0.0 if f.opp_castle == "-" else 1.0),
    ("opposite_castle", "opačné rošády (0/1)",
     lambda f: 1.0 if f.castle_relation == "opačná strana" else 0.0),
    ("exchanges_early", "výměny figur do 20. tahu", lambda f: float(f.exchanges_early)),
    ("max_deficit", "největší materiálové manko", lambda f: float(f.my_max_deficit)),
    ("max_surplus", "největší materiálový náskok", lambda f: float(f.my_max_surplus)),
    ("islands", "pěšcové ostrovy", lambda f: float(f.islands)),
    ("tension", "napětí ve struktuře", lambda f: float(f.tension)),
    ("backward", "zpátečnický pěšec (0/1)", lambda f: float(f.backward)),
    ("isolated", "izolovaný pěšec vč. IQP (0/1)",
     lambda f: 1.0 if (f.isolated or f.iqp) else 0.0),
    ("doubled", "zdvojení pěšci (0/1)", lambda f: float(f.doubled)),
    ("my_passed", "volný pěšec hráče (0/1)", lambda f: float(f.my_passed)),
    ("opp_passed", "volný pěšec soupeře (0/1)", lambda f: float(f.opp_passed)),
    ("bishop_pair", "dvojice střelců (0/1)", lambda f: float(f.had_bishop_pair)),
    ("pawn_storm", "pěšcová bouře (0/1)", lambda f: float(f.pawn_storm)),
    ("king_center", "král v centru (0/1)", lambda f: float(f.king_in_center)),
    ("center", "typ centra (otevř→zavř)", lambda f: _CENTER_ORD.get(f.center, 1.0)),
    ("elo_delta", "Elo rozdíl (hráč − soupeř)",
     lambda f: float(f.elo_delta) if f.elo_delta is not None else None),
    ("mate", "konec matem (0/1)", lambda f: 1.0 if f.ending == "mat" else 0.0),
    ("resign_time", "konec vzdáním / čas (0/1)",
     lambda f: 1.0 if f.ending == "vzdání / čas" else 0.0),
]


def _opt(v):
    return float(v) if v is not None else None


@dataclass
class Anomaly:
    game_index: int
    label: str
    result: str | None
    d2: float
    weirdness: float                                   # 0..1 (χ² percentil)
    reasons: list = field(default_factory=list)        # [(popisek, hodnota, medián, příspěvek)]


@dataclass
class AnomalyReport:
    n_games: int
    n_features: int
    features_used: list[str]
    anomalies: list[Anomaly]                           # top N podle d²
    median_d2: float


# ------------------------------------------------------------------ lineární algebra
def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _mad(xs: list[float], med: float) -> float:
    return _median([abs(x - med) for x in xs])


def _mean_std(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / n)


def _norm_ppf(p: float) -> float:
    """Inverzní normální CDF – Acklamova racionální aproximace (|chyba| < 1.2e-9)."""
    p = min(1 - 1e-12, max(1e-12, p))
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    dd = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
          3.754408661907416e+00)
    plow, phigh = 0.02425, 0.97575
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((dd[0] * q + dd[1]) * q + dd[2]) * q + dd[3]) * q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
           ((((dd[0] * q + dd[1]) * q + dd[2]) * q + dd[3]) * q + 1)


def _normal_scores(col: list[float]) -> list[float]:
    """Van der Waerdenova transformace: každou hodnotu nahradí normální kvantil
    jejího průměrného pořadí. Marginálně ~N(0,1) bez ohledu na tvar (šikmé počty,
    binární featury) → Mahalanobis pak měří jen mnohorozměrnou strukturu."""
    n = len(col)
    order = sorted(range(n), key=lambda i: col[i])
    avg_rank = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and col[order[j + 1]] == col[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0                   # průměrné pořadí (1-based)
        for t in range(i, j + 1):
            avg_rank[order[t]] = r
        i = j + 1
    return [_norm_ppf(avg_rank[k] / (n + 1.0)) for k in range(n)]


def _inv(mat: list[list[float]]):
    """Inverze čtvercové matice Gauss–Jordanovou eliminací s pivotací."""
    n = len(mat)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(mat)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(a[r][i]))
        if abs(a[p][i]) < 1e-12:
            return None
        a[i], a[p] = a[p], a[i]
        piv = a[i][i]
        a[i] = [x / piv for x in a[i]]
        for r in range(n):
            if r != i and abs(a[r][i]) > 1e-15:
                f = a[r][i]
                a[r] = [a[r][c] - f * a[i][c] for c in range(2 * n)]
    return [row[n:] for row in a]


def _cov(rows: list[list[float]]) -> list[list[float]]:
    n = len(rows)
    d = len(rows[0])
    cov = [[0.0] * d for _ in range(d)]
    for r in rows:
        for i in range(d):
            for j in range(i, d):
                cov[i][j] += r[i] * r[j]
    for i in range(d):
        for j in range(i, d):
            cov[i][j] /= n
            cov[j][i] = cov[i][j]
    return cov


def _shrink(cov: list[list[float]], alpha: float) -> list[list[float]]:
    d = len(cov)
    return [[(1 - alpha) * cov[i][j] + (alpha if i == j else 0.0)
             for j in range(d)] for i in range(d)]


def _mahalanobis_all(z_rows, alpha):
    """z_rows = standardizované řádky. Vrátí (d2 list, Σ⁻¹)."""
    sig = _shrink(_cov(z_rows), alpha)
    inv = _inv(sig)
    if inv is None:                       # fallback: silnější shrinkage
        inv = _inv(_shrink(_cov(z_rows), min(0.9, alpha * 3 + 0.2)))
    d = len(z_rows[0])
    out = []
    for z in z_rows:
        w = [sum(inv[i][j] * z[j] for j in range(d)) for i in range(d)]
        out.append(sum(z[i] * w[i] for i in range(d)))
    return out, inv


def _chi2_percentile(d2: float, k: int) -> float:
    """P(χ²_k ≤ d2) – Wilsonova–Hilfertyho aproximace + normální CDF (math.erf)."""
    if d2 <= 0:
        return 0.0
    t = (d2 / k) ** (1.0 / 3.0)
    m = 1.0 - 2.0 / (9.0 * k)
    s = math.sqrt(2.0 / (9.0 * k))
    zt = (t - m) / s
    return 0.5 * (1.0 + math.erf(zt / math.sqrt(2.0)))


# ------------------------------------------------------------------ hlavní funkce
def detect_anomalies(games, player: str, colors: str = "both", keep=None,
                     top: int = 25, min_games: int = 25) -> AnomalyReport:
    idx, feats = [], []
    for gi, g in enumerate(games):
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        try:
            feats.append(_features(g, player))
            idx.append(gi)
        except Exception:
            continue
    n = len(feats)
    if n < min_games:
        return AnomalyReport(n, 0, [], [], 0.0)

    # syrová matice + medián-imputace chybějících
    raw = [[fn(f) for _k, _l, fn in _FEATURES] for f in feats]
    keys = [k for k, _l, _fn in _FEATURES]
    labels = [l for _k, l, _fn in _FEATURES]
    d0 = len(_FEATURES)
    col_median = []
    for j in range(d0):
        present = [raw[i][j] for i in range(n) if raw[i][j] is not None]
        col_median.append(_median(present) if present else 0.0)
    for i in range(n):
        for j in range(d0):
            if raw[i][j] is None:
                raw[i][j] = col_median[j]

    # vyhodit sloupce prakticky bez rozptylu (< 1 % nebo > 99 % jedné hodnoty)
    use_cols = []
    for j in range(d0):
        col = [raw[i][j] for i in range(n)]
        if Counter(col).most_common(1)[0][1] / n <= 0.99:
            use_cols.append(j)
    if len(use_cols) < 3:
        return AnomalyReport(n, 0, [], [], 0.0)
    d = len(use_cols)
    # van der Waerdenova (rank → normální kvantil) transformace každého sloupce –
    # sjednotí tvary (šikmé počty, binární featury) na ~N(0,1)
    cols_ns = [_normal_scores([raw[i][j] for i in range(n)]) for j in use_cols]
    z = [[cols_ns[c][i] for c in range(d)] for i in range(n)]

    alpha = 0.25
    # 1. průchod – d² se stejnou standardizací, jen kvůli výběru inlierů
    d2a, _inv1 = _mahalanobis_all(z, alpha)
    order = sorted(range(n), key=lambda i: d2a[i])
    keep_n = max(d + 2, int(round(n * 0.90)))
    # reweighting: přepočítej JEN Σ z 90 % nejméně divných partií (stejné z)
    z_in = [z[i] for i in order[:keep_n]]
    inv = _inv(_shrink(_cov(z_in), alpha)) or _inv(_shrink(_cov(z_in), 0.6))
    d2f, contribs = [], []
    for zr in z:
        w = [sum(inv[i][j] * zr[j] for j in range(d)) for i in range(d)]
        d2f.append(sum(zr[i] * w[i] for i in range(d)))
        contribs.append([zr[i] * w[i] for i in range(d)])

    # reweighting Σ z inlierů d² trochu stlačí – přeškáluj, ať medián sedí na
    # teoretický medián χ²(d), aby „divnost %" (χ² percentil) byla poctivá
    obs_med = _median(d2f) or 1.0
    chi2_med = d * (1.0 - 2.0 / (9.0 * d)) ** 3
    sc = chi2_med / obs_med
    d2f = [x * sc for x in d2f]
    contribs = [[c * sc for c in row] for row in contribs]
    med_d2 = _median(d2f)
    ranked = sorted(range(n), key=lambda i: -d2f[i])[:top]
    anomalies = []
    for i in ranked:
        cs = contribs[i]
        top_feats = sorted(range(d), key=lambda c: -abs(cs[c]))[:3]
        reasons = []
        for c in top_feats:
            j = use_cols[c]
            reasons.append((labels[j], round(raw[i][j], 1), round(col_median[j], 1),
                            round(cs[c], 1)))
        anomalies.append(Anomaly(
            game_index=idx[i], label=games[idx[i]].label(),
            result=feats[i].result, d2=round(d2f[i], 1),
            weirdness=round(_chi2_percentile(d2f[i], d), 4),
            reasons=reasons))

    return AnomalyReport(n, d, [labels[use_cols[c]] for c in range(d)],
                         anomalies, round(med_d2, 1))
