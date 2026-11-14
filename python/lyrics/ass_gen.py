"""ASS KTV 字幕生成（PRD SF-009 / SF-010）。

- 不自研渲染引擎：输出 ASS，由 libass（FFmpeg ass 滤镜）渲染。
- 逐字高亮：ASS karaoke 标签 \\k（硬切换）/ \\kf（渐进填充 sweep）。
- 6 个样式模板（SF-010）：classic_ktv / apple_music / simple_white /
  gradient_highlight / two_line / single_line。
- 下句预显（SF-009）：当前行演唱时，上一行位置显示下一句淡色文本。

时间语义（\\k 时钟）：事件从 event_start 起，每个 {\\kN} 时钟推进 N 厘秒后
切到该块高亮。块时长按"到下一块开始"取，吸收字间隙保证时钟不漂移；
首个块从 words[0].start 起算（事件起点=首字起点）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .schema import Line, Lyrics, Word

# SF-010 六模板：字体/字号/主色(已唱)/高亮待唱色/描边/阴影/位置/对齐/行距
# 颜色为 (r, g, b)，输出时转 &HAABBGGRR。


@dataclass(frozen=True)
class Template:
    key: str
    style_name: str
    font: str
    font_size: int
    primary: tuple[int, int, int]      # 唱过后的颜色（\k 完成态）
    secondary: tuple[int, int, int]    # 未唱颜色（\k 前）
    outline_color: tuple[int, int, int]
    back: tuple[int, int, int]
    bold: bool
    outline: float
    shadow: float
    align: int          # ASS Alignment（2=底部居中）
    margin_v: int
    tag: str            # "k" 或 "kf"
    next_preview: bool  # 下句预显
    line_spacing: int   # 行距（预显行偏移系数用）
    render: str = "karaoke"  # karaoke=逐字卡拉OK / line=逐行歌词（播放器风）


TEMPLATES: dict[str, Template] = {
    "classic_ktv": Template(
        key="classic_ktv", style_name="ClassicKTV", font="Microsoft YaHei",
        font_size=68, primary=(32, 80, 224), secondary=(255, 255, 255),
        outline_color=(0, 0, 0), back=(0, 0, 0), bold=True,
        outline=3.0, shadow=1.5, align=2, margin_v=60,
        tag="kf", next_preview=True, line_spacing=20,
    ),
    "apple_music": Template(
        key="apple_music", style_name="AppleMusic", font="Microsoft YaHei",
        font_size=64, primary=(255, 255, 255), secondary=(140, 140, 140),
        outline_color=(0, 0, 0), back=(0, 0, 0), bold=True,
        outline=1.5, shadow=0.0, align=2, margin_v=50,
        tag="kf", next_preview=True, line_spacing=20,
    ),
    "simple_white": Template(
        key="simple_white", style_name="SimpleWhite", font="Microsoft YaHei",
        font_size=60, primary=(255, 255, 255), secondary=(255, 255, 255),
        outline_color=(20, 20, 20), back=(0, 0, 0), bold=False,
        outline=2.5, shadow=0.0, align=2, margin_v=50,
        tag="k", next_preview=True, line_spacing=18,
    ),
    "gradient_highlight": Template(
        key="gradient_highlight", style_name="GradientHighlight", font="Microsoft YaHei",
        font_size=72, primary=(255, 209, 102), secondary=(255, 255, 255),
        outline_color=(30, 20, 60), back=(0, 0, 0), bold=True,
        outline=3.0, shadow=1.0, align=2, margin_v=60,
        tag="kf", next_preview=True, line_spacing=22,
    ),
    "two_line": Template(
        key="two_line", style_name="TwoLine", font="Microsoft YaHei",
        font_size=52, primary=(255, 105, 97), secondary=(255, 255, 255),
        outline_color=(0, 0, 0), back=(0, 0, 0), bold=True,
        outline=2.5, shadow=1.0, align=2, margin_v=40,
        tag="kf", next_preview=True, line_spacing=16,
    ),
    "single_line": Template(
        key="single_line", style_name="SingleLine", font="Microsoft YaHei",
        font_size=84, primary=(255, 59, 48), secondary=(255, 255, 255),
        outline_color=(0, 0, 0), back=(0, 0, 0), bold=True,
        outline=3.5, shadow=1.5, align=2, margin_v=56,
        tag="kf", next_preview=False, line_spacing=24,
    ),
    # 纯逐行（播放器/网易云风）：整行显示与切换，不逐字渲染
    "lyric_line": Template(
        key="lyric_line", style_name="LyricLine", font="Microsoft YaHei",
        font_size=66, primary=(255, 255, 255), secondary=(120, 120, 120),
        outline_color=(0, 0, 0), back=(0, 0, 0), bold=True,
        outline=2.0, shadow=0.5, align=2, margin_v=150,
        tag="kf", next_preview=True, line_spacing=20,
        render="line",
    ),
}


def ms_to_ass_time(ms: int) -> str:
    """毫秒 → H:MM:SS.cc（厘秒精度）。"""
    cs = max(0, (ms + 5) // 10)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cc = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cc:02d}"


def _color(rgb: tuple[int, int, int], alpha: int = 0) -> str:
    r, g, b = rgb
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _style_line(t: Template) -> str:
    bold = -1 if t.bold else 0
    # ASS karaoke：未唱=SecondaryColour，唱完切换 PrimaryColour（sung）
    return (
        f"Style: {t.style_name},{t.font},{t.font_size},"
        f"{_color(t.primary)},{_color(t.secondary)},{_color(t.outline_color)},{_color(t.back)},"
        f"{bold},0,0,0,100,100,0,0,1,{t.outline},{t.shadow},{t.align},60,60,{t.margin_v},1"
    )


def _next_style_line(t: Template) -> str:
    """下句预显样式：淡色、略小、位于主行下方（经典 KTV 双行：上唱下预）。"""
    size = int(t.font_size * 0.78)
    margin_v = max(t.margin_v - int(t.font_size * 1.35), 8)
    grey = (150, 150, 150)
    bold = -1 if t.bold else 0
    return (
        f"Style: NextLine,{t.font},{size},"
        f"{_color(grey)},{_color(grey)},{_color(t.outline_color)},{_color(t.back)},"
        f"{bold},0,0,0,100,100,0,0,1,{t.outline * 0.7:.1f},{t.shadow * 0.5:.1f},{t.align},60,60,{margin_v},1"
    )


def _bg_style_line(t: Template) -> str:
    """和声/背景人声样式（x-bg）：主行上方、暗色、约 0.6 倍字号。"""
    size = int(t.font_size * 0.6)
    margin_v = t.margin_v + int(t.font_size * 1.5) + t.line_spacing * 3
    dim = (170, 170, 170)
    return (
        f"Style: Backing,{t.font},{size},"
        f"{_color(dim)},{_color(dim)},{_color(t.outline_color)},{_color(t.back)},"
        f"0,0,0,0,100,100,0,0,1,{t.outline * 0.6:.1f},{t.shadow * 0.4:.1f},{t.align},60,60,{margin_v},1"
    )


def _agent2_style_line(t: Template) -> str:
    """对唱第二声部样式（[v2]）：换色 + 右对齐，与 v1 区分。"""
    second = (102, 204, 255)
    return (
        f"Style: Agent2,{t.font},{t.font_size},"
        f"{_color(second)},{_color(t.secondary)},{_color(t.outline_color)},{_color(t.back)},"
        f"{-1 if t.bold else 0},0,0,0,100,100,0,0,1,{t.outline},{t.shadow},1,60,60,{t.margin_v},1"
    )


def _translation_style_line(t: Template) -> str:
    """翻译/音译行样式：主行下方小字、常显淡色。"""
    size = int(t.font_size * 0.42)
    margin_v = max(t.margin_v - int(t.font_size * 0.85), 10)
    dim = (200, 200, 200)
    return (
        f"Style: Translation,{t.font},{size},"
        f"{_color(dim)},{_color(dim)},{_color(t.outline_color)},{_color(t.back)},"
        f"0,0,0,0,100,100,0,0,1,{t.outline * 0.5:.1f},0,{t.align},60,60,{margin_v},1"
    )


def _countdown_style_line(t: Template) -> str:
    """长间奏三点倒数样式：居中偏上大号数字（KTV 招牌，迁移自 lrc-ttml）。"""
    size = int(t.font_size * 1.4)
    return (
        f"Style: Countdown,{t.font},{size},"
        f"{_color(t.primary)},{_color(t.primary)},{_color(t.outline_color)},{_color(t.back)},"
        f"-1,0,0,0,100,100,0,0,1,{t.outline},0,5,60,60,340,1"
    )


def _countdown_events(gap_start: int, gap_end: int, t: Template) -> list[str]:
    """长间奏倒数：前段淡入三个点，最后 3 秒 3→2→1。"""
    events: list[str] = []
    if gap_end - gap_start < 6000:
        return events
    dots = ["•    ", "• •  ", "• • •"]
    seg = (gap_end - gap_start - 3000) // 3
    for i, d in enumerate(dots):
        s = gap_start + i * seg
        e = gap_start + (i + 1) * seg if i < 2 else gap_end - 3000
        if e - s < 300:
            continue
        events.append(
            f"Dialogue: 1,{ms_to_ass_time(s)},{ms_to_ass_time(e)},Countdown,,0,0,0,,{d.strip()}"
        )
    for n in (3, 2, 1):
        s = gap_end - n * 1000
        e = s + 1000
        events.append(
            f"Dialogue: 1,{ms_to_ass_time(s)},{ms_to_ass_time(e)},Countdown,,0,0,0,,{n}"
        )
    return events


def _build_chunks(line: Line) -> list[str]:
    """每个 word 一个显示块；标点/空白并入相邻块（不占时长，SF-005）。"""
    text = line.text
    words = line.words
    # 取 text 中每个 token 的位置（schema 已校验 token == words[].char）
    import re as _re
    tokens = [(m.start(), m.group()) for m in _re.finditer(
        r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]|[A-Za-z0-9']+", text
    )]
    out = [w.char for w in words]
    prev = 0
    for ti, (ts, tok) in enumerate(tokens):
        gap = text[prev:ts]
        if ti == 0:
            out[0] = gap + out[0]  # 行首标点
        else:
            out[ti - 1] += gap  # 字间标点并入前块
        prev = ts + len(tok)
    if out:
        out[-1] += text[prev:]  # 行尾标点
    return out


def _karaoke_text(line: Line, tag: str) -> str:
    words = line.words
    if not words:
        return line.text
    event_start = words[0].start
    event_end = max(line.end, words[-1].end)
    starts_cs = [max(0, (w.start - event_start + 5) // 10) for w in words]
    total_cs = (event_end - event_start + 5) // 10
    durs_cs = []
    for i in range(len(words)):
        nxt = starts_cs[i + 1] if i + 1 < len(words) else total_cs
        durs_cs.append(max(nxt - starts_cs[i], 0))
    chunks = _build_chunks(line)
    return "".join(f"{{\\{tag}{d}}}{c}" for d, c in zip(durs_cs, chunks))


def build_ass(
    lyrics: Lyrics,
    template: str = "classic_ktv",
    next_preview: bool | None = None,
    play_res: tuple[int, int] = (1920, 1080),
    countdown_gap_ms: int = 8000,
) -> str:
    """lyrics.json → ASS 文本（预览与导出共用同一份输出，SF-009）。

    countdown_gap_ms：行间空隙超过该值时插入三点倒数（0 = 关闭，
    迁移自 lrc-ttml 的长间奏三点倒数）。
    """
    if template not in TEMPLATES:
        raise ValueError(f"未知模板 {template!r}，可选：{list(TEMPLATES)}")
    t = TEMPLATES[template]
    use_next = t.next_preview if next_preview is None else next_preview

    lines_out: list[str] = [
        "[Script Info]",
        "; Generated by SoundFlow lyrics pipeline",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res[0]}",
        f"PlayResY: {play_res[1]}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "YCbCr Matrix: TV.601",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle,"
        " Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line(t),
    ]
    if use_next:
        lines_out.append(_next_style_line(t))
    lines_out.append(_bg_style_line(t))
    lines_out.append(_agent2_style_line(t))
    lines_out.append(_translation_style_line(t))
    if countdown_gap_ms > 0:
        lines_out.append(_countdown_style_line(t))
    lines_out += ["", "[Events]",
                  "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    src = lyrics.lines
    prev_end: int | None = None
    for i, line in enumerate(src):
        if not line.words:
            continue
        start = ms_to_ass_time(line.words[0].start)
        end = ms_to_ass_time(max(line.end, line.words[-1].end))
        # 纯逐行模式：整行显示与切换（无逐字标签），下句预显走 NextLine
        if t.render == "line" and not line.is_background and line.agent != "v2":
            body = line.text or "".join(w.char for w in line.words)
            lines_out.append(f"Dialogue: 0,{start},{end},{t.style_name},,0,0,0,,{body}")
            if use_next:
                nxt = next((n for n in src[i + 1:]
                            if n.words and not n.is_background), None)
                if nxt is not None:
                    lines_out.append(
                        f"Dialogue: 1,{start},{end},NextLine,,0,0,0,,{nxt.text}"
                    )
            if line.translation:
                lines_out.append(
                    f"Dialogue: 1,{start},{end},Translation,,0,0,0,,{line.translation}"
                )
            prev_end = max(line.end, line.words[-1].end)
            continue
        body = _karaoke_text(line, t.tag)
        if line.is_background:
            lines_out.append(f"Dialogue: 0,{start},{end},Backing,,0,0,0,,{body}")
        elif line.agent == "v2":
            lines_out.append(f"Dialogue: 0,{start},{end},Agent2,,0,0,0,,{body}")
        else:
            lines_out.append(f"Dialogue: 0,{start},{end},{t.style_name},,0,0,0,,{body}")
            # 下句预显：跳过和声行，预显下一句主唱
            if use_next:
                nxt = next((n for n in src[i + 1:]
                            if n.words and not n.is_background), None)
                if nxt is not None:
                    lines_out.append(
                        f"Dialogue: 1,{start},{end},NextLine,,0,0,0,,{nxt.text}"
                    )
        if line.translation:
            lines_out.append(
                f"Dialogue: 1,{start},{end},Translation,,0,0,0,,{line.translation}"
            )
        # 长间奏三点倒数：主唱行之间（和声行不打断间奏判断）
        if countdown_gap_ms > 0 and not line.is_background:
            word_end = max(line.end, line.words[-1].end)
            if prev_end is not None and word_end - prev_end >= countdown_gap_ms:
                lines_out.extend(_countdown_events(prev_end, word_end, t))
        if not line.is_background:
            prev_end = max(line.end, line.words[-1].end) if line.words else line.end
    return "\n".join(lines_out) + "\n"


def save_ass(ass_text: str, path) -> None:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ass_text, encoding="utf-8-sig")
