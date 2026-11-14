# -*- coding: utf-8 -*-
"""worker_lyrics 辅助函数单测：尾音延长（拖音重叠） / 同声部压缩 / 输出高度。"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from lyrics.schema import Line, Lyrics, Word
from worker_lyrics import (_apply_tail_extend, _compress_same_agent_overlap,
                           _out_height)


def _line(lid, text, start, end, agent="", background=False):
    return Line(id=lid, text=text, start=start, end=end, agent=agent,
                is_background=background,
                words=[Word(text, start, end, 0.9)])


class TailExtendTests(unittest.TestCase):
    def test_clamped_to_next_line_by_default(self):
        ly = Lyrics(lines=[_line("L1", "啊", 0, 2000), _line("L2", "吧", 2200, 4000)])
        _apply_tail_extend(ly, 400)
        self.assertEqual(ly.lines[0].end, 2150)  # 2200-50 钳制
        self.assertEqual(ly.lines[1].end, 4400)  # 末行无钳制

    def test_overlap_allows_crossing_next_line(self):
        ly = Lyrics(lines=[_line("L1", "啊", 0, 2000), _line("L2", "吧", 2200, 4000)])
        _apply_tail_extend(ly, 400, overlap=True)
        self.assertEqual(ly.lines[0].end, 2400)  # 允许拖入下一行
        self.assertEqual(ly.lines[1].end, 4400)


class CompressOverlapTests(unittest.TestCase):
    def test_same_agent_overlap_compressed(self):
        ly = Lyrics(lines=[_line("L1", "啊", 0, 2400), _line("L2", "吧", 2200, 4000)])
        _compress_same_agent_overlap(ly)
        self.assertEqual(ly.lines[0].end, 2150)  # 压回 L2.start-50

    def test_different_agent_not_compressed(self):
        ly = Lyrics(lines=[_line("L1", "啊", 0, 2400, agent="v1"),
                           _line("L2", "吧", 2200, 4000, agent="v2")])
        _compress_same_agent_overlap(ly)
        self.assertEqual(ly.lines[0].end, 2400)  # 对唱声部不压缩

    def test_background_not_touched(self):
        ly = Lyrics(lines=[_line("L1", "啊", 0, 2400),
                           _line("B1", "和声", 2200, 4000, background=True)])
        _compress_same_agent_overlap(ly)
        self.assertEqual(ly.lines[0].end, 2400)


class OutHeightTests(unittest.TestCase):
    def test_zero_is_original(self):
        self.assertEqual(_out_height(0), 0)

    def test_missing_defaults_720(self):
        self.assertEqual(_out_height(None), 720)
        self.assertEqual(_out_height(""), 720)

    def test_negative_falls_back(self):
        self.assertEqual(_out_height(-5), 720)

    def test_valid_passthrough(self):
        self.assertEqual(_out_height(1080), 1080)
        self.assertEqual(_out_height("720"), 720)


if __name__ == "__main__":
    unittest.main()
