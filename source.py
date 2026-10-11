"""Ingliskeelsete lähtesubtiitrite leidmine: kõrvalfail, videos olev rada või Whisper."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}
EN_LANGS = {"eng", "en", "enm"}


LANG_TOKENS = {"et": {"et", "est", "estonian", "eesti"}, "en": {"en", "eng", "english"}}
OTHER_LANG_TOKENS = {
    "fr", "fre", "fra", "french", "de", "ger", "deu", "german", "es", "spa", "spanish", "ru", "rus", "russian",
    "fi", "fin", "finnish", "sv", "swe", "swedish", "it", "ita", "italian", "pt", "por", "portuguese",
    "nl", "dut", "dutch", "pl", "pol", "polish", "lv", "lav", "latvian", "lt", "lit", "lithuanian",
    "da", "dan", "danish", "no", "nor", "norwegian", "chi", "zho", "chinese", "ja", "jpn", "japanese",
}
MAX_SUB_DEPTH = 2  # nt Subs/<video nimi>/2_English.srt


def _tokens(path: Path) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", path.stem.lower())) - {""}


_SDH_RE = re.compile(r"^\s*[\[(]|^\s*-?\s*[A-Z][A-Z .']{2,}:", re.MULTILINE)


def _looks_sdh(path: Path) -> bool:
    """RARBG jt ei märgi SDH-d failinimes: tunneme ära helikirjelduste ([Elevator dings]) ja kõneleja siltide järgi."""
    from srt_utils import decode
    text = decode(path.read_bytes())
    cues = text.count("-->")
    return cues > 0 and len(_SDH_RE.findall(text)) / cues > 0.05


def find_local(video: Path, lang: str) -> Path | None:
    """Otsib video kaustast ja alamkaustadest subtiitrit keeles lang ('et'/'en').

    Kaust kuulub videole, kui failinimi algab video nimega, alamkaust kannab video nime
    või on video kaustas ainus (nt RARBG 2_Eng.srt, Subs/2_English.srt).
    """
    root = video.parent
    stem = video.stem.lower()
    own_output = video.with_name(video.stem + ".et.srt")
    sole_video = sum(
        1 for f in root.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXT and "sample" not in f.stem.lower()
    ) <= 1
    cands = []
    for f in root.rglob("*.srt"):
        rel = f.relative_to(root)
        if len(rel.parts) - 1 > MAX_SUB_DEPTH or f == own_output or "_work" in rel.parts[:-1]:
            continue
        belongs = (
            f.name.lower().startswith(stem)
            or any(p.lower() == stem for p in rel.parts[:-1])
            or sole_video
        )
        if not belongs:
            continue
        tokens = _tokens(f) - set(re.split(r"[^a-z0-9]+", stem))
        if tokens & LANG_TOKENS[lang]:
            explicit = True
        elif lang == "en" and not tokens & (OTHER_LANG_TOKENS | LANG_TOKENS["et"]) and f.stem.lower() == stem:
            explicit = False  # Film.srt keeleta: eeldame inglise keelt nagu varem
        else:
            continue
        hearing = bool(tokens & {"sdh", "hi", "cc"}) or _looks_sdh(f)
        forced = "forced" in tokens
        cands.append(((not forced, explicit, not hearing, f.stat().st_size), f))
    if not cands:
        return None
    return max(cands, key=lambda c: c[0])[1]


def _probe_subs(video: Path) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s",
         "-show_entries", "stream=index,codec_name:stream_tags=language,title:stream_disposition=forced",
         "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(out.stdout).get("streams", [])


def extract_embedded(video: Path, work: Path) -> Path | None:
    streams = _probe_subs(video)
    text_streams = [s for s in streams if s.get("codec_name") in TEXT_CODECS]
    if not text_streams:
        if streams:
            codecs = ", ".join(sorted({s.get("codec_name", "?") for s in streams}))
            print(f"  Videos on ainult pildipõhised subtiitrid ({codecs}), need jäetakse vahele.")
        return None

    # Kõik tekstirajad ühe läbimisega välja: forced/SDH tunneb ära alles sisu järgi
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
    for s in text_streams:
        s["path"] = work / f"{video.stem}.embedded{s['index']}.srt"
        cmd += ["-map", f"0:{s['index']}", "-c:s", "srt", str(s["path"])]
    subprocess.run(cmd, check=True)
    from srt_utils import decode
    for s in text_streams:
        s["cues"] = decode(s["path"].read_bytes()).count("-->")
    most = max(s["cues"] for s in text_streams)

    def score(s: dict) -> tuple:
        tags = s.get("tags", {})
        lang = tags.get("language", "").lower()
        title = tags.get("title", "").lower()
        # Märkimata forced-rada (nt ainult viipekeele kohad) on täisrajast mitu korda lühem
        forced = s.get("disposition", {}).get("forced", 0) or "forced" in title or s["cues"] < most * 0.2
        sdh = "sdh" in title or _looks_sdh(s["path"])
        return (lang in EN_LANGS, not forced, not sdh, s["cues"])

    best = max(text_streams, key=score)
    lang = best.get("tags", {}).get("language", "?")
    if lang.lower() not in EN_LANGS:
        print(f"  Hoiatus: ingliskeelset rada ei leitud, kasutan rada keelega '{lang}'.")
    out = best["path"]
    sdh_note = ", SDH: helikirjeldused eemaldatakse" if _looks_sdh(out) else ""
    print(f"  Võtsin välja rada #{best['index']} ({best['codec_name']}, {lang}, {best['cues']} repliiki{sdh_note}).")
    return out


def _add_cuda_dll_dirs() -> None:
    """pip-iga paigaldatud nvidia-cublas/cudnn DLL-id Windowsil leitavaks."""
    if sys.platform != "win32":
        return
    for p in sys.path:
        nvidia = Path(p) / "nvidia"
        if nvidia.is_dir():
            for bin_dir in nvidia.glob("*/bin"):
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]


MAX_CHARS = 80   # umbes kaks 40-märgist rida
MAX_SECONDS = 6.0


def _split_segment(seg) -> list[tuple[float, float, str]]:
    """Jagab pika Whisperi segmendi sõnade ajatemplite järgi subtiitri-suurusteks tükkideks."""
    text = seg.text.strip()
    words = seg.words or []
    if not text:
        return []
    if not words or (len(text) <= MAX_CHARS and seg.end - seg.start <= MAX_SECONDS):
        return [(seg.start, seg.end, text)]
    out, cur = [], []
    for w in words:
        cur.append(w)
        chunk = "".join(x.word for x in cur).strip()
        too_long = len(chunk) > MAX_CHARS * 0.8 or cur[-1].end - cur[0].start > MAX_SECONDS
        sentence_end = w.word.strip()[-1:] in ".?!" and len(chunk) > 15
        if too_long or sentence_end:
            out.append((cur[0].start, cur[-1].end, chunk))
            cur = []
    if cur:
        out.append((cur[0].start, cur[-1].end, "".join(x.word for x in cur).strip()))
    return out


def transcribe(video: Path, work: Path, model_size: str = "large-v3") -> Path:
    import srt
    from datetime import timedelta

    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    import numpy as np

    print("  Võtan välja heli...")
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    def run(device: str, compute_type: str) -> list:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            audio, language="en", vad_filter=True, beam_size=5,
            condition_on_previous_text=False, word_timestamps=True,
        )
        subs = []
        for seg in segments:  # generaator: tegelik töö (ja CUDA viga) toimub siin
            for start, end, text in _split_segment(seg):
                subs.append(srt.Subtitle(len(subs) + 1, timedelta(seconds=start), timedelta(seconds=end), text))
            if len(subs) and len(subs) % 100 == 0:
                print(f"    {timedelta(seconds=int(seg.end))} / {timedelta(seconds=int(info.duration))}")
        return subs

    try:
        print(f"  Whisper {model_size} GPU peal...")
        subs = run("cuda", "float16")
    except RuntimeError as e:  # puuduvad CUDA DLL-id vms
        print(f"  GPU ei tööta ({e}), kasutan CPU-d (aeglane).")
        subs = run("cpu", "int8")
    out = work / f"{video.stem}.whisper.en.srt"
    out.write_text(srt.compose(subs), encoding="utf-8")
    return out


def get_source(video: Path, work: Path, explicit: Path | None, online_cache: dict | None = None) -> Path:
    """online_cache=None lülitab internetiotsingu välja."""
    if explicit:
        print(f"Lähe: {explicit}")
        return explicit
    if side := find_local(video, "en"):
        print(f"Lähe: kohalik fail {side.relative_to(video.parent)}")
        return side
    print("Kohalikku ingliskeelset faili pole, otsin videost subtiitriradu...")
    if emb := extract_embedded(video, work):
        return emb
    if online_cache is not None:
        import online
        import srt_utils
        print("Otsin internetist ingliskeelseid subtiitreid...")
        if subs := online.find_synced(video, "eng", online_cache):
            out = work / f"{video.stem}.online.en.srt"
            srt_utils.write_srt(out, subs)
            return out
    print("Subtiitreid pole, tuvastan kõne Whisperiga...")
    return transcribe(video, work)
