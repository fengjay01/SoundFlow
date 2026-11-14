# -*- coding: utf-8 -*-
"""导入歌词的模型对齐（时间轴由模型给出，歌词文本以用户导入为准）。

主流方案（与 ELYSY / Karaoke-sync / whisperx-karaoke / lyric-sync / lrc-ttml 同思路）：
- LRC（行级时间戳）：按行时间窗切片逐片转写（歌词作 initial_prompt），
  再用 DP 强制对齐把识别词映射到歌词单元（lyrics.dp_match）；
- 纯文本：全曲 ASR（歌词作提示词）+ DP 全局对齐，识别不可信时回退比例均摊；
- 自动和声（实验，默认关）：DP 后剩余未匹配识别词聚成背景行。
"""
from __future__ import annotations

import os
from pathlib import Path

import difflib
from typing import Optional

from lyrics.lrc import looks_like_lrc, parse_lrc
from lyrics.schema import (Line, Lyrics, Word, realign_words, validate,
                           WORD_TOKEN_RE, tokenize_pronounceable)


class ImportLyricsError(ValueError):
    """导入歌词内容不可用（空文件 / 无有效行）。"""


def split_text_lines(text: str) -> list[str]:
    """纯文本歌词 → 非空行列表（剥离时间轴残留与空行）。"""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# 旧版兼容 API（difflib 行级匹配，保留给既有测试与降级路径使用）
# ---------------------------------------------------------------------------

def _normalize(tok: str) -> str:
    return tok.lower()


def match_line_timings(
    lines: list[str],
    asr_words: list[tuple[str, int, int]],
) -> list[Optional[tuple[int, int, bool]]]:
    """把导入行映射到 ASR 词窗上，返回每行 ``(start_ms, end_ms, matched)``。

    全局 SequenceMatcher 对齐「导入 token 流」与「ASR token 流」（忽略大小写），
    replace 块内按比例映射。主路径已被 dp_match 取代，此实现保留为降级路径。
    """
    a_tokens: list[tuple[int, str]] = []  # (行号, token)
    for li, line in enumerate(lines):
        for tok in tokenize_pronounceable(line):
            a_tokens.append((li, _normalize(tok)))
    b_tokens = [_normalize(tok) for tok, _s, _e in asr_words]

    b_by_a: dict[int, int] = {}
    if a_tokens and b_tokens:
        sm = difflib.SequenceMatcher(None, [t for _li, t in a_tokens], b_tokens,
                                     autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("equal", "replace"):
                span_a = max(i2 - i1, 1)
                span_b = max(j2 - j1, 1)
                for i in range(i1, i2):
                    j = j1 + round((i - i1) * span_b / span_a)
                    b_by_a[i] = min(j, len(b_tokens) - 1)

    bounds = [0]
    counts = [len(tokenize_pronounceable(line)) for line in lines]
    for count in counts:
        bounds.append(bounds[-1] + count)

    timings: list[Optional[tuple[int, int, bool]]] = []
    for li, line in enumerate(lines):
        mapped = [b_by_a[i] for i in range(bounds[li], bounds[li + 1]) if i in b_by_a]
        if mapped:
            start = min(asr_words[j][1] for j in mapped)
            end = max(asr_words[j][2] for j in mapped)
            timings.append((start, max(end, start + 1), True))
        else:
            timings.append(None)
    return timings


def _interpolate_gaps(
    lines: list[str],
    timings: list[Optional[tuple[int, int, bool]]],
) -> list[tuple[int, int, bool]]:
    """给未匹配的行插值时间轴：夹在相邻已匹配行之间，首尾外推。"""
    out = list(timings)
    matched_idx = [i for i, t in enumerate(out) if t is not None]
    if not matched_idx:
        span = 3000 * len(lines)
        step = span / max(len(lines), 1)
        for i, _line in enumerate(lines):
            out[i] = (int(i * step), int((i + 1) * step), False)
        return out

    first, last = matched_idx[0], matched_idx[-1]
    for i in range(0, first):
        nxt = out[i + 1] if out[i + 1] is not None else (3000, 3000, False)
        start = max(nxt[0] - 3000, 0)
        out[i] = (start, max(nxt[0] - 100, start + 200), False)
    for i in range(last + 1, len(lines)):
        prev = out[i - 1]
        start = prev[1] + 200
        out[i] = (start, max(start + 3000, prev[1] + 3200), False)
    for i in range(len(lines)):
        if out[i] is not None:
            continue
        prev_i = max(j for j in range(i) if out[j] is not None)
        next_i = min(j for j in range(i + 1, len(lines)) if out[j] is not None)
        lo = out[prev_i][1]
        hi = out[next_i][0]
        gaps = next_i - prev_i
        step = max(hi - lo, gaps * 200) / gaps
        start = lo + (i - prev_i) * step
        end = lo + (i - prev_i + 1) * step
        out[i] = (int(start), int(max(end, start + 200)), False)

    prev_end = 0
    for i, (s, e, m) in enumerate(out):
        if s < prev_end:
            s = prev_end
        if e < s + 200:
            e = s + 200
        out[i] = (s, e, m)
        prev_end = e
    return out


def build_lyrics_from_timings(
    lines: list[str],
    timings: list[tuple[int, int, bool]],
    source: str,
    model_info: str = "imported-text+asr-match",
    language: str = "zh",
) -> Lyrics:
    """行时间轴 → Lyrics（行内逐字按 token 数均摊，匹配行置信度 0.6）。"""
    ly_lines: list[Line] = []
    for i, line in enumerate(lines):
        start_ms, end_ms, matched = timings[i]
        toks = tokenize_pronounceable(line)
        span = max(end_ms - start_ms, len(toks))
        step = span / max(len(toks), 1)
        words = [
            Word(tok, int(start_ms + k * step), int(start_ms + (k + 1) * step),
                 0.6 if matched else 0.1)
            for k, tok in enumerate(toks)
        ]
        ly_lines.append(Line(id=f"L{i + 1}", text=line,
                             start=start_ms, end=max(end_ms, start_ms + 1), words=words))
    ly = Lyrics(version=1, language=language, source=source,
                alignment_model=model_info, lines=ly_lines)
    validate(ly)
    return ly


# ---------------------------------------------------------------------------
# DP 主路径（迁移自 lrc-ttml matcher.py）
# ---------------------------------------------------------------------------

def _clamp_line_words(ly: "Lyrics") -> None:
    """最终防线：行窗越界的标点 word 丢弃、越界的其余 word 钳回行窗。

    whisper DP/各对齐路径的行边界与字边界偶有 1-2 帧漂移（哼唱/呼吸段），
    直接 validate 会因单个标点 word 越界否掉整份正确结果——先自愈再校验。
    """
    from lyrics.schema import is_punct

    for ln in ly.lines:
        kept: list[Word] = []
        for w in ln.words:
            if w.end <= ln.start or w.start >= ln.end:
                # 完全在行窗外：标点/无发音内容的丢弃，其余钳到行首
                if is_punct(w.char) or not tokenize_pronounceable(w.char):
                    continue
                w.start, w.end = ln.start, min(ln.start + 80, ln.end)
                kept.append(w)
                continue
            w.start = max(w.start, ln.start)
            w.end = min(max(w.end, w.start + 1), ln.end)
            kept.append(w)
        if kept:
            ln.words = kept  # 只修正时间；text 原文保留（分词一致性不受时间钳制影响）


def _paraformer_timestamps(wav: str, models_root: Optional[str] = None) -> list[tuple[int, int]]:
    """FunASR Paraformer 全曲转写 → 发声字时间戳列表 [(start_ms, end_ms)]。

    MODELSCOPE_CACHE 强制指向模型库内的 alignment/funasr（严格本地加载）。
    """
    # 分类布局 <root>/alignment/funasr 优先，旧布局 l2t/funasr 兜底
    base = Path(models_root or ".")
    cache_root = None
    if models_root:
        for cand in (base / "alignment" / "funasr", base / "l2t" / "funasr"):
            if cand.is_dir():
                cache_root = cand
                break
        if cache_root is None:
            cache_root = base / "alignment" / "funasr"
    if cache_root is not None:
        # 模型库是唯一权威位置：dev-env.mjs 会用 force() 注入模型类缓存，
        # setdefault 盖不过它，会把 FunASR 引到 data/models 之外并触发联网下载。
        os.environ["MODELSCOPE_CACHE"] = str(cache_root)
    # 严格本地加载：预检 FunASR 缓存（Paraformer + FSMN-VAD），缺失明确报错
    models_dir = (cache_root or Path(
        os.environ.get("MODELSCOPE_CACHE", ""))) / "models"
    need = ["iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch"]
    missing = [n for n in need if not (models_dir / n).is_dir()]
    if missing:
        raise ImportError(
            "Paraformer 模型未下载。请到「模型管理」页下载条目「paraformer-zh」后重试"
            "（工作台只从本地加载模型，不会联网拉取）")
    from funasr import AutoModel

    model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad",
                      disable_update=True, disable_pbar=True)
    res = model.generate(input=wav)[0]
    ts = res.get("timestamp") or []
    return [(int(a), int(b)) for a, b in ts]


def _align_lrc_with_paraformer(wav: str, rows: list[dict], pf_ts: list[tuple[int, int]],
                               *, source: str, language: Optional[str]) -> "Lyrics":
    """LRC 行锚 + Paraformer 发声字时间密度 → 行内字级插值（无时长限制）。

    不依赖转写文本正确性：只用 Paraformer 的发声时间密度分布，把每行的
    N 个字锚定到该行实际演唱区间的发声时间上。
    """
    ly_lines: list[Line] = []
    seq = 0
    mids = [(a + b) // 2 for a, b in pf_ts]
    for row in rows:
        text = str(row["text"])
        toks = tokenize_pronounceable(text)
        if not toks:
            continue
        start = int(row["start_ms"])
        end = int(max(row.get("end_ms") or 0, start + 500))
        # 行窗内的发声字中点（实际演唱密度）
        mids_in = [m for m in mids if start <= m <= end]
        n = len(toks)
        if len(mids_in) >= 2:
            # 发声时间分位数 → 每字锚点（密度跟随实际演唱）
            anchors = [mids_in[int(k * (len(mids_in) - 1) / max(n - 1, 1))]
                       if n > 1 else mids_in[len(mids_in) // 2]
                       for k in range(n)]
        else:
            # 行窗内无发声采样（罕见）：行内均匀分布
            step = (end - start) / max(n, 1)
            anchors = [start + k * step for k in range(n)]
        words: list[Word] = []
        for k, ch in enumerate(toks):
            a = int(anchors[k])
            b = int(anchors[k + 1]) if k + 1 < n else min(end, a + 200)
            words.append(Word(ch, a, max(b, a + 40), 0.85))
        fixed = realign_words(words, text)
        if not fixed:
            continue
        seq += 1
        ly_lines.append(Line(id=f"L{seq}", text=text,
                             start=int(row["start_ms"]),
                             end=max(int(row.get("end_ms") or 0), fixed[-1].end),
                             words=fixed))
    if not ly_lines:
        raise ImportError("Paraformer 对齐没有产出任何歌词行")
    ly = Lyrics(version=1, language=language or "zh", source=source,
                alignment_model="imported-lrc+paraformer", lines=ly_lines)
    try:
        validate(ly)
    except ValueError:
        _clamp_line_words(ly)
        validate(ly)
    return ly


def _qwen_repo(engine: str) -> tuple[str, str, bool]:
    """引擎名归一化：qwen-ultra / qwen-ultra-head → ("qwen", Ultra 仓库, use_head)。

    返回 (归一化引擎, ForcedAligner 仓库 id, 是否启用 Local Head 精修)。
    Ultra 与官方版共用 qwen-asr 运行时，仅 checkpoint 不同；Ultra+Head 额外
    启用 companion LocalBoundaryAligner（local_head/ 边界精修头）。
    """
    from lyrics.qwen_align import MODEL_ID, ULTRA_MODEL_ID

    if engine == "qwen-ultra":
        return "qwen", ULTRA_MODEL_ID, False
    if engine == "qwen-ultra-head":
        return "qwen", ULTRA_MODEL_ID, True
    return engine, MODEL_ID, False


def align_imported(
    wav: str,
    lyrics_text: str,
    fmt: str,
    *,
    source: str,
    model_size: str = "small",
    language: Optional[str] = None,
    device: str = "cpu",
    compute_type: str = "int8",
    models_root: Optional[str] = None,
    auto_harmony: bool = False,
    engine: str = "whisper",
    notes: Optional[list] = None,
    progress=None,
) -> Lyrics:
    """导入歌词 → 模型对齐 → Lyrics。fmt: 'lrc' | 'text'。

    engine: 'whisper'（默认，强制 whisperx+DP）| 'tifa'（配置了 TIFA 外部环境
    时优先 TIFA，失败自动回退 whisperx）| 'mms'（Meta MMS-FA 多语言强制对齐，
    歌声场景实测显著优于 WhisperX）。

    notes: 可选列表，引擎级失败原因会 append 进去（供上层日志透出）。
    """
    if engine == "stars":
        raise ImportError("STARS 引擎正在接入开发中，请先用 Qwen3-FA / Paraformer / MMS 引擎")
    if fmt == "lrc" or (fmt not in ("text",) and looks_like_lrc(lyrics_text)):
        return _align_with_lrc_timestamps(
            wav, lyrics_text, source=source, model_size=model_size,
            language=language,
            device=device, compute_type=compute_type, models_root=models_root,
            auto_harmony=auto_harmony, engine=engine, notes=notes, progress=progress)
    if engine == "mms":
        return _align_with_text_mms(
            wav, lyrics_text, source=source, models_root=models_root,
            auto_harmony=auto_harmony)
    qwen_engine, qwen_repo, qwen_head = _qwen_repo(engine)
    if qwen_engine == "qwen":
        skeleton = None
        skeleton_ly = None
        try:
            # 超长预判：>290s 时先取 Whisper DP 行级骨架（whisper 转写无时长限制），
            # 供 Qwen3 分段行窗精对齐（破除模型单次 300s 架构限制）
            from lyrics.qwen_align import load_audio as _qload, MAX_SECONDS as _QMAX
            _dur = len(_qload(wav)) / 16000
            use_locator = str(model_size or "").strip() not in ("", "none", "off")
            if not use_locator and _dur > _QMAX:
                # 用户要求：未选识别模型且超长 → 直接报错，绝不自动兜底
                raise ImportError(
                    f"音频 {_dur:.0f}s 超 Qwen3-FA 单次上限（290s）且未选择识别模型。"
                    "请在「识别模型」选择一个识别模型用于定位每行，或改用「导入 LRC」模式")
            if use_locator:
                if notes is not None:
                    notes.append(f"已选识别模型 {model_size}：先定位每行，再分段逐字精对齐")
                if str(model_size) == "qwen3-asr":
                    # 识别模型选了 Qwen3-ASR-1.7B：行骨架也用它（全程无 Whisper）
                    if notes is not None:
                        notes.append(
                            f"启用 Qwen3-ASR-1.7B 行骨架 + Qwen3 分段精对齐")
                    from lyrics.qwen_align import transcribe_qwen as _tq
                    from lyrics.qwen_align import _locate_rows as _lr
                    from lyrics.qwen_align import _fill_anchors as _fill
                    _tw = _tq(wav, language or "zh", models_root=models_root)
                    _rows_txt = [str(t).strip() for t in lyrics_text.splitlines()
                                 if str(t).strip()]
                    _loc = _lr(_tw, [{"text": t} for t in _rows_txt])
                    # 锚点补全：转写↔歌词模糊匹配失败的行插值兜底，一行不丢
                    _anchors: list = [None] * len(_rows_txt)
                    for k, r in enumerate(_loc[:len(_rows_txt)]):
                        s0, e0 = int(r.get("start_ms") or 0), int(r.get("end_ms") or 0)
                        if e0 > s0 and s0 >= 0:
                            _anchors[k] = (s0, e0)
                    skeleton = [(r["text"], r["start_ms"], r["end_ms"])
                                for r in _fill(_rows_txt, _anchors)]
                else:
                    if notes is not None:
                        notes.append("启用 Whisper 行骨架 + Qwen3 分段精对齐")
                    skeleton_ly = _align_with_text(
                        wav, lyrics_text, source=source, model_size=model_size or "small",
                        language=language, device=device, compute_type=compute_type,
                        models_root=models_root, auto_harmony=False, engine="whisper")
                    skeleton = [(l.text, l.start, l.end) for l in skeleton_ly.lines]
            return _align_with_text_qwen(
                wav, lyrics_text, source=source, language=language,
                models_root=models_root, auto_harmony=auto_harmony, notes=notes,
                whisper_skeleton=skeleton, progress=progress, repo_id=qwen_repo,
                use_head=qwen_head)
        except ImportError as exc:
            # 用户要求：选择了 Qwen3 就全程 Qwen3，失败明确报错不静默换模型
            raise ImportError(f"Qwen3 对齐失败：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ImportError(f"Qwen3 对齐失败：{exc}") from exc
    return _align_with_text(
        wav, lyrics_text, source=source, model_size=model_size, language=language,
        device=device, compute_type=compute_type, models_root=models_root,
        auto_harmony=auto_harmony, engine=engine, notes=notes)


def _require_whisperx():
    try:
        import whisperx  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "whisperx 未安装。请先: python -m pip install --break-system-packages whisperx"
        ) from e
    return whisperx


def _asr_word_tuples(raw: dict) -> list[tuple[str, int, int, float]]:
    """whisperx 对齐结果 → (text, start_ms, end_ms, prob) 全局序列。"""
    from lyrics.asr import _chars_to_words

    out: list[tuple[str, int, int, float]] = []
    for seg in raw.get("segments", []):
        chars = seg.get("chars") or []
        if not chars:
            continue
        for w in _chars_to_words(chars):
            out.append((w.char, w.start, w.end, w.confidence))
    return out


def _unit_word(unit, line_start: int, line_end: int, last_end: int) -> Word:
    """对齐单元 → schema.Word（钳入行区间，保证单调）。"""
    start = max(unit.start, line_start, last_end)
    end = max(min(unit.end, line_end), start + 1)
    conf = min(max(unit.prob, 0.0), 1.0) if unit.matched else max(unit.prob, 0.1)
    return Word(unit.text, int(start), int(end), round(min(max(conf, 0.0), 1.0), 3))


def _harmony_lines_from_leftovers(asr_units, used_ids: set[int],
                                  duration_ms: int) -> list[Line]:
    """自动和声（实验）：未被任何歌词行使用的长 ASR 段聚成背景行。"""
    leftovers = [u for u in asr_units if id(u) not in used_ids]
    if not leftovers:
        return []
    groups: list[list] = []
    for u in leftovers:
        if groups and u.start - groups[-1][-1].end < 1200:
            groups[-1].append(u)
        else:
            groups.append([u])
    lines: list[Line] = []
    seq = 0
    for g in groups:
        if g[-1].end - g[0].start < 800:
            continue
        text = "".join(u.text for u in g)
        if not tokenize_pronounceable(text):
            continue
        seq += 1
        start = max(g[0].start, 0)
        end = min(g[-1].end, max(duration_ms, start + 1))
        words = []
        last = start
        for u in g:
            w = _unit_word_from_asr(u, start, end, last)
            words.append(w)
            last = w.end
        words = realign_words(words, text)
        lines.append(Line(id=f"B{seq}", text=text,
                          start=start, end=max(end, start + 1), words=words,
                          is_background=True))
    return lines


def _unit_word_from_asr(u, line_start: int, line_end: int, last_end: int) -> Word:
    start = max(u.start, line_start, last_end)
    end = max(min(u.end, line_end), start + 1)
    return Word(u.text, int(start), int(end),
                round(min(max(u.prob, 0.0), 1.0), 3))


def _lyrics_from_platform_words(rows: list[dict], *, source: str,
                                language: str) -> Lyrics:
    """平台逐字歌词（YRC 等，row['words']=[(char, start, end)]）→ Lyrics。

    字级时间人工校对，直接采用；行级 start/end 取首末字，校验保证一致。
    """
    ly_lines: list[Line] = []
    seq = 0
    for row in rows:
        if row.get("is_background") and not row.get("words"):
            continue
        start = int(row["start_ms"])
        end = int(max(row["end_ms"], start + 200))
        raw_words = row.get("words") or []
        if not raw_words:
            continue
        fixed: list[Word] = []
        last = start
        for ch, s, e in raw_words:
            s = max(int(s), start, last)
            e = max(int(e), s + 1)
            fixed.append(Word(ch, s, e, 1.0))  # 平台人工校对，置信度满
            last = e
        # 一致性自愈（text 与 words 边界差异，YRC 转换不应产生但防御）
        fixed = realign_words(fixed, str(row["text"]))
        if not fixed:
            continue
        seq += 1
        ly_lines.append(Line(
            id=f"L{seq}",
            text=str(row["text"]),
            start=start, end=max(end, fixed[-1].end),
            words=fixed,
            is_background=bool(row.get("is_background")),
            agent=str(row.get("agent", "") or ""),
            translation=str(row.get("translation", "") or ""),
        ))
    if not ly_lines:
        raise ImportLyricsError("平台逐字歌词没有产出任何行")
    ly = Lyrics(version=1, language=language, source=source,
                alignment_model="platform-yrc", lines=ly_lines)
    validate(ly)
    return ly


def _lyrics_from_words_rows(rows: list[dict], words_rows: list, *, source: str,
                            language: str, model_tag: str) -> Lyrics:
    """行级 rows + 字级 words_rows（qwen/MMS 通用）→ Lyrics。

    缺字行回退行窗均摊；钳入行窗并单调；校验一致性。
    """

    ly_lines: list[Line] = []
    seq = 0
    for row, words in zip(rows, words_rows):
        start = int(row["start_ms"])
        end = int(max(row.get("end_ms") or 0, start + 200))
        if not words:
            toks = tokenize_pronounceable(str(row["text"]))
            if not toks:
                continue
            step = max(end - start, len(toks)) / max(len(toks), 1)
            words = [Word(t, int(start + k * step), int(start + (k + 1) * step), 0.15)
                     for k, t in enumerate(toks)]
        fixed: list[Word] = []
        last = start
        for w in words:
            s = max(int(w.start), start, last)
            e = min(max(int(w.end), s + 1), max(end, s + 1))
            fixed.append(Word(w.char, s, e, w.confidence))
            last = e
        fixed = realign_words(fixed, str(row["text"]))
        if not fixed:
            continue
        seq += 1
        ly_lines.append(Line(
            id=f"L{seq}",
            text=str(row["text"]),
            start=start, end=max(end, fixed[-1].end),
            words=fixed,
            is_background=bool(row.get("is_background")),
            agent=str(row.get("agent", "") or ""),
            translation=str(row.get("translation", "") or ""),
        ))
    if not ly_lines:
        raise ImportLyricsError(f"{model_tag} 没有产出任何歌词行")
    ly = Lyrics(version=1, language=language, source=source,
                alignment_model=model_tag, lines=ly_lines)
    try:
        validate(ly)
    except ValueError:
        _clamp_line_words(ly)
        validate(ly)
    return ly


def _even_words(text: str, line_start: int, line_end: int, conf: float = 0.15) -> list[Word]:
    """行窗均摊兜底：无对齐结果时把字/词按行窗均分。"""
    toks = tokenize_pronounceable(text)
    span = max(line_end - line_start, len(toks))
    step = span / max(len(toks), 1)
    return [Word(t, int(line_start + k * step), int(line_start + (k + 1) * step), conf)
            for k, t in enumerate(toks)]


def _lyrics_from_mms_lrc(wav: str, rows: list[dict], *, source: str,
                         language: str, models_root: Optional[str]) -> Lyrics:
    """MMS-FA + LRC 行级锚：行内逐字强制对齐（不跑 Whisper，快且准）。"""
    from lyrics.mms_align import align_line_windows

    words_rows = align_line_windows(wav, rows, models_root=models_root)
    ly_lines: list[Line] = []
    seq = 0
    for row, words in zip(rows, words_rows):
        start = int(row["start_ms"])
        end = int(max(row["end_ms"], start + 200))
        if words:
            # 钳入行窗并保证单调
            fixed: list[Word] = []
            last = start
            for w in words:
                s = max(w.start, start, last)
                e = min(max(w.end, s + 1), max(end, s + 1))
                fixed.append(Word(w.char, int(s), int(e), w.confidence))
                last = e
            fixed = realign_words(fixed, str(row["text"]))
        else:
            fixed = _even_words(str(row["text"]), start, end)
        if not fixed:
            continue
        seq += 1
        ly_lines.append(Line(
            id=f"L{seq}",
            text=str(row["text"]),
            start=start, end=max(end, fixed[-1].end),
            words=fixed,
            is_background=bool(row.get("is_background")),
            agent=str(row.get("agent", "") or ""),
            translation=str(row.get("translation", "") or ""),
        ))
    if not ly_lines:
        raise ImportLyricsError("MMS 对齐没有产出任何歌词行")
    ly = Lyrics(version=1, language=language, source=source,
                alignment_model="imported-lrc+mms-fa", lines=ly_lines)
    validate(ly)
    return ly


def _align_with_text_mms(wav: str, lyrics_text: str, *, source: str,
                         models_root: Optional[str],
                         auto_harmony: bool = False) -> Lyrics:
    """纯文本 + MMS-FA 全曲分块强制对齐（无 LRC 行时间也可用）。"""
    from lyrics.mms_align import align_text_full

    rows = [row for row in split_text_lines(lyrics_text) if row]
    if not rows:
        raise ImportLyricsError("导入的歌词没有有效文本行")
    # 行间估算均不留时间：align_text_full 内部估计并分配绝对时间
    from lyrics.mms_align import _load_bundle, _load_wave, align_text_full

    bundle = _load_bundle(models_root)  # 加载一次（模型+采样率），供音频解码与对齐共用
    wav_data, wav_sr = _load_wave(wav, bundle[3])
    fake_rows = [{"text": r} for r in rows]
    words_rows = align_text_full(wav_data, wav_sr, fake_rows,
                                 models_root=models_root, bundle=bundle)
    ly_lines: list[Line] = []
    seq = 0
    prev_end = 0
    for row, words in zip(fake_rows, words_rows):
        text = str(row["text"])
        if not words:
            continue
        fixed = realign_words(words, text)
        start = fixed[0].start
        end = max(fixed[-1].end, start + 1)
        # 单调防护：与前一行重叠则压缩
        if start < prev_end:
            shift = prev_end - start
            fixed = [Word(w.char, w.start + shift, w.end + shift, w.confidence)
                     for w in fixed]
            start, end = start + shift, end + shift
        prev_end = end
        seq += 1
        ly_lines.append(Line(id=f"L{seq}", text=str(row["text"]),
                             start=start, end=end, words=fixed))
    if not ly_lines:
        raise ImportLyricsError("MMS 对齐没有产出任何歌词行")
    ly = Lyrics(version=1, language="zh", source=source,
                alignment_model="imported-text+mms-fa", lines=ly_lines)
    validate(ly)
    return ly


def _lyrics_from_qwen_lrc(wav: str, rows: list[dict], *, source: str,
                          language: str, models_root: Optional[str]) -> Lyrics:
    """Qwen3-ForcedAligner + LRC 行锚：邻近行上下文音节对齐 + MMS 交叉校验。"""
    from lyrics.qwen_align import align_line_windows_qwen

    words_rows = align_line_windows_qwen(wav, rows, models_root=models_root)
    ly_lines: list[Line] = []
    seq = 0
    for row, words in zip(rows, words_rows):
        start = int(row["start_ms"])
        end = int(max(row["end_ms"], start + 200))
        if not words:
            continue
        fixed: list[Word] = []
        last = start
        for w in words:
            s = max(w.start, start, last)
            e = min(max(w.end, s + 1), max(end, s + 1))
            fixed.append(Word(w.char, int(s), int(e), w.confidence))
            last = e
        fixed = realign_words(fixed, str(row["text"]))
        if not fixed:
            continue
        seq += 1
        ly_lines.append(Line(
            id=f"L{seq}",
            text=str(row["text"]),
            start=start, end=max(end, fixed[-1].end),
            words=fixed,
            is_background=bool(row.get("is_background")),
            agent=str(row.get("agent", "") or ""),
            translation=str(row.get("translation", "") or ""),
        ))
    if not ly_lines:
        raise ImportLyricsError("Qwen 对齐没有产出任何歌词行")
    ly = Lyrics(version=1, language=language, source=source,
                alignment_model="imported-lrc+qwen-fa", lines=ly_lines)
    validate(ly)
    return ly


def _align_with_text_qwen(wav: str, lyrics_text: str, *, source: str,
                          language: Optional[str], models_root: Optional[str],
                          auto_harmony: bool = False,
                          notes: Optional[list] = None,
                          whisper_skeleton: Optional[list] = None,
                          progress=None, repo_id=None, use_head=False) -> Lyrics:
    """纯文本 + Qwen3-ASR 行定位 + Qwen3-ForcedAligner 逐字精对齐。"""
    from lyrics.qwen_align import align_text_qwen

    rows = [{"text": r} for r in split_text_lines(lyrics_text) if r]
    if not rows:
        raise ImportLyricsError("导入的歌词没有有效文本行")
    tifa_rows, words_rows, model_tag = align_text_qwen(
        wav, rows, models_root=models_root,
        language=language or "zh", whisper_skeleton=whisper_skeleton,
        progress=progress, **({"repo_id": repo_id} if repo_id else {}),
        use_head=use_head)
    ly_lines: list[Line] = []
    seq = 0
    for row, words in zip(tifa_rows, words_rows):
        text = str(row["text"])
        if not words:
            continue
        fixed: list[Word] = []
        last = int(row["start_ms"])
        for w in words:
            s = max(w.start, int(row["start_ms"]), last)
            e = min(max(w.end, s + 1), max(int(row["end_ms"]), s + 1))
            fixed.append(Word(w.char, int(s), int(e), w.confidence))
            last = e
        fixed = realign_words(fixed, text)
        if not fixed:
            continue
        seq += 1
        ly_lines.append(Line(id=f"L{seq}",
                             text=str(row["text"]),
                             start=int(row["start_ms"]),
                             end=max(int(row["end_ms"]), fixed[-1].end),
                             words=fixed))
    if not ly_lines:
        raise ImportLyricsError("Qwen 对齐没有产出任何歌词行")
    ly = Lyrics(version=1, language=language or "zh", source=source,
                alignment_model=model_tag, lines=ly_lines)
    try:
        validate(ly)
    except ValueError:
        _clamp_line_words(ly)  # 行窗越界的标点/漂移 word 自愈后重校
        validate(ly)
    return ly


def _lyrics_from_tifa(rows: list[dict], tifa: dict, *, source: str,
                      language: str) -> Lyrics:
    """TIFA 对齐结果 → Lyrics（未匹配行回退 LRC 行窗均摊）。"""
    from lyrics.dp_match import Unit, interpolate

    ly_lines: list[Line] = []
    seq = 0
    for li, row in enumerate(rows):
        mapped = tifa.get("lines", {}).get(str(li))
        text = str(row["text"])
        line_start = int(row["start_ms"])
        line_end = int(max(row["end_ms"], line_start + 200))
        words: list[Word] = []
        if mapped:
            last = line_start
            for char, s, e in mapped:
                start = max(int(s), line_start, last)
                end = max(min(int(e), line_end), start + 1)
                words.append(Word(char, start, end, 0.85))
                last = end
        else:
            toks = tokenize_pronounceable(text)
            span = max(line_end - line_start, len(toks))
            step = span / max(len(toks), 1)
            words = [Word(t, int(line_start + k * step), int(line_start + (k + 1) * step), 0.15)
                     for k, t in enumerate(toks)]
        if not words:
            continue
        # TIFA 音素切分的英文边界可能与 text 分词不一致，自愈重切分
        words = realign_words(words, text)
        seq += 1
        ly_lines.append(Line(
            id=f"L{seq}",
            text=text,
            start=line_start, end=max(line_end, words[-1].end),
            words=words,
            is_background=bool(row.get("is_background")),
            agent=str(row.get("agent", "") or ""),
            translation=str(row.get("translation", "") or ""),
        ))
    if not ly_lines:
        raise ImportLyricsError("TIFA 对齐没有产出任何歌词行")
    ly = Lyrics(version=1, language=language, source=source,
                alignment_model="imported-lrc+tifa-align", lines=ly_lines)
    validate(ly)
    return ly


def _align_with_lrc_timestamps(
    wav: str, lyrics_text: str, *, source: str, model_size: Optional[str] = None,
    language: Optional[str],
    device: str, compute_type: str, models_root: Optional[str],
    auto_harmony: bool = False, engine: str = "whisper",
    notes: Optional[list] = None, progress=None,
) -> Lyrics:
    """LRC 锚点切片转写 + DP 强制对齐（迁移自 lrc-ttml：按行切窗 + 歌词作提示词）。

    engine='tifa' 且配置了 TIFA 外部环境（LYRICS_TIFA_PYTHON +
    LYRICS_TIFA_MODEL）时优先 TIFA 歌声音素对齐（逐字更准）；否则/失败
    自动回退 whisperx 主路径（失败原因 append 到 notes）。
    """
    from lyrics.dp_match import (align_sequences, expand_asr_units, interpolate,
                                 scan_units, similarity, trustworthy)

    rows = [row for row in parse_lrc(lyrics_text) if row["text"]]
    if not rows:
        raise ImportLyricsError("LRC 中没有可用的歌词行")
    # LRC 行锚合理性：正常歌词每行 2~5s。行距过短说明时间轴损坏
    # （转换出错/版本不匹配），用它对齐必然产出垃圾，直接拒绝并指路
    gaps = [rows[i + 1]["start_ms"] - rows[i]["start_ms"]
            for i in range(len(rows) - 1)]
    if gaps and sum(gaps) / len(gaps) < 1200:
        raise ImportLyricsError(
            "这份 LRC 的行时间异常（平均行距不足 1.2 秒，正常歌词每行 2~5 秒）："
            "时间轴可能损坏，或与当前音频的版本不匹配。"
            "请改用「在线搜索歌词」获取正确的时间轴")
    lang = language or "zh"

    # 平台逐字歌词（网易云 YRC 等转出的字级时间戳，人工校对）直接采用：
    # 精度天花板、零计算。任一行缺字时间则整体回退模型对齐。
    if all(row.get("words") for row in rows if not row.get("is_background")):
        try:
            return _lyrics_from_platform_words(rows, source=source, language=lang)
        except Exception:  # noqa: BLE001 — 直接采用失败回退模型对齐
            pass

    # MMS-FA 路径：LRC 行级锚 + 行内逐字强制对齐，完全不依赖 whisperx
    if engine == "mms":
        try:
            return _lyrics_from_mms_lrc(wav, rows, source=source, language=lang,
                                        models_root=models_root)
        except Exception as exc:  # noqa: BLE001 — MMS 失败回退 whisperx 主路径
            if notes is not None:
                notes.append(f"MMS 对齐失败，已回退 Whisper：{exc}")

    # Qwen3-ForcedAligner 路径（AutoKaraoke 方案迁移）：行锚 + 行窗上下文精对齐
    # + 能量包络后处理，实测字级最准。失败回退 TIFA → whisperx。
    if engine == "paraformer":
        try:
            pf_ts = _paraformer_timestamps(wav, models_root=models_root)
            if not pf_ts:
                raise ImportError("Paraformer 没有产出时间戳")
            return _align_lrc_with_paraformer(wav, rows, pf_ts, source=source,
                                              language=lang)
        except ImportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ImportError(f"Paraformer 对齐失败：{exc}") from exc

    lrc_engine, lrc_repo, lrc_head = _qwen_repo(engine)
    if lrc_engine == "qwen":
        try:
            from lyrics.qwen_align import align_lrc_lines
            words_rows = align_lrc_lines(wav, rows, models_root=models_root,
                                         notes=notes, progress=progress,
                                         repo_id=lrc_repo, use_head=lrc_head,
                                         language=lang)
            if words_rows:
                tag = ("imported-lrc+qwen3-fa-ultra-head" if lrc_head
                       else "imported-lrc+qwen3-fa-ultra"
                       if lrc_repo != "Qwen/Qwen3-ForcedAligner-0.6B"
                       else "imported-lrc+qwen3-fa")
                return _lyrics_from_words_rows(rows, words_rows, source=source,
                                               language=lang,
                                               model_tag=tag)
            raise ImportError("Qwen3 行窗对齐没有产出结果")
        except ImportError:
            raise  # 用户要求：Qwen3 失败不回退其他模型，明确报错
        except Exception as exc:  # noqa: BLE001
            raise ImportError(f"Qwen3 对齐失败：{exc}") from exc

    # Qwen3-ForcedAligner 路径：LRC 行锚 + 邻近行上下文音节对齐（AutoKaraoke
    # 方案，实测最强）+ MMS 交叉校验 + 能量包络后处理
    if engine == "qwen":
        try:
            return _lyrics_from_qwen_lrc(wav, rows, source=source, language=lang,
                                         models_root=models_root)
        except ImportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ImportError(f"Qwen3 对齐失败：{exc}") from exc

    # TIFA 路径：逐行音素对齐，完全不依赖 whisperx
    if engine == "tifa":
        try:
            from lyrics.tifa_align import TifaUnavailable, align_with_tifa, tifa_available
            if tifa_available():
                try:
                    tifa = align_with_tifa(wav, rows, lang)
                    if tifa:
                        return _lyrics_from_tifa(rows, tifa, source=source, language=lang)
                except TifaUnavailable:
                    pass  # 回退 whisperx
                except Exception as exc:  # noqa: BLE001 — 失败原因透出供排查
                    if notes is not None:
                        notes.append(f"TIFA 推理失败，已回退 Whisper：{exc}")
            elif notes is not None:
                notes.append("TIFA 未就绪（模型或环境缺失）")
        except Exception as exc:  # noqa: BLE001 — TIFA 任何异常都不阻断主路径
            if notes is not None:
                notes.append(f"TIFA 初始化失败，已回退 Whisper：{exc}")

    # 行窗口：[start-0.35s, min(next_start, start+15s)+0.35s]，prompt=本行歌词
    windows: list[tuple[float, float]] = []
    prompts: list[Optional[str]] = []
    duration_s = None
    for i, row in enumerate(rows):
        start_s = row["start_ms"] / 1000
        next_start = rows[i + 1]["start_ms"] / 1000 if i + 1 < len(rows) else start_s + 15
        end_s = min(next_start, start_s + 15)
        windows.append((max(start_s - 0.35, 0), end_s + 0.35))
        prompts.append(row["text"][:120] or None)

    from lyrics.asr import resolve_align_model_source, transcribe_slices
    from lyrics.dp_match import similarity

    align_source = resolve_align_model_source(None, lang, models_root)
    # 选什么用什么：Whisper 引擎 + LRC 时，切片转写用选中的 Whisper 模型；
    # 非 Whisper 模型（qwen3-asr 等）在此路径不可用，回退 small
    _msz_raw = (model_size or "").strip() or "small"
    _msz = {"whisper-small": "small", "whisper-medium": "medium",
            "whisper-large-v3": "large-v3", "large": "large-v3"}.get(_msz_raw, _msz_raw)
    if _msz not in ("small", "medium", "large-v3"):
        _msz = "small"
    raw = transcribe_slices(wav, windows, prompts, model_size=_msz,
                            language=lang, device=device, compute_type=compute_type,
                            align_model=align_source, models_root=models_root)
    asr_tuples = _asr_word_tuples(raw)
    if not asr_tuples:
        raise ImportLyricsError("切片转写未得到可用时间轴，无法对齐导入歌词")

    infos = rows
    units = scan_units(infos)
    asr_units = expand_asr_units(asr_tuples)

    # ASR 单元按中心点分桶进行窗（window_pad 双向扩展已在窗口构造里）
    used_ids: set[int] = set()
    total_duration = asr_tuples[-1][2]
    ly_lines: list[Line] = []
    seq = 0
    for li, row in enumerate(infos):
        row_units = [u for u in units if u.line == li]
        line_start = int(row["start_ms"])
        line_end = int(max(row["end_ms"], line_start + 200))
        lo = max(0, line_start - 350)
        hi = min(total_duration, line_end + 350)
        window_units = [u for u in asr_units if lo <= (u.start + u.end) / 2 < hi]
        for u in window_units:
            used_ids.add(id(u))
        if row_units and window_units:
            mapping = align_sequences(row_units, window_units, lo, hi)
            avg_sim = 0.0
            if mapping:
                avg_sim = sum(similarity(row_units[k].text, window_units[j].text)
                              for k, j in mapping.items()) / len(mapping)
            if not trustworthy(len(mapping), len(row_units), window_units,
                               avg_sim, line_end - line_start):
                mapping = {}
            interpolate(row_units, mapping, window_units, lo, hi)
        elif row_units:
            interpolate(row_units, {}, [], lo, hi)
        # 行时间以 LRC 锚点为准；词钳入行区间
        words: list[Word] = []
        last_end = line_start
        for u in row_units:
            w = _unit_word(u, line_start, line_end, last_end)
            words.append(w)
            last_end = w.end
        if not words:
            continue
        # LRC 逐字时间戳会把英文串拆开（Chieh/lun/Chou），与 text 分词
        # 边界不一致 → 按 text 分词自愈重切分（合并时间）
        words = realign_words(words, text)
        seq += 1
        ly_lines.append(Line(
            id=f"L{seq}",
            text=text,
            start=line_start, end=max(line_end, last_end),
            words=words,
            is_background=bool(row.get("is_background")),
            agent=str(row.get("agent", "") or ""),
            translation=str(row.get("translation", "") or ""),
        ))

    if auto_harmony and asr_units:
        ly_lines.extend(_harmony_lines_from_leftovers(
            asr_units, used_ids, total_duration))

    ly = Lyrics(version=1, language=raw.get("language", lang), source=source,
                alignment_model="imported-lrc+dp-align", lines=ly_lines)
    validate(ly)
    return ly


def _align_with_text(
    wav: str, lyrics_text: str, *, source: str, model_size: str,
    language: Optional[str], device: str, compute_type: str,
    models_root: Optional[str],
    auto_harmony: bool = False,
    engine: str = "whisper",
    notes: Optional[list] = None,
) -> Lyrics:
    """纯文本：全曲 ASR（歌词作提示词）→ DP 行级定位 → 引擎精对齐。

    engine='tifa'：DP 只产出行级时间骨架，随后 TIFA 在每行窗口内做音素级
    逐字精对齐（实测最准）；TIFA 失败回退 DP 字级（notes 记录原因）。
    """
    from lyrics.asr import segments_to_lyrics, transcribe
    from lyrics.dp_match import (align_sequences, expand_asr_units, interpolate,
                                 scan_units, trustworthy)

    rows = [row for row in split_text_lines(lyrics_text)]
    if not rows:
        raise ImportLyricsError("导入的歌词没有有效文本行")

    prompt = "".join(rows)[:400] or None
    raw = transcribe(wav, model_size=model_size, language=language,
                     device=device, compute_type=compute_type,
                     models_root=models_root, initial_prompt=prompt)
    asr_tuples = _asr_word_tuples(raw)
    if not asr_tuples:
        raise ImportLyricsError("语音识别未得到可用的时间轴，无法对齐导入歌词")

    infos = [{"text": r} for r in rows]
    units = scan_units(infos)
    asr_units = expand_asr_units(asr_tuples)
    total_duration = asr_tuples[-1][2]

    mapping = align_sequences(units, asr_units, 0, total_duration)
    if not trustworthy(len(mapping), len(units), asr_units, 0.0, total_duration):
        # ASR 与歌词行匹配率过低（歌声 ASR 本质弱 + 前奏幻觉段）：
        # DP 行定位不可信 → 回退 MMS 全局对齐（不依赖 ASR，无幻觉段问题）
        if engine in ("tifa", "mms"):
            try:
                if notes is not None:
                    notes.append("Whisper 行定位匹配率过低，已回退 MMS 全局对齐")
                return _align_with_text_mms(
                    wav, lyrics_text, source=source, models_root=models_root,
                    auto_harmony=auto_harmony)
            except Exception as exc:  # noqa: BLE001 — MMS 也失败则继续 DP
                if notes is not None:
                    notes.append(f"MMS 回退也失败：{exc}")
        mapping = {}
    interpolate(units, mapping, asr_units, 0, total_duration)

    # 行边界 = 该行首/末单元时间；未匹配行按首末单元插值结果
    # 先收集行级骨架（供 TIFA 精对齐用），再构建字级
    line_bounds: list[tuple[int, dict, list]] = []  # (start, info, row_units)
    ly_lines: list[Line] = []
    seq = 0
    for li, info in enumerate(infos):
        row_units = [u for u in units if u.line == li]
        if not row_units:
            continue
        line_start = row_units[0].start
        line_end = max(row_units[-1].end, line_start + 1)
        line_bounds.append((li, info, row_units))
        seq += 1
        words: list[Word] = []
        last_end = line_start
        for u in row_units:
            w = _unit_word(u, line_start, line_end, last_end)
            words.append(w)
            last_end = w.end
        # ASR 词边界（英文撇号/连写）与 text 分词不一致时自愈
        row_text = str(info["text"])
        words = realign_words(words, row_text)
        ly_lines.append(Line(id=f"L{seq}",
                             text=row_text,
                             start=line_start, end=max(line_end, last_end),
                             words=words))

    # TIFA 精对齐：DP 行级骨架 → 每行窗口内音素级逐字（实测最准）；
    # 失败回退上面的 DP 字级结果
    if engine == "tifa" and line_bounds:
        try:
            from lyrics.tifa_align import TifaUnavailable, align_with_tifa, tifa_available
            if tifa_available():
                try:
                    tifa_rows = [{
                        "text": str(info["text"]),
                        "start_ms": int(row_units[0].start),
                        "end_ms": int(max(row_units[-1].end, row_units[0].start + 1)),
                    } for _li, info, row_units in line_bounds]
                    tifa = align_with_tifa(wav, tifa_rows, language or "zh")
                    if tifa:
                        ly = _lyrics_from_tifa(tifa_rows, tifa, source=source,
                                               language=raw.get("language", "zh"))
                        if auto_harmony and asr_units:
                            used_ids = {id(asr_units[j]) for j in mapping.values()}
                            ly.lines.extend(_harmony_lines_from_leftovers(
                                asr_units, used_ids, total_duration))
                        validate(ly)
                        return ly
                except TifaUnavailable:
                    pass  # 回退 DP 字级
                except Exception as exc:  # noqa: BLE001 — 失败原因透出
                    if notes is not None:
                        notes.append(f"TIFA 精对齐失败，已回退 DP：{exc}")
            elif notes is not None:
                notes.append("TIFA 未就绪（模型缺失），使用 DP 对齐")
        except Exception as exc:  # noqa: BLE001
            if notes is not None:
                notes.append(f"TIFA 初始化失败，已回退 DP：{exc}")

    if auto_harmony and asr_units:
        used_ids = {id(asr_units[j]) for j in mapping.values()}
        ly_lines.extend(_harmony_lines_from_leftovers(
            asr_units, used_ids, total_duration))

    ly = Lyrics(version=1, language=raw.get("language", "zh"), source=source,
                alignment_model="imported-text+dp-align", lines=ly_lines)
    try:
        validate(ly)
    except ValueError:
        # 自愈：行窗越界的标点/漂移 word 钳回行窗后重新校验（DP 行边界偶有帧级漂移）
        _clamp_line_words(ly)
        validate(ly)
    return ly
