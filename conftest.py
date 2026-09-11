"""Sdílená konfigurace pytestu – přidá kořen aplikace na ``sys.path``, aby
testy v ``tests/`` mohly dělat ploché importy (``import accuracy`` apod.),
stejně jako to dělá samotná aplikace.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# GUI testy (MainWindow) běží bez displeje
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_persistent_files(tmp_path, monkeypatch):
    """Bezpečnostní síť: přesměruje cesty VŠECH trvalých souborů appky (cache
    rozborů, cache Opening Exploreru, populační data koncovek, postup
    v úlohách, uložené reporty) do dočasné složky – u KAŽDÉHO testu, i když on
    sám žádnou izolaci nedělá.

    Historie: test jednou tiše přepsal skutečný `endgame_population.json.gz`
    uživatele vedle aplikace daty ze syntetické partie, protože
    `save_population(state, path=POP_PATH)` mělo cestu svázanou jako výchozí
    hodnotu parametru (vyhodnotí se jednou při importu modulu, `monkeypatch`
    na ni pak nedosáhne). Ta konkrétní funkce je opravená (čte modulovou
    proměnnou až v těle), ale tohle je navíc plošná pojistka proti stejné
    třídě chyby kdekoli jinde – i budoucí."""
    import analysis_cache
    import endgame_dump
    import opening_explorer
    import player_report
    import tactics
    monkeypatch.setattr(analysis_cache, "_PATH", str(tmp_path / "analysis_cache.json.gz"))
    monkeypatch.setattr(analysis_cache, "_LEGACY_PATH", str(tmp_path / "analysis_cache.json"))
    monkeypatch.setattr(opening_explorer, "_CACHE_PATH", str(tmp_path / "opening_explorer_cache.json.gz"))
    monkeypatch.setattr(endgame_dump, "POP_PATH", str(tmp_path / "endgame_population.json.gz"))
    monkeypatch.setattr(tactics, "_STORE_PATH", str(tmp_path / "tactics_progress.json"))
    monkeypatch.setattr(player_report, "_STORE_PATH", str(tmp_path / "player_reports.json"))
    yield
