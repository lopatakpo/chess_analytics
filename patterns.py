"""Statistické vzorce v partiích hráče – jedním průchodem každé partie.

Skupiny rozborů (každý = rozdělení partií do košů + úspěšnost / V-R-P):
- Rošáda (strana, vzájemný vztah, načasování),
- Výměna dam / těžké figury,
- Materiál a výměny (první braní, tempo výměn, oběti, náskok, dvojice střelců),
- Pěšcová struktura (izolák/IQP, zdvojení, volný pěšec, typ centra) – snímek kolem 20. tahu,
- Král a útok (jak partie skončila, král v centru),
- Délka a průběh partie,
- Forma a kontext (podle výsledku předchozí partie ve stejné seanci).

Navíc dvě volně-textové statistiky (``PatternReport.summary_lines``) – Elo-adjusted
výkonnost a „štěstí" (z-skóre skutečného skóre vs. Elo-očekávaného).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import chess

from endgames import is_endgame
from filters import opponent_elo_delta, player_opponent_elo
from player_analysis import game_matches, player_result
from stats_util import elo_expected_from_delta, luck_z


# --------------------------------------------------------------- materiál
def _material(board: chess.Board, color: chess.Color) -> int:
    occ = board.occupied_co[color]
    pc = chess.popcount
    return (pc(board.pawns & occ)
            + 3 * pc((board.knights | board.bishops) & occ)
            + 5 * pc(board.rooks & occ)
            + 9 * pc(board.queens & occ))


# ------------------------------------------------------- pěšcová struktura
def _has_passed(pawns: int, enemy: int, color: chess.Color) -> bool:
    for sq in chess.scan_forward(pawns):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        files_mask = chess.BB_FILES[f]
        if f > 0:
            files_mask |= chess.BB_FILES[f - 1]
        if f < 7:
            files_mask |= chess.BB_FILES[f + 1]
        if color == chess.WHITE:
            ahead = 0
            for rr in range(r + 1, 8):
                ahead |= chess.BB_RANKS[rr]
        else:
            ahead = 0
            for rr in range(0, r):
                ahead |= chess.BB_RANKS[rr]
        if not (enemy & files_mask & ahead):
            return True
    return False


def _islands(file_counts: list[int]) -> int:
    """Počet souvislých skupin pěšců na sousedních sloupcích."""
    n, prev = 0, False
    for c in file_counts:
        cur = c > 0
        if cur and not prev:
            n += 1
        prev = cur
    return n


def _backward(myp: int, oppp: int, color: chess.Color) -> bool:
    """Zpátečnický pěšec: bez opory souseda a s nepřítelem, co drží postupové pole."""
    for sq in chess.scan_forward(myp):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        adj = 0
        if f > 0:
            adj |= chess.BB_FILES[f - 1]
        if f < 7:
            adj |= chess.BB_FILES[f + 1]
        behind_eq = 0
        for rr in (range(0, r + 1) if color == chess.WHITE else range(r, 8)):
            behind_eq |= chess.BB_RANKS[rr]
        if myp & adj & behind_eq:
            continue                      # má oporu souseda
        ahead = 0
        for rr in (range(r + 1, 8) if color == chess.WHITE else range(0, r)):
            ahead |= chess.BB_RANKS[rr]
        if oppp & adj & ahead:
            return True
    return False


def _tension(board: chess.Board) -> int:
    """Počet dvojic pěšců, které se navzájem mohou vzít (napětí ve struktuře)."""
    wp = board.pawns & board.occupied_co[chess.WHITE]
    bp = board.pawns & board.occupied_co[chess.BLACK]
    t = 0
    for sq in chess.scan_forward(wp):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        if r == 7:
            continue
        for df in (-1, 1):
            tf = f + df
            if 0 <= tf < 8 and bp & chess.BB_SQUARES[chess.square(tf, r + 1)]:
                t += 1
    return t


def _structure(board: chess.Board, my: chess.Color) -> dict:
    opp = not my
    myp = board.pawns & board.occupied_co[my]
    oppp = board.pawns & board.occupied_co[opp]
    mf = [chess.popcount(myp & chess.BB_FILES[f]) for f in range(8)]
    of = [chess.popcount(oppp & chess.BB_FILES[f]) for f in range(8)]
    isolated = any(mf[f] and not ((f > 0 and mf[f - 1]) or (f < 7 and mf[f + 1]))
                   for f in range(8))
    iqp = mf[3] >= 1 and mf[2] == 0 and mf[4] == 0 and of[3] == 0
    doubled = any(c >= 2 for c in mf)
    central = mf[3] + mf[4] + of[3] + of[4]
    center = "otevřené" if central <= 1 else "pootevřené" if central == 2 else "zavřené"
    return {
        "isolated": isolated,
        "iqp": iqp,
        "doubled": doubled,
        "my_passed": _has_passed(myp, oppp, my),
        "opp_passed": _has_passed(oppp, myp, opp),
        "center": center,
        "bishop_pair": (chess.popcount(board.bishops & board.occupied_co[my]) >= 2
                        and chess.popcount(board.bishops & board.occupied_co[opp]) < 2),
        "islands": _islands(mf),
        "backward": _backward(myp, oppp, my),
        "tension": _tension(board),
    }


# --------------------------------------------------------------- příznaky partie
@dataclass
class Features:
    result: str | None
    moves: int
    my_castle: str
    opp_castle: str
    my_castle_move: int | None
    castle_relation: str
    queens_off_move: int | None
    first_capture_move: int | None
    exchanges_early: int          # sebrané figury (mimo pěšce) do 20. tahu, obě strany
    my_max_deficit: int
    my_max_surplus: int
    had_bishop_pair: bool
    isolated: bool
    iqp: bool
    doubled: bool
    my_passed: bool
    opp_passed: bool
    center: str
    islands: int
    backward: bool
    tension: int
    pawn_storm: bool
    ending: str
    king_in_center: bool
    elo_delta: int | None          # Elo hráče − Elo soupeře
    game_dt: datetime | None       # pro řazení do „seancí" (forma po předchozí partii)
    prev_result: str | None = None  # doplní se až po sestavení všech partií hráče


def _features(g, player: str) -> Features:
    my = chess.WHITE if (g.headers.get("White") or "").strip() == player else chess.BLACK
    opp = not my
    result = player_result(g, player)
    moves = g.moves
    board = g.start_board()
    n_full = (len(moves) + 1) // 2

    my_castle = opp_castle = "-"
    my_castle_move: int | None = None
    first_capture_move: int | None = None
    queens_off_move: int | None = None
    exchanges_early = 0
    diffs = [_material(board, my) - _material(board, opp)]   # materiál hráče − soupeře po každém půltahu
    my_pawn_moves: list[tuple[int, int]] = []                # (číslo tahu, cílový sloupec)

    snap_ply = min(len(moves), 40)
    snap = None

    for k, mv in enumerate(moves):
        mover = board.turn
        moved_pt = board.piece_type_at(mv.from_square)
        is_cap = board.is_capture(mv)
        castl = board.is_castling(mv)
        kside = board.is_kingside_castling(mv) if castl else False
        cap_pt = None
        if is_cap and not board.is_en_passant(mv):
            p = board.piece_at(mv.to_square)
            cap_pt = p.piece_type if p is not None else None
        board.push(mv)
        ply = k + 1
        move_no = (ply + 1) // 2

        if first_capture_move is None and is_cap:
            first_capture_move = move_no
        if castl:
            tag = "O-O" if kside else "O-O-O"
            if mover == my and my_castle == "-":
                my_castle, my_castle_move = tag, move_no
            elif mover == opp and opp_castle == "-":
                opp_castle = tag
        if queens_off_move is None and board.queens == 0:
            queens_off_move = move_no
        if ply <= 40 and cap_pt is not None and cap_pt != chess.PAWN:
            exchanges_early += 1
        if mover == my and moved_pt == chess.PAWN:
            my_pawn_moves.append((move_no, chess.square_file(mv.to_square)))

        diffs.append(_material(board, my) - _material(board, opp))
        if ply == snap_ply:
            snap = _structure(board, my)

    if snap is None:
        snap = _structure(board, my)

    pawn_storm = False
    if opp_castle in ("O-O", "O-O-O"):
        storm_files = {4, 5, 6, 7} if opp_castle == "O-O" else {0, 1, 2, 3, 4}
        pawn_storm = sum(1 for mno, f in my_pawn_moves
                         if mno <= 25 and f in storm_files) >= 3

    # „usazený" materiálový rozdíl: úroveň, která přežila i soupeřovu odpověď
    # (odfiltruje běžné braní–zpětné braní, nechá skutečné oběti a zisky)
    settled = [d for k, d in enumerate(diffs)
               if k + 1 >= len(diffs) or diffs[k + 1] == d]
    max_def = max([0] + [-d for d in settled])
    max_sur = max([0] + [d for d in settled])

    if board.is_checkmate():
        ending = "mat"
    elif board.is_stalemate():
        ending = "pat"
    else:
        res = (g.headers.get("Result") or "*").strip()
        if res == "*":
            ending = "nedohráno"
        elif res in ("1/2-1/2", "1/2", "½-½"):
            ending = ("remíza (nedostatek materiálu)"
                      if board.is_insufficient_material() else "remíza dohodou")
        else:
            ending = "vzdání / čas"

    if my_castle != "-" and opp_castle != "-":
        rel = "stejná strana" if my_castle == opp_castle else "opačná strana"
    elif my_castle == "-" and opp_castle == "-":
        rel = "nikdo nerošoval"
    elif my_castle == "-":
        rel = "hráč nerošoval"
    else:
        rel = "soupeř nerošoval"

    return Features(
        result=result, moves=n_full,
        my_castle=my_castle, opp_castle=opp_castle, my_castle_move=my_castle_move,
        castle_relation=rel,
        queens_off_move=queens_off_move, first_capture_move=first_capture_move,
        exchanges_early=exchanges_early,
        my_max_deficit=max(0, max_def), my_max_surplus=max(0, max_sur),
        had_bishop_pair=snap["bishop_pair"],
        isolated=snap["isolated"], iqp=snap["iqp"], doubled=snap["doubled"],
        my_passed=snap["my_passed"], opp_passed=snap["opp_passed"], center=snap["center"],
        islands=snap["islands"], backward=snap["backward"], tension=snap["tension"],
        pawn_storm=pawn_storm,
        ending=ending,
        king_in_center=(my_castle == "-" and n_full >= 20),
        elo_delta=opponent_elo_delta(g, player),
        game_dt=_game_dt(g),
    )


_DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
_TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})")


def _game_dt(g) -> datetime | None:
    """Časové razítko partie z hlaviček (UTCDate/Date + UTCTime), pro řazení do seancí."""
    h = g.headers
    dm = _DATE_RE.match((h.get("UTCDate") or h.get("Date") or "").strip())
    if not dm:
        return None
    y, mo, d = (int(x) for x in dm.groups())
    hh = mm = ss = 0
    tm = _TIME_RE.match((h.get("UTCTime") or h.get("Time") or "").strip())
    if tm:
        hh, mm, ss = (int(x) for x in tm.groups())
    try:
        return datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None


def _last_sunday_day(year: int, month: int) -> int:
    """Den v měsíci, na který padne poslední neděle."""
    from calendar import monthrange
    last = monthrange(year, month)[1]
    # datetime.weekday(): Po=0 … Ne=6; (wd+1)%7 = kolik dní zpět je poslední neděle
    return last - ((datetime(year, month, last).weekday() + 1) % 7)


def to_prague_local(dt_utc: datetime) -> datetime:
    """UTC → místní čas ČR (CET/CEST). Uživatel potvrdil, že hraje z ČR, takže
    stačí pevné evropské pravidlo letního času: letní (UTC+2) od poslední neděle
    v březnu 01:00 UTC do poslední neděle v října 01:00 UTC, jinak zimní (UTC+1).
    Bez závislosti na ``zoneinfo``/``tzdata`` (na Windows nebývá IANA databáze)."""
    y = dt_utc.year
    dst_from = datetime(y, 3, _last_sunday_day(y, 3), 1, 0)
    dst_to = datetime(y, 10, _last_sunday_day(y, 10), 1, 0)
    offset = 2 if dst_from <= dt_utc < dst_to else 1
    return dt_utc + timedelta(hours=offset)


# --------------------------------------------------------------- rozdělení do košů
@dataclass
class Breakdown:
    title: str
    hint: str
    rows: list = field(default_factory=list)   # [(label, [result, ...])]


@dataclass
class PatternGroup:
    title: str
    breakdowns: list[Breakdown] = field(default_factory=list)


@dataclass
class PatternReport:
    n_games: int
    groups: list[PatternGroup] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)   # {elo_surplus, luck_z, n} – strukturovaně


def _by(feats, keyfn, order, title, hint) -> Breakdown:
    bucket: dict[str, list] = {}
    for f in feats:
        k = keyfn(f)
        if k is None:
            continue
        bucket.setdefault(k, []).append(f.result)
    rows = [(lab, bucket[lab]) for lab in order if lab in bucket]
    # koše mimo předepsané pořadí (kdyby nějaký přibyl) na konec
    rows += [(lab, res) for lab, res in bucket.items() if lab not in set(order)]
    return Breakdown(title, hint, rows)


def _castle_time(f: Features):
    if f.my_castle == "-":
        return "bez rošády"
    m = f.my_castle_move or 99
    if m <= 8:
        return "do 8. tahu"
    if m <= 12:
        return "9.–12. tah"
    if m <= 18:
        return "13.–18. tah"
    return "19. tah a později"


def _queens_time(f: Features):
    m = f.queens_off_move
    if m is None:
        return "dámy zůstaly na šachovnici"
    if m <= 15:
        return "do 15. tahu"
    if m <= 25:
        return "16.–25. tah"
    if m <= 40:
        return "26.–40. tah"
    return "po 40. tahu"


def _first_cap(f: Features):
    m = f.first_capture_move
    if m is None:
        return "bez braní"
    if m <= 6:
        return "do 6. tahu"
    if m <= 12:
        return "7.–12. tah"
    if m <= 20:
        return "13.–20. tah"
    return "po 20. tahu"


def _tempo(f: Features):
    n = f.exchanges_early
    if n <= 2:
        return "klidná (0–2 figury)"
    if n <= 4:
        return "živá (3–4 figury)"
    return "ostrá (5+ figur)"


def _deficit(f: Features):
    d = f.my_max_deficit
    if d <= 0:
        return "nikdy v manku"
    if d <= 2:
        return "manko 1–2 body"
    return "manko 3+ body (oběť)"


def _surplus(f: Features):
    s = f.my_max_surplus
    if s <= 0:
        return "nikdy v náskoku"
    if s <= 2:
        return "náskok 1–2 body"
    return "náskok 3+ body"


def _isolani(f: Features):
    if f.iqp:
        return "izolovaný dámský pěšec (IQP)"
    if f.isolated:
        return "jiný izolovaný pěšec"
    return "bez izolovaného pěšce"


def _passed(f: Features):
    if f.my_passed and f.opp_passed:
        return "volného pěšce mají obě strany"
    if f.my_passed:
        return "volného pěšce má jen hráč"
    if f.opp_passed:
        return "volného pěšce má jen soupeř"
    return "volný pěšec není"


def _length(f: Features):
    n = f.moves
    if n <= 20:
        return "do 20 tahů"
    if n <= 40:
        return "21–40 tahů"
    if n <= 60:
        return "41–60 tahů"
    return "přes 60 tahů"


def _build_groups(feats: list[Features]) -> list[PatternGroup]:
    return [
        PatternGroup("Rošáda", [
            _by(feats, lambda f: {"O-O": "krátká (O-O)", "O-O-O": "dlouhá (O-O-O)"}
                .get(f.my_castle, "bez rošády"),
                ["krátká (O-O)", "dlouhá (O-O-O)", "bez rošády"],
                "Strana rošády hráče", "Na kterou stranu (a jestli vůbec) hráč rošoval."),
            _by(feats, lambda f: f.castle_relation,
                ["stejná strana", "opačná strana", "soupeř nerošoval",
                 "hráč nerošoval", "nikdo nerošoval"],
                "Vzájemný vztah rošád",
                "Opačné rošády = zpravidla ostřejší hra s útokem na krále."),
            _by(feats, _castle_time,
                ["do 8. tahu", "9.–12. tah", "13.–18. tah", "19. tah a později", "bez rošády"],
                "Načasování rošády hráče", "Kolikátým tahem se hráč uklidil do bezpečí."),
        ]),
        PatternGroup("Výměna dam / těžké figury", [
            _by(feats, _queens_time,
                ["do 15. tahu", "16.–25. tah", "26.–40. tah", "po 40. tahu",
                 "dámy zůstaly na šachovnici"],
                "Kdy zmizely dámy", "Půltah, po kterém byly obě dámy pryč."),
            _by(feats, lambda f: "dámy vyměněny" if f.queens_off_move is not None
                else "dámy na šachovnici",
                ["dámy vyměněny", "dámy na šachovnici"],
                "Došlo k výměně dam?", "Partie s dámami vs. bez dam."),
        ]),
        PatternGroup("Materiál a výměny", [
            _by(feats, _first_cap,
                ["do 6. tahu", "7.–12. tah", "13.–20. tah", "po 20. tahu", "bez braní"],
                "První braní", "Kdy padla první figura nebo pěšec."),
            _by(feats, _tempo,
                ["klidná (0–2 figury)", "živá (3–4 figury)", "ostrá (5+ figur)"],
                "Tempo výměn do 20. tahu", "Kolik figur (mimo pěšce) zmizelo z šachovnice."),
            _by(feats, _deficit,
                ["nikdy v manku", "manko 1–2 body", "manko 3+ body (oběť)"],
                "Největší materiálové manko hráče",
                "Byl hráč někdy pod materiálem? Manko 3+ obvykle značí oběť."),
            _by(feats, _surplus,
                ["nikdy v náskoku", "náskok 1–2 body", "náskok 3+ body"],
                "Největší materiálový náskok hráče",
                "Naznačuje, jak často se daří získat převahu (a jestli ji hráč udrží)."),
            _by(feats, lambda f: "měl dvojici střelců" if f.had_bishop_pair
                else "bez dvojice střelců",
                ["měl dvojici střelců", "bez dvojice střelců"],
                "Dvojice střelců (kolem 20. tahu)",
                "Hráč měl oba střelce a soupeř ne."),
        ]),
        PatternGroup("Pěšcová struktura (snímek kolem 20. tahu)", [
            _by(feats, _isolani,
                ["izolovaný dámský pěšec (IQP)", "jiný izolovaný pěšec", "bez izolovaného pěšce"],
                "Izolovaný pěšec hráče", "Izolák bez sousedních pěšců na vedlejších sloupcích."),
            _by(feats, lambda f: "zdvojení pěšci" if f.doubled else "bez zdvojených pěšců",
                ["zdvojení pěšci", "bez zdvojených pěšců"],
                "Zdvojení pěšci hráče", "Dva a víc pěšců hráče na jednom sloupci."),
            _by(feats, _passed,
                ["volného pěšce má jen hráč", "volného pěšce má jen soupeř",
                 "volného pěšce mají obě strany", "volný pěšec není"],
                "Volný pěšec", "Pěšec bez soupeřových pěšců před ním na svém i sousedních sloupcích."),
            _by(feats, lambda f: f.center,
                ["otevřené", "pootevřené", "zavřené"],
                "Typ centra", "Podle počtu pěšců na sloupcích d a e u obou stran."),
            _by(feats, lambda f: ("1 ostrov (kompaktní)" if f.islands <= 1
                                  else "2 ostrovy" if f.islands == 2
                                  else "3+ ostrovů (roztříštěná)"),
                ["1 ostrov (kompaktní)", "2 ostrovy", "3+ ostrovů (roztříštěná)"],
                "Pěšcové ostrovy hráče",
                "Počet souvislých skupin pěšců na sousedních sloupcích – míň je líp."),
            _by(feats, lambda f: ("žádné (vyjasněná)" if f.tension == 0
                                  else "malé (1–2)" if f.tension <= 2 else "velké (3+)"),
                ["žádné (vyjasněná)", "malé (1–2)", "velké (3+)"],
                "Napětí ve struktuře",
                "Kolik dvojic pěšců se navzájem může vzít – 0 = struktura vyjasněná."),
            _by(feats, lambda f: "má zpátečnický pěšec" if f.backward
                else "bez zpátečnického pěšce",
                ["má zpátečnický pěšec", "bez zpátečnického pěšce"],
                "Zpátečnický pěšec hráče",
                "Pěšec bez opory sousedů, který nemůže bezpečně postoupit."),
        ]),
        PatternGroup("Král a útok", [
            _by(feats, lambda f: f.ending,
                ["mat", "vzdání / čas", "remíza dohodou", "remíza (nedostatek materiálu)",
                 "pat", "nedohráno"],
                "Jak partie skončila", "Typ ukončení partie."),
            _by(feats, lambda f: "král zůstal v centru" if f.king_in_center
                else "král se uklidil / krátká partie",
                ["král zůstal v centru", "král se uklidil / krátká partie"],
                "Král hráče v centru", "Hráč nerošoval a partie měla aspoň 20 tahů."),
            _by(feats, lambda f: "ano (3+ pěšcových tahů)" if f.pawn_storm else "ne",
                ["ano (3+ pěšcových tahů)", "ne"],
                "Pěšcová bouře na soupeřova krále",
                "3 a víc pěšcových tahů hráče na křídle, kam se uklidil soupeřův král, "
                "do 25. tahu (jen když soupeř rošoval)."),
        ]),
        PatternGroup("Délka a průběh partie", [
            _by(feats, _length,
                ["do 20 tahů", "21–40 tahů", "41–60 tahů", "přes 60 tahů"],
                "Délka partie", "Počet tahů celé partie."),
            _by(feats, lambda f: ("rozhodnuto" if f.result in ("win", "loss")
                                  else "remíza" if f.result == "draw" else None),
                ["rozhodnuto", "remíza"],
                "Rozhodnuto vs. remíza podle délky",
                "Úspěšnost v řádku „rozhodnuto“ = podíl výher mezi rozhodnutými partiemi."),
        ]),
        PatternGroup("Forma a kontext", [
            _by(feats, lambda f: ({"win": "po výhře", "draw": "po remíze",
                                   "loss": "po prohře"}.get(f.prev_result)),
                ["po výhře", "po remíze", "po prohře"],
                "Forma po předchozí partii (stejná seance)",
                "Výsledek partie podle toho, jak dopadla bezprostředně předchozí "
                "partie hráče (mezera < 60 minut). Partie bez zjistitelného pořadí "
                "(chybí čas v hlavičce, nebo první v seanci) se nepočítají – "
                "signifikanci suď podle Wilsonova intervalu v tooltipu."),
        ]),
    ]


_SESSION_GAP = timedelta(minutes=60)


def _assign_prev_result(feats: list[Features]) -> None:
    """Seřadí partie s časovým razítkem chronologicky a doplní ``prev_result`` –
    výsledek partie, co bezprostředně (< 60 min) předcházela, ve stejné „seanci"."""
    order = sorted((i for i in range(len(feats)) if feats[i].game_dt is not None),
                   key=lambda i: feats[i].game_dt)
    for pos in range(1, len(order)):
        i, prev_i = order[pos], order[pos - 1]
        if feats[i].game_dt - feats[prev_i].game_dt <= _SESSION_GAP:
            feats[i].prev_result = feats[prev_i].result


def context_stats(feats: list[Features]) -> dict:
    """Strukturovaně to samé, co ``_context_lines`` píše slovně: Elo-adjusted
    převaha (bodů/partii) a „štěstí" z-skóre. Prázdné, když chybí Elo soupeřů."""
    score = {"win": 1.0, "draw": 0.5, "loss": 0.0}
    pairs = [(score[f.result], elo_expected_from_delta(f.elo_delta))
             for f in feats if f.result in score and f.elo_delta is not None]
    if not pairs:
        return {"elo_surplus": None, "luck_z": None, "n": 0}
    surplus = (sum(a for a, _ in pairs) - sum(e for _, e in pairs)) / len(pairs)
    return {"elo_surplus": surplus, "luck_z": luck_z(pairs), "n": len(pairs)}


def _context_lines(feats: list[Features]) -> list[str]:
    """Elo-adjusted výkonnost a „štěstí" (z-skóre) – z hlaviček, bez enginu."""
    score = {"win": 1.0, "draw": 0.5, "loss": 0.0}
    pairs = [(score[f.result], elo_expected_from_delta(f.elo_delta))
             for f in feats if f.result in score and f.elo_delta is not None]
    if not pairs:
        return []
    surplus = (sum(a for a, _ in pairs) - sum(e for _, e in pairs)) / len(pairs)
    lines = [
        f"Elo-adjusted výkonnost: skutečné skóre je v průměru "
        f"{'+' if surplus >= 0 else ''}{surplus:.2f} bodu na partii oproti tomu, co by "
        f"čekal rozdíl v Elu soupeřů ({len(pairs)} partií se známým Elem soupeře)."
    ]
    z = luck_z(pairs)
    if z is not None:
        lines.append(
            f"„Štěstí“ (z-skóre skutečného skóre vs. Elo-očekávaného): {z:+.1f} – "
            f"blízko 0 = odpovídá síle soupeřů, vysoké |z| = neobvykle "
            f"šťastná/nešťastná série (nebo skutečná změna formy)."
        )
    return lines


def analyze_patterns(games, player: str, colors: str = "both", keep=None) -> PatternReport:
    feats: list[Features] = []
    for g in games:
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        feats.append(_features(g, player))
    _assign_prev_result(feats)
    return PatternReport(len(feats), _build_groups(feats), _context_lines(feats),
                         context_stats(feats))


@dataclass
class DistributionEntry:
    moves: int
    first_capture_move: int | None
    my_max_deficit: int
    my_max_surplus: int
    elo_delta: int | None
    eg_entry_move: int | None       # první tah, kdy partie vstoupila do koncovky
    result: str | None = None       # "win"/"draw"/"loss" z pohledu hráče
    diffs: list = field(default_factory=list)   # materiál hráč−soupeř po každém půltahu


def distribution_stats(games, player: str, colors: str = "both",
                       keep=None) -> list[DistributionEntry]:
    """Lehký jednoprůchodový rozbor pro histogramy přes CELOU databázi (délka
    partie, první braní, materiálové výkyvy, vstup do koncovky, Elo soupeře) –
    bez enginu. Levnější než :func:`analyze_patterns` (nepočítá rošády,
    pěšcovou strukturu ani výměnu dam – jen to, co tyhle grafy potřebují)."""
    out: list[DistributionEntry] = []
    for g in games:
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        my = chess.WHITE if (g.headers.get("White") or "").strip() == player else chess.BLACK
        opp = not my
        moves = g.moves
        board = g.start_board()
        first_cap = None
        eg_entry = None
        diffs = [_material(board, my) - _material(board, opp)]
        for k, mv in enumerate(moves):
            is_cap = board.is_capture(mv)
            board.push(mv)
            ply = k + 1
            move_no = (ply + 1) // 2
            if first_cap is None and is_cap:
                first_cap = move_no
            if eg_entry is None and is_endgame(board):
                eg_entry = move_no
            diffs.append(_material(board, my) - _material(board, opp))
        # „usazený" materiálový rozdíl – stejná logika jako ve Vzorcích
        settled = [d for i, d in enumerate(diffs) if i + 1 >= len(diffs) or diffs[i + 1] == d]
        max_def = max([0] + [-d for d in settled])
        max_sur = max([0] + [d for d in settled])
        out.append(DistributionEntry(
            moves=(len(moves) + 1) // 2,
            first_capture_move=first_cap,
            my_max_deficit=max(0, max_def), my_max_surplus=max(0, max_sur),
            elo_delta=opponent_elo_delta(g, player),
            eg_entry_move=eg_entry,
            result=player_result(g, player),
            diffs=diffs,
        ))
    return out


def game_log(games, player: str, colors: str = "both", keep=None) -> list[tuple]:
    """Lehký chronologický deník partií hráče: (datum, elo_delta, result) – jen
    z hlaviček, bez průchodu tahy (na rozdíl od :func:`analyze_patterns`). Partie
    bez časového razítka jdou na konec. Pro grafy (kumulativní „štěstí" apod.)."""
    rows = []
    for g in games:
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        rows.append((_game_dt(g), opponent_elo_delta(g, player), player_result(g, player)))
    rows.sort(key=lambda r: (r[0] is None, r[0]))
    return rows


def rating_pairs(games, player: str, colors: str = "both", keep=None) -> list[tuple]:
    """Lehký seznam (Elo hráče, Elo soupeře, výsledek) – jen z hlaviček, bez
    průchodu tahy. Partie bez Ela na jedné či druhé straně se přeskočí. Pro
    graf „Opponent Analysis" (scatter Elo hráče × Elo soupeře podle výsledku)."""
    rows = []
    for g in games:
        if keep is not None and not keep(g):
            continue
        if not game_matches(g, player, colors):
            continue
        mine, opp = player_opponent_elo(g, player)
        if mine is None or opp is None:
            continue
        rows.append((mine, opp, player_result(g, player)))
    return rows
