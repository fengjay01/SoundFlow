# -*- coding: utf-8 -*-
"""DP 歌词映射器：把 ASR 词级时间戳通过动态规划强制对齐到用户导入的歌词上。

算法移植自 lrc-ttml（https://github.com/6hdj/lrc-ttml，MIT License，
Copyright (c) 2026 HXJ）的 matcher.py，按本项目 schema（整数毫秒、
WORD_TOKEN_RE 单一事实来源）改写。相对 difflib.SequenceMatcher 的关键优势：

- 相似度矩阵而非二元匹配：拼音同音字 0.85、错字 0.4、跨语种 -0.2——
  负分让 DP 把幻觉词（如"优优独播剧场"）自然丢弃；
- 早唱偏置 EARLY_BONUS：同分时优先更早的识别词，治 whisper 复读整句
  对齐到第一遍的问题；
- min_match_score 阈值过滤：低于阈值的配对视为未匹配，交给插值；
- "借时间"插值：未匹配单元空间不足时从相邻锚点借时间（保最小时长）；
- _trustworthy 回退：识别结果不可信时按字数比例均分行窗，宁缺毋滥。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lyrics.schema import WORD_TOKEN_RE

try:  # 可选依赖：缺省时拼音同音匹配自动降级
    from pypinyin import lazy_pinyin
except ImportError:  # pragma: no cover
    lazy_pinyin = None

GAP_OPEN = -0.5          # 线性 gap 罚分（跳过 ASR 词 / 歌词字无对应识别词）
EARLY_BONUS = 1e-4       # 早唱偏置：同分时优先更早出现的识别词
MIN_MATCH_SCORE = 0.2    # 低于该相似度的配对视为未匹配
MIN_UNIT_DURATION = 60   # 单元最小时长 ms
MATCH_RATE_FLOOR = 0.35  # 命中率低于此值 → 识别不可信
SIM_FLOOR_SHORT = 0.45   # 短行（span ≤ 3.5s）平均相似度下限
ASR_BLOAT_RATIO = 2.5    # 识别词数 > 歌词单元数 × 该值 → whisper 编词特征

_HAN_STARTS = ("（", "(")
_HAN_ENDS = ("）", ")")


def _py(char: str) -> str:
    if lazy_pinyin is None:
        return ""
    parts = lazy_pinyin(char, errors="ignore")
    return parts[0] if parts else ""


def _py_initial_final(py: str) -> tuple[str, str]:
    for initial in ("zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
                    "g", "k", "h", "j", "q", "x", "r", "y", "w", "s", "z"):
        if py.startswith(initial):
            return initial, py[len(initial):]
    return "", py


def is_cjk(ch: str) -> bool:
    o = ord(ch[0]) if ch else 0
    return (0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
            or 0xF900 <= o <= 0xFAFF or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF)


@dataclass
class Unit:
    """歌词对齐单元：中文单字 / 英文单词（与 WORD_TOKEN_RE 语义一致）。"""
    text: str
    line: int                 # 行号（rows 下标）
    index: int                # 行内序号
    start: int = 0            # ms
    end: int = 0              # ms
    prob: float = 0.0
    matched: bool = False


@dataclass
class AsrUnit:
    text: str
    start: int
    end: int
    prob: float = 1.0


@dataclass
class LineInfo:
    """一行导入歌词的解析结果（标记已剥离，text 为干净显示文本）。"""
    text: str
    start: int = 0
    end: int = 0
    is_background: bool = False
    agent: str = ""
    translation: str = ""


_BG_MARK_RE = re.compile(r"[（(]\s*(和声|背景|伴唱|backing)\s*[）)]")
_AGENT_RE = re.compile(r"^\s*\[(v[12]|主唱[12]?|合唱)\]\s*", re.IGNORECASE)
_AGENT_MAP = {"v1": "v1", "v2": "v2", "主唱": "v1", "主唱1": "v1", "主唱2": "v2",
              "合唱": "v1"}


def parse_line_info(raw: str, start: int = 0, end: int = 0) -> LineInfo:
    """剥离 （和声）/（背景） 与 [v1]/[v2] 标记 → LineInfo（标记不进 text）。"""
    text = raw.strip()
    is_bg = False
    agent = ""
    if _BG_MARK_RE.search(text):
        is_bg = True
        text = _BG_MARK_RE.sub(" ", text)
    m = _AGENT_RE.match(text)
    if m:
        agent = _AGENT_MAP.get(m.group(1).lower(), "v1")
        text = text[m.end():]
    return LineInfo(text=text.strip(), start=start, end=end,
                    is_background=is_bg, agent=agent)


def scan_units(infos) -> list[Unit]:
    """行列表 → 对齐单元序列（token 与 WORD_TOKEN_RE 完全一致）。

    infos 元素可以是 LineInfo 或带 ``text`` 键的 dict（LRC rows）。
    """
    units: list[Unit] = []
    for li, info in enumerate(infos):
        text = info["text"] if isinstance(info, dict) else info.text
        for mi, m in enumerate(WORD_TOKEN_RE.finditer(text)):
            units.append(Unit(text=m.group(), line=li, index=mi))
    return units


def split_asr_word(word: str) -> list[str]:
    """ASR 词（如 "OK啦"）拆成 ["OK", "啦"]：CJK 逐字，拉丁/数字/撇号成段。"""
    out: list[str] = []
    buf = ""
    for ch in word:
        if is_cjk(ch):
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def expand_asr_units(words: list[tuple[str, int, int, float]]) -> list[AsrUnit]:
    """(text, start_ms, end_ms, prob) 列表 → AsrUnit 列表（混拆 + 时长按字符数加权）。"""
    out: list[AsrUnit] = []
    for text, start, end, prob in words:
        parts = split_asr_word(text)
        if not parts:
            continue
        weights = [max(1, len(p)) for p in parts]
        total_w = sum(weights)
        span = max(end - start, len(parts))
        cursor = start
        for k, (p, w) in enumerate(zip(parts, weights)):
            if k == len(parts) - 1:
                dur = end - cursor  # 余数归末段，保证 end 精确
            else:
                dur = int(span * w / total_w)
            out.append(AsrUnit(p, cursor, cursor + dur, prob))
            cursor += dur
    return out


def similarity(t: str, s: str) -> float:
    """配对得分：[−0.2, 1.0]，负分让 DP 丢弃幻觉词。"""
    if t == s:
        return 1.0
    if t.lower() == s.lower():
        return 0.97
    t_cjk, s_cjk = is_cjk(t), is_cjk(s)
    if t_cjk and s_cjk:
        tp, sp = _py(t), _py(s)
        if tp and sp:
            if tp == sp:
                return 0.85  # 同音字：识别/用字差异，位置有效
            ti, tf = _py_initial_final(tp)
            si, sf = _py_initial_final(sp)
            if (ti and ti == si) or (tf and tf == sf):
                return 0.5
        return 0.4
    if t_cjk != s_cjk:
        return -0.2  # 跨语种：让 DP 选择丢弃而非硬贴
    # 拉丁词：包含 / 部分字符重合
    a, b = t.lower(), s.lower()
    if a in b or b in a:
        return 0.6
    common = sum(1 for ca, cb in zip(a, b) if ca == cb)
    n = max(len(a), len(b), 1)
    if common:
        return 0.3 + 0.4 * common / n
    return -0.1


def align_sequences(targets: list[Unit], sources: list[AsrUnit],
                    lo: int = 0, hi: int = 0) -> dict[int, int]:
    """Needleman–Wunsch 全局对齐（线性 gap 罚分 + 早唱偏置）。

    返回 {targets 下标: sources 下标}。lo/hi 为早唱偏置的分母基准（窗口宽度）。
    """
    n, m = len(targets), len(sources)
    if n == 0 or m == 0:
        return {}
    width = max(hi - lo, m, 1)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = GAP_OPEN * i
    for j in range(1, m + 1):
        dp[0][j] = GAP_OPEN * j
    for i in range(1, n + 1):
        ti = targets[i - 1].text
        for j in range(1, m + 1):
            su = sources[j - 1]
            score = similarity(ti, su.text) + EARLY_BONUS * (width - (j - 1)) / width
            dp[i][j] = max(
                dp[i - 1][j - 1] + score,   # 匹配
                dp[i - 1][j] + GAP_OPEN,    # 跳过歌词单元
                dp[i][j - 1] + GAP_OPEN,    # 跳过识别词
            )
    mapping: dict[int, int] = {}
    i, j = n, m
    while i > 0 and j > 0:
        ti = targets[i - 1].text
        su = sources[j - 1]
        score = similarity(ti, su.text) + EARLY_BONUS * (width - (j - 1)) / width
        cur = dp[i][j]
        if abs(cur - (dp[i - 1][j - 1] + score)) < 1e-9:
            mapping[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif abs(cur - (dp[i - 1][j] + GAP_OPEN)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return mapping


def interpolate(units: list[Unit], matched: dict[int, int],
                sources: list[AsrUnit], lo: int, hi: int) -> None:
    """给未匹配单元填时间：锚点直接赋值，空洞居中插值，不足时向锚点借时间。"""
    for idx, j in matched.items():
        su = sources[j]
        units[idx].start, units[idx].end, units[idx].prob = su.start, su.end, su.prob
        units[idx].matched = True
    # 阈值过滤：低分配对视为未匹配，防止 DP 总分驱动下"贴"到幻觉词
    for idx in list(matched):
        u = units[idx]
        if similarity(u.text, sources[matched[idx]].text) < MIN_MATCH_SCORE:
            u.matched = False
    idxs = sorted(matched)
    anchors = [i for i in idxs if units[i].matched]
    if not anchors:
        _proportional(units, lo, hi)
        return
    first, last = anchors[0], anchors[-1]
    # 头部
    if first > 0:
        bound = max(lo, units[first].start - 600)
        _spread(units, 0, first, bound, units[first].start)
    # 中部
    for a, b in zip(anchors, anchors[1:]):
        _spread(units, a + 1, b, units[a].end, units[b].start)
    # 尾部
    if last < len(units) - 1:
        bound = min(hi, units[last].end + 600) if hi > units[last].end else units[last].end + 5000
        _spread(units, last + 1, len(units), units[last].end, bound)
    _ensure_min_duration(units)


def _spread(units: list[Unit], a: int, b: int, lo: int, hi: int) -> None:
    """units[a:b] 均匀摆放在 [lo, hi]（空间不足时向两侧锚点借时间）。"""
    gap = b - a
    if gap <= 0:
        return
    span = hi - lo
    need = gap * MIN_UNIT_DURATION + 50
    if span < need:
        borrow = min(need - span, max(0, lo))
        lo -= borrow // 2
        hi += borrow - borrow // 2
        span = hi - lo
    step = span / gap
    for k, i in enumerate(range(a, b)):
        units[i].start = int(lo + k * step)
        units[i].end = int(lo + (k + 1) * step)
        units[i].prob = 0.1


def _proportional(units: list[Unit], lo: int, hi: int) -> None:
    """无锚点：按字数比例均分窗口（低置信度标记）。"""
    _spread(units, 0, len(units), lo, hi)
    for u in units:
        u.prob = 0.15


def _ensure_min_duration(units: list[Unit]) -> None:
    for u in units:
        if u.end - u.start < MIN_UNIT_DURATION:
            u.end = u.start + MIN_UNIT_DURATION


def trustworthy(matched_count: int, total_units: int, sources: list[AsrUnit],
                avg_sim: float, span_ms: int) -> bool:
    """识别结果是否可信：不可信时调用方应放弃 ASR、按窗口比例均分。"""
    if total_units == 0:
        return False
    if matched_count / total_units < MATCH_RATE_FLOOR:
        return False
    if span_ms <= 3500 and avg_sim < SIM_FLOOR_SHORT:
        return False
    if len(sources) > total_units * ASR_BLOAT_RATIO:
        return False
    return True
