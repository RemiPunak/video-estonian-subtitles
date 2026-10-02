"""Ingliskeelsete lähtesubtiitrite leidmine: kõrvalfail, videos olev rada või Whisper."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
EN_LANGS = {"eng", "en", "enm"}


def find_sidecar(video: Path) -> Path | None:
    for suffix in (".en.srt", ".eng.srt", ".english.srt", ".srt"):
        cand = video.with_name(video.stem + suffix)
        if cand.exists():
            return cand
    return None


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

    def score(s: dict) -> tuple:
        tags = s.get("tags", {})
        lang = tags.get("language", "").lower()
        title = tags.get("title", "").lower()
        forced = s.get("disposition", {}).get("forced", 0) or "forced" in title
        return (lang in EN_LANGS, not forced, "sdh" not in title)

    best = max(text_streams, key=score)
    lang = best.get("tags", {}).get("language", "?")
    if lang.lower() not in EN_LANGS:
        print(f"  Hoiatus: ingliskeelset rada ei leitud, kasutan rada keelega '{lang}'.")
    out = work / f"{video.stem}.embedded.srt"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", f"0:{best['index']}", "-c:s", "srt", str(out)],
        check=True,
    )
    print(f"  Võtsin välja rada #{best['index']} ({best['codec_name']}, {lang}).")
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
    if side := find_sidecar(video):
        print(f"Lähe: kõrvalfail {side.name}")
        return side
    print("Kõrvalfaili pole, otsin videost subtiitriradu...")
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
