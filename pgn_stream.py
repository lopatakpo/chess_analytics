"""Streamované čtení PGN – i z komprimovaných souborů (``.pgn.zst`` / ``.gz`` /
``.bz2`` / ``.xz``), po jedné partii, bez načtení celého souboru do paměti.

``.zst`` (formát měsíčních dumpů z database.lichess.org) potřebuje buď Python
3.14+ (`compression.zstd` ve standardní knihovně), nebo balíček ``zstandard`` /
``pyzstd``.
"""
from __future__ import annotations

import io
import re

_HDR_RE = re.compile(r'\[([A-Za-z0-9_]+)\s+"([^"]*)"\]')


def open_text(path: str):
    """Vrátí textový stream nad ``path``; podle přípony transparentně rozbalí
    zstd / gzip / bzip2 / xz. Volající stream zavře (nebo použije ve ``with``)."""
    low = path.lower()
    if low.endswith((".zst", ".zstd")):
        return _open_zst(path)
    if low.endswith((".gz", ".gzip")):
        import gzip
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    if low.endswith(".bz2"):
        import bz2
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    if low.endswith(".xz"):
        import lzma
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8-sig", errors="replace")


def _open_zst(path: str):
    try:
        from compression.zstd import DecompressionParameter, ZstdFile  # Python 3.14+
        zf = ZstdFile(path, "r",
                      options={DecompressionParameter.window_log_max: 31})
        return io.TextIOWrapper(zf, encoding="utf-8", errors="replace")
    except ImportError:
        pass
    try:
        import zstandard
        dctx = zstandard.ZstdDecompressor(max_window_size=2 ** 31)
        return io.TextIOWrapper(dctx.stream_reader(open(path, "rb")),
                                encoding="utf-8", errors="replace")
    except ImportError:
        pass
    try:
        import pyzstd
        return io.TextIOWrapper(pyzstd.ZstdFile(path, "r"),
                                encoding="utf-8", errors="replace")
    except ImportError:
        raise RuntimeError(
            "Soubor .pgn.zst vyžaduje Python 3.14+ (má zstd ve stdlib) nebo "
            "balíček „zstandard“ (pip install zstandard).")


def iter_game_texts(fh):
    """Prochází PGN stream a vrací **syrový text** každé partie (blok hlaviček +
    tahy). Levné – jen dělení řádků, žádné parsování šachu."""
    buf: list[str] = []
    have_movetext = False
    for line in fh:
        if line.startswith("[Event ") and have_movetext:
            yield "".join(buf)
            buf = []
            have_movetext = False
        buf.append(line)
        s = line.strip()
        if s and not s.startswith("["):
            have_movetext = True
    if buf and have_movetext:
        yield "".join(buf)


def quick_headers(game_text: str) -> dict:
    """Vytáhne hlavičky partie z jejího syrového textu (bez parsování tahů)."""
    out: dict = {}
    for line in game_text.splitlines():
        if not line.startswith("["):
            if line.strip():
                break                       # už jsme v tazích
            continue
        m = _HDR_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out
