# -*- coding: utf-8 -*-
"""lyrics 包单元测试（stdlib unittest，`python -m unittest lyrics.test_lyrics`）。

覆盖：schema 校验/容错解析、lint 质检、karaoke 时间语义、ASS 生成、FFmpeg 命令构建。
"""
from __future__ import annotations

import json
import re
import unittest

from lyrics.schema import (
    Line, Lyrics, Word, lint, load_lyrics, save_lyrics, tokenize_pronounceable, validate,
)
from lyrics.ass_gen import build_ass, ms_to_ass_time
from lyrics.compose import AUDIO_TRACKS, build_ffmpeg_command, _escape_filter_path


def _demo_lyrics() -> Lyrics:
    # words 只含可发音字符（标点不是 word，由 ass_gen 并块）；词区间必须落在行区间内
    return Lyrics(
        version=1,
        language="zh",
        source="unit-test",
        alignment_model="test",
        lines=[
            Line(id="L1", text="星光落在安静的屋檐", start=5000, end=9000, words=[
                Word(ch, 5000 + i * 444, 5000 + (i + 1) * 444, 0.9)
                for i, ch in enumerate("星光落在安静的屋檐")
            ]),
            Line(id="L2", text="风把夏天吹得很远。", start=9500, end=13000, words=[
                Word(ch, 9500 + i * 437, 9500 + (i + 1) * 437, 0.85)
                for i, ch in enumerate("风把夏天吹得很远")
            ]),
        ],
    )


class TestSchema(unittest.TestCase):
    def test_tokenize(self):
        toks = tokenize_pronounceable("那句 hello 轻轻落在耳边")
        self.assertIn("hello", toks)
        self.assertIn("那", toks)
        self.assertIn("句", toks)
        self.assertIn("耳", toks)
        self.assertNotIn(" ", toks)

    def test_validate_ok(self):
        validate(_demo_lyrics())  # 不抛异常即通过

    def test_validate_word_mismatch(self):
        ly = _demo_lyrics()
        ly.lines[0].words = ly.lines[0].words[:-1]  # 少一个词
        with self.assertRaises(ValueError):
            validate(ly)

    def test_realign_words_merges_split_english(self):
        """LRC 逐字时间戳把英文串拆开（真实踩坑：Chieh/lun/Chou），应自愈重切分。"""
        from lyrics.schema import realign_words
        words = [Word("作", 1000, 1400, 0.9), Word("词", 1400, 1800, 0.9),
                 Word("Chieh", 1800, 2100, 0.8), Word("lun", 2100, 2400, 0.7),
                 Word("Chou", 2400, 2800, 0.6)]
        fixed = realign_words(words, "作词ChiehlunChou")
        self.assertEqual([w.char for w in fixed], ["作", "词", "ChiehlunChou"])
        self.assertEqual((fixed[2].start, fixed[2].end), (1800, 2800))  # 时间合并
        self.assertEqual(fixed[2].confidence, 0.6)                      # min 置信度
        ly = Line(id="L1", text="作词ChiehlunChou", start=1000, end=2800, words=fixed)
        validate(Lyrics(version=1, lines=[ly]))  # 自愈后校验通过

    def test_realign_words_camelcase_and_syllables(self):
        """Alfa/Studio 与 Re/So/So/Si/Do/Si/La（真实踩坑样本）。"""
        from lyrics.schema import realign_words
        words = [Word(c, 1000 + k * 200, 1200 + k * 200, 0.8)
                 for k, c in enumerate(["录", "音", "工", "程", "杨", "瑞", "代",
                                        "Alfa", "Studio"])]
        fixed = realign_words(words, "录音工程杨瑞代AlfaStudio")
        self.assertEqual([w.char for w in fixed][-1], "AlfaStudio")
        ly = Line(id="L1", text="录音工程杨瑞代AlfaStudio", start=1000, end=2800, words=fixed)
        validate(Lyrics(version=1, lines=[ly]))

        syl = [Word(c, 500 + k * 150, 650 + k * 150, 0.8)
               for k, c in enumerate(["Re", "So", "So", "Si", "Do", "Si", "La"])]
        fixed2 = realign_words(syl, "ReSoSoSiDoSiLa")
        self.assertEqual([w.char for w in fixed2], ["ReSoSoSiDoSiLa"])
        self.assertEqual((fixed2[0].start, fixed2[0].end), (500, 1550))

    def test_realign_words_keeps_true_mismatch(self):
        """字符级不一致（真错词）不自愈，交由 validate 报错。"""
        from lyrics.schema import realign_words
        words = [Word("作", 1000, 1400, 0.9), Word("曲", 1400, 1800, 0.9)]
        fixed = realign_words(words, "作词")   # "曲" ≠ "词"
        self.assertEqual([w.char for w in fixed], ["作", "曲"])  # 原样返回

    def test_validate_bad_time(self):
        ly = _demo_lyrics()
        ly.lines[1].words[0] = Word(ly.lines[1].words[0].char, 100, 50, 1.0)  # start>end
        with self.assertRaises(ValueError):
            validate(ly)

    def test_roundtrip_float_coercion(self):
        raw = {
            "version": "1.0", "language": "zh", "source": "t", "alignment_model": "t",
            "lines": [{
                "id": "L1", "text": "啊", "start": 1500.4, "end": 2500.7,
                "words": [{"char": "啊", "start": 1500.4, "end": 2500.7, "confidence": 0.5}],
            }],
        }
        import tempfile, os
        p = os.path.join(tempfile.gettempdir(), "lyrics_float_test.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        ly = load_lyrics(p)
        self.assertEqual(ly.version, 1)
        self.assertEqual(ly.lines[0].start, 1500)      # int(float(1500.4))，毫秒浮点容错
        self.assertEqual(ly.lines[0].words[0].end, 2500)

    def test_lint_flags_overlap_and_gap(self):
        ly = _demo_lyrics()
        # 制造重叠：L2 起点提前进 L1 尾部
        ly.lines[1] = Line(id="L2", text=ly.lines[1].text, start=8800, end=13000,
                           words=[Word(w.char, w.start, w.end, w.confidence) for w in ly.lines[1].words])
        ly.lines[1].words[0] = Word(ly.lines[1].words[0].char, 8800, 8938, 0.85)
        msgs = lint(ly)
        self.assertTrue(any("overlap" in m or "重叠" in m for m in msgs), msgs)


class TestAssGen(unittest.TestCase):
    def test_ms_to_ass_time(self):
        self.assertEqual(ms_to_ass_time(0), "0:00:00.00")
        self.assertEqual(ms_to_ass_time(61500), "0:01:01.50")
        self.assertEqual(ms_to_ass_time(3_661_234), "1:01:01.23")

    def test_karaoke_chunk_durations_sum(self):
        ly = _demo_lyrics()
        ass = build_ass(ly, template="classic_ktv")
        # 取 L1 事件
        ev = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and "星{" in ln][0]
        ksum = sum(int(m) for m in re.findall(r"\\k(?:f)?(\d+)", ev))
        # L1 全行块宽 = 500ms*8 + 末块 500ms = 4000ms = 400cs
        self.assertEqual(ksum, 400)

    def test_karaoke_sum_matches_line_duration(self):
        ly = _demo_lyrics()
        ass = build_ass(ly, template="classic_ktv")
        evs = [l for l in ass.splitlines() if l.startswith("Dialogue:")
               and "NextLine" not in l and ",Dots," not in l and ",Countdown," not in l]
        for ev, line in zip(evs, ly.lines):
            ksum = sum(int(m) for m in re.findall(r"\\k(?:f)?(\d+)", ev))
            expected_cs = (max(line.end, line.words[-1].end) - line.words[0].start) // 10
            self.assertEqual(ksum, expected_cs, f"{line.text}: {ev}")

    def test_punct_no_duration(self):
        ly = _demo_lyrics()
        ass = build_ass(ly, template="classic_ktv")
        ev = [l for l in ass.splitlines() if "远。" in l and l.startswith("Dialogue:")][0]
        # 标点并块：不应出现独立的 0 时长块导致错位；块总数 < 字符数
        chunks = re.findall(r"\\k(?:f)?\d+[^\{\}]*", ev)
        self.assertLess(len(chunks), len("风把夏天吹得很远。"))

    def test_next_preview_present(self):
        ly = _demo_lyrics()
        ass = build_ass(ly, template="classic_ktv", next_preview=True)
        self.assertIn("NextLine", ass)

    def test_all_templates_render(self):
        from lyrics.ass_gen import TEMPLATES
        ly = _demo_lyrics()
        for name in TEMPLATES:
            self.assertIn("Style:", build_ass(ly, template=name))

    def test_color_semantics(self):
        """ASS karaoke：sung=PrimaryColour（模板 primary），unsung=SecondaryColour。"""
        ly = _demo_lyrics()
        ass = build_ass(ly, template="classic_ktv")
        style = [l for l in ass.splitlines() if l.startswith("Style: ClassicKTV")][0]
        fields = style.split(",")
        # fields: 0=样式名 1=字体 2=字号 3=PrimaryColour(sung) 4=SecondaryColour(unsung)
        self.assertTrue(fields[3].startswith("&H"), fields)
        self.assertTrue(fields[4].startswith("&H"), fields)
        self.assertNotEqual(fields[3], fields[4], "sung/unsung 颜色必须不同")


class TestCompose(unittest.TestCase):
    def test_escape_filter_path_double_backslash(self):
        s = _escape_filter_path(r"E:\x\y.ass")
        self.assertIn("\\\\:", s)          # 冒号前是双反斜杠
        self.assertNotIn(":", s.replace("\\\\:", ""))  # 不再有裸冒号

    def test_build_command_defaults(self):
        cmd = build_ffmpeg_command("in.mp4", "out.mp4", "a.ass", has_video=True)
        s = " ".join(cmd)
        self.assertIn("ass=", s)
        self.assertNotIn("audio_track", s)

    def test_build_command_instrumental_requires_audio(self):
        with self.assertRaises(Exception):
            build_ffmpeg_command("in.mp4", "out.mp4", "a.ass", audio_track="instrumental")
        cmd = build_ffmpeg_command("in.mp4", "out.mp4", "a.ass",
                                   audio_path="v.wav", audio_track="instrumental",
                                   has_video=True)
        s = " ".join(cmd)
        self.assertIn("v.wav", s)

    def test_build_command_start_seek(self):
        cmd = build_ffmpeg_command("in.mp4", "out.mp4", "a.ass", start=30, duration=25,
                                   has_video=True)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "30")       # 输入端 seek
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))      # 在 -i 之前
        self.assertEqual(cmd[cmd.index("-t") + 1], "25")

    def test_build_command_audio_only_uses_black_background(self):
        """纯音频输入（MP3 等）：lavfi 黑底 + 烧字幕，不再 map 0:v:0 报错。"""
        cmd = build_ffmpeg_command("song.mp3", "out.mp4", "a.ass", start=30, duration=25,
                                   has_video=False)
        s = " ".join(cmd)
        self.assertIn("lavfi", s)                 # 黑底背景源
        self.assertIn("color=c=black", s)
        self.assertIn("-shortest", s)             # 背景随音频结束
        self.assertIn("ass=", s)                  # 字幕照常烧录
        self.assertNotIn("scale=-2", s)           # 无源分辨率可缩放

    def test_build_command_audio_only_instrumental(self):
        cmd = build_ffmpeg_command("song.mp3", "out.mp4", "a.ass",
                                   audio_path="v.wav", audio_track="instrumental",
                                   has_video=False)
        s = " ".join(cmd)
        self.assertIn("v.wav", s)
        self.assertIn("2:a:0", s)                 # 第三输入（伴奏）的音轨

    def test_audio_track_modes(self):
        self.assertEqual(set(AUDIO_TRACKS), {"instrumental", "original", "source"})


class TestAsrHelpers(unittest.TestCase):
    """asr.py 的纯函数（不依赖 whisperx 安装）。"""

    def test_chars_to_words_merges_latin(self):
        from lyrics.asr import _chars_to_words
        chars = [
            {"char": "那", "start": 0.0, "end": 0.3, "score": 0.9},
            {"char": "句", "start": 0.3, "end": 0.6, "score": 0.9},
            {"char": " ", "start": 0.6, "end": 0.7, "score": 0.0},
            {"char": "h", "start": 0.7, "end": 0.75, "score": 0.8},
            {"char": "e", "start": 0.75, "end": 0.8, "score": 0.8},
            {"char": "l", "start": 0.8, "end": 0.85, "score": 0.8},
            {"char": "l", "start": 0.85, "end": 0.9, "score": 0.8},
            {"char": "o", "start": 0.9, "end": 1.0, "score": 0.8},
            {"char": "。", "start": 1.0, "end": 1.1, "score": 0.0},
            {"char": "耳", "start": 1.1, "end": 1.4, "score": 0.9},
        ]
        words = _chars_to_words(chars)
        self.assertEqual([w.char for w in words], ["那", "句", "hello", "耳"])
        self.assertEqual(words[2].start, 700)
        self.assertEqual(words[2].end, 1000)

    def test_chars_to_words_skips_punct_and_space(self):
        from lyrics.asr import _chars_to_words
        words = _chars_to_words([
            {"char": "啊", "start": 0.0, "end": 1.0, "score": 0.5},
            {"char": "，", "start": 1.0, "end": 1.2, "score": 0.0},
            {"char": " ", "start": 1.2, "end": 1.3, "score": 0.0},
        ])
        self.assertEqual([w.char for w in words], ["啊"])

    def test_fallback_spread(self):
        from lyrics.asr import _fallback_spread_words
        words = _fallback_spread_words("你好", 2.0, 4.0)
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0].start, 2000)
        self.assertEqual(words[1].end, 4000)
        self.assertTrue(all(w.confidence == 0.1 for w in words))

    def test_iter_tokens(self):
        from lyrics.asr import _iter_tokens
        self.assertEqual(list(_iter_tokens("那句 hello，轻轻。")),
                         ["那", "句", "hello", "轻", "轻"])


class TestLrc(unittest.TestCase):
    """LRC 解析（导入歌词来源之一）。"""

    def test_parse_basic_and_order(self):
        from lyrics.lrc import parse_lrc
        lrc = "[00:20.00]第二行\n[00:10.50]第一行\n"
        rows = parse_lrc(lrc)
        self.assertEqual([r["text"] for r in rows], ["第一行", "第二行"])
        self.assertEqual(rows[0]["start_ms"], 10500)
        self.assertEqual(rows[0]["end_ms"], 20000)       # 下一行 start
        self.assertEqual(rows[1]["end_ms"], 25000)       # 末行 +5s

    def test_parse_multi_timestamp_and_word_tags(self):
        from lyrics.lrc import parse_lrc
        lrc = "[00:10.00][01:30.00]<00:10.10>副歌<00:10.20>部分\n"
        rows = parse_lrc(lrc)
        self.assertEqual([r["text"] for r in rows], ["副歌部分", "副歌部分"])
        self.assertEqual([r["start_ms"] for r in rows], [10000, 90000])

    def test_parse_offset(self):
        from lyrics.lrc import parse_lrc
        lrc = "[offset:500]\n[00:10.00]歌词\n"
        rows = parse_lrc(lrc)
        self.assertEqual(rows[0]["start_ms"], 9500)      # 正值整体提前

    def test_looks_like_lrc(self):
        from lyrics.lrc import looks_like_lrc
        self.assertTrue(looks_like_lrc("[00:10.00]x"))
        self.assertFalse(looks_like_lrc("只是普通文本"))


class TestImportAlign(unittest.TestCase):
    """导入歌词：纯文本 → ASR 锚定 + 模糊匹配（不依赖 whisperx）。"""

    def test_match_line_timings_exact(self):
        from lyrics.import_align import match_line_timings
        lines = ["你好世界", "再见"]
        asr_words = [("你", 1000, 1200), ("好", 1200, 1500),
                     ("世", 3000, 3200), ("界", 3200, 3500),
                     ("再", 5000, 5200), ("见", 5200, 5500)]
        timings = match_line_timings(lines, asr_words)
        self.assertEqual(timings[0], (1000, 3500, True))  # 「你好世界」覆盖到「界」
        self.assertEqual(timings[1], (5000, 5500, True))

    def test_match_line_timings_survives_asr_errors(self):
        from lyrics.import_align import match_line_timings
        # ASR 把「世界」误识别成「世代」：replace 块按比例映射，时间窗仍可用
        lines = ["你好世界", "再见"]
        asr_words = [("你", 1000, 1200), ("好", 1200, 1500),
                     ("世", 3000, 3200), ("代", 3200, 3500),
                     ("再", 5000, 5200), ("见", 5200, 5500)]
        timings = match_line_timings(lines, asr_words)
        self.assertTrue(timings[0][2])
        self.assertEqual(timings[0][0], 1000)
        self.assertEqual(timings[1], (5000, 5500, True))

    def test_interpolate_gaps_fills_unmatched(self):
        from lyrics.import_align import _interpolate_gaps, match_line_timings
        lines = ["第一句", "间奏没有词", "第三句"]
        asr_words = [("第", 1000, 1100), ("一", 1100, 1300), ("句", 1300, 1500),
                     ("第", 8000, 8100), ("三", 8100, 8300), ("句", 8300, 8500)]
        timings = match_line_timings(lines, asr_words)
        self.assertIsNone(timings[1])                    # 中间行匹配不到
        filled = _interpolate_gaps(lines, timings)
        self.assertTrue(filled[1][2] is False)           # 插值行打未匹配标记
        self.assertGreaterEqual(filled[1][0], filled[0][1])  # 不与前行重叠
        self.assertLessEqual(filled[1][1], filled[2][0])     # 不与后行重叠

    def test_build_lyrics_from_timings_validates(self):
        from lyrics.import_align import build_lyrics_from_timings
        ly = build_lyrics_from_timings(
            ["你好世界", "再见"],
            [(1000, 1500, True), (3000, 3500, True)],
            source="test", language="zh")
        self.assertEqual(ly.language, "zh")
        self.assertEqual([w.char for w in ly.lines[0].words], ["你", "好", "世", "界"])

    def test_iter_tokens_keeps_apostrophes(self):
        from lyrics.asr import _iter_tokens
        # 词中撇号属于单词（与 schema.tokenize_pronounceable 一致）
        self.assertEqual(list(_iter_tokens("You're in the middle, I'm in the street.")),
                         ["You're", "in", "the", "middle", "I'm", "in", "the", "street"])

    def test_chars_to_words_keeps_apostrophes(self):
        from lyrics.asr import _chars_to_words
        # whisperx 对 "You're" 返回单字符对齐：'You', "'", 'r', 'e'
        # 分组必须与 text 分词一致（撇号并入整词），否则英文歌词校验必挂。
        chars = []
        timings = {"Y": (1.0, 1.1), "o": (1.1, 1.2), "u": (1.2, 1.3),
                   "'": (1.3, 1.35), "r": (1.35, 1.5), "e": (1.5, 1.6)}
        for ch, (s, e) in timings.items():
            chars.append({"char": ch, "start": s, "end": e, "score": 0.9})
        words = _chars_to_words(chars)
        self.assertEqual([w.char for w in words], ["You're"])
        self.assertEqual(words[0].start, 1000)
        self.assertEqual(words[0].end, 1600)

    def test_chars_to_words_matches_text_tokens(self):
        from lyrics.asr import _chars_to_words, _iter_tokens
        text = "I'll give you the eyes of your heart, why don't you tell me?"
        # 构造逐字对齐：每个字符一个时间片
        chars = []
        t = 0.0
        for ch in text:
            chars.append({"char": ch, "start": t, "end": t + 0.05, "score": 0.9})
            t += 0.05
        words = _chars_to_words(chars)
        self.assertEqual([w.char for w in words], list(_iter_tokens(text)))


class TestLocateRowsDP(unittest.TestCase):
    """行骨架定位：全局单调 DP（重复副歌按出现顺序分配，不塌缩）。"""

    @staticmethod
    def _mk_transcript(phrases: list[tuple[str, int, int]]):
        """(整句文本, 起ms, 止ms) → 每字一个转写词，字均分时长。"""
        out = []
        for text, s, e in phrases:
            chars = [ch for ch in text if not ch.isspace()]
            n = len(chars)
            step = max((e - s) // max(n, 1), 1)
            for k, ch in enumerate(chars):
                out.append((ch, s + k * step, s + (k + 1) * step))
        return out

    def test_dp_align_repeated_lines_in_order(self):
        from lyrics.qwen_align import _dp_align
        l_chars = list("爱在西元前爱在西元前")
        t_chars = list("爱在西元前爱在西元前")
        j = _dp_align(l_chars, t_chars)
        # 全部字符一一对应且单调
        self.assertEqual(len(j), len(l_chars))
        vals = [j[i] for i in sorted(j)]
        self.assertEqual(vals, list(range(len(t_chars))))

    def test_locate_rows_repeated_chorus_gets_distinct_anchors(self):
        # 爱在西元前塌缩事故回归：重复副歌行必须按出现顺序分配锚点，
        # 绝不允许 27 行被挤进相邻两锚点间的 1 秒窗口
        from lyrics.qwen_align import _locate_rows
        p1 = "我给你的爱写在西元前"
        p2 = "深埋在美索不达米亚平原"
        tw = self._mk_transcript([
            (p1, 1000, 3000), (p2, 3100, 5300),      # 副歌 1
            (p1, 6000, 8000), (p2, 8100, 10300),     # 副歌 2（重复）
        ])
        rows = [{"text": t} for t in
                ["前奏", p1, p2, p1, p2, "尾声"]]
        out = _locate_rows(tw, rows)
        self.assertEqual(len(out), len(rows))
        # 两次出现的行各自拿到出现位置的锚（而不是全挤在第一次）
        r1, r2, r3, r4 = out[1], out[2], out[3], out[4]
        self.assertLess(r1["start_ms"], r3["start_ms"] - 2000)
        self.assertLess(r2["start_ms"], r4["start_ms"] - 2000)
        self.assertTrue(900 <= r1["start_ms"] <= 1200)
        self.assertTrue(5900 <= r3["start_ms"] <= 6100)
        # 单调：锚点随行序不减
        starts = [r["start_ms"] for r in out]
        self.assertEqual(starts, sorted(starts))

    def test_assign_times_extends_partial_match_window(self):
        # 转写残缺：整行只有前几个字有锚 → 行窗按每字时长中位数扩展，
        # 避免精对齐把整句挤进几百毫秒
        from lyrics.qwen_align import _assign_times
        p1 = "泥板上的字迹依然清晰可见"          # 12 字，只有前 4 字被转写
        p2 = "当古文明只剩下难解的语言"          # 12 字，完整转写
        tw = self._mk_transcript([
            (p1[:4], 1000, 1400),
            (p2, 6000, 8400),
        ])
        rows = [{"text": p1}, {"text": p2}]
        j_of_i = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10, 11: 11}
        out = _assign_times(rows, [(w[1], w[2]) for w in tw], j_of_i, list(p1 + p2))
        win = out[0]["end_ms"] - out[0]["start_ms"]
        # 期望 ~12 字 × 200ms/字 = 2400ms（p2 的每字时长），远大于原始 400ms
        self.assertGreaterEqual(win, 1800)
        # 不越过下一行
        self.assertLess(out[0]["end_ms"], out[1]["start_ms"])


if __name__ == "__main__":
    unittest.main()
