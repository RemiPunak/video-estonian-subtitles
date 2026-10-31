"""Plexi kohene uuendus pärast subtiitrite kirjutamist (osaline skannimine filmi kaustale).

Seadistus: keskkonnamuutuja PLEX_TOKEN (kohustuslik), PLEX_URL (vaikimisi http://127.0.0.1:32400).
Tokenit ei salvestata ega logita; see saadetakse päisega X-Plex-Token, mitte URL-is.
Kollektsioonid (nutikad) uuenevad Plexis ise filtri järgi; tavaliste kollektsioonide liikmeid skript ei muuda.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:32400"


def section_for_path(sections_xml: str, folder: Path) -> str | None:
    """Leiab raamatukogu (section) key, mille asukoha (Location) all folder asub."""
    target = os.path.normcase(os.path.normpath(str(folder)))
    best: tuple[int, str] | None = None
    for d in ET.fromstring(sections_xml).iter("Directory"):
        for loc in d.iter("Location"):
            root = os.path.normcase(os.path.normpath(loc.get("path", "")))
            if root and (target == root or target.startswith(root + os.sep)):
                if best is None or len(root) > best[0]:
                    best = (len(root), d.get("key", ""))
    return best[1] if best else None


def _request(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers={"X-Plex-Token": token, "Accept": "application/xml"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def refresh_folder(folder: Path) -> bool:
    """Käivitab Plexis kausta osalise skannimise. True = päring tehtud; muidu hoiatus, mitte viga."""
    token = os.environ.get("PLEX_TOKEN")
    if not token:
        print("  Plexi uuendust ei tehtud: PLEX_TOKEN pole seatud (Plex leiab muudatuse ise osalise skannimisega).")
        return False
    base = os.environ.get("PLEX_URL", DEFAULT_URL).rstrip("/")
    try:
        key = section_for_path(_request(f"{base}/library/sections", token).decode("utf-8"), folder)
        if key is None:
            print(f"  Plexi uuendust ei tehtud: kaust ei kuulu ühtegi Plexi raamatukokku ({folder}).")
            return False
        query = urllib.parse.urlencode({"path": str(folder)})
        _request(f"{base}/library/sections/{key}/refresh?{query}", token)
        print(f"  Plexi raamatukogu {key} osaline skannimine käivitatud.")
        return True
    except (urllib.error.URLError, OSError, ET.ParseError) as e:
        print(f"  Plexi uuendus ebaõnnestus ({type(e).__name__}); Plex leiab muudatuse ise.")
        return False
