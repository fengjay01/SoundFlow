# -*- coding: utf-8 -*-
"""歌词导出器：统一歌词 JSON → LRC（标准行级 / 增强逐字）。"""
from __future__ import annotations

from lyrics.schema import Lyrics


def _ms_to_lrc_tag(ms: int) -> str:
    """毫秒 → [mm:ss.xx]（厘秒）。"""
    if ms < 0:
        ms = 0
    total_cs = (ms + 5) // 10
    m, rem = divmod(total_cs, 6000)
    s, cs = divmod(rem, 100)
    return f"[{m:02d}:{s:02d}.{cs:02d}]"


def _ms_to_word_tag(ms: int) -> str:
    """毫秒 → <mm:ss.xx>（增强 LRC 逐字标签）。"""
    if ms < 0:
        ms = 0
    total_cs = (ms + 5) // 10
    m, rem = divmod(total_cs, 6000)
    s, cs = divmod(rem, 100)
    return f"<{m:02d}:{s:02d}.{cs:02d}>"


def to_lrc(lyrics: Lyrics, *, enhanced: bool = False) -> str:
    """Lyrics → LRC 文本。

    enhanced=False：标准 LRC，行级时间戳（滚动歌词 / 各播放器通用）。
    enhanced=True ：增强 LRC，行级标签后每字带 <mm:ss.xx> 逐字标签
                    （Lyricify / 歌词滚动姬等逐字渲染器可用）。
    """
    lines: list[str] = []
    for line in lyrics.lines:
        tag = _ms_to_lrc_tag(line.start)
        if enhanced and line.words:
            parts = [tag]
            for w in line.words:
                parts.append(_ms_to_word_tag(w.start))
                parts.append(w.char)
            lines.append("".join(parts))
        else:
            lines.append(tag + line.text)
    return "\n".join(lines) + "\n"


def _ms_to_srt_time(ms: int) -> str:
    """毫秒 → SRT 时间戳 HH:MM:SS,mmm。"""
    if ms < 0:
        ms = 0
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, mmm = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{mmm:03d}"


def to_srt(lyrics: Lyrics) -> str:
    """Lyrics → 行级 SRT（主歌词行；和声行合并进主行时段或独立块）。"""
    lines = [ln for ln in lyrics.lines if ln.words or ln.text.strip()]
    out: list[str] = []
    seq = 0
    for ln in lines:
        text = ln.text.strip() or "".join(w.char for w in ln.words).strip()
        if not text:
            continue
        start = ln.words[0].start if ln.words else ln.start
        end = ln.words[-1].end if ln.words else ln.end
        if end <= start:
            end = start + 500
        seq += 1
        out.append(f"{seq}")
        out.append(f"{_ms_to_srt_time(start)} --> {_ms_to_srt_time(end)}")
        out.append(text)
        out.append("")
    return "\n".join(out)


def to_krc(lyrics: Lyrics, *, offset_ms: int = 0) -> bytes:
    """Lyrics → 酷狗 KRC 逐字歌词（二进制）。

    文本结构（zlib 压缩 + 每字节 XOR 0x64 加密）::

        [offset:0]
        [start,duration]<start,duration,0>字<duration,0>字…

    KRC 行格式：行头 [行起始ms, 行时长ms]，字标签 <字起始ms, 字时长ms, 0>
    （第三字段为元音起始偏移，酷狗兼容用 0）。语言标记行可选，省略。
    """
    import zlib

    rows: list[str] = [f"[offset:{offset_ms}]"]
    for ln in lyrics.lines:
        if not ln.words:
            continue
        start = ln.words[0].start + offset_ms
        end = max(ln.words[-1].end + offset_ms, start + 1)
        dur = end - start
        parts = [f"[{start},{dur}]"]
        for w in ln.words:
            ws = max(w.start + offset_ms, start)
            we = max(w.end + offset_ms, ws + 1)
            parts.append(f"<{ws - start},{we - ws},0>{w.char}")
        rows.append("".join(parts))
    plain = "\n".join(rows).encode("utf-8")
    compressed = zlib.compress(plain, 9)
    return bytes(b ^ 0x64 for b in compressed)
