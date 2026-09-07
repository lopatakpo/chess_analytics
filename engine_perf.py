"""Výchozí výkonnostní nastavení enginu (vlákna, hash tabulka) – sdílené mezi
``game_analyzer.py`` a ``accuracy_batch.py``, ať se výchozí hodnoty nepočítají
na dvou místech nezávisle a jinak.

**Naměřeno, ne odhadnuto** (na reálných partiích, `stockfish-avx2`, 8jádrový
CPU). První hrubý sweep (vlákna 1→2→4→7 pěkně popořadě) vypadal jednoznačně –
čím víc vláken, tím hůř, monotónně, až 3.3× pomaleji se 7 vlákny na hloubce 15.
Jenže dodatečný kontrolní test (5× stejná konfigurace za sebou) ukázal, že
samotný běh na tomhle stroji v čase pomalu "plave" (pravděpodobně tepelné
throttlování CPU při delším zatížení) – takže ten hezký monotónní trend byl
částečně zkreslený POŘADÍM měření, ne čistě počtem vláken. Přesnější prokládaný
test (střídavě 1 vs. N vláken v tom samém běhu, ne popořadě) by byl potřeba na
přesné číslo, ale směr zůstává stejný a dává smysl i teoreticky: appka volá
engine PER POZICI (``go depth N`` stovky/tisíce krát za běh), ne jednou na
dlouhý běh, takže u tak krátkých volání snadno převládne režie zapnutí/
synchronizace vláknového poolu (Lazy SMP) nad ziskem z paralelního hledání.
Hash tabulka na rychlost nemá měřitelný vliv (16/256/512 MB prakticky totéž).
**Proto zůstává výchozí hodnota 1 vlákno** – nejde o konzervativní placeholder,
ale o to, že nemáme žádný spolehlivý důkaz, že by víc vláken pro TENHLE způsob
volání enginu pomohlo, a teoretický důvod čekat opak. Menu „Nastavit výkon
enginu…“ zůstává k dispozici pro experimenty (jiný engine/HW se může chovat
jinak) – jen default neměň bez pořádného (prokládaného, ne sekvenčního)
benchmarku.

Co naopak skutečně a spolehlivě pomohlo (potvrzeno prokládaným A/B testem,
4 opakování, stejné pořadí problému vyloučeno): ``game_analyzer.analyse_position``
přešlo ze streamování dílčích UCI zpráv (``with engine.analysis(...): for _ in
analysis: ...``) na jedno blokující volání (``engine.analyse(...)``) – konzistentně
~9 % rychlejší (a jednodušší kód), cena je, že přerušení rozboru (Zastavit)
teď zabere až o jednu právě počítanou pozici navíc, ne okamžitě.
"""
from __future__ import annotations

import os

DEFAULT_THREADS = 1
DEFAULT_HASH_MB = 16
DEFAULT_PARALLEL = 1   # kolik enginů paralelně u DÁVKOVÉHO rozboru (karta Přesnost)
# ^ paralelizace NAPŘÍČ partiemi (každý engine řeší jinou partii) je jiná věc než
#   Threads uvnitř jednoho enginu – partie na sobě nezávisí, žádná Lazy-SMP režie.
#   Model (z profilu: ~69 % čas = čekání na engine, ~31 % = parsování v Pythonu pod
#   GILem): zrychlení ~ 1 / (0.69/N + 0.31), tj. 2 enginy ~1.5×, 5 ~2.3×, strop ~3.2×.


def default_threads() -> int:
    return DEFAULT_THREADS


def clamp_parallel(n) -> int:
    """1..počet jader; nesmysly (None, 0, moc) srovná do rozumu."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_PARALLEL
    return max(1, min(n, os.cpu_count() or 4))


def engine_options(threads: int | None = None, hash_mb: int | None = None) -> dict:
    """UCI volby pro ``engine.configure()``. ``None``/0 → automatický výchozí.
    ``UCI_ShowWDL`` zapíná reálné W/D/L z enginu (default bývá vypnuté) – appka
    z něj počítá šanci na výhru / očekávané body přesněji než ze sigmoidy."""
    return {
        "Threads": threads if threads else DEFAULT_THREADS,
        "Hash": hash_mb if hash_mb else DEFAULT_HASH_MB,
        "UCI_ShowWDL": True,
    }


def apply_engine_options(engine, threads: int | None = None,
                         hash_mb: int | None = None) -> None:
    """Nastaví jen ty volby, které engine skutečně má (starší/jiný engine
    nemusí znát ``UCI_ShowWDL``) – jinak by ``configure`` spadlo celé."""
    opts = {k: v for k, v in engine_options(threads, hash_mb).items()
            if k in getattr(engine, "options", {})}
    if opts:
        try:
            engine.configure(opts)
        except Exception:
            pass
