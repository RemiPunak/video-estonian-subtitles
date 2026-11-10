"""PGS (Blu-ray pildisubtiitrite) lugemine: SUP-voog -> pildid -> Tesseract OCR -> SRT."""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np
import srt
from PIL import Image

TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{code}.traineddata"
TESSDATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "estsub" / "tessdata"
TESSERACT_FALLBACK = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

PCS, WDS, PDS, ODS, END = 0x16, 0x17, 0x14, 0x15, 0x80


@dataclass
class _Object:
    width: int = 0
    height: int = 0
    data: bytearray = field(default_factory=bytearray)


@dataclass
class Event:
    start: float
    end: float | None
    images: list[tuple[int, Image.Image]]  # (y, kujutis), kõige ülemisest alates


def _decode_rle(data: bytes, width: int, height: int) -> np.ndarray:
    """PGS run-length -> paletiindeksite maatriks."""
    out = np.zeros((height, width), dtype=np.uint8)
    x = y = i = 0
    n = len(data)
    while i < n and y < height:
        b = data[i]; i += 1
        if b:
            run, color = 1, b
        else:
            if i >= n:
                break
            c = data[i]; i += 1
            if c == 0:
                x, y = 0, y + 1
                continue
            flag = c & 0xC0
            if flag == 0x00:
                run, color = c & 0x3F, 0
            elif flag == 0x40:
                run, color = ((c & 0x3F) << 8) | data[i], 0; i += 1
            elif flag == 0x80:
                run, color = c & 0x3F, data[i]; i += 1
            else:
                run, color = ((c & 0x3F) << 8) | data[i], data[i + 1]; i += 2
        if color and x < width:
            out[y, x:min(x + run, width)] = color
        x += run
    return out


def _render(indexed: np.ndarray, palette: dict[int, tuple[int, int]]) -> Image.Image:
    """Heledad (täidetud) pikslid mustaks tekstiks valgel taustal, ääris ja läbipaistev taust valgeks."""
    lut = np.full(256, 255, dtype=np.uint8)
    for idx, (luma, alpha) in palette.items():
        if alpha > 128 and luma > 100:
            lut[idx] = 0
    img = Image.fromarray(lut[indexed], "L")
    pad = Image.new("L", (img.width + 40, img.height + 40), 255)
    pad.paste(img, (20, 20))
    return pad


def parse_sup(path: Path) -> list[Event]:
    raw = path.read_bytes()
    pos, n = 0, len(raw)
    objects: dict[int, _Object] = {}
    palette: dict[int, tuple[int, int]] = {}
    shown: list[tuple[int, int, int]] = []  # (objekt, x, y) praeguse PCS-i järgi
    pts = 0.0
    events: list[Event] = []
    open_ev: Event | None = None

    def close(at: float) -> None:
        nonlocal open_ev
        if open_ev:
            open_ev.end = at
            open_ev = None

    while pos + 13 <= n:
        if raw[pos:pos + 2] != b"PG":
            pos += 1
            continue
        pts = struct.unpack(">I", raw[pos + 2:pos + 6])[0] / 90000
        seg_type = raw[pos + 10]
        size = struct.unpack(">H", raw[pos + 11:pos + 13])[0]
        body = raw[pos + 13:pos + 13 + size]
        pos += 13 + size

        if seg_type == PCS:
            state = body[7]
            if state == 0x80:  # uus epohh
                objects.clear()
            count = body[10]
            shown, p = [], 11
            for _ in range(count):
                oid, _win, crop = struct.unpack(">HBB", body[p:p + 4])
                x, y = struct.unpack(">HH", body[p + 4:p + 8])
                shown.append((oid, x, y))
                p += 8 + (8 if crop & 0x40 else 0)
        elif seg_type == PDS:
            for q in range(2, len(body) - 4, 5):
                idx, luma, _cr, _cb, alpha = body[q:q + 5]
                palette[idx] = (luma, alpha)
        elif seg_type == ODS:
            oid, _ver, seq = struct.unpack(">HBB", body[:4])
            if seq & 0x80:
                w, h = struct.unpack(">HH", body[7:11])
                objects[oid] = _Object(w, h, bytearray(body[11:]))
            elif oid in objects:
                objects[oid].data += body[4:]
        elif seg_type == END:
            close(pts)
            if shown:
                imgs = []
                for oid, _x, y in shown:
                    o = objects.get(oid)
                    if o and o.width and o.height:
                        imgs.append((y, _render(_decode_rle(bytes(o.data), o.width, o.height), palette)))
                if imgs:
                    open_ev = Event(pts, None, sorted(imgs, key=lambda t: t[0]))
                    events.append(open_ev)
    if open_ev and open_ev.end is None:
        open_ev.end = open_ev.start + 3
    return events


def find_tesseract() -> str:
    exe = shutil.which("tesseract") or (str(TESSERACT_FALLBACK) if TESSERACT_FALLBACK.exists() else None)
    if not exe:
        raise RuntimeError("Tesseract puudub (winget install UB-Mannheim.TesseractOCR).")
    return exe


def ensure_language(code: str) -> None:
    target = TESSDATA_DIR / f"{code}.traineddata"
    if target.exists():
        return
    TESSDATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Laen Tesseracti keelefaili {code}...")
    urllib.request.urlretrieve(TESSDATA_URL.format(code=code), target)


def _ocr_one(exe: str, code: str, img: Image.Image, tmp: Path, key: str) -> str:
    png = tmp / f"{key}.png"
    img.save(png)
    r = subprocess.run(
        [exe, str(png), "stdout", "--tessdata-dir", str(TESSDATA_DIR), "-l", code, "--psm", "6"],
        capture_output=True, text=True, encoding="utf-8",
    )
    return "\n".join(ln.strip() for ln in r.stdout.splitlines() if ln.strip())


def _fix(text: str, code: str) -> str:
    if code == "eng":
        text = text.replace("|", "I")
    return text


def ocr_events(events: list[Event], code: str) -> list[srt.Subtitle]:
    exe = find_tesseract()
    ensure_language(code)
    subs: list[srt.Subtitle] = []
    with tempfile.TemporaryDirectory(prefix="estsub_ocr_") as d, ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        tmp = Path(d)
        futures = [
            [ex.submit(_ocr_one, exe, code, img, tmp, f"{i}_{j}") for j, (_y, img) in enumerate(ev.images)]
            for i, ev in enumerate(events)
        ]
        for i, (ev, futs) in enumerate(zip(events, futures), 1):
            text = _fix("\n".join(f.result() for f in futs).strip(), code)
            if text:
                subs.append(srt.Subtitle(len(subs) + 1, timedelta(seconds=ev.start), timedelta(seconds=ev.end), text))
            if i % 200 == 0:
                print(f"    OCR {i}/{len(events)}")
    return subs


def ocr_pgs_stream(video: Path, stream_index: int, code: str, work: Path) -> Path:
    """Võtab PGS-raja välja, loeb OCR-iga ja kirjutab SRT; tagastab SRT tee."""
    sup = work / f"{video.stem}.s{stream_index}.sup"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", f"0:{stream_index}", "-c", "copy", "-f", "sup", str(sup)],
        check=True,
    )
    events = parse_sup(sup)
    print(f"  PGS: {len(events)} subtiitrikaadrit, loen OCR-iga ({code})...")
    subs = ocr_events(events, code)
    out = work / f"{video.stem}.ocr{stream_index}.srt"
    out.write_text(srt.compose(subs), encoding="utf-8")
    return out
