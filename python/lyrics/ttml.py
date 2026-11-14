# -*- coding: utf-8 -*-
"""逐字 TTML 导出（迁移自 lrc-ttml 的 Apple Music / AMLL 生态支持）。

输出 apple-internal 风格（对齐真实 Apple Music 样例）：
- begin/end 用 MM:SS.mmm；单 div；行级 itunes:key；无 xml:lang/tts 装饰；
- （和声）行 → ``<span ttm:role="x-bg">``；对唱 → ``ttm:agent="v1|v2"`` +
  ``<ttm:agent>`` 声明；翻译 → 行内 ``<span ttm:role="x-translation">``。

自检：``validate_ttml`` 用 ElementTree 解析并校验结构（时间格式、p/span
必备属性、逐字 span 时间落在行区间内）。AMLL 官方解析器为 AGPL，开发环境
可选验收（不捆绑），此处内置确定性结构校验。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from lyrics.schema import Lyrics

_TTML_HEAD = (
    '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
    'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
    'xmlns:tts="http://www.w3.org/ns/ttml#styling">'
)


def _fmt_time(ms: int) -> str:
    """毫秒 → Apple 风格 MM:SS.mmm（分钟不补零到两位以上）。"""
    if ms < 0:
        ms = 0
    total_s, mmm = divmod(ms, 1000)
    m, s = divmod(total_s, 60)
    return f"{m:02d}:{s:02d}.{mmm:03d}"


def build_ttml(lyrics: Lyrics, *, title: str = "", artist: str = "") -> str:
    """Lyrics → apple-internal 风格逐字 TTML 字符串。"""
    agents = sorted({ln.agent for ln in lyrics.lines if ln.agent}) or ["v1"]
    out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', _TTML_HEAD]
    out.append("  <head>")
    out.append('    <ttm:agent type="person" xml:id="v1"/>')
    if "v2" in agents:
        out.append('    <ttm:agent type="person" xml:id="v2"/>')
    meta: list[str] = []
    if artist:
        meta.append(f'      <itunes:artist>{escape(artist)}</itunes:artist>')
    if title:
        meta.append(f'      <itunes:song>{escape(title)}</itunes:song>')
    if meta:
        out.append("    <metadata>")
        out.extend(meta)
        out.append("    </metadata>")
    out.append("  </head>")
    out.append('  <body dur="' + _fmt_time(lyrics.lines[-1].end if lyrics.lines else 0) + '">')
    out.append('    <div>')
    seq = 0
    for line in lyrics.lines:
        seq += 1
        begin = _fmt_time(line.start)
        end = _fmt_time(max(line.end, line.start + 1))
        agent_attr = f' ttm:agent="{line.agent}"' if line.agent else ""
        key = f' itunes:key="L{seq}"'
        out.append(f'      <p begin="{begin}" end="{end}"{agent_attr}{key}>')
        if line.is_background:
            out.append('        <span ttm:role="x-bg">')
        for w in line.words:
            sp_begin = _fmt_time(w.start)
            sp_end = _fmt_time(max(w.end, w.start + 1))
            out.append(
                f'          <span begin="{sp_begin}" end="{sp_end}">{escape(w.char)}</span>'
            )
        if line.is_background:
            out.append("        </span>")
        if line.translation:
            out.append(
                f'        <span ttm:role="x-translation" xml:lang="zh-Hans">'
                f'{escape(line.translation)}</span>'
            )
        out.append("      </p>")
    out.append("    </div>")
    out.append("  </body>")
    out.append("</tt>")
    return "\n".join(out) + "\n"


_TIME_RE = re.compile(r"^\d{2,}:\d{2}\.\d{3}$")


def _local(tag: str) -> str:
    """ElementTree 展开名 → 本地名（TTML 有默认命名空间）。"""
    return tag.rsplit("}", 1)[-1]


def _children(elem, name: str):
    return [c for c in elem if _local(c.tag) == name]


def _iter_all(elem, name: str):
    return [c for c in elem.iter() if _local(c.tag) == name and c is not elem]


def _attr(elem, name: str) -> str | None:
    """命名空间无关的属性读取（ttm:role → {uri}role）。"""
    for k, v in elem.attrib.items():
        if _local(k) == name:
            return v
    return None


def validate_ttml(ttml_text: str) -> list[str]:
    """确定性自检：解析 + 结构规则（失败返回问题列表，空列表 = 通过）。"""
    errors: list[str] = []
    try:
        root = ET.fromstring(ttml_text)
    except ET.ParseError as exc:
        return [f"XML 解析失败: {exc}"]
    if _local(root.tag) != "tt":
        errors.append("根元素不是 tt")
    bodies = _children(root, "body")
    if not bodies:
        errors.append("缺少 body")
        return errors
    body = bodies[0]
    divs = _children(body, "div")
    if len(divs) != 1:
        errors.append(f"apple-internal 要求单 div，实际 {len(divs)}")
    p_count = 0
    for p in _iter_all(body, "p"):
        p_count += 1
        begin, end = p.get("begin"), p.get("end")
        if not begin or not _TIME_RE.match(begin):
            errors.append(f"p#{p_count}: begin 非法 ({begin})")
        if not end or not _TIME_RE.match(end):
            errors.append(f"p#{p_count}: end 非法 ({end})")
        # itunes: 是命名空间前缀，ET 存成 {uri}key
        if not any(_local(k) == "key" for k in p.attrib):
            errors.append(f"p#{p_count}: 缺 itunes:key")
        spans = [s for s in p.iter() if _local(s.tag) == "span"]
        if not spans:
            errors.append(f"p#{p_count}: 无逐字 span")
            continue
        for s in spans:
            role = _attr(s, "role")
            if role in ("x-bg", "x-translation"):
                continue  # 容器/翻译行不要求逐字时间（子 span 单独检查）
            if not s.get("begin") or not s.get("end"):
                errors.append(f"p#{p_count}: 字级 span 缺 begin/end")
    return errors


def _has_kanji(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf" for ch in text)


def _reading_map(text: str):
    """日文文本 → [(base, reading), ...]（pykakasi 汉字→假名，词级）。

    假名/拉丁段原样保留（base=reading），无需注音。
    返回 None 表示 pykakasi 不可用。
    """
    try:
        import pykakasi
    except ImportError:
        return None
    kks = pykakasi.kakasi()
    out: list[tuple[str, str]] = []
    for item in kks.convert(text):
        base, reading = item["orig"], item["hira"] or item["orig"]
        if base:
            out.append((base, reading))
    return out


def build_ttml_furigana(lyrics: Lyrics, *, title: str = "", artist: str = "",
                        lang: str = "ja") -> str:
    """Lyrics → 带假名注音（TTML2 ruby）的 TTML。

    含汉字的行逐词包 ``<tts:ruby>``（base=汉字 / text=平假名读音），
    假名与拉丁段原样。时间轴结构与 build_ttml 一致（逐字 span 保留）。
    """
    reading_of = _reading_map if _reading_map is not None else None
    out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', _TTML_HEAD]
    out.append("  <head>")
    out.append('    <ttm:agent type="person" xml:id="v1"/>')
    meta: list[str] = []
    if artist:
        meta.append(f'      <itunes:artist>{escape(artist)}</itunes:artist>')
    if title:
        meta.append(f'      <itunes:song>{escape(title)}</itunes:song>')
    if meta:
        out.append("    <metadata>")
        out.extend(meta)
        out.append("    </metadata>")
    out.append("  </head>")
    out.append('  <body dur="' + _fmt_time(lyrics.lines[-1].end if lyrics.lines else 0) + '">')
    out.append('    <div>')
    seq = 0
    for line in lyrics.lines:
        seq += 1
        begin = _fmt_time(line.start)
        end = _fmt_time(max(line.end, line.start + 1))
        out.append(f'      <p begin="{begin}" end="{end}" xml:lang="{lang}" itunes:key="L{seq}">')
        if line.is_background:
            out.append('        <span ttm:role="x-bg">')
        if reading_of is None:
            # pykakasi 缺失：整行原样，不注音
            for w in line.words:
                sp_begin = _fmt_time(w.start)
                sp_end = _fmt_time(max(w.end, w.start + 1))
                out.append(
                    f'          <span begin="{sp_begin}" end="{sp_end}">{escape(w.char)}</span>')
        else:
            base = "".join(w.char for w in line.words) or line.text
            pairs = reading_of(base) or [(base, base)]
            # 逐字时间均分到 ruby 词（与整词演唱节奏对齐）
            total_chars = sum(len(b) for b, _ in pairs) or 1
            char_times: list[tuple[int, int]] = []
            for w in line.words:
                char_times.append((w.start, w.end))
            if not char_times:
                char_times = [(line.start, line.end)]
            span_start = char_times[0][0]
            span_end = char_times[-1][1]
            cursor = span_start
            total_ms = max(span_end - span_start, total_chars * 20)
            pos = 0
            for b, r in pairs:
                n = max(len(b), 1)
                ws = cursor
                we = cursor + int(total_ms * n / total_chars)
                cursor = we
                pos += n
                rb = escape(b)
                rt = escape(r)
                out.append(
                    f'          <tts:ruby tts:rubyPosition="over">'
                    f'<tts:rubyBase><span>{rb}</span></tts:rubyBase>'
                    f'<tts:rubyText>{rt}</tts:rubyText></tts:ruby>')
        if line.is_background:
            out.append("        </span>")
        if line.translation:
            out.append(
                f'        <span ttm:role="x-translation" xml:lang="zh-Hans">'
                f'{escape(line.translation)}</span>'
            )
        out.append("      </p>")
    out.append("    </div>")
    out.append("  </body>")
    out.append("</tt>")
    return "\n".join(out) + "\n"
