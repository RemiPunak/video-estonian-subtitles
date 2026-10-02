"""Valmis subtiitrite otsimine OpenSubtitlesist ja nende sünkroonimine videoga.

Ajastuse võrdlusalus on videos oleva subtiitriraja (ka PGS) sündmuste ajad. Kui rada pole,
kasutatakse kõnelõikude algusi (faster-whisperi VAD). Leitud fail venitatakse ja nihutatakse
lineaarselt (t' = a*t + b), mis katab PAL/NTSC kiiruse erinevuse ja algusnihke.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import srt

import srt_utils

UA = "Mozilla/5.0 (estsub)"
SEARCH_URL = "https://www.opensubtitles.org/en/search/sublanguageid-{lang}/moviename-{name}/simplexml"
MAX_CANDIDATES = 6
# PAL (25) <-> film (23.976) kiiruse suhted
RATIOS = (1.0, 25 / 23.976, 23.976 / 25, 25 / 24, 24 / 25)
SPAM = re.compile(r"getray|opensubtitles|osdb\.link|advertise your|become vip|subtiitrite otsimisest|"
                  r"api\.open|addic7ed|yts\.|\bwww\.", re.IGNORECASE)


@dataclass
class Candidate:
    id: str
    lang: str
    release: str
    subs: list[srt.Subtitle]


# ---------- otsing ----------

def parse_title(video: Path) -> tuple[str, str | None]:
    for name in (video.parent.name, video.stem):
        m = re.match(r"^(.+?)[\s._]*[(\[]?((?:19|20)\d{2})[)\]]?", name)
        if m:
            return re.sub(r"[._]", " ", m.group(1)).strip(), m.group(2)
    return re.sub(r"[._]", " ", video.stem), None


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def search(title: str, year: str | None, lang: str) -> list[dict]:
    url = SEARCH_URL.format(lang=lang, name=urllib.parse.quote_plus(title.lower()))
    try:
        xml = _get(url).decode("utf-8", "replace")
    except OSError as e:
        print(f"  OpenSubtitles otsing ebaõnnestus: {e}")
        return []
    out = []
    want = f"{title} ({year})".lower() if year else None
    for block in re.findall(r"<subtitle>(.*?)</subtitle>", xml, re.S):
        def tag(t):
            m = re.search(rf"<{t}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", block, re.S)
            return m.group(1).strip() if m else ""
        movie = tag("movie")
        if want and movie.lower() != want:
            continue  # nt seriaali samanimeline osa
        if tag("files") not in ("1", ""):
            continue  # mitme CD-ga väljalase
        out.append({"id": tag("idsubtitle"), "url": tag("download"), "release": tag("releasename"),
                    "rating": float(tag("subrating") or 0)})
    return out


def _parse_microdvd(text: str, fps: float) -> list[srt.Subtitle]:
    subs = []
    for line in text.splitlines():
        m = re.match(r"\{(\d+)\}\{(\d+)\}(.*)", line.strip())
        if not m:
            continue
        a, b, body = int(m.group(1)), int(m.group(2)), m.group(3)
        if a == b == 1:  # {1}{1}23.976 on fps päis
            continue
        body = re.sub(r"\{[^}]*\}", "", body).replace("|", "\n")
        subs.append(srt.Subtitle(len(subs) + 1, timedelta(seconds=a / fps), timedelta(seconds=b / fps), body))
    return subs


def download(item: dict, lang: str, video_fps: float) -> Candidate | None:
    try:
        data = _get(item["url"])
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile):
        return None
    for name in zf.namelist():
        ext = Path(name).suffix.lower()
        if ext not in (".srt", ".sub"):
            continue
        text = srt_utils.decode(zf.read(name))
        if ext == ".srt":
            subs = list(srt.parse(text, ignore_errors=True))
        else:
            m = re.search(r"\{1\}\{1\}([\d.,]+)", text)
            fps = float(m.group(1).replace(",", ".")) if m else video_fps
            subs = _parse_microdvd(text, fps)
        subs = [s for s in subs if s.content.strip() and not SPAM.search(s.content)]
        if len(subs) > 50:
            return Candidate(item["id"], lang, item["release"], subs)
    return None


# ---------- võrdlusalus ----------

def reference_times(video: Path) -> tuple[np.ndarray, str]:
    """Subtiitriraja sündmuste algused või (kui rada pole) kõnelõikude algused."""
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
         "stream=index,codec_name:stream_tags=language", "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8").stdout or "{}").get("streams", [])
    if probe:
        st = max(probe, key=lambda s: s.get("tags", {}).get("language", "") in ("eng", "en"))
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", str(st["index"]), "-show_entries",
             "packet=pts_time,size", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True).stdout
        rows = [l.split(",") for l in out.split() if "," in l]
        pgs = st.get("codec_name") in ("hdmv_pgs_subtitle", "dvd_subtitle")
        # Pildipõhistel radadel on iga subtiitri järel väike "kustuta" pakett
        times = [float(t) for t, size in rows if t != "N/A" and (not pgs or int(size) > 100)]
        if len(times) > 50:
            return np.array(sorted(times)), f"subtiitrirada #{st['index']} ({st['codec_name']})"
    return _speech_onsets(video), "kõnelõigud (VAD)"


def _speech_onsets(video: Path) -> np.ndarray:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=0))
    return np.array([t["start"] / 16000 for t in ts])


# ---------- sünkroon ----------

def _score(starts: np.ndarray, ref: np.ndarray, a: float, b: float, tol: float) -> float:
    t = starts * a + b
    i = np.clip(np.searchsorted(ref, t), 1, len(ref) - 1)
    d = np.minimum(np.abs(ref[i] - t), np.abs(ref[i - 1] - t))
    return float((d < tol).mean())


def fit(subs: list[srt.Subtitle], ref: np.ndarray) -> tuple[float, float, float, float]:
    """Tagastab (a, b, kattuvus ±0.25 s, mediaanviga)."""
    starts = np.array([s.start.total_seconds() for s in subs])
    best = (0.0, 1.0, 0.0)
    for r in RATIOS:
        for a in np.linspace(r * 0.999, r * 1.001, 5):
            for b in np.arange(-120, 120, 0.25):
                s = _score(starts, ref, a, b, 0.6)
                if s > best[0]:
                    best = (s, a, b)
    _, a0, b0 = best
    best = (0.0, a0, b0)
    for a in np.linspace(a0 - 0.0006, a0 + 0.0006, 25):
        for b in np.arange(b0 - 0.5, b0 + 0.5, 0.02):
            s = _score(starts, ref, a, b, 0.2)
            if s > best[0]:
                best = (s, a, b)
    _, a, b = best
    t = starts * a + b
    i = np.clip(np.searchsorted(ref, t), 1, len(ref) - 1)
    d = np.minimum(np.abs(ref[i] - t), np.abs(ref[i - 1] - t))
    return a, b, float((d < 0.25).mean()), float(np.median(d))


def apply_fit(subs: list[srt.Subtitle], a: float, b: float) -> list[srt.Subtitle]:
    out = []
    for s in subs:
        start, end = s.start.total_seconds() * a + b, s.end.total_seconds() * a + b
        if end <= 0:
            continue
        out.append(srt.Subtitle(len(out) + 1, timedelta(seconds=max(0, start)), timedelta(seconds=end), s.content))
    return out


# ---------- avalik liides ----------

def find_synced(video: Path, lang: str, ref_cache: dict) -> list[srt.Subtitle] | None:
    """Otsib keele `lang` (OpenSubtitlesi kood: est, eng) subtiitrid ja tagastab parima sünkroonitud versiooni."""
    title, year = parse_title(video)
    items = search(title, year, lang)
    print(f"  OpenSubtitles [{lang}] '{title}' ({year}): {len(items)} tulemust")
    if not items:
        return None
    if "ref" not in ref_cache:
        ref_cache["ref"] = reference_times(video)
        print(f"  Ajastuse võrdlusalus: {ref_cache['ref'][1]}, {len(ref_cache['ref'][0])} punkti")
    ref, kind = ref_cache["ref"]
    threshold = 0.45 if kind.startswith("subtiitri") else 0.30

    fps = _video_fps(video)
    best = None
    seen_texts = set()
    for item in sorted(items, key=lambda x: -x["rating"])[:MAX_CANDIDATES]:
        cand = download(item, lang, fps)
        time.sleep(1)  # viisakas tempo
        if not cand:
            continue
        sig = " ".join(s.content for s in cand.subs[10:20])
        if sig in seen_texts:
            continue  # sama tõlge teise ajastusega: üks sünk sobib kõigile
        seen_texts.add(sig)
        a, b, match, med = fit(cand.subs, ref)
        print(f"    #{cand.id} {cand.release[:50]:50} kiirus {a:.4f} nihe {b:+.2f}s "
              f"kattuvus {match:.0%} mediaan {med * 1000:.0f}ms")
        if match >= threshold and (best is None or match > best[0]):
            best = (match, cand, a, b)
    if not best:
        print("  Sobiva ajastusega faili ei leitud.")
        return None
    match, cand, a, b = best
    print(f"  Valitud #{cand.id} (kattuvus {match:.0%})")
    return apply_fit(cand.subs, a, b)


def _video_fps(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()
    try:
        n, d = out.split("/")
        return float(n) / float(d)
    except ValueError:
        return 23.976
