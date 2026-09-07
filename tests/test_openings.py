"""Testy klasifikace zahájení (``openings.classify_by_moves``) – hlavně
transpozice. Bez enginu; potřebuje ``eco.tsv`` vedle aplikace."""
import chess
import pytest

from openings import classify_by_moves, _MAX_LINE, _czech


def _ucis(*sans):
    b = chess.Board()
    out = []
    for s in sans:
        mv = b.parse_san(s)
        out.append(mv.uci())
        b.push(mv)
    return out


@pytest.fixture(autouse=True)
def _eco_loaded():
    if _MAX_LINE <= 0:
        pytest.skip("eco.tsv není k dispozici")


def test_sicilska_normalnim_poradim():
    eco, name, depth = classify_by_moves(_ucis("e4", "c5", "Nf3", "d6"))
    assert eco is not None and eco[0] == "B"
    assert "Sicil" in name


def test_sicilska_pres_transpozici_z_jf3():
    # 1.Nf3 c5 2.e4 d6 → stejná pozice jako 1.e4 c5 2.Nf3 d6
    eco_t, name_t, _ = classify_by_moves(_ucis("Nf3", "c5", "e4", "d6"))
    eco_d, name_d, _ = classify_by_moves(_ucis("e4", "c5", "Nf3", "d6"))
    assert eco_t is not None and eco_t[0] == "B"
    assert eco_t == eco_d


def test_damsky_gambit_odmitnuty_transpozici():
    # 1.c4 e6 2.d4 d5 3.Nc3 Nf6 → QGD (D-kód)
    eco, name, _ = classify_by_moves(_ucis("c4", "e6", "d4", "d5", "Nc3", "Nf6"))
    assert eco is not None and eco[0] == "D"


def test_prazdny_vstup_je_nezarazeno():
    eco, name, depth = classify_by_moves([])
    assert eco is None
    assert name == "Nezařazeno"
    assert depth == 0


def test_hluboka_linie_ma_omezenou_hloubku_knihy():
    ucis = _ucis("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O",
                 "Be7", "Re1", "b5", "Bb3", "d6", "c3", "O-O", "h3", "Nb8",
                 "d4", "Nbd7")
    eco, name, depth = classify_by_moves(ucis)
    assert depth <= len(ucis)
    assert eco and eco[0] == "C"


def test_hloubka_knihy_je_rozumna():
    ucis = _ucis("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6")
    eco, name, depth = classify_by_moves(ucis)
    assert eco == "C" or (eco and eco[0] == "C")
    assert 0 < depth <= len(ucis)


def test_pos_keys_dava_stejny_vysledek_jako_dopocet():
    ucis = _ucis("Nf3", "c5", "e4", "d6")
    b = chess.Board()
    keys = [b._transposition_key()]
    for u in ucis:
        b.push(chess.Move.from_uci(u))
        keys.append(b._transposition_key())
    assert classify_by_moves(ucis, pos_keys=keys) == classify_by_moves(ucis)


def test_czech_preklada_rodinu():
    assert _czech("Sicilian Defense") == "Sicilská obrana"
    assert _czech("Sicilian Defense: Najdorf Variation").startswith("Sicilská obrana")
    assert _czech("Neznámé zahájení") == "Neznámé zahájení"
