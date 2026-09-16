"""Testy mapy přesnosti podle čísla tahu (``move_map_widget``) – čistá matematika,
bez GUI vykreslování (to se ověřuje ručně/vizuálně)."""
import pytest

from move_map_widget import acc_color, catmull_rom


def test_acc_color_krajni_hodnoty():
    red = acc_color(0)
    yellow = acc_color(50)
    green = acc_color(100)
    assert (red.red(), red.green(), red.blue()) == (196, 74, 68)
    assert (yellow.red(), yellow.green(), yellow.blue()) == (223, 180, 70)
    assert (green.red(), green.green(), green.blue()) == (61, 139, 89)


def test_acc_color_orezava_mimo_rozsah():
    assert acc_color(-50).getRgb()[:3] == acc_color(0).getRgb()[:3]
    assert acc_color(500).getRgb()[:3] == acc_color(100).getRgb()[:3]


def test_acc_color_monotonni_zelenani():
    # se stoupající přesností roste zelená složka (celkově, ne nutně bod od bodu
    # přes celý rozsah, ale mezi 50 a 100 ano)
    g50 = acc_color(50).green()
    g75 = acc_color(75).green()
    g100 = acc_color(100).green()
    assert g50 <= g75 <= g100 or g50 >= g75 >= g100  # monotónní v jednom směru
    # konkrétně: mezi žlutou (50) a zelenou (100) červená klesá
    r50 = acc_color(50).red()
    r100 = acc_color(100).red()
    assert r100 < r50


def test_catmull_rom_prochazi_vsemi_body():
    pts = [(0.0, 0.0), (10.0, 5.0), (20.0, 2.0), (30.0, 8.0)]
    curve = catmull_rom(pts, samples_per_seg=6)
    # první a poslední bod křivky = první a poslední zadaný bod
    assert curve[0] == pytest.approx(pts[0])
    assert curve[-1] == pytest.approx(pts[-1])
    # křivka má víc bodů než vstup (je to interpolace, ne jen průchod)
    assert len(curve) > len(pts)


def test_catmull_rom_dva_body_je_usecka():
    pts = [(0.0, 0.0), (10.0, 10.0)]
    curve = catmull_rom(pts, samples_per_seg=4)
    assert curve[0] == pytest.approx((0.0, 0.0))
    assert curve[-1] == pytest.approx((10.0, 10.0))
    # všechny mezilehlé body leží na přímce y=x
    for x, y in curve:
        assert x == pytest.approx(y, abs=1e-6)


def test_catmull_rom_jeden_bod_vraci_beze_zmeny():
    assert catmull_rom([(1.0, 2.0)]) == [(1.0, 2.0)]
    assert catmull_rom([]) == []
