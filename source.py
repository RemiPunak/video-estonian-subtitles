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
IMAGE_CODECS = {"hdmv_pgs_subtitle"}  # OCR toetab ainult PGS-i; VobSub jääb vahele

# ISO 639-2 (ffprobe) -> (Tesseracti kood, DeepL lähtekeel, nimi); eesti keel on sihtkeel, siin pole
FOREIGN_LANGS = {
    "eng": ("eng", "EN", "inglise"), "en": ("eng", "EN", "inglise"),
    "ger": ("deu", "DE", "saksa"), "deu": ("deu", "DE", "saksa"), "de": ("deu", "DE", "saksa"),
    "fre": ("fra", "FR", "prantsuse"), "fra": ("fra", "FR", "prantsuse"), "fr": ("fra", "FR", "prantsuse"),
    "spa": ("spa", "ES", "hispaania"), "es": ("spa", "ES", "hispaania"),
    "ita": ("ita", "IT", "itaalia"), "it": ("ita", "IT", "itaalia"),
    "rus": ("rus", "RU", "vene"), "ru": ("rus", "RU", "vene"),
    "fin": ("fin", "FI", "soome"), "fi": ("fin", "FI", "soome"),
    "swe": ("swe", "SV", "rootsi"), "sv": ("swe", "SV", "rootsi"),
    "por": ("por", "PT-PT", "portugali"), "pt": ("por", "PT-PT", "portugali"),
    "dut": ("nld", "NL", "hollandi"), "nld": ("nld", "NL", "hollandi"), "nl": ("nld", "NL", "hollandi"),
    "pol": ("pol", "PL", "poola"), "pl": ("pol", "PL", "poola"),
    "dan": ("dan", "DA", "taani"), "da": ("dan", "DA", "taani"),
}


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


def extract_embedded(video: Path, work: Path) -> tuple[Path, str] | None:
    """Videosse pandud subtiitrid: tekstirajad otse, PGS-rajad OCR-iga. Tagastab (SRT, DeepL lähtekeel).

    Eelistus: inglise keel, siis muu võõrkeel (mida DeepL ja Tesseract tunnevad); igas keeles
    tekstirada enne pildirada. Forced ja SDH rajad jäävad täisraja ees tahaplaanile."""
    streams = _probe_subs(video)

    def lang_of(s: dict) -> str:
        return s.get("tags", {}).get("language", "").lower()

    text_streams = [s for s in streams if s.get("codec_name") in TEXT_CODECS]
    image_streams = [s for s in streams if s.get("codec_name") in IMAGE_CODECS and lang_of(s) in FOREIGN_LANGS]
    skipped = {s.get("codec_name", "?") for s in streams} - TEXT_CODECS - IMAGE_CODECS
    if skipped and not text_streams and not image_streams:
        print(f"  Videos on ainult toetamata pildisubtiitrid ({', '.join(sorted(skipped))}), need jäetakse vahele.")
    if not text_streams and not image_streams:
        return None

    from srt_utils import decode
    if text_streams:
        # Kõik tekstirajad ühe läbimisega välja: forced/SDH tunneb ära alles sisu järgi
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
        for s in text_streams:
            s["path"] = work / f"{video.stem}.embedded{s['index']}.srt"
            cmd += ["-map", f"0:{s['index']}", "-c:s", "srt", str(s["path"])]
        subprocess.run(cmd, check=True)
        for s in text_streams:
            s["cues"] = decode(s["path"].read_bytes()).count("-->")
    most = max((s["cues"] for s in text_streams), default=0)

    def is_forced(s: dict) -> bool:
        title = s.get("tags", {}).get("title", "").lower()
        return bool(s.get("disposition", {}).get("forced", 0) or "forced" in title)

    def score(s: dict) -> tuple:
        title = s.get("tags", {}).get("title", "").lower()
        if "path" in s:  # tekstirada: forced tunneb ära ka lühiduse järgi (nt ainult viipekeele kohad)
            forced = is_forced(s) or s["cues"] < most * 0.2
            sdh = "sdh" in title or _looks_sdh(s["path"])
        else:
            forced, sdh = is_forced(s), "sdh" in title or "hearing" in title
        return (lang_of(s) in EN_LANGS, lang_of(s) in FOREIGN_LANGS, not forced, "path" in s, not sdh, s.get("cues", 0))

    best = max(text_streams + image_streams, key=score)
    lang = lang_of(best) or "?"
    tess, deepl_lang, name = FOREIGN_LANGS.get(lang, ("eng", "EN", "inglise?"))
    if lang not in FOREIGN_LANGS:
        print(f"  Hoiatus: tundmatu keel '{lang}', eeldan inglise keelt.")
    elif lang not in EN_LANGS:
        print(f"  Ingliskeelset rada ei leitud, kasutan {name}keelset rada.")
    if "path" in best:
        out = best["path"]
        sdh_note = ", SDH: helikirjeldused eemaldatakse" if _looks_sdh(out) else ""
        print(f"  Võtsin välja rada #{best['index']} ({best['codec_name']}, {lang}, {best['cues']} repliiki{sdh_note}).")
        return out, deepl_lang
    import pgs_ocr
    print(f"  Videos on pildipõhine rada #{best['index']} (PGS, {lang}), loen OCR-iga.")
    return pgs_ocr.ocr_pgs_stream(video, best["index"], tess, work), deepl_lang


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


class WhisperNeeded(Exception):
    """Ühtegi tasuta lähtesubtiitrit pole; ainus võimalus on kallis Whisperi kõnetuvastus."""


def audio_language(video: Path) -> str:
    """Esimese helitrajekti keelekood ffprobe'i järgi (väiketähed) või '' kui märkimata."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream_tags=language",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, encoding="utf-8",
    )
    lang = out.stdout.strip().lower()
    return "" if lang in {"und", "unknown"} else lang


def is_estonian_audio(video: Path) -> bool:
    return audio_language(video) in {"est", "et"}


def duration_seconds(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, encoding="utf-8",
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def get_source(video: Path, work: Path, explicit: Path | None, online_cache: dict | None = None,
               allow_whisper: bool = False) -> tuple[Path, str]:
    """Tagastab (lähte-SRT, DeepL lähtekeel). online_cache=None lülitab internetiotsingu välja.

    Järjekord: kohalik EN -> videosse pandud rajad (EN, siis muu võõrkeel; PGS OCR-iga) -> internet EN
    -> Whisper. Whisper käivitub ainult allow_whisper=True korral; muidu tõstetakse WhisperNeeded."""
    if explicit:
        print(f"Lähe: {explicit}")
        return explicit, "EN"
    if side := find_local(video, "en"):
        print(f"Lähe: kohalik fail {side.relative_to(video.parent)}")
        return side, "EN"
    print("Kohalikku ingliskeelset faili pole, otsin videost subtiitriraju...")
    if emb := extract_embedded(video, work):
        return emb
    if online_cache is not None:
        import online
        import srt_utils
        print("Otsin internetist ingliskeelseid subtiitreid...")
        if subs := online.find_synced(video, "eng", online_cache):
            out = work / f"{video.stem}.online.en.srt"
            srt_utils.write_srt(out, subs)
            return out, "EN"
    if not allow_whisper:
        raise WhisperNeeded(video)
    print("Subtiitreid pole, tuvastan kõne Whisperiga (kinnitatud)...")
    return transcribe(video, work), "EN"
