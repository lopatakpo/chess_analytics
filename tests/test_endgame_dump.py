"""Testy populačního agregátoru koncovek z dumpu (``endgame_dump``) – bez sítě,
malý vygenerovaný PGN místo skutečného měsíčního dumpu."""
import random

import chess
import chess.pgn
import pytest

import endgame_dump as ed


def _ply_depth(node):
    d = 0
    while node.parent is not None:
        d += 1
        node = node.parent
    return d


def _make_game(seed: int, event: str, we: int, be: int) -> str:
    r = random.Random(seed)
    b = chess.Board()
    g = chess.pgn.Game()
    node = g
    for _ in range(240):
        if b.is_game_over():
            break
        legal = list(b.legal_moves)
        caps = [m for m in legal if b.is_capture(m)]
        m = r.choice(caps) if (caps and r.random() < 0.82) else r.choice(legal)
        node = node.add_variation(m)
        b.push(m)
        if (ed.EG._table_ok(ed.EG._piece_counts(b))
                and _ply_depth(node) > 26 and r.random() < 0.4):
            break
    res = b.result(claim_draw=True)
    if res == "*":
        res = r.choice(["1-0", "0-1", "1/2-1/2"])
    g.headers.update({"Event": event, "White": "a", "Black": "b",
                      "WhiteElo": str(we), "BlackElo": str(be), "Result": res})
    return g.accept(chess.pgn.StringExporter(headers=True, variations=False,
                                             comments=False))


@pytest.fixture
def dump(tmp_path):
    parts = []
    for i in range(120):
        ev = ["Rated Blitz game", "Rated Rapid game", "Rated Classical game"][i % 3]
        parts.append(_make_game(i, ev, 1800 + (i * 31) % 600, 1820 + (i * 47) % 560))
    # partie, které filtr musí zahodit:
    parts.append(_make_game(999, "Rated Bullet game", 2000, 2000))     # tempo
    parts.append(_make_game(998, "Rated Blitz game", 1500, 1500))      # Elo
    p = tmp_path / "mini_dump.pgn"
    p.write_text("\n\n".join(parts) + "\n\n", encoding="utf-8")
    return str(p)


def test_aggregate_filters_and_counts(dump):
    st = ed.aggregate(dump)
    m = st["meta"]
    assert m["done"] is True
    assert m["scanned"] == 122
    assert m["matched"] == 120                     # bullet + 1500 vyřazeny
    assert 0 < m["with_eg"] <= 120
    assert st["cats"]                              # něco se zařadilo


def test_aggregate_max_games(dump):
    st = ed.aggregate(dump, max_games=25)
    assert st["meta"]["matched"] == 25
    assert st["meta"]["done"] is False             # nedojeto → dá se navázat


def test_aggregate_time_class_filter(dump):
    st = ed.aggregate(dump, time_classes=("classical",))
    for c in st["cats"].values():
        assert set(c["tc"]) <= {"classical"}


def test_aggregate_elo_band(dump):
    st = ed.aggregate(dump, elo_lo=2000, elo_hi=2400)
    # žádný Elo bucket pod 2000
    for c in st["cats"].values():
        assert all(int(b) >= 2000 for b in c["elo"])


def test_resume_matches_full_run(dump):
    full = ed.aggregate(dump)
    partial = ed.aggregate(dump, max_games=30)
    resumed = ed.aggregate(dump, resume=partial)
    assert resumed["meta"]["matched"] == full["meta"]["matched"]
    assert resumed["meta"]["with_eg"] == full["meta"]["with_eg"]
    assert set(resumed["cats"]) == set(full["cats"])
    a = next(iter(full["cats"]))
    assert resumed["cats"][a]["n"] == full["cats"][a]["n"]


def test_rows_and_views(dump):
    st = ed.aggregate(dump)
    rws = ed.rows(st, min_n=1)
    assert rws and rws == sorted(rws, key=lambda r: -r["n"])
    for r in rws:
        for k in ("draw_pct", "bal_draw_pct", "e1_conv_pct"):
            assert r[k] is None or 0.0 <= r[k] <= 100.0
    top = rws[0]["label"]
    assert isinstance(ed.elo_trend(st, top), list)
    assert isinstance(ed.tc_split(st, top), list)


def test_population_lookup(dump):
    st = ed.aggregate(dump)
    top = ed.rows(st, min_n=1)[0]["label"]
    pl = ed.population_lookup(st, top, 2000)
    assert pl is not None and pl["n"] >= 1
    assert ed.population_lookup(st, "Neexistující koncovka", 2000) is None


def test_persistence_roundtrip(dump, tmp_path):
    st = ed.aggregate(dump)
    p = str(tmp_path / "pop.json.gz")
    ed.save_population(st, p)
    back = ed.load_population(p)
    assert back is not None
    assert back["meta"]["matched"] == st["meta"]["matched"]
    assert set(back["cats"]) == set(st["cats"])
    ed.clear_population(p)
    assert ed.load_population(p) is None


def test_tc_of_and_edge_key():
    assert ed._tc_of("Rated Rapid game") == "rapid"
    assert ed._tc_of("Rated UltraBullet game") == "ultrabullet"
    assert ed._tc_of("Something") is None
    assert ed._edge_key(1) == "1" and ed._edge_key(2) == "2" and ed._edge_key(5) == "3+"
