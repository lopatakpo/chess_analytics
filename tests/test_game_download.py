"""Testy stažení partií z lichess/chess.com (``game_download``) – bez sítě,
``net_util.open_url`` se mockuje odpovědí z paměti."""
import io
import json

import pytest

import game_download as gd


class _Resp(io.BytesIO):
    """Minimální náhrada za HTTP odpověď (context manager + read + headers)."""
    def __init__(self, data: bytes, headers: dict | None = None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


_SAMPLE_PGN = (
    '[Event "Rated blitz game"]\n[White "a"]\n[Black "b"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n\n'
    '[Event "Rated blitz game"]\n[White "b"]\n[Black "a"]\n[Result "0-1"]\n\n1. d4 d5 0-1\n\n'
)


def test_count():
    assert gd._count(_SAMPLE_PGN) == 2
    assert gd._count("") == 0


def test_month_in_range():
    u = "https://api.chess.com/pub/player/x/games/2024/06"
    assert gd._month_in_range(u, None, None) is True
    assert gd._month_in_range(u, (2024, 1), (2024, 12)) is True
    assert gd._month_in_range(u, (2024, 7), None) is False
    assert gd._month_in_range(u, None, (2024, 5)) is False


def test_lichess_games(monkeypatch):
    seen = {}

    def fake_open(url, headers=None, timeout=30.0):
        seen["url"] = url
        seen["headers"] = headers
        return _Resp(_SAMPLE_PGN.encode())

    monkeypatch.setattr(gd, "open_url", fake_open)
    got = []
    txt = gd.lichess_games("Magnus", rated_only=True, on_progress=got.append)
    assert gd._count(txt) == 2
    assert "games/user/Magnus" in seen["url"]
    assert "rated=true" in seen["url"]
    assert seen["headers"]["Accept"] == "application/x-chess-pgn"
    assert got and got[-1] == 2


def test_lichess_games_cancel(monkeypatch):
    monkeypatch.setattr(gd, "open_url",
                        lambda *a, **k: _Resp((_SAMPLE_PGN * 100).encode()))
    with pytest.raises(gd.DownloadCancelled):
        gd.lichess_games("x", should_stop=lambda: True)


def test_chesscom_games(monkeypatch):
    archives = {"archives": [
        "https://api.chess.com/pub/player/hikaru/games/2024/05",
        "https://api.chess.com/pub/player/hikaru/games/2024/06",
    ]}

    def fake_open(url, headers=None, timeout=30.0):
        if url.endswith("/games/archives"):
            return _Resp(json.dumps(archives).encode())
        assert url.endswith("/pgn")
        return _Resp(_SAMPLE_PGN.encode())

    monkeypatch.setattr(gd, "open_url", fake_open)
    monkeypatch.setattr(gd.time, "sleep", lambda *_: None)
    txt = gd.chesscom_games("Hikaru")
    assert gd._count(txt) == 4                       # 2 archivy × 2 partie


def test_chesscom_games_month_filter(monkeypatch):
    archives = {"archives": [
        "https://api.chess.com/pub/player/x/games/2023/12",
        "https://api.chess.com/pub/player/x/games/2024/06",
    ]}
    calls = []

    def fake_open(url, headers=None, timeout=30.0):
        if url.endswith("/archives"):
            return _Resp(json.dumps(archives).encode())
        calls.append(url)
        return _Resp(_SAMPLE_PGN.encode())

    monkeypatch.setattr(gd, "open_url", fake_open)
    monkeypatch.setattr(gd.time, "sleep", lambda *_: None)
    gd.chesscom_games("x", since=(2024, 1))
    assert len(calls) == 1 and "2024/06" in calls[0]


def test_worker_combines_sources(monkeypatch, qtbot_none=None):
    monkeypatch.setattr(gd, "lichess_games",
                        lambda u, **k: _SAMPLE_PGN)
    monkeypatch.setattr(gd, "chesscom_games",
                        lambda u, **k: _SAMPLE_PGN)
    w = gd.GameDownloadWorker("li", "cc")
    out = {}
    w.finished_ok.connect(lambda text, n: out.update(text=text, n=n))
    w.failed.connect(lambda m: out.update(err=m))
    w.run()
    assert out.get("err") is None
    assert out["n"] == 4


def test_worker_no_games_fails(monkeypatch):
    monkeypatch.setattr(gd, "lichess_games", lambda u, **k: "")
    w = gd.GameDownloadWorker("li", "")
    out = {}
    w.finished_ok.connect(lambda text, n: out.update(ok=True))
    w.failed.connect(lambda m: out.update(err=m))
    w.run()
    assert out.get("err") and "žádné" in out["err"]


def test_http_message():
    import urllib.error
    e404 = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    assert "nenalezen" in gd._http_message(e404, "lichess")
    e429 = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "30"}, None)
    assert "429" in gd._http_message(e429, "lichess")
