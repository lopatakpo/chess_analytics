"""Testy práce s časem na hodinách (``clock_util``) – čistá matematika."""
import pytest

from clock_util import (
    PIECE_LABEL, PIECE_ORDER, TIME_BINS, bin_label, move_seconds,
    parse_time_control, time_bin,
)


def test_parse_time_control_zaklad():
    assert parse_time_control("180+2") == (180.0, 2.0)
    assert parse_time_control("60+0") == (60.0, 0.0)
    assert parse_time_control("300") == (300.0, 0.0)


def test_parse_time_control_chybejici_nebo_nerozpoznatelne():
    assert parse_time_control(None) is None
    assert parse_time_control("") is None
    assert parse_time_control("-") is None
    assert parse_time_control("1/86400") is None      # korespondenční, appka to nezná
    assert parse_time_control("bla+bla") is None


def test_move_seconds_zaklad():
    # 180+2, hráč hraje 4 tahy: 178 (2s), 174 (6s), 173 (3s), 170 (5s)
    clocks = [178.0, 174.0, 173.0, 170.0]
    spent = move_seconds(clocks, initial=180.0, increment=2.0)
    assert spent == pytest.approx([4.0, 6.0, 3.0, 5.0])


def test_move_seconds_bez_prirustku():
    clocks = [55.0, 50.0, 40.0]
    spent = move_seconds(clocks, initial=60.0, increment=0.0)
    assert spent == pytest.approx([5.0, 5.0, 10.0])


def test_move_seconds_chybejici_zaznam_prerusi_navaznost():
    clocks = [58.0, None, 40.0]
    spent = move_seconds(clocks, initial=60.0, increment=0.0)
    assert spent[0] == pytest.approx(2.0)
    assert spent[1] is None
    assert spent[2] is None          # návazný tah po chybějícím taky neznámý


def test_move_seconds_nezaporne():
    # anotace může driftovat (zaokrouhlení) – appka to neukáže jako záporný čas
    clocks = [61.0]                   # "více" než initial kvůli zaokrouhlení
    spent = move_seconds(clocks, initial=60.0, increment=0.0)
    assert spent[0] == 0.0


def test_time_bin_hranice():
    assert time_bin(0.0) == 0
    assert time_bin(1.9) == 0
    assert time_bin(2.0) == 1
    assert time_bin(59.9) == 4
    assert time_bin(60.0) == 5
    assert time_bin(1000.0) == 5
    assert len(TIME_BINS) == 6
    assert bin_label(0) and bin_label(5)


def test_piece_label_pokryva_vsechny_druhy():
    assert set(PIECE_LABEL) == set(PIECE_ORDER)
    assert len(PIECE_ORDER) == 6
    assert PIECE_LABEL[PIECE_ORDER[0]] == "pěšec"
