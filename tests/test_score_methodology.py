"""Regresní testy na to, že se remízy počítají všude stejně (skóre: výhra=1,
remíza=0,5, prohra=0), ne jako holý podíl výher.

Uživatel se ptal, jestli appka používá stejnou metodiku pro VŠECHNY výpočty
úspěšnosti – audit odhalil, že dva grafy (Úspěšnost podle zahájení a heatmapa
dne × hodiny), každý ve dvou verzích (živý graf na kartě *Grafy* a jeho
protějšek na kartě *Report*), počítaly čistý podíl výher, zatímco zbytek appky
(strom na kartě Zahájení, karta Vzorce, Opening Explorer, …) už dávno počítal
skóre. Tyhle 4 testy hlídají, že se to znovu nerozjede – partie s 1 výhrou
a 1 remízou musí dát 75 %, ne 50 %, jaké by vyšlo z holého podílu výher."""
import chess
import pytest

from pgn_game import LoadedGame


def _game(result_hdr: str, white="hrac", black="souper",
         utc_date="2024.01.15", utc_time="10:00:00") -> LoadedGame:
    headers = {"White": white, "Black": black, "Result": result_hdr,
              "ECO": "C00", "Opening": "Testovací zahájení",
              "UTCDate": utc_date, "UTCTime": utc_time}
    return LoadedGame(headers, [])


@pytest.fixture
def window(qapp):
    import main_window
    w = main_window.MainWindow()
    w.games = [_game("1-0"), _game("1/2-1/2")]   # 1 výhra + 1 remíza hráče "hrac"
    w._filter_keep = None
    w.cmb_player.blockSignals(True)
    w.cmb_player.addItem("hrac", "hrac")
    w.cmb_player.setCurrentIndex(w.cmb_player.count() - 1)
    w.cmb_player.blockSignals(False)
    return w


def test_chart_openings_pouziva_skore_ne_holy_podil_vyher(window):
    window._chart_openings()
    chart = window.chart_view.chart()
    barset = chart.series()[0].barSets()[0]
    assert barset.count() == 1
    assert barset.at(0) == pytest.approx(75.0)   # NE 50.0 (holý podíl výher)


def test_chart_time_heatmap_pouziva_skore_ne_holy_podil_vyher(window):
    window._chart_time_heatmap()
    # 2024-01-15 10:00 UTC (zima, offset +1) -> pondělí 11:00 místního času
    assert window.chart_heat._wr[0][11] == pytest.approx(0.75)   # NE 0.5


def test_report_openings_pouziva_skore(qapp):
    import report_charts as rc
    snap = {"label": "hrac",
            "openings": [{"label": "C00: Testovací zahájení", "w": 1, "d": 1, "l": 0, "n": 2}]}
    _title, chart = rc._openings([snap])[0]
    barset = chart.series()[0].barSets()[0]
    assert barset.at(0) == pytest.approx(75.0)


def test_report_time_heatmap_pouziva_skore(qapp):
    import report_charts as rc
    snap = {"label": "hrac",
            "game_log": [["2024-01-15T10:00:00", None, "win"],
                        ["2024-01-15T10:00:00", None, "draw"]]}
    _title, widget = rc._time_heatmap([snap])[0]
    assert widget._wr[0][11] == pytest.approx(0.75)
