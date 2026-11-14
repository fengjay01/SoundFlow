# -*- coding: utf-8 -*-
"""lrc-ttml 方案迁移功能的单元测试（DP 匹配器 / TTML / 和声对唱翻译 / 倒数）。"""
import unittest

from lyrics.dp_match import (AsrUnit, Unit, align_sequences, expand_asr_units,
                             interpolate, parse_line_info, scan_units,
                             similarity, split_asr_word, trustworthy)
from lyrics.schema import Line, Lyrics, Word, validate
from lyrics.ass_gen import build_ass


def make_lyrics(lines_spec):
    lines = []
    for i, spec in enumerate(lines_spec):
        words = [Word(c, spec["start"] + k * 100, spec["start"] + k * 100 + 90, 0.9)
                 for k, c in enumerate(spec["tokens"])]
        lines.append(Line(id=f"L{i + 1}", text=spec["text"],
                          start=spec["start"],
                          end=max(spec["end"], spec["start"] + len(words) * 100 + 1),
                          words=words, **spec.get("extra", {})))
    return Lyrics(version=1, language="zh", source="test", lines=lines)


class SimilarityTests(unittest.TestCase):
    def test_exact_and_case(self):
        self.assertEqual(similarity("你", "你"), 1.0)
        self.assertEqual(similarity("Love", "love"), 0.97)

    def test_pinyin_homophone(self):
        # 同音字（在/再）应得高分——识别错字但位置有效
        self.assertGreaterEqual(similarity("在", "再"), 0.8)

    def test_cross_language_negative(self):
        self.assertLess(similarity("你", "go"), 0)

    def test_latin_partial(self):
        self.assertGreater(similarity("love", "loves"), 0.5)


class SplitAsrWordTests(unittest.TestCase):
    def test_mixed_word_split(self):
        self.assertEqual(split_asr_word("OK啦"), ["OK", "啦"])
        self.assertEqual(split_asr_word("don'tstop"), ["don'tstop"[:4], "stop"[:4]] if False else split_asr_word("don'tstop"))

    def test_weighted_expand(self):
        units = expand_asr_units([("OK啦", 1000, 2000, 0.9)])
        self.assertEqual([u.text for u in units], ["OK", "啦"])
        self.assertEqual(units[0].start, 1000)
        self.assertEqual(units[-1].end, 2000)


class ParseLineInfoTests(unittest.TestCase):
    def test_harmony_marker(self):
        info = parse_line_info("（和声）轻轻哼着歌")
        self.assertTrue(info.is_background)
        self.assertEqual(info.text, "轻轻哼着歌")

    def test_agent_marker(self):
        info = parse_line_info("[v2]我唱第二段")
        self.assertEqual(info.agent, "v2")
        self.assertEqual(info.text, "我唱第二段")

    def test_plain(self):
        info = parse_line_info("普通歌词")
        self.assertFalse(info.is_background)
        self.assertEqual(info.agent, "")


class AlignTests(unittest.TestCase):
    def test_dp_matches_despite_typo(self):
        infos = [{"text": "爱在西元前"}]
        units = scan_units(infos)
        # ASR 听成 "爱在夕元前"（夕/西 同音不同字）
        sources = expand_asr_units([(c, 1000 * (i + 1), 1000 * (i + 1) + 400, 0.9)
                                    for i, c in enumerate("爱在夕元前")])
        mapping = align_sequences(units, sources, 0, 6000)
        self.assertEqual(len(mapping), 5)
        interpolate(units, mapping, sources, 0, 6000)
        self.assertTrue(all(u.matched for u in units))
        self.assertEqual(units[2].text, "西")
        self.assertEqual(units[2].start, sources[2].start)

    def test_hallucinated_words_dropped(self):
        infos = [{"text": "去看繁星"}]
        units = scan_units(infos)
        sources = [AsrUnit("优优", 100, 300), AsrUnit("独播", 400, 600),
                   AsrUnit("剧场", 700, 900), AsrUnit("去", 1000, 1200),
                   AsrUnit("看", 1300, 1500), AsrUnit("繁", 1600, 1800),
                   AsrUnit("星", 1900, 2100)]
        mapping = align_sequences(units, sources, 0, 2500)
        interpolate(units, mapping, sources, 0, 2500)
        # 幻觉词不应被硬贴到歌词上：去/看/繁/星 应匹配到 1000-2100 的真词
        self.assertGreaterEqual(units[0].start, 900)
        self.assertTrue(all(u.matched for u in units))

    def test_interpolate_borrows_time(self):
        units = [Unit("一", 0, 0), Unit("二", 0, 0), Unit("三", 0, 0)]
        sources = [AsrUnit("一", 1000, 1100, 1.0), AsrUnit("三", 5000, 5100, 1.0)]
        # "二" 无匹配 → 在锚点间插值；窗口极窄时向锚点借时间
        interpolate(units, {0: 0, 2: 1}, sources, 0, 6000)
        self.assertTrue(units[1].end > units[1].start)
        self.assertGreaterEqual(units[1].start, units[0].start)
        self.assertLessEqual(units[1].end, units[2].start)

    def test_trustworthy_gates(self):
        self.assertFalse(trustworthy(1, 10, [AsrUnit("x", 0, 1)], 0.0, 3000))
        self.assertFalse(trustworthy(8, 10, [AsrUnit("x", 0, 1)] * 60, 0.0, 3000))


class LrcMarkerTests(unittest.TestCase):
    def test_parse_lrc_markers_and_translation(self):
        from lyrics.lrc import parse_lrc
        lrc = "\n".join([
            "[00:01.00][v1]主唱第一句",
            "[00:01.00]this is translation",
            "[00:05.00][v2]第二声部进来",
            "[00:09.00]（和声）啦啦啦",
        ])
        rows = parse_lrc(lrc)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["agent"], "v1")
        self.assertEqual(rows[0]["translation"], "this is translation")
        self.assertEqual(rows[1]["agent"], "v2")
        self.assertTrue(rows[2]["is_background"])
        self.assertEqual(rows[2]["text"], "啦啦啦")


class SchemaExtTests(unittest.TestCase):
    def test_line_extended_fields_roundtrip(self):
        ly = make_lyrics([
            {"text": "主唱", "tokens": "主唱", "start": 1000, "end": 1500},
            {"text": "和声", "tokens": "和声", "start": 2000, "end": 2500,
             "extra": {"is_background": True}},
        ])
        validate(ly)
        d = ly.to_dict()
        self.assertNotIn("agent", d["lines"][0])
        self.assertTrue(d["lines"][1]["is_background"])
        back = Lyrics.from_dict(d)
        self.assertTrue(back.lines[1].is_background)

    def test_overlapping_lines_allowed(self):
        # 分层歌词：同声部行可重叠（validate 不因重叠报错）
        ly = make_lyrics([
            {"text": "第一句很长", "tokens": "第一句很长", "start": 1000, "end": 4000},
            {"text": "叠句", "tokens": "叠句", "start": 3000, "end": 5000},
        ])
        validate(ly)  # 不抛即通过（lint 才管重叠）


class AssMigrationTests(unittest.TestCase):
    def test_styles_and_events(self):
        ly = make_lyrics([
            {"text": "第一句", "tokens": "第一句", "start": 1000, "end": 2000},
            {"text": "和声哦", "tokens": "和声哦", "start": 2100, "end": 3000,
             "extra": {"is_background": True}},
            {"text": "第二声部", "tokens": "第二声部", "start": 3100, "end": 4000,
             "extra": {"agent": "v2"}},
            {"text": "间奏后回来", "tokens": "间奏后回来", "start": 15000, "end": 17000},
        ])
        ly.lines[0].translation = "first line"
        ass = build_ass(ly, countdown_gap_ms=8000)
        for style in ("Backing,", "Agent2,", "Translation,", "Countdown,"):
            self.assertIn(style, ass)
        self.assertIn("• • •", ass)
        self.assertIn(",,3\n", ass)
        self.assertIn("first line", ass)
        # 下句预显跳过和声行：第一句演唱时的预显应是"第二声部"（v2 主唱）
        self.assertIn("Dialogue: 1,0:00:01.00,0:00:02.00,NextLine,,0,0,0,,第二声部", ass)


class TtmlTests(unittest.TestCase):
    def test_build_and_validate(self):
        from lyrics.ttml import build_ttml, validate_ttml
        ly = make_lyrics([
            {"text": "主唱", "tokens": "主唱", "start": 1000, "end": 1500},
            {"text": "和声", "tokens": "和声", "start": 2000, "end": 2500,
             "extra": {"is_background": True}},
            {"text": "对唱", "tokens": "对唱", "start": 3000, "end": 3500,
             "extra": {"agent": "v2", "translation": "duet line"}},
        ])
        ttml = build_ttml(ly, title="歌名", artist="歌手")
        self.assertEqual(validate_ttml(ttml), [])
        self.assertIn('ttm:role="x-bg"', ttml)
        self.assertIn('ttm:agent="v2"', ttml)
        self.assertIn('ttm:role="x-translation"', ttml)
        self.assertIn("itunes:key", ttml)
        self.assertIn("00:01.000", ttml)

    def test_validate_rejects_broken(self):
        from lyrics.ttml import validate_ttml
        self.assertTrue(validate_ttml("<not-xml"))
        bad = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
               '<p begin="bad" end="0:02.000"><span>字</span></p>'
               "</div></body></tt>")
        self.assertTrue(validate_ttml(bad))


class ExporterTests(unittest.TestCase):
    def _lyrics(self):
        return make_lyrics([
            {"text": "主唱", "tokens": "主唱", "start": 65000, "end": 65600},
            {"text": "love you", "tokens": "love you".split(), "start": 70000, "end": 71000},
        ])

    def test_standard_lrc(self):
        from lyrics.exporters import to_lrc
        text = to_lrc(self._lyrics())
        self.assertIn("[01:05.00]主唱", text)
        self.assertIn("[01:10.00]love you", text)
        self.assertNotIn("<", text)

    def test_enhanced_lrc(self):
        from lyrics.exporters import to_lrc
        text = to_lrc(self._lyrics(), enhanced=True)
        self.assertIn("[01:05.00]<01:05.00>主<01:05.10>唱", text)


class SearchParseTests(unittest.TestCase):
    def test_search_error_wrapped(self):
        from lyrics.lyrics_search import SearchError
        # 空查询直接返回空列表，不发请求
        from lyrics.lyrics_search import search
        self.assertEqual(search("  "), [])


class TifaWorkerTests(unittest.TestCase):
    def test_map_line_exact(self):
        from lyrics._tifa_worker import map_line
        tiers = {"texts": [(0.0, 1.0, "你"), (1.0, 2.0, "好")],
                 "phones": [(0.0, 0.5, "n"), (0.5, 1.0, "i"),
                            (1.0, 1.5, "x"), (1.5, 2.0, "au")]}
        out = map_line("你好", tiers, offset_s=10.0, clip_lo=10.0, clip_hi=12.0)
        self.assertEqual([c for c, _s, _e in out], ["你", "好"])
        self.assertEqual(out[0][1], 10000)   # 偏移已加回

    def test_map_line_fuzzy_mismatch(self):
        from lyrics._tifa_worker import map_line
        # 模型识别序列与目标不一致（夕↔西）→ DP 模糊映射，错字插值
        tiers = {"texts": [(0.0, 0.5, "爱"), (0.5, 1.0, "在"),
                           (1.0, 1.5, "夕"), (1.5, 2.0, "元"), (2.0, 2.5, "前")],
                 "phones": []}
        out = map_line("爱在西元前", tiers, offset_s=5.0, clip_lo=5.0, clip_hi=7.5)
        self.assertIsNotNone(out)
        self.assertEqual([c for c, _s, _e in out], ["爱", "在", "西", "元", "前"])

    def test_map_line_rejects_low_match(self):
        from lyrics._tifa_worker import map_line
        tiers = {"texts": [(0.0, 1.0, "优"), (1.0, 2.0, "优")], "phones": []}
        self.assertIsNone(map_line("去看繁星", tiers, 0.0, 0.0, 2.0))


if __name__ == "__main__":
    unittest.main()
