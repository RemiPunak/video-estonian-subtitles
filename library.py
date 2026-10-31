"""Kogu läbimine: tasuta eestikeelsed SRT-d kõigile, siis kinnitatud tõlge, siis kinnitatud Whisper.

Faas 1 (tasuta, automaatne): kohalik/internetist eestikeelne SRT.
Faas 2 (üks kinnitus kogu partii kohta): tõlge DeepL + Claude'iga.
Faas 3 (kinnitus filmi kaupa): Whisper kõnetuvastus; ei käivitu kunagi ilma jah-vastuseta.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from source import WhisperNeeded, is_estonian_audio

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}


def _excluded(path: Path, excludes: list[Path]) -> bool:
    p = path.resolve()
    return any(p == e.resolve() or e.resolve() in p.parents for e in excludes)


def find_videos(roots: list[Path], excludes: list[Path]) -> list[Path]:
    """Kõik videod juurte all (rekursiivselt), v.a. välistatud kaustad, sample-failid ja _work."""
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            print(f"Hoiatus: kausta pole: {root}")
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in VIDEO_EXT:
                continue
            if "sample" in f.stem.lower() or "_work" in f.parts or _excluded(f, excludes):
                continue
            found.append(f)
    return sorted(found)


def pending(videos: list[Path], force: bool = False) -> list[Path]:
    """Videod, millel pole `.et.srt` ja mille heli pole eesti keeles."""
    todo = []
    for v in videos:
        if v.with_name(v.stem + ".et.srt").exists() and not force:
            continue
        if is_estonian_audio(v):
            continue
        todo.append(v)
    return todo


def phase1_free(videos: list[Path], args: argparse.Namespace, estsub) -> list[Path]:
    """Tasuta samm kõigile. Tagastab videod, millele eestikeelset faili ei leitud."""
    cache = None if args.no_online else {}
    remaining = []
    for v in videos:
        print(f"\n=== {v.name} ===")
        try:
            if not estsub.try_free_estonian(v, args, cache):
                remaining.append(v)
        except Exception as e:  # üks vigane film ei tohi kogu läbimist peatada
            print(f"  Viga ({type(e).__name__}: {e}), jätkan järgmisega.")
            remaining.append(v)
    return remaining


def phase2_translate(videos: list[Path], args: argparse.Namespace, estsub) -> list[Path]:
    """Üks kinnitus kogu partii tõlkeks. Tagastab videod, mis vajavad Whisperit."""
    if not videos:
        return []
    print(f"\nEestikeelset faili ei leitud {len(videos)} filmile:")
    for v in videos:
        print(f"  - {v}")
    if not (args.yes_translate or estsub.confirm(f"Tõlgin need ingliskeelsest lähtest (DeepL + Claude)?")):
        print("Tõlget ei tehtud.")
        return []
    cache = None if args.no_online else {}
    needs_whisper = []
    for v in videos:
        print(f"\n=== {v.name} ===")
        try:
            estsub.translate_video(v, args, cache, allow_whisper=False)
        except WhisperNeeded:
            needs_whisper.append(v)
        except (Exception, SystemExit) as e:
            print(f"  Viga ({type(e).__name__}: {e}), jätkan järgmisega.")
    return needs_whisper


def phase3_whisper(videos: list[Path], args: argparse.Namespace, estsub) -> None:
    """Whisper filmi kaupa, ainult selge kinnitusega."""
    if not videos:
        return
    print(f"\n{len(videos)} filmil pole ühtegi tasuta lähtesubtiitrit. Kõnetuvastus on kallis (aeg + GPU).")
    cache = None if args.no_online else {}
    for v in videos:
        if not estsub.ask_whisper(v):
            print("  Jäetud vahele.")
            continue
        try:
            estsub.translate_video(v, args, cache, allow_whisper=True)
        except (Exception, SystemExit) as e:
            print(f"  Viga ({type(e).__name__}: {e}), jätkan järgmisega.")


def run(args: argparse.Namespace) -> None:
    import estsub

    videos = find_videos(args.library, args.exclude)
    todo = pending(videos, force=args.force)
    print(f"Videoid kokku: {len(videos)}, vajab subtiitreid: {len(todo)}")
    remaining = phase1_free(todo, args, estsub)
    needs_whisper = phase2_translate(remaining, args, estsub)
    phase3_whisper(needs_whisper, args, estsub)
    print("\nKogu läbimine valmis. Plexis tee uutel filmidel Refresh Metadata.")
