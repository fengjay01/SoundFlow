"""统一歌词 JSON（PRD SoundFlow 第六章）。

结构（整数毫秒，以原始媒体时间轴为基准）：
{
  "version": 1,
  "language": "zh",
  "source": "asr" | "user" | "mixed",
  "alignment_model": "whisperx-...",
  "lines": [
    {
      "id": "L0001",
      "text": "星光落在安静的屋檐",
      "start": 5000, "end": 9000,
      "words": [
        {"char": "星", "start": 5000, "end": 5450, "confidence": 0.97},
        ...
      ]
    }
  ]
}

规则：
- 每个可发音单元（中文单字 / 英文单词）必须有 start/end；
  标点、空白不出现在 words 里（无时长）。
- words[].char 顺序拼接 == line.text 去掉标点/空白。
- start < end；时间戳为非负整数毫秒。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# 标点/空白：不出现在 words 中
_PUNCT_RE = re.compile(
    r"[\s\u2000-\u206F\u3000-\u303F\uFF00-\uFF65"
    r"。，、！？；：…—·「」『』（）《》【】~～\-—,.!?;:'\"()\[\]<>/\\]"
)

# 可发音 token：CJK 单字（含扩展A）或 英文/数字词
_TOKEN_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]|[A-Za-z0-9']+")

# 对外共享：词级 token 的唯一正则。lyrics.asr 的 chars→words 分组必须用同一
# 规则（含词中撇号 you're / I'm），否则 words 与 text 分词不一致、校验必挂。
WORD_TOKEN_RE = _TOKEN_RE


def is_punct(ch: str) -> bool:
    return bool(_PUNCT_RE.match(ch))


def tokenize_pronounceable(text: str) -> list[str]:
    """按 PRD SF-005：中文按字符、英文按词。"""
    return _TOKEN_RE.findall(text)


def realign_words(words: list["Word"], text: str) -> list["Word"]:
    """把 words 的时间重切分到 text 的分词边界（SF-005 一致性自愈）。

    LRC 逐字时间戳 / TIFA 音素切分常把英文串拆成多段（Chieh/lun/Chou），
    与 :func:`tokenize_pronounceable` 的词边界不一致，validate 会报
    "words 与 text 不一致"。当 words 字符序列与 text 分词序列在字符级完全
    一致、仅边界不同时，按新边界合并时间（min 置信度）；无法修复时原样
    返回，交由 validate 报错。
    """
    toks = tokenize_pronounceable(text)
    if not words or not toks:
        return words
    chars = "".join(w.char for w in words)
    if chars != "".join(toks):
        return words
    out: list[Word] = []
    wi = 0
    for tok in toks:
        need = len(tok)
        if wi >= len(words):
            return words  # 字符数核对已过，理论上不会发生；防御
        start_w = words[wi]
        span_start = start_w.start
        span_end = start_w.end
        conf = start_w.confidence
        taken = 0
        while wi < len(words) and taken < need:
            w = words[wi]
            span_end = max(span_end, w.end)
            conf = min(conf, w.confidence)
            taken += len(w.char)
            wi += 1
        if taken != need:
            return words  # 边界错位（word 跨 token），放弃自愈
        out.append(Word(tok, span_start, max(span_end, span_start + 1), conf))
    return out


@dataclass
class Word:
    char: str
    start: int
    end: int
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "Word":
        return cls(
            char=str(d["char"]),
            start=int(float(d["start"])),
            end=int(float(d["end"])),
            confidence=float(d.get("confidence", 1.0)),
        )

    def to_dict(self) -> dict:
        return {
            "char": self.char,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
        }


@dataclass
class Line:
    id: str
    text: str
    start: int
    end: int
    words: list[Word] = field(default_factory=list)
    # 扩展字段（迁移自 lrc-ttml 方案；旧 JSON 缺省时加载不受影响）：
    # agent         对唱声部 "v1"/"v2"（来自歌词 [v1]/[v2] 标记），空 = 主唱默认
    # is_background 和声/背景人声（来自 （和声）/（背景） 标记），ASS 用暗色小字渲染
    # translation   翻译行（LRC 同时间戳的后续行），ASS 渲染为第二行小字
    agent: str = ""
    is_background: bool = False
    translation: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Line":
        return cls(
            id=str(d["id"]),
            text=str(d["text"]),
            start=int(float(d["start"])),
            end=int(float(d["end"])),
            words=[Word.from_dict(w) for w in d.get("words", [])],
            agent=str(d.get("agent", "") or ""),
            is_background=bool(d.get("is_background", False)),
            translation=str(d.get("translation", "") or ""),
        )

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "words": [w.to_dict() for w in self.words],
        }
        if self.agent:
            d["agent"] = self.agent
        if self.is_background:
            d["is_background"] = True
        if self.translation:
            d["translation"] = self.translation
        return d


@dataclass
class Lyrics:
    version: int = 1
    language: str = "zh"
    source: str = "asr"
    alignment_model: str = ""
    lines: list[Line] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Lyrics":
        return cls(
            version=int(float(d.get("version", 1))),
            language=str(d.get("language", "zh")),
            source=str(d.get("source", "asr")),
            alignment_model=str(d.get("alignment_model", "")),
            lines=[Line.from_dict(x) for x in d.get("lines", [])],
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "language": self.language,
            "source": self.source,
            "alignment_model": self.alignment_model,
            "lines": [x.to_dict() for x in self.lines],
        }


def load_lyrics(path: str | Path) -> Lyrics:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lyrics = Lyrics.from_dict(data)
    validate(lyrics)
    return lyrics


def save_lyrics(lyrics: Lyrics, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(lyrics.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def validate(lyrics: Lyrics) -> None:
    """结构校验，失败抛 ValueError（对应 SF-005 切分一致性 / SF-006 必备字段）。"""
    errors: list[str] = []
    seen_ids: set[str] = set()

    if not lyrics.lines:
        errors.append("lines 为空")

    for li, line in enumerate(lyrics.lines):
        tag = f"lines[{li}]({line.id})"
        if line.id in seen_ids:
            errors.append(f"{tag}: id 重复")
        seen_ids.add(line.id)

        if not (0 <= line.start < line.end):
            errors.append(f"{tag}: line.start/end 非法 ({line.start}, {line.end})")
        if not line.words:
            errors.append(f"{tag}: words 为空")
            continue

        # words.char 顺序拼接 == text 去标点
        expect = tokenize_pronounceable(line.text)
        actual = [w.char for w in line.words]
        if expect != actual:
            errors.append(
                f"{tag}: words 与 text 不一致\n  text 分词: {expect}\n  words:    {actual}"
            )

        for wi, w in enumerate(line.words):
            wtag = f"{tag}.words[{wi}]({w.char})"
            if not (0 <= w.start < w.end):
                errors.append(f"{wtag}: start/end 非法 ({w.start}, {w.end})")
            if w.start < line.start or w.end > line.end:
                errors.append(f"{wtag}: 超出 line 区间 [{line.start}, {line.end}]")
            if not (0.0 <= w.confidence <= 1.0):
                errors.append(f"{wtag}: confidence 越界 {w.confidence}")

    if errors:
        raise ValueError("歌词 JSON 校验失败:\n" + "\n".join(errors))


def lint(lyrics: Lyrics, media_duration_ms: int | None = None) -> list[str]:
    """时间轴质检（SF-007 子集，非致命，返回问题列表）。"""
    issues: list[str] = []
    prev_line: Line | None = None
    for line in lyrics.lines:
        tag = f"{line.id}"
        if prev_line is not None:
            if line.start < prev_line.end:
                issues.append(f"{tag}: 与上一行重叠 (start {line.start} < prev end {prev_line.end})")
            gap = line.start - prev_line.end
            if gap > 3000:
                issues.append(f"{tag}: 与上一行空隙过大 {gap} ms")
        prev_w: Word | None = None
        for w in line.words:
            if prev_w is not None:
                if w.start < prev_w.end:
                    issues.append(f"{tag}.{w.char}: 与上一字重叠 ({w.start} < {prev_w.end})")
                if w.start - prev_w.end > 1500:
                    issues.append(f"{tag}.{w.char}: 字内空隙 {w.start - prev_w.end} ms")
            if w.end - w.start < 30:
                issues.append(f"{tag}.{w.char}: 过短 {w.end - w.start} ms")
            if w.end - w.start > 8000:
                issues.append(f"{tag}.{w.char}: 过长 {w.end - w.start} ms")
            prev_w = w
        if media_duration_ms is not None and line.end > media_duration_ms:
            issues.append(f"{tag}: end {line.end} 超过媒体时长 {media_duration_ms}")
        prev_line = line
    return issues
