"""Teeb videole eestikeelse subtiitrifaili Film.et.srt (Plexi jaoks).

Kasutus:
    python estsub.py "D:\\Filmid\\Film (2020)\\Film.mkv"
    python estsub.py film.mkv --source film.en.srt --engine deepl
    python estsub.py --library "D:\\Torrent\\Video" "E:\\TorrentE" --exclude "E:\\TorrentE\\Game"
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
from source import WhisperNeeded, find_local, get_source, is_estonian_audio

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}
DEST_DIR = r"D:\Torrent\Video\Films-with-estonian-subtitles"


def movie_title(video: Path) -> str:
    # Plexi kausta nimi on tavaliselt "Pealkiri (aasta)"
    name = video.parent.name if re.search(r"\(\d{4}\)", video.parent.name) else video.stem
    return re.sub(r"[._]", " ", name)


def out_path(video: Path) -> Path:
    return video.with_name(video.stem + ".et.srt")


def confirm(question: str) -> bool:
    """Küsib jah/ei. Vaikimisi EI; mitte-interaktiivses keskkonnas alati EI."""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [j/N] ").strip().lower() in {"j", "jah", "y", "yes"}
    except EOFError:
        return False


def refresh_plex(video: Path, args: argparse.Namespace) -> None:
    if not getattr(args, "no_plex", False):
        import plex
        plex.refresh_folder(video.parent)


def move_to_dest(video: Path, args: argparse.Namespace) -> Path:
    """Liigutab video koos selle kõrval olevate failidega (`<nimi>.*`, sh `.et.srt`) sihtkausta.
    Tagastab video uue asukoha; olemasolevat faili ei kirjutata kunagi üle."""
    dest = Path(getattr(args, "dest", None) or DEST_DIR)
    if getattr(args, "no_move", False) or video.parent.resolve() == dest.resolve():
        return video
    files = [f for f in video.parent.iterdir()
             if f.is_file() and (f == video or f.name.startswith(video.stem + "."))]
    clash = [f.name for f in files if (dest / f.name).exists()]
    if clash:
        print(f"  Liigutamata: sihtkaustas on juba {', '.join(clash)} ({dest}).")
        return video
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.move(str(f), str(dest / f.name))
    print(f"  Liigutatud koos tõlkega: {dest / video.name}")
    return dest / video.name


def finish(video: Path, args: argparse.Namespace) -> None:
    """Pärast .et.srt kirjutamist: liiguta sihtkausta ja uuenda Plex."""
    refresh_plex(move_to_dest(video, args), args)


def try_free_estonian(video: Path, args: argparse.Namespace, online_cache: dict | None) -> bool:
    """1. samm (tasuta): kohalik või internetist valmis eestikeelne SRT. True = fail kirjutatud."""
    out = out_path(video)
    if local_et := find_local(video, "et"):
        et = srt_utils.read_srt(local_et)
        srt_utils.write_srt(out, [_rewrap(s) for s in et])
        print(f"Valmis (kohalik eestikeelne {local_et.relative_to(video.parent)}, tõlget polnud vaja): {out}  ({len(et)} subtiitrit)")
        finish(video, args)
        return True
    if online_cache is not None:
        import online
        print("Otsin internetist valmis eestikeelseid subtiitreid...")
        if et := online.find_synced(video, "est", online_cache):
            srt_utils.write_srt(out, [_rewrap(s) for s in et])
            print(f"Valmis (internetist, tõlget polnud vaja): {out}  ({len(et)} subtiitrit)")
            finish(video, args)
            return True
    return False


def translate_video(video: Path, args: argparse.Namespace, online_cache: dict | None,
                    allow_whisper: bool = False) -> None:
    """2. samm: lähtetekst (inglise) -> DeepL -> Claude. Tõstab WhisperNeeded, kui allikat pole
    ja allow_whisper on False."""
    out = out_path(video)
    work = Path(tempfile.mkdtemp(prefix="estsub_"))
    try:
        src_path, src_lang = get_source(video, work, args.source, online_cache, allow_whisper=allow_whisper)
        subs = srt_utils.read_srt(src_path)
        if not subs:
            raise SystemExit("Lähtesubtiitrid on tühjad.")
        source = {s.index: srt_utils.clean(s.content) for s in subs}
        source = {i: t for i, t in source.items() if t}
        print(f"Subtiitreid: {len(source)}, märke: {sum(map(len, source.values())):,}")

        from translate_deepl import translate
        draft = translate(source, src_lang)

        final = draft
        if args.engine == "hybrid":
            from polish_claude import polish
            final = polish(source, draft, movie_title(video), model=args.model, source_lang=src_lang)

        if args.keep_intermediate:
            keep = video.parent / "_work"
            keep.mkdir(exist_ok=True)
            shutil.copy(src_path, keep / f"{video.stem}.source.srt")
            srt_utils.write_srt(keep / f"{video.stem}.et.deepl.srt", _apply(subs, draft))

        result = _apply(subs, final)
        srt_utils.write_srt(out, result)
        changed = sum(1 for i in final if final[i] != draft[i])
        print(f"Valmis: {out}  ({len(result)} subtiitrit, Claude muutis {changed} rida)")
        finish(video, args)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def whisper_estimate(video: Path) -> str:
    from source import duration_seconds
    mins = duration_seconds(video) / 60
    # large-v3 GPU peal umbes 8x reaalajast + mudeli laadimine; CPU on mitu korda aeglasem
    return f"film {mins:.0f} min, hinnanguliselt {mins / 8:.0f}-{mins / 4:.0f} min GPU-l (esimesel korral +3 GB mudeli allalaadimine)"


def ask_whisper(video: Path) -> bool:
    """Whisper on kallis: käivitub ainult selge jah-vastusega, filmi kaupa."""
    print(f"  Tasuta lähtesubtiitreid ei leitud: {video.name}")
    print(f"  Kõnetuvastus Whisperiga: {whisper_estimate(video)}.")
    return confirm("  Kas käivitan kõnetuvastuse?")


def process(video: Path, args: argparse.Namespace) -> None:
    """Ühe video töö (paremklõps): tasuta samm, tõlge, vajadusel kinnitatud Whisper."""
    out = out_path(video)
    if out.exists() and not args.force:
        print(f"{out.name} on juba olemas (--force kirjutab üle).")
        return
    print(f"\n=== {video.name} ===")
    if not args.force and not args.source and is_estonian_audio(video):
        print("Filmi heli on eesti keeles, subtiitreid pole vaja.")
        return

    online_cache = None if args.no_online else {}
    if not args.source and try_free_estonian(video, args, online_cache):
        return
    try:
        translate_video(video, args, online_cache, allow_whisper=False)
    except WhisperNeeded:
        if not ask_whisper(video):
            print("Whisperit ei käivitatud. Subtiitreid ei tehtud.")
            raise SystemExit(1)
        translate_video(video, args, online_cache, allow_whisper=True)


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
    p.add_argument("video", type=Path, nargs="?", help="videofail või kaust (töötleb kausta videod, alamkaustu mitte)")
    p.add_argument("--library", type=Path, nargs="+", metavar="JUUR",
                   help="käi läbi terve kogu (rekursiivselt), kolm faasi kinnitustega")
    p.add_argument("--exclude", type=Path, action="append", default=[], help="kaust, mida kogu läbimisel ei puudutata")
    p.add_argument("--yes-translate", action="store_true", help="kogu läbimisel kinnita tõlge automaatselt (Whisperile sellist lippu pole)")
    p.add_argument("--source", type=Path, help="kasuta seda ingliskeelset SRT faili")
    p.add_argument("--engine", choices=["hybrid", "deepl"], default="hybrid")
    p.add_argument("--model", default="claude-sonnet-5-5", help="Claude Code mudel järeltoimetuseks")
    p.add_argument("--no-online", action="store_true", help="ära otsi subtiitreid internetist")
    p.add_argument("--no-plex", action="store_true", help="ära käivita Plexi uuendust pärast subtiitrite kirjutamist")
    p.add_argument("--dest", type=Path, default=Path(DEST_DIR), help="kuhu video koos tõlkega liigutatakse")
    p.add_argument("--no-move", action="store_true", help="ära liiguta videot pärast tõlke lisamist")
    p.add_argument("--upload", action="store_true",
                   help="laadi olemasolev .et.srt üles opensubtitles.org-i (filmi kaupa kinnitusega); ei tõlgi")
    p.add_argument("--imdb", help="filmi IMDB ID üleslaadimiseks (nt tt1234567)")
    p.add_argument("--force", action="store_true", help="kirjuta olemasolev .et.srt üle")
    p.add_argument("--keep-intermediate", action="store_true", help="salvesta vahefailid kausta _work/")
    args = p.parse_args()

    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if args.library:
        import library
        library.run(args)
        return
    if not args.video:
        p.error("anna videofail/kaust või --library JUUR")

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
    if args.upload:
        import upload
        for v in videos:
            print(f"\n=== {v.name} ===")
            upload.upload_video(v, args, confirm)
        return
    for v in videos:
        process(v, args)


if __name__ == "__main__":
    main()
