"""Teeb videole eestikeelse subtiitrifaili Film.et.srt (Plexi jaoks).

Kasutus:
    python estsub.py "D:\\Filmid\\Film (2020)\\Film.mkv"
    python estsub.py film.mkv --source film.en.srt --engine deepl
"""
from __future__ import annotations

import argparse
import copy
import re
import shutil
import sys
import tempfile
from pathlib import Path

import srt_utils
from source import find_local, get_source

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}

def movie_title(video: Path) -> str:
    # Plexi kausta nimi on tavaliselt "Pealkiri (aasta)"
    name = video.parent.name if re.search(r"\(\d{4}\)", video.parent.name) else video.stem
    return re.sub(r"[._]", " ", name)


def process(video: Path, args: argparse.Namespace) -> None:
    out = video.with_name(video.stem + ".et.srt")
    if out.exists() and not args.force:
        print(f"{out.name} on juba olemas (--force kirjutab üle).")
        return
    print(f"\n=== {video.name} ===")

    if not args.source and (local_et := find_local(video, "et")):
        et = srt_utils.read_srt(local_et)
        srt_utils.write_srt(out, [_rewrap(s) for s in et])
        print(f"Valmis (kohalik eestikeelne {local_et.relative_to(video.parent)}, tõlget polnud vaja): {out}  ({len(et)} subtiitrit)")
        return

    online_cache = None if args.no_online else {}
    if online_cache is not None and not args.source:
        import online
        print("Otsin internetist valmis eestikeelseid subtiitreid...")
        if et := online.find_synced(video, "est", online_cache):
            srt_utils.write_srt(out, [_rewrap(s) for s in et])
            print(f"Valmis (internetist, tõlget polnud vaja): {out}  ({len(et)} subtiitrit)")
            return

    work = Path(tempfile.mkdtemp(prefix="estsub_"))
    try:
        src_path = get_source(video, work, args.source, online_cache)
        subs = srt_utils.read_srt(src_path)
        if not subs:
            raise SystemExit("Lähtesubtiitrid on tühjad.")
        source = {s.index: srt_utils.clean(s.content) for s in subs}
        source = {i: t for i, t in source.items() if t}
        print(f"Subtiitreid: {len(source)}, märke: {sum(map(len, source.values())):,}")

        from translate_deepl import translate
        draft = translate(source)

        final = draft
        if args.engine == "hybrid":
            from polish_claude import polish
            final = polish(source, draft, movie_title(video), model=args.model)

        if args.keep_intermediate:
            keep = video.parent / "_work"
            keep.mkdir(exist_ok=True)
            shutil.copy(src_path, keep / f"{video.stem}.source.srt")
            srt_utils.write_srt(keep / f"{video.stem}.et.deepl.srt", _apply(subs, draft))

        result = _apply(subs, final)
        srt_utils.write_srt(out, result)
        changed = sum(1 for i in final if final[i] != draft[i])
        print(f"Valmis: {out}  ({len(result)} subtiitrit, Claude muutis {changed} rida)")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rewrap(s):
    # Inimese tehtud subtiitri reamurrud jäävad alles, murrame ainult üle pika rea
    if any(len(line) > srt_utils.MAX_LINE + 8 for line in s.content.splitlines()):
        s.content = srt_utils.wrap(srt_utils.clean(s.content))
    return s


def _apply(subs, texts: dict[int, str]):
    res = []
    for s in subs:
        if s.index in texts:
            n = copy.copy(s)
            n.content = srt_utils.wrap(texts[s.index])
            res.append(n)
    return res


def main() -> None:
    p = argparse.ArgumentParser(description="Eestikeelsed subtiitrid Plexi jaoks (DeepL + Claude).")
    p.add_argument("video", type=Path, help="videofail või kaust (töötleb kausta videod, alamkaustu mitte)")
    p.add_argument("--source", type=Path, help="kasuta seda ingliskeelset SRT faili")
    p.add_argument("--engine", choices=["hybrid", "deepl"], default="hybrid")
    p.add_argument("--model", default="claude-sonnet-5-5", help="Claude Code mudel järeltoimetuseks")
    p.add_argument("--no-online", action="store_true", help="ära otsi subtiitreid internetist")
    p.add_argument("--force", action="store_true", help="kirjuta olemasolev .et.srt üle")
    p.add_argument("--keep-intermediate", action="store_true", help="salvesta vahefailid kausta _work/")
    args = p.parse_args()

    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    target: Path = args.video
    if target.is_dir():
        # Ainult kausta enda videod; alamkaustu (Featurettes jms) ei tõlgita kunagi
        videos = sorted(
            f for f in target.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXT and "sample" not in f.stem.lower()
        )
        if not videos:
            raise SystemExit(f"Kaustas pole videofaile: {target}")
        if args.source and len(videos) > 1:
            raise SystemExit("--source sobib ainult ühe videoga.")
    elif target.exists():
        videos = [target]
    else:
        raise SystemExit(f"Faili ei leitud: {target}")
    for v in videos:
        process(v, args)


if __name__ == "__main__":
    main()
