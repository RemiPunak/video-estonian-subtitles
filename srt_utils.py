"""SRT lugemine, kirjutamine ja teksti puhastus."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import srt

MAX_LINE = 42

_TAG_RE = re.compile(r"</?(?:i|b|u|font)[^>]*>|\{\\[^}]*\}", re.IGNORECASE)


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def read_srt(path: Path) -> list[srt.Subtitle]:
    subs = list(srt.parse(decode(path.read_bytes()), ignore_errors=True))
    for i, s in enumerate(subs, start=1):
        s.index = i
    return [s for s in subs if s.content.strip()]


def write_srt(path: Path, subs: list[srt.Subtitle]) -> None:
    path.write_text(srt.compose(subs, reindex=True), encoding="utf-8")


def clean(text: str) -> str:
    """Eemaldab vormindustagid ja liidab read üheks lauseks tõlkimiseks."""
    text = _TAG_RE.sub("", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def wrap(text: str, width: int = MAX_LINE) -> str:
    """Murrab teksti kuni kaheks reaks; dialoogi kriipsud jäävad eraldi ridadele."""
    text = text.strip()
    if "\n" in text:
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())
    # Dialoog: "- Tere. - Tšau." -> kaks rida
    m = re.match(r"^(-\s*.+?[.?!…\"”])\s+(-\s*.+)$", text)
    if m:
        return f"{m.group(1)}\n{m.group(2)}"
    if len(text) <= width:
        return text
    # Jagame võimalikult keskelt, et read oleksid tasakaalus
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - mid))
    first, second = text[:cut], text[cut + 1 :]
    if len(first) > width or len(second) > width:
        return "\n".join(textwrap.wrap(text, width)[:3])
    return f"{first}\n{second}"
