"""Testy gzipované cache rozborů (``analysis_cache``) – migrace ze starého
nezabaleného JSONu, komprese, čtení/zápis, ``clear``. Cesty se přesměrují do
``tmp_path``, aby se nesáhlo na skutečnou cache uživatele."""
import gzip
import json
import os

import pytest

import analysis_cache as ac


@pytest.fixture
def paths(tmp_path, monkeypatch):
    gz = tmp_path / "analysis_cache.json.gz"
    legacy = tmp_path / "analysis_cache.json"
    monkeypatch.setattr(ac, "_PATH", str(gz))
    monkeypatch.setattr(ac, "_LEGACY_PATH", str(legacy))
    return gz, legacy


def _entry():
    return dict(key="abc", depth=12, evals=[0, 10, -5], bestmoves=["e2e4", None, "g1f3"])


def test_put_get_save_roundtrip(paths):
    gz, legacy = paths
    c = ac.AnalysisCache()
    c.put(**_entry())
    c.save()
    assert gz.exists()
    # znovu načteno z .gz
    c2 = ac.AnalysisCache()
    got = c2.get("abc", min_depth=12)
    assert got["evals"] == [0, 10, -5]
    assert got["bestmoves"] == ["e2e4", None, "g1f3"]


def test_get_respektuje_min_depth(paths):
    c = ac.AnalysisCache()
    c.put(**_entry())
    assert c.get("abc", min_depth=13) is None
    assert c.get("abc", min_depth=12) is not None


def test_get_need_topk(paths):
    c = ac.AnalysisCache()
    c.put(**_entry())                      # bez topk
    assert c.get("abc", 12, need_topk=True) is None
    c.put(key="abc", depth=12, evals=[0], bestmoves=["e2e4"],
          topk=[[("e2e4", 20)]])
    assert c.get("abc", 12, need_topk=True) is not None


def test_migrace_ze_stareho_json(paths):
    gz, legacy = paths
    legacy.write_text(json.dumps({"old": {"depth": 10, "evals": [1, 2],
                                          "bestmoves": ["a", "b"]}}), encoding="utf-8")
    c = ac.AnalysisCache()
    assert c.get("old", min_depth=10)["evals"] == [1, 2]
    # nový zápis → .gz vznikne, starý se smaže
    c.put(**_entry())
    c.save()
    assert gz.exists()
    assert not legacy.exists()


def test_soubor_je_opravdu_gzip_a_mensi(paths):
    gz, _ = paths
    c = ac.AnalysisCache()
    for i in range(200):
        c.put(key=f"g{i}", depth=12, evals=list(range(30)),
              bestmoves=["e2e4"] * 30)
    c.save()
    with gzip.open(gz, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    assert len(data) == 200
    raw = json.dumps(data).encode("utf-8")
    assert gz.stat().st_size < len(raw)      # komprese něco ušetřila


def test_clear_smaze_oba_soubory(paths):
    gz, legacy = paths
    legacy.write_text("{}", encoding="utf-8")
    c = ac.AnalysisCache()
    c.put(**_entry())
    c.save()
    assert gz.exists()
    n = c.clear()
    assert n >= 1
    assert not gz.exists()
    assert not legacy.exists()
    assert c.get("abc", min_depth=1) is None


def test_save_bez_zmen_nezapisuje(paths):
    gz, _ = paths
    c = ac.AnalysisCache()
    c.save()
    assert not gz.exists()
