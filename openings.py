"""Kategorizace partií podle zahájení (ECO).

Pořadí zdrojů názvu zahájení:
1. hlavičky PGN ``[Opening]`` / ``[Variation]``,
2. databáze ``eco.tsv`` (lichess chess-openings, ~3800 variant) podle úvodních
   tahů – nejdelší shodný začátek,
3. ``[ECOUrl]`` z chess.com (podrobný název varianty).
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

import chess

from endgames import sort_key
from player_analysis import game_matches, player_result

# ---- databáze zahájení (lichess chess-openings, ~3800 variant) --------------
_ECO_DB: dict[tuple, tuple] = {}      # (UCI tahy) -> (ECO kód, název)
_ECO_NAMES: dict[str, str] = {}       # ECO kód -> nejobecnější název


def _load_eco() -> int:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eco.tsv")
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) == 3 and p[2]:
                    rows.append((p[0], p[1], p[2]))
    except OSError:
        return 0
    rows.sort(key=lambda r: r[2].count(" "))     # kratší linie první
    maxlen = 0
    for eco, name, sans in rows:
        board = chess.Board()
        ucis = []
        try:
            for san in sans.split():
                mv = board.parse_san(san)
                ucis.append(mv.uci())
                board.push(mv)
        except Exception:
            continue
        if ucis:
            _ECO_DB[tuple(ucis)] = (eco, name)
            _ECO_NAMES.setdefault(eco, name)
            maxlen = max(maxlen, len(ucis))
    return maxlen


_MAX_LINE = _load_eco()


def classify_by_moves(uci_moves) -> tuple[str | None, str, int]:
    """(ECO, název, hloubka knihy) – nejdelší začátek partie shodný s databází."""
    best = (None, "Nezařazeno", 0)
    for n in range(1, min(len(uci_moves), _MAX_LINE) + 1):
        hit = _ECO_DB.get(tuple(uci_moves[:n]))
        if hit:
            best = (hit[0], hit[1], n)
    return best



_SLUG_APOS = {
    "Kings": "King's", "Queens": "Queen's", "Bishops": "Bishop's",
    "Owens": "Owen's", "Alekhines": "Alekhine's", "Colles": "Colle's",
    "Reti": "Réti", "Ruy": "Ruy", "Nimzo": "Nimzo", "Grunfeld": "Grünfeld",
}
_SLUG_DROP_PREFIX = (
    "King's Pawn Opening ", "King's Pawn Game ", "Queen's Pawn Opening ",
    "Queen's Pawn Game ", "King's Knight Opening ", "Queen's Pawn ",
)

# překlad nejběžnějších rodin zahájení na český název kvůli konzistenci
_SLUG_CZ = {
    "Sicilian Defense": "Sicilská obrana", "French Defense": "Francouzská obrana",
    "Caro-Kann Defense": "Caro-Kann obrana", "Scandinavian Defense": "Skandinávská obrana",
    "Pirc Defense": "Pircova obrana", "Modern Defense": "Moderní obrana",
    "Alekhine Defense": "Alechinova obrana", "Owen Defense": "Owenova obrana",
    "Ruy Lopez": "Španělská hra", "Ruy Lopez Opening": "Španělská hra",
    "Italian Game": "Italská hra", "Vienna Game": "Vídeňská hra",
    "Bishop's Opening": "Zahájení střelcem", "Scotch Game": "Skotská hra",
    "Four Knights Game": "Hra čtyř jezdců", "Three Knights Opening": "Hra tří jezdců",
    "Petrov's Defense": "Ruská hra", "Russian Game": "Ruská hra",
    "Philidor Defense": "Philidorova obrana", "Latvian Gambit": "Lotyšský gambit",
    "Elephant Gambit": "Elefantův gambit", "Danish Gambit": "Dánský gambit",
    "Center Game": "Středová hra", "King's Gambit": "Královský gambit",
    "King's Gambit Accepted": "Královský gambit přijatý",
    "King's Gambit Declined": "Královský gambit odmítnutý",
    "Queen's Gambit Declined": "Dámský gambit odmítnutý",
    "Queen's Gambit Accepted": "Dámský gambit přijatý",
    "Queen's Gambit": "Dámský gambit", "Slav Defense": "Slovanská obrana",
    "London System": "Londýnský systém", "Colle System": "Colleův systém",
    "Nimzo-Indian Defense": "Nimcovičova indická obrana",
    "Nimzo Indian Defense": "Nimcovičova indická obrana",
    "King's Indian Defense": "Královská indická obrana",
    "Queen's Indian Defense": "Dámská indická obrana",
    "Grunfeld Defense": "Grünfeldova indická obrana",
    "Grünfeld Defense": "Grünfeldova indická obrana",
    "Catalan Opening": "Katalánská hra", "English Opening": "Anglická hra",
    "Reti Opening": "Rétiho hra", "Réti Opening": "Rétiho hra",
    "Dutch Defense": "Holandská obrana", "Benoni Defense": "Benoni",
    "Benko Gambit": "Volžský gambit", "Trompowsky Attack": "Trompowského útok",
}
_SLUG_CZ_KEYS = sorted(_SLUG_CZ, key=len, reverse=True)


def _czech(name: str) -> str:
    """Přeloží začátek anglického názvu zahájení na český (rodina + zbytek)."""
    for en in _SLUG_CZ_KEYS:
        if name == en:
            return _SLUG_CZ[en]
        for sep in (": ", ", ", " "):
            if name.startswith(en + sep):
                return _SLUG_CZ[en] + name[len(en):]
    return name


def _prettify_slug(url_or_slug: str) -> tuple[str, int]:
    """'…/Kings-Gambit-Accepted-Fischer-Defense-4.Bc4'
    -> (\"King's Gambit Accepted Fischer Defense\", 7).  Druhá hodnota = odhad
    hloubky teorie v půltazích z tahové přípony ve slugu."""
    slug = url_or_slug.rstrip("/").rsplit("/", 1)[-1]
    if not slug or slug.lower() == "undefined":
        return "", 0
    words = []
    depth = 0
    for part in slug.split("-"):
        m = re.match(r"^(\d+)\.(\.\.)?", part)
        if m:
            mvno = int(m.group(1))
            depth = max(depth, mvno * 2 if m.group(2) else mvno * 2 - 1)
            continue
        # do názvu jen jména (žádné tahy, čísla, castling O, spojky)
        if (depth == 0 and part not in ("", "O", "with", "and", "the", "of", "in")
                and part.replace("'", "").isalpha()):
            words.append(_SLUG_APOS.get(part, part))
    if len(words) >= 2 and words[-1] in ("Variation", "Line"):
        words.pop()
    name = " ".join(words).strip()
    for pref in _SLUG_DROP_PREFIX:
        if name.startswith(pref) and len(name) > len(pref):
            name = name[len(pref):]
            break
    if name in ("Opening", "Game", "Defense", "Defence", "Variation",
                "System", "Attack", "Gambit"):
        return "", depth
    return _czech(name), depth


def game_opening(game) -> tuple[str | None, str, int]:
    """(ECO nebo None, název zahájení, ply na konci teoretické linie)."""
    eco = (game.headers.get("ECO") or "").strip().upper()
    hdr_name = (game.headers.get("Opening") or "").strip()
    var = (game.headers.get("Variation") or "").strip()
    if var and var.lower() not in hdr_name.lower():
        hdr_name = f"{hdr_name}: {var}" if hdr_name else var

    by_eco, by_name, depth = classify_by_moves([m.uci() for m in game.moves[:_MAX_LINE]])
    hdr_code = eco[:3] if (len(eco) >= 3 and eco[0] in "ABCDE" and eco[1:3].isdigit()) else None
    code = by_eco or hdr_code

    if hdr_name:
        name = hdr_name
    elif by_name != "Nezařazeno":
        name = _czech(by_name)
    else:
        slug_name, slug_depth = _prettify_slug(
            game.headers.get("ECOUrl") or game.headers.get("ECOURL") or "")
        depth = max(depth, min(slug_depth, len(game.moves)))
        name = slug_name or _czech(_ECO_NAMES.get(code or "", "")) or code or "Nezařazeno"

    return code, name, depth


# ------------------------------------------------------------------- rozbor
@dataclass
class OpeningEntry:
    game_index: int
    enter_ply: int
    result: str | None


@dataclass
class OpeningVariation:
    name: str
    entries: list[OpeningEntry] = field(default_factory=list)

    def results(self) -> list[str | None]:
        return [e.result for e in self.entries]


@dataclass
class EcoGroup:
    """Jeden ECO kód s podkategoriemi (variantami)."""
    code: str  # "C40" nebo "?"
    variations: list[OpeningVariation] = field(default_factory=list)

    def results(self) -> list[str | None]:
        return [e.result for v in self.variations for e in v.entries]

    @property
    def label(self) -> str:
        if self.code == "?":
            return "Bez ECO kódu"
        hint = _czech(_ECO_NAMES.get(self.code, ""))
        return f"{self.code}  ·  {hint}" if hint else self.code


def analyze_openings(games, player: str, colors: str = "both", keep=None) -> list[EcoGroup]:
    """Partie hráče rozřazené podle ECO kódu → varianty → partie."""
    codes: dict[str, dict[str, OpeningVariation]] = {}
    for gi, g in enumerate(games):
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        result = player_result(g, player)
        eco, name, depth = game_opening(g)
        ck = eco if (eco and eco[0] in "ABCDE") else "?"
        var = codes.setdefault(ck, {}).setdefault(name or "Nezařazeno",
                                                  OpeningVariation(name or "Nezařazeno"))
        var.entries.append(OpeningEntry(gi, depth, result))

    out: list[EcoGroup] = []
    for ck in sorted(codes, key=lambda c: (c == "?", c)):
        vs = sorted(codes[ck].values(), key=lambda v: (-len(v.entries), sort_key(v.name)))
        out.append(EcoGroup(ck, vs))
    return out


# ------------------------------------------------------ diverzita repertoáru
def _diversity(counts: list[int]) -> dict:
    """Shannonova entropie / efektivní počet / HHI / pokrytí top-K nad počty partií
    v jednotlivých koších (zahájeních)."""
    total = sum(counts)
    if total == 0 or not counts:
        return {"n": 0, "entropy_norm": None, "n_eff": 0.0, "top3_share": None,
                "top5_share": None}
    ps = sorted((c / total for c in counts if c > 0), reverse=True)
    n = len(ps)
    h = -sum(p * math.log2(p) for p in ps)
    hhi = sum(p * p for p in ps)
    return {
        "n": n,
        "entropy_norm": (h / math.log2(n)) if n > 1 else None,  # 0 = jedno, 1 = rovnoměrně
        "n_eff": (1.0 / hhi) if hhi > 0 else 0.0,                # „efektivní počet zahájení"
        "top3_share": sum(ps[:3]),
        "top5_share": sum(ps[:5]),
    }


def repertoire_diversity(groups: list[EcoGroup]) -> dict:
    """Diverzita repertoáru na úrovni ECO rodiny i konkrétní varianty."""
    eco_counts = [len(g.results()) for g in groups]
    variant_counts = [len(v.entries) for g in groups for v in g.variations]
    return {"by_eco": _diversity(eco_counts), "by_variant": _diversity(variant_counts)}


# --------------------------------------------------- hloubka teorie / book exit
@dataclass
class PersonalBookEntry:
    game_index: int
    exit_ply: int              # první půltah, kde pozice nebyla nikde jinde u hráče
    named_depth: int           # hloubka podle „jmenované" databáze (eco.tsv apod.)
    result: str | None


@dataclass
class PersonalBookReport:
    entries: list[PersonalBookEntry] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.entries)

    def exit_moves(self) -> list[int]:
        return [e.exit_ply // 2 + 1 for e in self.entries]

    def named_moves(self) -> list[int]:
        return [e.named_depth // 2 + 1 for e in self.entries]

    def gaps(self) -> list[int]:
        """Osobní hloubka − jmenovaná hloubka, v tazích (kladné = jdeš za „knihu")."""
        return [e.exit_ply // 2 - e.named_depth // 2 for e in self.entries]


def personal_book_depth(games, player: str, colors: str = "both", keep=None) -> PersonalBookReport:
    """Hloubka teorie z vlastní historie hráče – bez enginu a bez externí databáze.

    Kolikátým půltahem partie poprvé opustí pozici, kterou hráč nikdy jinde
    v načtené databázi nehrál (transpozice se počítají – klíč je stejný jako
    u stromu zahájení).
    """
    matched: list[tuple[int, object]] = []
    for gi, g in enumerate(games):
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        matched.append((gi, g))

    freq: Counter = Counter()
    for _, g in matched:
        for key in g.keys:
            freq[key] += 1

    entries = []
    for gi, g in matched:
        keys = g.keys
        exit_ply = len(keys) - 1
        for k, key in enumerate(keys):
            if freq[key] < 2:
                exit_ply = k
                break
        result = player_result(g, player)
        _, _, named_depth = game_opening(g)
        entries.append(PersonalBookEntry(gi, exit_ply, named_depth, result))
    return PersonalBookReport(entries)
