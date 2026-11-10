"""Reklaami eemaldamine subtiitritest ja failinimedest."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import srt  # noqa: E402

import adclean  # noqa: E402
import srt_utils  # noqa: E402


def cue(i, text):
    return srt.Subtitle(i, srt.srt_timestamp_to_timedelta(f"00:00:0{i},000"), srt.srt_timestamp_to_timedelta(f"00:00:0{i},500"), text)


class AdClean(unittest.TestCase):
    def test_ad_cues_are_dropped_and_reindexed(self):
        subs = [cue(1, "Allalaaditud saidilt YTS.BZ"), cue(2, "Tere, maailm."), cue(3, "Laadi alla www.yifysubtitles.com")]
        out = srt.parse(srt.compose(adclean.strip_ad_cues(subs), reindex=True))
        self.assertEqual([(s.index, s.content) for s in out], [(1, "Tere, maailm.")])

    def test_normal_dialogue_is_kept(self):
        subs = [cue(1, "Ma ei tea, kas sa kuuled."), cue(2, "Sa oled alati hiljaks jäänud.")]
        self.assertEqual(len(adclean.strip_ad_cues(subs)), 2)

    def test_clean_name(self):
        self.assertEqual(adclean.clean_name("All.Thats.Left.Of.You.2025.1080p.WEBRip.x265.10bit.AAC5.1-[YTS.BZ]"),
                         "All.Thats.Left.Of.You.2025.1080p.WEBRip.x265.10bit.AAC5.1")
        self.assertEqual(adclean.clean_name("[ OxTorrent.cc ] Film.2020.FRENCH.1080p.BluRay"), "Film.2020.FRENCH.1080p.BluRay")
        self.assertEqual(adclean.clean_name("www.site.com - Film 2020"), "Film_2020")

    def test_write_srt_strips_ads(self):
        import tempfile
        p = Path(tempfile.mkdtemp()) / "a.srt"
        srt_utils.write_srt(p, [cue(1, "Downloaded from YTS.MX"), cue(2, "Tere.")])
        self.assertNotIn("YTS", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
