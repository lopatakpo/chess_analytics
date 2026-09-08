"""Testy porovnání zahájení s populací (``opening_explorer``) – bez sítě
(``_fetch`` se mockuje) a bez doteku skutečné cache (cesta do tmp)."""
import chess
import pytest

import opening_explorer as oe


# ------------------------------------------------------------- Elo pásma / tempa
def test_bands_for():
    assert oe.bands_for(None) == "1600,1800,2000"
    assert oe.bands_for(2054) == "1800,2000,2200"     # 2054 ∈ [2000,2200)
    assert oe.bands_for(500) == "0,1000"              # pod nejnižší hranicí
    assert oe.bands_for(3000).endswith("2500")


def test_speeds_csv():
    assert oe.speeds_csv() == "bullet,blitz,rapid"
    assert oe.speeds_csv("blitz") == "blitz"
    assert oe.speeds_csv("nesmysl") == "bullet,blitz,rapid"


# --------------------------------------------------------------- fen_after
class _FakeGame:
    def __init__(self, uci_moves, result="win"):
        self.moves = [chess.Move.from_uci(u) for u in uci_moves]
        self.result = result

    def start_board(self):
        return chess.Board()


def _ucis(*sans):
    b = chess.Board()
    out = []
    for s in sans:
        m = b.parse_san(s)
        out.append(m.uci())
        b.push(m)
    return out


def test_fen_after():
    g = _FakeGame(_ucis("e4", "c5", "Nf3", "d6"))
    b = chess.Board()
    for m in g.moves:
        b.push(m)
    assert oe.fen_after(g, 4) == b.fen()
    assert oe.fen_after(g, 0) == chess.Board().fen()
    assert oe.fen_after(g, 99).split()[0] == b.fen().split()[0]  # nad délku → poslední


# --------------------------------------------------------------- build_tasks
class _Var:
    def __init__(self, name, entries):
        self.name = name
        self.entries = entries


class _Entry:
    def __init__(self, gi, enter_ply, result):
        self.game_index = gi
        self.enter_ply = enter_ply
        self.result = result


class _Grp:
    def __init__(self, variations):
        self.variations = variations


def test_build_tasks_like_for_like():
    line = _ucis("e4", "c5", "Nf3", "d6", "d4", "cxd4")
    games = [_FakeGame(line, "win" if i % 2 else "loss") for i in range(10)]
    # varianta s 10 partiemi, všechny stejná linka → 1 FEN, celá se porovná
    grp = _Grp([_Var("Sicilská: test", [_Entry(i, 6, games[i].result) for i in range(10)])])
    tasks = oe.build_tasks([grp], games, "white")
    assert len(tasks) == 1
    t = tasks[0]
    assert t["is_white"] is True
    assert t["n_repr"] == 10 and t["n_total"] == 10
    assert len(t["results"]) == 10


def test_build_tasks_vynecha_male_varianty():
    line = _ucis("e4", "e5")
    games = [_FakeGame(line) for _ in range(5)]
    grp = _Grp([_Var("malá", [_Entry(i, 4, "win") for i in range(5)])])
    assert oe.build_tasks([grp], games, "white") == []


# --------------------------------------------------------------- worker (mock net)
@pytest.fixture
def _no_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(oe, "_CACHE_PATH", str(tmp_path / "expl.json.gz"))


def test_worker_assembles_rows(monkeypatch, _no_disk):
    monkeypatch.setattr(oe, "_fetch", lambda fen, r, s, timeout=12.0: {
        "white": 4000, "draws": 1000, "black": 5000, "total": 10000, "opening": "X"})
    line = _ucis("e4", "e5", "f4")
    games = [_FakeGame(line, ["win", "win", "win", "draw", "loss"][i % 5])
             for i in range(20)]
    grp = _Grp([_Var("Královský gambit", [_Entry(i, 6, games[i].result) for i in range(20)])])
    tasks = oe.build_tasks([grp], games, "white")
    w = oe.ExplorerWorker(tasks, "1800,2000", "blitz")
    rows = []
    w.finished_ok.connect(rows.extend)
    w.failed.connect(lambda m: pytest.fail(m))
    w.run()
    assert len(rows) == 1
    r = rows[0]
    assert r["n_pop"] == 10000
    assert r["wr_pop"] == pytest.approx(0.4)          # white 4000/10000
    assert 0.0 <= r["wr_player"] <= 1.0
    assert r["p"] is not None
    assert "sig" in r


def test_worker_skips_thin_population(monkeypatch, _no_disk):
    monkeypatch.setattr(oe, "_fetch", lambda *a, **k: {
        "white": 5, "draws": 2, "black": 8, "total": 15, "opening": None})
    line = _ucis("e4", "e5", "f4")
    games = [_FakeGame(line) for _ in range(12)]
    grp = _Grp([_Var("okrajovka", [_Entry(i, 6, "win") for i in range(12)])])
    tasks = oe.build_tasks([grp], games, "white")
    w = oe.ExplorerWorker(tasks, "1800", "blitz")
    got = {"rows": None, "fail": None}
    w.finished_ok.connect(lambda r: got.update(rows=r))
    w.failed.connect(lambda m: got.update(fail=m))
    w.run()
    assert got["rows"] is None and got["fail"] is not None   # nic nad prahem MIN_POP_GAMES
