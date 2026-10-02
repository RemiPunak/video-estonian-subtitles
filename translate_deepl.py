"""DeepL tõlge partiidena: iga subtiiter on XML-tagis, nii et DeepL näeb lauseid tervikuna."""
from __future__ import annotations

import html
import os
import re

import deepl

BATCH = 40
CONTEXT_LINES = 8
_SEG_RE = re.compile(r'<c id="(\d+)">(.*?)</c>', re.DOTALL)


def _translator() -> deepl.Translator:
    key = os.environ.get("DEEPL_API_KEY")
    if not key:
        raise SystemExit("DEEPL_API_KEY keskkonnamuutuja puudub.")
    return deepl.Translator(key)


def translate(texts: dict[int, str]) -> dict[int, str]:
    """texts: {id: ingliskeelne tekst} -> {id: eestikeelne tekst}."""
    tr = _translator()
    ids = list(texts)
    result: dict[int, str] = {}
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        prev = ids[max(0, start - CONTEXT_LINES) : start]
        context = " ".join(texts[i] for i in prev) or None
        xml = "".join(f'<c id="{i}">{html.escape(texts[i], quote=False)}</c>' for i in chunk)
        res = tr.translate_text(
            xml, source_lang="EN", target_lang="ET", tag_handling="xml",
            context=context, preserve_formatting=True,
            outline_detection=False, split_sentences="nonewlines",
        )
        got = {int(m.group(1)): html.unescape(m.group(2)).strip() for m in _SEG_RE.finditer(res.text)}
        missing = [i for i in chunk if not got.get(i)]
        for i in missing:  # DeepL liitis või kaotas tagi: tõlgime selle rea eraldi
            got[i] = tr.translate_text(texts[i], source_lang="EN", target_lang="ET", context=context).text
        result.update({i: got[i] for i in chunk})
        print(f"  DeepL: {min(start + BATCH, len(ids))}/{len(ids)}" + (f" ({len(missing)} eraldi)" if missing else ""))
    usage = tr.get_usage()
    if usage.character.valid:
        print(f"  DeepL kvoot: {usage.character.count:,} / {usage.character.limit:,} märki")
    return result
