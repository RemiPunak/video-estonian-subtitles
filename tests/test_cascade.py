"""Kaskaadi kinnituste testid (unittest, väliseid teenuseid ei kutsuta)."""
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import library  # noqa: E402
import source  # noqa: E402


def make_args(**kw):
    base = dict(no_online=True, yes_translate=False, force=False, source=None, engine="deepl",
                model="m", keep_intermediate=False)
    base.update(kw)
    return argparse.Namespace(**base)


class WhisperGating(unittest.TestCase):
    def test_ac2_get_source_raises_without_permission(self):
        """AC1+AC2: ilma allow_whisper-ita tõstetakse WhisperNeeded ja transcribe'i ei kutsuta."""
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(source, "find_local", return_value=None), \
                mock.patch.object(source, "extract_embedded", return_value=None), \
                mock.patch.object(source, "transcribe") as tr:
            video = Path(d) / "Film.mkv"
            video.write_bytes(b"x")
            with self.assertRaises(source.WhisperNeeded):
                source.get_source(video, Path(d), None, online_cache=None)
            tr.assert_not_called()

    def test_get_source_runs_whisper_only_when_allowed(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(source, "find_local", return_value=None), \
                mock.patch.object(source, "extract_embedded", return_value=None), \
                mock.patch.object(source, "transcribe", return_value=Path("x.srt")) as tr:
            video = Path(d) / "Film.mkv"
            video.write_bytes(b"x")
            source.get_source(video, Path(d), None, online_cache=None, allow_whisper=True)
            tr.assert_called_once()


class LibraryPhases(unittest.TestCase):
    def test_ac4_estonian_audio_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            et, en = Path(d) / "Eesti.mkv", Path(d) / "Inglise.mkv"
            et.write_bytes(b"x"); en.write_bytes(b"x")
            with mock.patch.object(library, "is_estonian_audio", side_effect=lambda v: v.name == "Eesti.mkv"):
                self.assertEqual(library.pending([et, en]), [en])

    def test_existing_et_srt_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            v = Path(d) / "Film.mkv"
            v.write_bytes(b"x")
            (Path(d) / "Film.et.srt").write_text("1\n", encoding="utf-8")
            with mock.patch.object(library, "is_estonian_audio", return_value=False):
                self.assertEqual(library.pending([v]), [])
                self.assertEqual(library.pending([v], force=True), [v])

    def test_exclude_and_sample(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Game").mkdir(); (root / "Film").mkdir()
            (root / "Game" / "a.mkv").write_bytes(b"x")
            (root / "Film" / "b.mkv").write_bytes(b"x")
            (root / "Film" / "b.sample.mkv").write_bytes(b"x")
            found = library.find_videos([root], [root / "Game"])
            self.assertEqual([f.name for f in found], ["b.mkv"])

    def test_ac5_declined_translation_does_not_translate(self):
        estsub = mock.Mock()
        estsub.confirm.return_value = False
        out = library.phase2_translate([Path("a.mkv")], make_args(), estsub)
        estsub.translate_video.assert_not_called()
        self.assertEqual(out, [])

    def test_yes_translate_translates_and_collects_whisper_candidates(self):
        estsub = mock.Mock()
        estsub.translate_video.side_effect = source.WhisperNeeded(Path("a.mkv"))
        out = library.phase2_translate([Path("a.mkv")], make_args(yes_translate=True), estsub)
        estsub.translate_video.assert_called_once()
        self.assertFalse(estsub.translate_video.call_args.kwargs["allow_whisper"])
        self.assertEqual(out, [Path("a.mkv")])

    def test_ac3_phase3_skips_when_not_confirmed(self):
        estsub = mock.Mock()
        estsub.ask_whisper.return_value = False
        library.phase3_whisper([Path("a.mkv")], make_args(), estsub)
        estsub.translate_video.assert_not_called()

    def test_phase3_translates_with_whisper_when_confirmed(self):
        estsub = mock.Mock()
        estsub.ask_whisper.return_value = True
        library.phase3_whisper([Path("a.mkv")], make_args(), estsub)
        self.assertTrue(estsub.translate_video.call_args.kwargs["allow_whisper"])


class PlexRefresh(unittest.TestCase):
    XML = (
        '<MediaContainer>'
        '<Directory key="1"><Location path="D:\\Torrent\\Video"/></Directory>'
        '<Directory key="2"><Location path="E:\\TorrentE\\Film"/></Directory>'
        '</MediaContainer>'
    )

    def test_section_for_path_picks_matching_library(self):
        import plex
        self.assertEqual(plex.section_for_path(self.XML, Path("D:/Torrent/Video/Arze (2024)")), "1")
        self.assertEqual(plex.section_for_path(self.XML, Path("E:/TorrentE/Film/X")), "2")
        self.assertIsNone(plex.section_for_path(self.XML, Path("E:/TorrentE/Game/X")))

    def test_refresh_without_token_makes_no_request(self):
        import plex
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(plex, "_request") as req:
            self.assertFalse(plex.refresh_folder(Path("D:/Torrent/Video/X")))
            req.assert_not_called()

    def test_token_goes_in_header_not_url(self):
        import plex
        calls = []
        def fake(url, token):
            calls.append((url, token))
            return self.XML.encode() if url.endswith("/library/sections") else b""
        with mock.patch.dict("os.environ", {"PLEX_TOKEN": "SECRET"}, clear=True), \
                mock.patch.object(plex, "_request", side_effect=fake):
            self.assertTrue(plex.refresh_folder(Path("D:/Torrent/Video/X")))
        self.assertTrue(all("SECRET" not in url for url, _ in calls))
        self.assertTrue(all(tok == "SECRET" for _, tok in calls))
        self.assertIn("/library/sections/1/refresh?", calls[-1][0])


class ConfirmNonInteractive(unittest.TestCase):
    def test_ac3_confirm_is_false_without_tty(self):
        import estsub
        with mock.patch.object(sys, "stdin", mock.Mock(isatty=lambda: False)):
            self.assertFalse(estsub.confirm("?"))


if __name__ == "__main__":
    unittest.main()
