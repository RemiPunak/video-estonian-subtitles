"""Video liigutamine sihtkausta pärast tõlke lisamist (kettale kirjutab ainult ajutine kaust)."""
import argparse
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import estsub  # noqa: E402


class MoveToDest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src" / "Film (2020)"
        self.src.mkdir(parents=True)
        self.dest = self.tmp / "dest"
        self.video = self.src / "Film.mkv"
        for name in ("Film.mkv", "Film.et.srt", "Film.en.srt", "Other.mkv"):
            (self.src / name).write_text(name)

    def args(self, **kw):
        return argparse.Namespace(dest=self.dest, no_move=False, **kw)

    def test_moves_video_and_sidecars_only(self):
        new = estsub.move_to_dest(self.video, self.args())
        self.assertEqual(new, self.dest / "Film.mkv")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["Film.en.srt", "Film.et.srt", "Film.mkv"])
        self.assertEqual([p.name for p in self.src.iterdir()], ["Other.mkv"])

    def test_no_overwrite_on_clash(self):
        self.dest.mkdir()
        (self.dest / "Film.mkv").write_text("old")
        self.assertEqual(estsub.move_to_dest(self.video, self.args()), self.video)
        self.assertTrue(self.video.exists())
        self.assertEqual((self.dest / "Film.mkv").read_text(), "old")

    def test_no_move_flag_and_already_in_dest(self):
        self.assertEqual(estsub.move_to_dest(self.video, argparse.Namespace(dest=self.dest, no_move=True)), self.video)
        self.assertEqual(estsub.move_to_dest(self.video, argparse.Namespace(dest=self.src, no_move=False)), self.video)


if __name__ == "__main__":
    unittest.main()
