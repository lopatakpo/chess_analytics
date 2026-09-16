"""Testy pomocné funkce pro graf „přesnost/hrubky podle čísla tahu"
(``accuracy_batch._cap_move_tot``) – čistá matematika, bez enginu/vlákna."""
from accuracy_batch import MAX_MOVE_CHART, _cap_move_tot


def test_cap_move_tot_pod_prahem_beze_zmeny():
    move_tot = {1: [90.0, 1, 0], 2: [80.0, 1, 1], 5: [95.0, 2, 0]}
    out = _cap_move_tot(move_tot, max_move=10)
    assert out == move_tot
    assert out is not move_tot          # nová struktura, ne alias


def test_cap_move_tot_slouci_ocas():
    move_tot = {1: [90.0, 1, 0], 15: [80.0, 1, 1], 20: [70.0, 1, 1]}
    out = _cap_move_tot(move_tot, max_move=10)
    assert set(out) == {1, 11}          # 15 i 20 spadnou do koše "11" (max_move+1)
    tail = out[11]
    assert tail[1] == 2                 # n sečteno (15. a 20. tah)
    assert tail[2] == 2                 # blund sečteno
    assert tail[0] == 150.0             # sum_acc sečteno (80+70)


def test_cap_move_tot_default_prah():
    move_tot = {MAX_MOVE_CHART: [90.0, 1, 0], MAX_MOVE_CHART + 5: [80.0, 1, 0]}
    out = _cap_move_tot(move_tot)
    assert MAX_MOVE_CHART in out
    assert MAX_MOVE_CHART + 1 in out
    assert (MAX_MOVE_CHART + 5) not in out


def test_cap_move_tot_prazdny():
    assert _cap_move_tot({}) == {}
