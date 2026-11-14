"""LRC 歌词解析（容错：多时间戳行、增强型逐字标签、offset 全局偏移）。"""
from __future__ import annotations

import re

_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_OFFSET_TAG = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)
_WORD_TAG = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")


def _word_tag_ms(m: re.Match) -> int:
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    frac = m.group(3) or "0"
    if len(frac) == 1:
        ms = int(frac) * 100
    elif len(frac) == 2:
        ms = int(frac) * 10
    else:
        ms = int(frac)
    return (minutes * 60 + seconds) * 1000 + ms


def _to_ms(m: re.Match) -> int:
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    frac = m.group(3) or "0"
    # 两位小数 = 厘秒，三位 = 毫秒
    if len(frac) == 1:
        ms = int(frac) * 100
    elif len(frac) == 2:
        ms = int(frac) * 10
    else:
        ms = int(frac)
    return (minutes * 60 + seconds) * 1000 + ms


def looks_like_lrc(text: str) -> bool:
    """是否 LRC 格式：任一行带 [mm:ss] 时间标签。"""
    for line in text.splitlines():
        if _TIME_TAG.search(line):
            return True
    return False


def parse_lrc(text: str) -> list[dict]:
    """LRC → ``[{"start_ms", "end_ms", "text", "is_background", "agent", "translation"}]``。

    - 多时间戳行展开为多行（同一文本重复）
    - 增强型逐字标签 ``<mm:ss.xx>`` 剥离（时间轴由模型重新对齐）
    - ``[offset:±ms]`` 全局偏移：正值使歌词整体提前（时间减去 offset）
    - end = 下一行 start；最后一行 end = start + 5s
    - 无时间标签的行（元数据/空行）忽略
    - ``（和声）/（背景）`` 标记 → is_background；``[v1]/[v2]`` → agent
      （迁移自 lrc-ttml；标记从 text 剥离，只留干净显示文本）
    - 同一时间戳的紧随行（常见为翻译/音译）→ 上一行的 translation
    """
    from lyrics.dp_match import parse_line_info

    offset_ms = 0
    for m in _OFFSET_TAG.finditer(text):
        offset_ms = int(m.group(1))
        break

    rows: list[tuple[int, str, list[tuple[str, int]]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        tags = list(_TIME_TAG.finditer(line))
        if not tags:
            continue  # 元数据标签（[ti:...] 等）与纯文本行
        body = line[tags[-1].end():]
        # 增强逐字标签 <mm:ss.xx>：提取 (start_ms, 后继文本)，平台 YRC 转出的
        # 字级时间戳直接采用（不再二次对齐）
        word_marks: list[tuple[int, str]] = []
        pos = 0
        cur_start = None
        for wm in _WORD_TAG.finditer(body):
            text_before = body[pos:wm.start()]
            if cur_start is not None:
                for ch in text_before:
                    word_marks.append((cur_start, ch))
            cur_start = _word_tag_ms(wm)
            pos = wm.end()
        if cur_start is not None:
            for ch in body[pos:]:
                word_marks.append((cur_start, ch))
        content = _WORD_TAG.sub("", body).strip()
        for tag in tags:
            start = _to_ms(tag) - offset_ms
            shifted = [(s - offset_ms, ch) for s, ch in word_marks]
            rows.append((max(start, 0), content, shifted))
    rows.sort(key=lambda r: r[0])

    infos: list[dict] = []
    for start, content, word_marks in rows:
        info = parse_line_info(content, start=start)
        # 同时间戳紧随行 = 翻译/音译（lrc-ttml 同规则），不生成独立行
        if infos and infos[-1]["start_ms"] == start and not info.text:
            continue
        if infos and infos[-1]["start_ms"] == start and infos[-1]["text"]:
            prev = infos[-1]
            prev["translation"] = (prev["translation"] + " " + info.text).strip()
            continue
        # 逐字 → [(char, start_ms, end_ms)]：end = 下一字起点（末字 +300ms）
        words: list[tuple[str, int, int]] = []
        if word_marks:
            for i, (s, ch) in enumerate(word_marks):
                nxt = word_marks[i + 1][0] if i + 1 < len(word_marks) else s + 300
                words.append((ch, s, max(nxt, s + 1)))
        infos.append({"start_ms": start, "end_ms": start + 200, "text": info.text,
                      "is_background": info.is_background, "agent": info.agent,
                      "translation": "", "words": words})

    for i, info in enumerate(infos):
        end = infos[i + 1]["start_ms"] if i + 1 < len(infos) else info["start_ms"] + 5000
        info["end_ms"] = max(end, info["start_ms"] + 200)
    return infos
