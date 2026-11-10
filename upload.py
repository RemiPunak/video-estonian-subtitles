"""Laeb valmis `.et.srt` üles opensubtitles.org-i (ilmub seal sama konto all ka opensubtitles.com-is).

opensubtitles.com-il veebis üleslaadimist pole (REST-i upload vajab kinnitatud rakendust), seega kasutame .org vormi.
Ühendub sinu Chrome'iga pordil 9222 (`start_chrome_os.ps1`), kus oled opensubtitles.org-i sisse loginud.
Üleslaadimine on avalik, seega käib alati filmi kaupa kinnitusega; mitte-interaktiivses keskkonnas ei saada midagi.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

import srt

import adclean

CDP_URL = "http://127.0.0.1:9222"
UPLOAD_URL = "https://www.opensubtitles.org/en/upload"
LOG = Path(__file__).with_name("uploaded.json")
COMMENT = ("Machine translated from English (DeepL), then proofread with AI (Claude). "
           "Synced to this release.")


def _logged(video: Path) -> bool:
    return str(video.resolve()) in json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else False


def _remember(video: Path) -> None:
    done = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    LOG.write_text(json.dumps(done + [str(video.resolve())], indent=2), encoding="utf-8")


def _imdb_id(video: Path, args: argparse.Namespace, confirm) -> str | None:
    """IMDB ID on vormis kohustuslik; arvata ei tohi, seega lipp --imdb või küsimine."""
    raw = getattr(args, "imdb", None)
    if not raw and sys.stdin and sys.stdin.isatty():
        raw = input(f"  IMDB ID filmile {video.name} (nt tt1234567, tühi = jäta vahele): ").strip()
    m = re.search(r"(\d{5,9})", raw or "")
    return m.group(1) if m else None


def upload_video(video: Path, args: argparse.Namespace, confirm) -> bool:
    """Laeb video `.et.srt` üles. True = üles laaditud."""
    srt_file = video.with_name(video.stem + ".et.srt")
    if not srt_file.exists():
        print(f"  Jäetud vahele: {srt_file.name} puudub.")
        return False
    if _logged(srt_file) and not args.force:
        print(f"  Jäetud vahele: {srt_file.name} on juba üles laaditud (--force laeb uuesti).")
        return False
    imdb = _imdb_id(video, args, confirm)
    if not imdb:
        print("  Jäetud vahele: IMDB ID puudub (anna --imdb tt1234567).")
        return False

    from playwright.sync_api import sync_playwright
    import online

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception:
            sys.exit("Chrome'i ei leitud pordilt 9222. Käivita: powershell -File start_chrome_os.ps1 (ja logi opensubtitles.org-i sisse).")
        page = browser.contexts[0].new_page()
        try:
            page.goto(UPLOAD_URL, wait_until="networkidle")
            if page.get_by_text("You are not logged in!").count():
                sys.exit("opensubtitles.org-i pole Chrome'is sisse logitud. Logi sisse (.com-i sessioon ei kehti) ja proovi uuesti.")
            fps = f"{online._video_fps(video):.3f}"
            page.select_option("select[name=sublanguageid]", "est")
            page.fill("input[name=IDMovieImdb]", imdb)
            if page.query_selector(f"select[name=MovieFPS] option[value='{fps}']"):
                page.select_option("select[name=MovieFPS]", fps)
            release = adclean.clean_name(video.stem)
            page.fill("input[name=MovieReleaseName]", release)
            page.fill("textarea[name=SubAutorComment]", COMMENT)
            page.check("input[name=AutoTranslation]")  # masintõlge märgitakse ausalt
            tmp = Path(tempfile.mkdtemp(prefix="estsub_up_")) / (release + ".srt")
            cues = adclean.strip_ad_cues(list(srt.parse(srt_file.read_text(encoding="utf-8"))))
            tmp.write_text(srt.compose(cues, reindex=True), encoding="utf-8")
            page.set_input_files("input[name='subs[]']", str(tmp))
            # Saidi JS lubab nupu alles falivälja klõpsu peale (set_input_files ei klõpsa)
            page.eval_on_selector("#submit", "e => { e.disabled = false; }")

            print(f"  Vorm täidetud: {srt_file.name} | est | IMDB {imdb} | FPS {fps} | masintõlge: jah")
            if not confirm("  Kas laadin selle opensubtitles.org-i üles (avalik, raskesti tagasivõetav)?"):
                print("  Üles ei laaditud (vorm jäi Chrome'is lahti ülevaatamiseks).")
                return False
            page.click("#submit")
            page.wait_for_load_state("networkidle")
            text = page.inner_text("body")
            # edu = vorm on kadunud (sait viis filmi lehele); veateade jääb vormi lehele
            if re.search(r"unsupported|error|invalid|failed|already", text, re.I) or page.query_selector("input[name=IDMovieImdb]"):
                print("  Üleslaadimine ebaõnnestus või lükati tagasi:", re.sub(r"\s+", " ", text)[:300])
                return False
            _remember(srt_file)
            print("  Üles laaditud. Kontrolli: https://www.opensubtitles.org/en/search/sublanguageid-est")
            page.close()
            return True
        except SystemExit:
            raise
