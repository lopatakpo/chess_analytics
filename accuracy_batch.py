"""Dávkový rozbor přesnosti hráče přes databázi partií (na pozadí, s cache).

Kromě přesnosti / ACPL / EP / hrubek / T1 počítá i:
- **kompozitní index přesnosti** a **IPR** (odhad výkonnosti, ukotvený na Elo hráče),
- **charakter partií** – volatilita hodnocení, vstup do koncovky, a (v „důkladném"
  režimu s multipv) ostrost/komplexita pozic a kritičností vážený ACPL,
- **dotahování** – jak často hráč zúročí vyhranou pozici / zachrání prohranou.
"""
from __future__ import annotations

import math
import queue
import threading
import time

import chess
import chess.engine
from PySide6.QtCore import QThread, Signal

import accuracy
import charakter
from accuracy import win_pct
from analysis_cache import AnalysisCache, game_key
from engine_perf import apply_engine_options, clamp_parallel
from stats_util import kaplan_meier
from endgames import is_endgame
from filters import game_year, game_year_month, time_class
from game_analyzer import analyse_boards
from move_class import brilliant_plies as _brilliant_plies_ccom
from move_class import count_kinds as _count_kinds_ccom
from openings import classify_by_moves

_MIN_RATED_FOR_ANCHOR = 4        # kolik hodnocených partií stačí na „kotvu" IPR
_MIN_SPREAD_FOR_SLOPE = 30.0     # rozptyl Ela nutný, aby šlo fitovat i sklon
_DEFAULT_SLOPE = -580.0          # Elo na jednotku ln(ACPL); víc ACPL → míň Ela
WILD_THRESHOLD = 12.0            # % – nad tím je partie „divoká" (volatilita)
N_CRIT_BINS = 10                 # koše kritičnosti (0..1) pro graf kritičnost×přesnost


def _phase_list(boards, opening_plies: int) -> list[str]:
    out = []
    for k in range(len(boards) - 1):
        b = boards[k]
        if is_endgame(b):
            out.append("Koncovka")
        elif k < opening_plies:
            out.append("Zahájení")
        else:
            out.append("Středhra")
    return out


def _forced_list(boards) -> list[bool]:
    out = []
    for k in range(len(boards) - 1):
        it = iter(boards[k].legal_moves)
        next(it, None)
        out.append(next(it, None) is None)
    return out


def _player_rating(game, is_white: bool):
    try:
        return int(game.headers.get("WhiteElo" if is_white else "BlackElo"))
    except (TypeError, ValueError):
        return None


def _result_for(game, is_white: bool) -> str | None:
    res = (game.headers.get("Result") or "*").strip()
    if res == "1-0":
        return "win" if is_white else "loss"
    if res == "0-1":
        return "loss" if is_white else "win"
    if res in ("1/2-1/2", "1/2", "½-½"):
        return "draw"
    return None


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


# ------------------------------------------------------------- akumulátory přesnosti
def _acc0() -> dict:
    return {"games": 0, "moves": 0, "cp_loss": 0.0, "ep_loss": 0.0,
            "inacc": 0, "mist": 0, "blund": 0, "t1": 0, "t1_den": 0,
            "cwl_num": 0.0, "cwl_den": 0.0, "tact": 0, "tact_den": 0,
            "acc_sum": 0.0, "acc_n": 0, "ipr_sum": 0.0, "ipr_n": 0,
            "had_win": 0, "won": 0, "had_loss": 0, "not_lost": 0,
            "ep_wasted_sum": 0.0,
            "vol_sum": 0.0, "vol_n": 0, "rev_sum": 0.0,
            "sharp_sum": 0.0, "sharp_n": 0, "cx_sum": 0.0, "cx_n": 0}


def _merge(dst: dict, stats: dict, game_acc=None, ipr=None, conv: dict | None = None,
          count_game: bool = True, rec: dict | None = None) -> None:
    if count_game:
        dst["games"] += 1
    for k in ("moves", "cp_loss", "ep_loss", "inacc", "mist", "blund",
              "t1", "t1_den", "cwl_num", "cwl_den", "tact", "tact_den"):
        dst[k] += stats[k]
    if rec is not None:
        v = rec.get("vol")
        if v:
            dst["vol_sum"] += v["mean_swing"]
            dst["rev_sum"] += v["reversals"]
            dst["vol_n"] += 1
        if rec.get("mean_sharp") is not None:
            dst["sharp_sum"] += rec["mean_sharp"]
            dst["sharp_n"] += 1
        if rec.get("mean_cx") is not None:
            dst["cx_sum"] += rec["mean_cx"]
            dst["cx_n"] += 1
    if game_acc is not None:
        dst["acc_sum"] += game_acc
        dst["acc_n"] += 1
    if ipr is not None:
        dst["ipr_sum"] += ipr
        dst["ipr_n"] += 1
    if conv is not None and conv.get("result_known"):
        if conv["had_win"]:
            dst["had_win"] += 1
            if conv["won"]:
                dst["won"] += 1
            dst["ep_wasted_sum"] += conv["ep_wasted"]
        if conv["had_loss"]:
            dst["had_loss"] += 1
            if conv["not_lost"]:
                dst["not_lost"] += 1


def _final(a: dict) -> dict:
    m = a["moves"] or 1
    g = a["games"] or 1
    acc = round(a["acc_sum"] / a["acc_n"], 1) if a["acc_n"] else None
    acpl = round(a["cp_loss"] / m)
    blund_100 = round(a["blund"] * 100 / m, 1)
    t1 = round(a["t1"] * 100 / a["t1_den"], 1) if a["t1_den"] else None
    tact = round(a["tact"] * 100 / a["tact_den"], 1) if a["tact_den"] else None
    conversion = round(a["won"] * 100 / a["had_win"], 1) if a["had_win"] else None
    resourcefulness = round(a["not_lost"] * 100 / a["had_loss"], 1) if a["had_loss"] else None
    ep_wasted = round(a["ep_wasted_sum"] / a["had_win"], 2) if a["had_win"] else None
    return {
        "games": a["games"], "moves": a["moves"],
        "accuracy": acc, "acpl": acpl,
        "ep_per_game": round(a["ep_loss"] / g, 2),
        "blund_100": blund_100,
        "mist_100": round(a["mist"] * 100 / m, 1),
        "inacc_100": round(a["inacc"] * 100 / m, 1),
        "t1": t1, "tact": tact,
        "index": accuracy.composite_index(acc, acpl, blund_100, t1),
        "ipr": round(a["ipr_sum"] / a["ipr_n"]) if a["ipr_n"] else None,
        "conversion": conversion, "resourcefulness": resourcefulness,
        "ep_wasted": ep_wasted,
        "had_win": a["had_win"], "had_loss": a["had_loss"],
        "volatility": round(a["vol_sum"] / a["vol_n"], 1) if a["vol_n"] else None,
        "reversals": round(a["rev_sum"] / a["vol_n"], 1) if a["vol_n"] else None,
        "sharpness": round(a["sharp_sum"] / a["sharp_n"], 2) if a["sharp_n"] else None,
        "complexity": round(a["cx_sum"] / a["cx_n"], 2) if a["cx_n"] else None,
    }


class AccuracyBatch(QThread):
    progress = Signal(int, int, str)     # hotovo, celkem, popisek
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, engine_path: str, jobs: list, depth: int,
                 cache: AnalysisCache, thorough: bool = False, parent=None,
                 threads: int | None = None, hash_mb: int | None = None,
                 parallel: int = 1, use_wdl: bool = True) -> None:
        super().__init__(parent)
        self._path = engine_path
        self._jobs = jobs          # [(game_index, game, player_is_white), ...]
        self._depth = depth
        self._cache = cache
        self._thorough = thorough
        self._multipv = 3 if thorough else 1
        self._threads = threads
        self._hash_mb = hash_mb
        self._parallel = clamp_parallel(parallel)
        self._use_wdl = bool(use_wdl)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------ běh
    def _process_one(self, engine_box: list, ordinal: int, gi, game, is_white):
        """Zpracuje jednu partii. ``engine_box`` je [engine|None] daného workeru –
        engine se vytvoří líně až při prvním cache-miss. Vrací (rec | None)."""
        key = game_key(game)
        cached = self._cache.get(key, self._depth, need_topk=self._thorough)
        if cached is not None:
            evals, bests = cached["evals"], cached["bestmoves"]
            wdls, topks = cached.get("wdl"), cached.get("topk")
        else:
            if engine_box[0] is None:
                eng = chess.engine.SimpleEngine.popen_uci(self._path)
                apply_engine_options(eng, self._threads, self._hash_mb)
                engine_box[0] = eng
            evals, bests, wdls, topks = analyse_boards(
                engine_box[0], list(game.boards), self._depth,
                should_stop=lambda: self._stop, multipv=self._multipv)
            if self._stop:
                return None
            self._cache.put(key, self._depth, evals, bests, wdls, topks)
        return self._game_record(gi, game, is_white, evals, bests, wdls, topks)

    def run(self) -> None:  # noqa: C901
        total = len(self._jobs)
        n_workers = max(1, min(self._parallel, total))

        job_q: queue.Queue = queue.Queue()
        for item in enumerate(self._jobs):     # (ordinal, (gi, game, is_white))
            job_q.put(item)

        results: list[tuple[int, dict]] = []
        results_lock = threading.Lock()
        engines: list = []
        engines_lock = threading.Lock()
        errors: list[str] = []
        done_count = [0]
        count_lock = threading.Lock()

        def worker() -> None:
            engine_box: list = [None]
            try:
                while not self._stop:
                    try:
                        ordinal, (gi, game, is_white) = job_q.get_nowait()
                    except queue.Empty:
                        break
                    rec = self._process_one(engine_box, ordinal, gi, game, is_white)
                    if rec is not None:
                        with results_lock:
                            results.append((ordinal, rec))
                    with count_lock:
                        done_count[0] += 1
            except chess.engine.EngineTerminatedError:
                errors.append("Engine během rozboru skončil.")
            except Exception as exc:  # ať pád workeru nezasekne celý rozbor
                errors.append(f"Chyba dávkového rozboru: {exc}")
            finally:
                if engine_box[0] is not None:
                    with engines_lock:
                        engines.append(engine_box[0])

        pool = [threading.Thread(target=worker, daemon=True, name=f"acc-batch-{i}")
                for i in range(n_workers)]
        self.progress.emit(0, total, f"počítám… ({n_workers} enginů souběžně)")
        for t in pool:
            t.start()
        # průběh hlásíme z tohoto (Qt) vlákna, ne z workerů
        while any(t.is_alive() for t in pool):
            with count_lock:
                d = done_count[0]
            self.progress.emit(d, total, f"počítám… ({n_workers} enginů souběžně)")
            time.sleep(0.25)
        for t in pool:
            t.join()
        self.progress.emit(done_count[0], total, "")

        for eng in engines:
            try:
                eng.quit()
            except Exception:
                pass
        self._cache.save()

        if not results:
            self.failed.emit(errors[0] if errors else "Rozbor přerušen (nic se nespočítalo).")
            return

        # zpět do původního pořadí úloh – _build_result bere records[:10] jako
        # „poslední partie" (forma), takže na pořadí záleží
        records = [rec for _ord, rec in sorted(results, key=lambda x: x[0])]
        model = _fit_ipr(records)
        for r in records:
            r["ipr"] = _game_ipr(model, r)
        self.finished_ok.emit(self._build_result(records, model))

    # --------------------------------------------------------- záznam jedné partie
    def _game_record(self, gi, game, is_white, evals, bests, wdls, topks):
        boards = list(game.boards)
        if len(evals) < 2 or len(evals) != len(boards):
            return None
        opening_plies = 12
        try:
            _, _, d = classify_by_moves([m.uci() for m in game.moves])
            opening_plies = min(max(int(d), 8), 24)
        except Exception:
            pass
        phases = _phase_list(boards, opening_plies)
        forced = _forced_list(boards)
        moves_uci = [m.uci() for m in game.moves]
        result = _result_for(game, is_white)
        wdls = wdls if (self._use_wdl and wdls
                        and any(x is not None for x in wdls)) else None

        crit = sharp = cx = None
        if self._thorough and topks:
            crit, sharp, cx = [], [], []
            for k in range(len(evals) - 1):
                tk = topks[k]
                mover_white = (k % 2 == 0)
                if not tk:
                    crit.append(0.0)
                    sharp.append(0.0)
                    cx.append(0.0)
                    continue
                eps = [win_pct(c if mover_white else -c) / 100.0 for c, _ in tk]
                crit.append(charakter.criticality(eps))
                nlegal = boards[k].legal_moves.count()
                sharp.append(charakter.sharpness(eps, nlegal))
                cx.append(charakter.complexity(eps))

        rep = accuracy.analyze_game(evals, moves_uci, bests, phases, forced,
                                    opening_plies, crit=crit, wdls=wdls)
        side = "white" if is_white else "black"
        s = rep["sides"][side]
        moves = s["moves"] or 1

        my_idx = [k for k in range(len(phases)) if (k % 2 == 0) == is_white]
        eg_entry = next((k for k in my_idx if phases[k] == "Koncovka"), None)

        mv_all = accuracy.per_move(evals, wdls)

        crit_bins = None
        if crit is not None:
            sums = [0.0] * N_CRIT_BINS
            cnts = [0] * N_CRIT_BINS
            for k in my_idx:
                if k >= len(crit) or k >= len(mv_all):
                    continue
                bidx = min(N_CRIT_BINS - 1, int(crit[k] * N_CRIT_BINS))
                sums[bidx] += mv_all[k]["acc"]
                cnts[bidx] += 1
            crit_bins = (sums, cnts)

        # přesnost / hrubky podle tažené figury a typu tahu (jen tahy hráče)
        piece_stat: dict = {}     # piece_type -> [sum_acc, n, blund]
        mtype_stat: dict = {}     # "braní"/"tichý tah"/… -> [sum_acc, n, blund]
        # chybová heatmapa – pole (odkud/kam) sjednocené na perspektivu hráče
        err_sq = {"bf": [0] * 64, "bt": [0] * 64, "mf": [0] * 64, "mt": [0] * 64,
                  "af": [0] * 64, "at": [0] * 64}
        gmoves = list(game.moves)
        for k in my_idx:
            if k >= len(mv_all) or k >= len(gmoves) or k >= len(boards):
                continue
            b, m = boards[k], gmoves[k]
            acc_k = mv_all[k]["acc"]
            kind_k = accuracy.classify(mv_all[k]["win_drop"])
            is_bl = kind_k == "??"
            pt = b.piece_type_at(m.from_square)
            if pt:
                st_p = piece_stat.setdefault(pt, [0.0, 0, 0])
                st_p[0] += acc_k
                st_p[1] += 1
                st_p[2] += int(is_bl)
            if b.is_castling(m):
                mt = "rošáda"
            elif m.promotion:
                mt = "proměna"
            elif b.gives_check(m):
                mt = "šach"
            elif b.is_capture(m):
                mt = "braní"
            else:
                mt = "tichý tah"
            st_m = mtype_stat.setdefault(mt, [0.0, 0, 0])
            st_m[0] += acc_k
            st_m[1] += 1
            st_m[2] += int(is_bl)
            sf = m.from_square if is_white else chess.square_mirror(m.from_square)
            stg = m.to_square if is_white else chess.square_mirror(m.to_square)
            err_sq["af"][sf] += 1
            err_sq["at"][stg] += 1
            if is_bl:
                err_sq["bf"][sf] += 1
                err_sq["bt"][stg] += 1
            if kind_k in ("?", "??"):
                err_sq["mf"][sf] += 1
                err_sq["mt"][stg] += 1

        try:
            cc_counts = _count_kinds_ccom(evals, moves_uci, boards, crit, wdls)
        except Exception:
            cc_counts = {}
        try:
            brilliants = [
                {"ply": k + 1, "move_no": k // 2 + 1,
                 "by_player": ((k % 2 == 0) == is_white)}
                for k in _brilliant_plies_ccom(evals, moves_uci, boards, crit, wdls)
            ]
        except Exception:
            brilliants = []

        return {
            "gi": gi, "is_white": is_white, "label": game.label(),
            "tc": time_class(game), "year": game_year(game), "ym": game_year_month(game),
            "rating": _player_rating(game, is_white),
            "side": s,
            "phase": {ph: st for (sd, ph), st in rep["phase"].items() if sd == side},
            "acc": rep["acc"].get(side),
            "acpl": round(s["cp_loss"] / moves),
            "blund_100": round(s["blund"] * 100 / moves, 1),
            "vol": charakter.volatility(evals, wdls),
            "conv": charakter.conversion_record(evals, is_white, result, wdls),
            "eg_entry_move": (eg_entry // 2 + 1) if eg_entry is not None else None,
            "game_len_moves": moves,     # partie skončila po tolika tazích hráče
            "result": result,

            "mean_sharp": _mean(sharp[k] for k in my_idx) if sharp else None,
            "mean_cx": _mean(cx[k] for k in my_idx) if cx else None,
            "tact_pct": round(s["tact"] * 100 / s["tact_den"], 1) if s["tact_den"] else None,
            "crit_bins": crit_bins,
            "cc_counts": cc_counts,
            "brilliants": brilliants,
            "piece_stat": piece_stat,
            "mtype_stat": mtype_stat,
            "err_sq": err_sq,
        }

    # --------------------------------------------------------- sestavení výsledku
    def _build_result(self, records: list[dict], model: dict) -> dict:
        overall = _acc0()
        by_color = {"Bílé": _acc0(), "Černé": _acc0()}
        by_phase = {"Zahájení": _acc0(), "Středhra": _acc0(), "Koncovka": _acc0()}
        by_tc: dict = {}
        by_year: dict = {}
        by_month: dict = {}
        per_game: list = []
        crit_sums = [0.0] * N_CRIT_BINS
        crit_cnts = [0] * N_CRIT_BINS
        cc_total: dict = {}
        brilliant_games: list = []
        piece_tot: dict = {}
        mtype_tot: dict = {}
        err_tot = {k: [0] * 64 for k in ("bf", "bt", "mf", "mt", "af", "at")}

        for r in records:
            s, g_acc, ipr, conv = r["side"], r["acc"], r["ipr"], r["conv"]
            _merge(overall, s, g_acc, ipr, conv, rec=r)
            _merge(by_color["Bílé" if r["is_white"] else "Černé"], s, g_acc, ipr, conv, rec=r)
            for ph, st in r["phase"].items():
                _merge(by_phase[ph], st, count_game=False)
                by_phase[ph]["games"] += 1
            _merge(by_tc.setdefault(r["tc"], _acc0()), s, g_acc, ipr, conv, rec=r)
            if r["year"] is not None:
                _merge(by_year.setdefault(r["year"], _acc0()), s, g_acc, ipr, conv, rec=r)
            if r["ym"] is not None:
                _merge(by_month.setdefault(r["ym"], _acc0()), s, g_acc, ipr, conv, rec=r)
            per_game.append({
                "gi": r["gi"], "is_white": r["is_white"], "label": r["label"],
                "acc": g_acc, "acpl": r["acpl"], "blund": s["blund"],
                "ep_lost": round(s["ep_loss"], 2),
                "t1": round(s["t1"] * 100 / s["t1_den"], 1) if s["t1_den"] else None,
                "index": accuracy.composite_index(
                    g_acc, r["acpl"], r["blund_100"],
                    round(s["t1"] * 100 / s["t1_den"], 1) if s["t1_den"] else None),
                "ipr": ipr, "vol": r["vol"]["mean_swing"],
                "reversals": r["vol"]["reversals"],
                "sharp": r["mean_sharp"], "cx": r["mean_cx"],
                "result": r["result"], "game_len_moves": r["game_len_moves"],
                "tact_pct": r["tact_pct"],
                "had_win": conv["had_win"] if conv["result_known"] else False,
                "won": conv["won"] if conv["result_known"] else False,
                "had_loss": conv["had_loss"] if conv["result_known"] else False,
                "not_lost": conv["not_lost"] if conv["result_known"] else False,
                "ep_wasted": conv["ep_wasted"] if conv["result_known"] and conv["had_win"] else None,
            })
            if r["crit_bins"] is not None:
                sums, cnts = r["crit_bins"]
                for i in range(N_CRIT_BINS):
                    crit_sums[i] += sums[i]
                    crit_cnts[i] += cnts[i]
            for kind, c in r["cc_counts"].items():
                cc_total[kind] = cc_total.get(kind, 0) + c
            for b in r.get("brilliants") or []:
                brilliant_games.append({
                    "gi": r["gi"], "label": r["label"], "is_white": r["is_white"],
                    "ply": b["ply"], "move_no": b["move_no"],
                    "by_player": b["by_player"],
                })
            for pt, (sa, n, bl) in (r.get("piece_stat") or {}).items():
                t = piece_tot.setdefault(pt, [0.0, 0, 0])
                t[0] += sa
                t[1] += n
                t[2] += bl
            for mt, (sa, n, bl) in (r.get("mtype_stat") or {}).items():
                t = mtype_tot.setdefault(mt, [0.0, 0, 0])
                t[0] += sa
                t[1] += n
                t[2] += bl
            es = r.get("err_sq") or {}
            for key, arr in err_tot.items():
                src = es.get(key) or []
                for i in range(min(64, len(src))):
                    arr[i] += src[i]

        sections = [
            ("Souhrn", [("Celkem", _final(overall))]),
            ("Podle barvy", [(k, _final(v)) for k, v in by_color.items() if v["games"]]),
            ("Podle fáze", [(k, _final(v)) for k, v in by_phase.items() if v["moves"]]),
        ]
        if by_tc:
            sections.append(("Podle tempa",
                             [(k, _final(by_tc[k])) for k in sorted(by_tc)]))
        # "Vývoj v čase": po letech, pokud partie pokrývají aspoň 2 roky (hrubší,
        # čitelnější osa u dlouhé historie); jinak po měsících – vyžadovat vždy
        # aspoň 2 roky nemá smysl, když někdo hraje intenzivně jen pár měsíců.
        if len(by_year) >= 2:
            sections.append(("Vývoj v čase",
                             [(str(y), _final(by_year[y])) for y in sorted(by_year)]))
        elif len(by_month) >= 2:
            sections.append(("Vývoj v čase",
                             [(ym, _final(by_month[ym])) for ym in sorted(by_month)]))

        iprs = [r["ipr"] for r in records if r["ipr"] is not None]
        ratings = [r["rating"] for r in records if r["rating"] is not None]
        recent = [r["ipr"] for r in records[:10] if r["ipr"] is not None]
        ipr_info = {
            "value": round(sum(iprs) / len(iprs)) if iprs else None,
            "anchor": round(model["y0"]) if model.get("kind") == "anchor" else None,
            "mean_rating": round(sum(ratings) / len(ratings)) if ratings else None,
            "recent": round(sum(recent) / len(recent)) if recent else None,
            "method": model.get("method", "hrubý odhad z ACPL"),
            "n_rated": model.get("n_rated", 0),
        }

        crit_accuracy = None
        if any(crit_cnts):
            crit_accuracy = {
                "edges": [i / N_CRIT_BINS for i in range(N_CRIT_BINS + 1)],
                "mean_acc": [round(crit_sums[i] / crit_cnts[i], 1) if crit_cnts[i] else None
                            for i in range(N_CRIT_BINS)],
                "n": crit_cnts,
            }

        def _fmt_group(d: dict) -> dict:
            return {k: {"acc": round(v[0] / v[1], 1) if v[1] else None,
                        "blund_100": round(v[2] * 100 / v[1], 1) if v[1] else None,
                        "n": v[1]}
                    for k, v in d.items()}

        return {
            "n_games": len(records), "requested": len(self._jobs), "depth": self._depth,
            "partial": self._stop, "thorough": self._thorough,
            "sections": sections, "per_game": per_game, "ipr_info": ipr_info,
            "overall": _final(overall),
            "character": _character_lines(records, overall),
            "conversion": _conversion_lines(records),
            "crit_accuracy": crit_accuracy,
            "cc_counts": cc_total,
            "brilliants": brilliant_games,
            "piece_accuracy": _fmt_group(piece_tot),
            "movetype_accuracy": _fmt_group(mtype_tot),
            "error_map": err_tot,   # 64-pole, perspektiva hráče: b*=hrubky, m*=chyby+, a*=všechny tahy; *f=odkud, *t=kam
        }


# ------------------------------------------------------------- IPR model
#
# IPR pro jednoho hráče je záludný: jeho vlastní Elo se přes pár desítek partií
# skoro nehne, takže „regrese proti vlastnímu Elu" nemá cíl s rozptylem. Proto se
# model *ukotví* na hráčovo průměrné Elo v rozboru a per-partii jen měří, o kolik
# hrál nad/pod svou úroveň podle relativní kvality tahů (ln ACPL). Sklon se vezme
# pevný (-580 Elo / ln), nebo se dofituje, když Elo v rozboru dost kolísá.
def _fit_ipr(records: list[dict]) -> dict:
    rated = [(math.log(max(3.0, min(300.0, r["acpl"]))), float(r["rating"]))
             for r in records if r["rating"] is not None]
    n = len(rated)
    if n < _MIN_RATED_FOR_ANCHOR:
        return {"kind": "curve", "n_rated": n, "method": "hrubý odhad z ACPL"}
    xs = [x for x, _ in rated]
    ys = [y for _, y in rated]
    x0 = sum(xs) / n
    y0 = sum(ys) / n
    r_std = math.sqrt(sum((y - y0) ** 2 for y in ys) / n)
    var_x = sum((x - x0) ** 2 for x in xs) / n
    slope = _DEFAULT_SLOPE
    method = f"kotva na tvé Elo ({round(y0)}), pevný sklon"
    if r_std >= _MIN_SPREAD_FOR_SLOPE and var_x > 1e-6:
        s = sum((x - x0) * (y - y0) for x, y in rated) / n / var_x
        if -1500.0 < s < -80.0:
            slope = s
            method = f"kotva na tvé Elo ({round(y0)}) + sklon z tvých partií"
    return {"kind": "anchor", "x0": x0, "y0": y0, "slope": slope,
            "n_rated": n, "method": method}


def _game_ipr(model: dict, r: dict):
    if r["acpl"] is None:
        return None
    if model.get("kind") == "anchor":
        x = math.log(max(3.0, min(300.0, r["acpl"])))
        v = model["y0"] + model["slope"] * (x - model["x0"])
        # jedna partie je hodně šumivá – odchylku od kotvy omez na ±450
        v = max(model["y0"] - 450.0, min(model["y0"] + 450.0, v))
    else:
        v = accuracy.ipr_from_acpl(r["acpl"])
    return round(max(400.0, min(2900.0, v)))


# ------------------------------------------------------------- charakter partií
def _character_lines(records: list[dict], overall: dict) -> list[str]:
    vols = [r["vol"]["mean_swing"] for r in records]
    maxv = max(((r["vol"]["max_swing"], r["label"]) for r in records), default=None)
    wild = sum(1 for v in vols if v > WILD_THRESHOLD)
    revs = _mean(r["vol"]["reversals"] for r in records)

    # medián vstupu do koncovky (Kaplan–Meier): partie, co skončí dřív bez dosažení
    # koncovky, jsou cenzurované (aspoň tolik tahů to vydrželo), ne „nikdy by nedošly"
    km_records = [((r["eg_entry_move"], True) if r["eg_entry_move"] is not None
                  else (r["game_len_moves"], False)) for r in records]
    km = kaplan_meier(km_records)
    if km["median"] is not None:
        eg_line = (f"Medián vstupu do koncovky (Kaplan–Meier): {km['median']}. tah "
                  f"({km['n_events']} z {km['n']} partií do koncovky skutečně došlo; "
                  f"partie, co skončily dřív, se počítají korektně jako cenzurované, "
                  f"ne jako by koncovky nedošly).")
    elif km["n_events"] > 0:
        eg_line = (f"Do koncovky došlo jen u {km['n_events']} z {km['n']} partií – "
                  f"na medián je to málo, ale k dispozici jsou.")
    else:
        eg_line = "Do koncovky (podle detektoru koncovek) nedošla žádná z rozebraných partií."

    lines = [
        f"Volatilita: průměrný skok šance na výhru {_mean(vols):.1f} % na půltah "
        f"(nejvíc {maxv[0]:.0f} % v partii „{maxv[1][:40]}…“)." if maxv else "",
        f"Obratů (kdo stojí líp) na partii v průměru {revs:.1f}." if revs is not None else "",
        f"Divokých partií (volatilita > {WILD_THRESHOLD:.0f} %): {wild} z {len(records)}.",
        eg_line,
    ]
    sharps = [r["mean_sharp"] for r in records if r["mean_sharp"] is not None]
    cxs = [r["mean_cx"] for r in records if r["mean_cx"] is not None]
    if sharps:
        lines.append(f"Ostrost pozic (0 = vše drží, 1 = drží jediný tah): "
                     f"{_mean(sharps):.2f}.")
    if cxs:
        lines.append(f"Komplexita pozic (rozptyl top tahů): {_mean(cxs):.2f}.")
    if overall["cwl_den"] > 0:
        cwl = round(overall["cwl_num"] / overall["cwl_den"])
        lines.append(f"Kritičností vážený ACPL: {cwl} (syrový ACPL {round(overall['cp_loss'] / (overall['moves'] or 1))}) "
                     f"– počítá jen chyby v pozicích, kde na tahu skutečně záleželo.")
    elif not (sharps or cxs):
        lines.append("Ostrost / komplexita / kritičností vážený ACPL: "
                     "zapni „důkladný rozbor“ (multipv) a spusť znovu.")
    if overall["tact_den"] > 0:
        tact = round(overall["tact"] * 100 / overall["tact_den"], 1)
        lines.append(f"Tactical Awareness: {tact} % – shoda s enginem v pozicích s jasně "
                     f"nejlepším tahem (kritičnost ≥ {accuracy.TACT_CRIT_THRESHOLD:.2f}, "
                     f"{overall['tact_den']} takových tahů); jak dobře hráč vidí taktiku, "
                     f"když na tahu skutečně záleží.")
    return [ln for ln in lines if ln]


def _conversion_lines(records: list[dict]) -> list[str]:
    known = [r for r in records if r["conv"]["result_known"]]
    wins = [r for r in known if r["conv"]["had_win"]]
    losses = [r for r in known if r["conv"]["had_loss"]]
    lines = []
    if wins:
        converted = sum(1 for r in wins if r["conv"]["won"])
        wasted = _mean(r["conv"]["ep_wasted"] for r in wins)
        lines.append(f"Vyhrané pozice dotažené k výhře: {converted} z {len(wins)} "
                     f"({100 * converted / len(wins):.0f} %); "
                     f"v průměru ztraceno {wasted:.2f} bodu z dosaženého maxima.")
    else:
        lines.append("Žádná z rozebraných partií neměla jasně vyhranou pozici.")
    if losses:
        saved = sum(1 for r in losses if r["conv"]["not_lost"])
        lines.append(f"Záchrany z prohraných pozic: {saved} z {len(losses)} "
                     f"({100 * saved / len(losses):.0f} %).")
    else:
        lines.append("Žádná z rozebraných partií neměla jasně prohranou pozici.")
    return lines
