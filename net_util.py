"""Sdílené HTTP nástroje pro pár funkcí aplikace, které chodí na síť
(Opening Explorer, stažení partií z lichess/chess.com).

Bez závislostí navíc: ``urllib.request`` ze stdlib. Ověření certifikátu použije
CA balík z ``certifi`` (nese si ho ``requests``, který bývá v prostředí), když
je k dispozici; jinak systémové kořeny.

Když HTTPS zachytává antivirus / firewall vlastním (nedůvěryhodným nebo
nestandardním) kořenovým certifikátem, ověření selže s :class:`CertVerifyError`.
Aplikace se pak zeptá uživatele a případně přes :func:`allow_insecure` přepne na
spojení bez ověření certifikátu (jen pro tu relaci; stahují se jen veřejná PGN).
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request

USER_AGENT = ("chess_analytics (osobní rozbor partií; "
              "+https://github.com/lopatakpo/chess_analytics)")

try:
    import certifi
    _VERIFIED_CTX: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except Exception:
    try:
        _VERIFIED_CTX = ssl.create_default_context()
    except Exception:
        _VERIFIED_CTX = None

_INSECURE_CTX = ssl._create_unverified_context()
_insecure = False


class CertVerifyError(Exception):
    """Ověření TLS certifikátu selhalo (nejspíš kvůli AV/firewall MITM proxy)."""


def allow_insecure(value: bool = True) -> None:
    """Přepne spojení na režim bez ověření certifikátu (jen tato relace)."""
    global _insecure
    _insecure = bool(value)


def insecure_enabled() -> bool:
    return _insecure


def _ctx():
    return _INSECURE_CTX if _insecure else _VERIFIED_CTX


def open_url(url: str, headers: dict | None = None, timeout: float = 30.0):
    """Otevře URL (GET) s naším User-Agentem. Vrací odpověď (context manager).
    Při selhání ověření certifikátu vyhodí :class:`CertVerifyError`."""
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ctx())
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError) or isinstance(e, ssl.SSLError):
            raise CertVerifyError(str(reason or e)) from e
        raise


def probe(url: str = "https://lichess.org", timeout: float = 8.0) -> bool:
    """True když jde navázat ověřené HTTPS spojení; False právě když selže
    *ověření certifikátu* (jiné chyby – offline apod. – nechá projít jako True,
    ať je řeší samotné stahování s konkrétní hláškou)."""
    if _insecure:
        return True
    try:
        with open_url(url, timeout=timeout):
            return True
    except CertVerifyError:
        return False
    except Exception:
        return True
