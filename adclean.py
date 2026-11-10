"""Reklaami eemaldamine subtiitritest ja failinimedest (lähte-saitide vesimärgid: YTS, YIFY, OxTorrent jms).

OpenSubtitles lükkab reklaamikaadriga faili tagasi ("Unsupported subtitle format"), seega puhastame alati enne kirjutamist.
"""
from __future__ import annotations

import re

AD_CUE = re.compile(
    r"\byts\b|\byify\b|opensubtitles|osdb|oxtorrent|rarbg|1xbet|\bettv\b|advertise|become (?:a )?vip|"
    r"allalaaditud saidilt|downloaded from|subtitles? by|www\.\w[\w-]*\.(?:com|org|net|cc|bz|to|me|io)",
    re.I,
)
# [ OxTorrent.cc ], [YTS.BZ], (YTS.MX), "www.site.com - " nimes
_NAME_TAG = re.compile(r"\[[^\]]*\]|\((?:[^)]*(?:yts|yify|torrent|rarbg|www\.)[^)]*)\)", re.I)
_NAME_SITE = re.compile(r"www\.\w[\w-]*\.\w+\s*[-_]*", re.I)


def strip_ad_cues(subs: list) -> list:
    """Jätab välja kaadrid, mille tekstis on reklaam. Indeksid nummerdab ümber `srt.compose(reindex=True)`."""
    return [s for s in subs if not AD_CUE.search(s.content)]


def clean_name(stem: str) -> str:
    """Eemaldab failinimest reklaamsildid ja sanitiseerib nime (ainult tähed, numbrid, punkt, kriips)."""
    name = _NAME_SITE.sub("", _NAME_TAG.sub("", stem))
    name = re.sub(r"[^\w.-]+", "_", name)
    name = re.sub(r"[-_.]{2,}", lambda m: m.group(0)[0], name)
    return name.strip("-_. ") or "subtitle"
