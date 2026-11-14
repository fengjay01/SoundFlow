# -*- coding: utf-8 -*-
"""Qwen3-ForcedAligner-0.6B 字级对齐（完整迁移自 AutoKaraoke 项目方案）。

原理：音频-文本强制对齐模型——把歌词单元（中文一字/英文一词）排进 prompt，
每个单元后跟 <timestamp><timestamp> 两个时间戳槽，模型直接预测每个槽的
时间戳 token。专为歌声对齐设计，实测优于 MMS-FA（AutoKaraoke 用它做主力、
MMS 做兜底/交叉校验）。

迁移的核心策略（AutoKaraoke align/pipeline.py）：
- 行窗带 ±1 行上下文 + 1s 前置静音 pad：行不贴窗边、复歌段落不混淆；
- 行窗上限 = 行 start + 1s + 每单元 1.2s（长间奏自动裁剪）；
- 合理性校验（越界/坍缩/偏离 LRC 锚 >2s）→ 失败行单行重试 → 仍失败回退
  调用方（MMS/均摊）；
- 能量包络后处理：首字粘上行尾（>4×行中位时长）→ 局部能量 onset 重锚；
  零长字借相邻字时长；行区间静音 → 置信度降级；行尾侵入下一行裁剪。

模型：Qwen/Qwen3-ForcedAligner-0.6B（Apache 2.0，可商用），
HuggingFace 缓存位于 <模型库>/l2t/hf_cache（幂等解析，兼容两种根传法）。
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from .schema import Word

MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
ULTRA_MODEL_ID = "17slever17/Qwen3-ForcedAligner-Ultra"  # 社区 70h LoRA 精修（Apache-2.0）
ASR_MODEL_ID = "Qwen/Qwen3-ASR-1.7B"
SAMPLE_RATE = 16000
MAX_SECONDS = 290.0     # 模型单样本音频上限（~4:50）
BATCH_SIZE = 8
PAD_BEFORE = 1.0        # 行窗前置静音（首单元不贴窗边）
PAD_AFTER = 0.5
CONTEXT_LINES = 1       # 行窗包含的邻近行（上下文）
MIN_DURATION = 0.08     # 单元最短时长（秒）
MAX_LINE_SECONDS_PER_UNIT = 1.2  # 长间奏：行窗 = start + 1s + 每单元 1.2s 上限
ANCHOR_TOLERANCE = 0.8  # 与 LRC 行锚偏差超过此值 → 置信度 ×0.5
MAX_ANCHOR_DEVIATION = 2.0  # 超过此值整行判失败（允许首字搁浅，见 _plausible）
STRANDED_GAP = 0.6

_hf_root_cache: list = []  # 解析后的 HF 根（幂等）


def _category_root_exists(root: Path) -> bool:
    """root 是否歌词模型库根（新旧两种布局任一命中即可）。"""
    return ((root / "asr").is_dir() or (root / "alignment").is_dir()
            or (root / "l2t").is_dir())


def _hf_home(models_root: str | None) -> Path:
    """解析 HF 缓存根（<模型库>/asr/hf_cache，whisper 快照归属语音识别分类），
    兼容裸数据根/歌词根两种传法。"""
    if _hf_root_cache:
        return _hf_root_cache[0]
    from .model_store import store_root
    from worker_lyrics_models import lyrics_models_root

    root = Path(models_root) if models_root else None
    if root is None or not _category_root_exists(root):
        root = Path(lyrics_models_root(str(root) if models_root else None))
    hf = store_root(str(root)) / "asr" / "hf_cache"
    # 旧布局兜底（l2t/hf_cache）
    if not hf.is_dir():
        legacy = store_root(str(root)) / "l2t" / "hf_cache"
        if legacy.is_dir():
            hf = legacy
    _hf_root_cache.append(hf)
    hf.mkdir(parents=True, exist_ok=True)
    return hf


def load_audio(path: str, sample_rate: int = SAMPLE_RATE):
    """加载并归一化单声道 float32 numpy 音频（复用 mms_align 的解码管线）。"""
    import torch

    from .mms_align import _load_wave
    wav, sr = _load_wave(path, sample_rate)
    if sr != sample_rate:
        import torchaudio.functional as _taf
        wav = _taf.resample(wav, sr, sample_rate)
    return wav[0].cpu().numpy().astype("float32")


def _clean(unit: str) -> str:
    """模型只认字母/数字/撇号——剔除其余（保留原样给展示层）。"""
    return "".join(
        ch for ch in unit if ch == "'" or unicodedata.category(ch)[0] in "LN"
    )


def split_units(text: str) -> list[str]:
    """行文本 → 对齐单元：中文逐字、拉丁连串整词、其余跳过。"""
    units: list[str] = []
    buf = ""
    for ch in text:
        if re.match(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", ch):
            if buf:
                units.append(buf)
                buf = ""
            units.append(ch)
        elif ch.isascii() and (ch.isalpha() or ch == "'"):
            buf += ch
        else:
            if buf:
                units.append(buf)
                buf = ""
    if buf:
        units.append(buf)
    return units


def units_to_words(units: list[str], timings: list[tuple[int, int]],
                   confidence: float) -> list[Word]:
    """单元时间 → schema.Word（英文整词 end→start 顺延，一字一 Word 保持 schema 一致）。"""
    words: list[Word] = []
    for u, (s, e) in zip(units, timings):
        chars = list(u)  # 英文词也逐字母，时间均分（KTV 逐字点亮粒度）
        n = max(len(chars), 1)
        step = max(e - s, 20 * n) / n  # 毫秒/字符（s/e 均为毫秒）
        for k, ch in enumerate(chars):
            words.append(Word(ch, int(s + k * step),
                              int(s + (k + 1) * step), confidence))
    return words


def _model_dir(models_root: str | None, repo_id: str) -> str:
    """模型加载源：本地分类目录优先，否则 HF repo id（走 HF_HOME 缓存）。"""
    name = repo_id.split("/")[-1]
    # 分类布局：<root>/<asr|alignment>/<name>；旧布局兜底 l2t/models/<name>
    from .model_store import store_root
    from worker_lyrics_models import lyrics_models_root

    root = Path(models_root) if models_root else None
    if root is None or not _category_root_exists(root):
        root = Path(lyrics_models_root(str(root) if models_root else None))
    cat = "asr" if name == "Qwen3-ASR-1.7B" else "alignment"
    local = root / cat / name
    if not (local / "config.json").is_file():
        legacy = root / "l2t" / "models" / name
        if (legacy / "config.json").is_file():
            local = legacy
    if (local / "config.json").is_file():
        return str(local)
    # HF 快照缓存兜底（whisper 系归 asr/hf_cache；旧布局 l2t/hf_cache 兜底）
    cache_key = "models--" + repo_id.replace("/", "--")
    store = store_root(str(root))
    snaps = store / "asr" / "hf_cache" / cache_key / "snapshots"
    if not snaps.is_dir():
        snaps = store / "hf_cache" / cache_key / "snapshots"
    if snaps.is_dir():
        for snap in sorted(snaps.iterdir()):
            if (snap / "config.json").is_file():
                return str(snap)
    # 严格本地加载：本地没有 → 明确报错引导去模型管理下载，绝不联网取模型。
    # （旧逻辑回退 hub id 并允许在线拉取，弱网/离线时报出难懂的连接错误）
    os.environ["HF_HOME"] = str(_hf_home(str(root)))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    mid = {"Qwen/Qwen3-ForcedAligner-0.6B": "qwen3-fa",
           ULTRA_MODEL_ID: "qwen3-fa-ultra",
           ASR_MODEL_ID: "qwen3-asr"}.get(repo_id, repo_id)
    raise ImportError(
        f"模型未下载：{repo_id}。请到「模型管理」页下载对应条目「{mid}」后重试"
        "（工作台只从本地加载模型，不会联网拉取）")


class QwenAligner:
    """Qwen3-ForcedAligner 封装：prompt 构造 + 时间戳 token 解析。"""

    name = "qwen"

    def __init__(self, models_root: str | None = None, device: str | None = None,
                 repo_id: str = MODEL_ID, use_head: bool = False,
                 language: str = "zh"):
        import torch
        from qwen_asr import Qwen3ForcedAligner

        hf = _hf_home(models_root)
        os.environ["HF_HOME"] = str(hf)
        source = _model_dir(models_root, repo_id)
        device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        self._wrapper = Qwen3ForcedAligner.from_pretrained(
            source, dtype=dtype, device_map=device)
        self._fix = self._wrapper.aligner_processor.fix_timestamp
        self.device = device
        # Local Head（Ultra + Local Head 专属）：companion 运行时，边界精修
        self._head = None
        self._head_lang = (_ASR_LANG.get(str(language).split("_")[0].lower())
                           or "Chinese")
        if use_head:
            try:
                from qwen_fa_ultra import LocalBoundaryAligner
            except ImportError as exc:
                raise ImportError(
                    "qwen_fa_ultra 未安装（Ultra + Local Head 配套运行时）。"
                    "安装：pip install qwen_fa_ultra 的 companion wheel"
                    "（HF 仓库 17slever17/Qwen3-ForcedAligner-Ultra 的 "
                    "companion/dist/*.whl）") from exc
            self._head = LocalBoundaryAligner.from_pretrained(
                source, device=device, dtype=dtype, head_batch_size=128)

    def _align_batch_head(self, segments: list[dict]) -> list[list[tuple[int, int]] | None]:
        """Local Head 路径：units 以空格连接成"词"，align() 逐段精修边界。

        返回与 units 一一对应的 (start_ms, end_ms)；数量不一致的段返回 None
        （交调用方的该段失败处理，如重试/均摊）。
        """
        audios = [seg["audio"] for seg in segments]
        texts = [" ".join(seg["units"]) for seg in segments]
        langs = [self._head_lang] * len(segments)
        results = self._head.align(audio=audios, text=texts, language=langs)
        out: list[list[tuple[int, int]] | None] = []
        for seg, res in zip(segments, results):
            units = seg["units"]
            items = list(res)
            if len(items) != len(units):
                out.append(None)
                continue
            seg_out = []
            for it in items:
                # head 返回的是切片内相对时间 → 必须加回切片起点（与 base 路径
                # seg["start"]+ms 同语义）；否则片段起点>0 时全部落在 0 附近，
                # _plausible 全拒 → 整首歌退化成均摊（LRC 模式 0/61 事故根因）
                s_ms = int(round((seg["start"] + it.start_time) * 1000))
                e_ms = max(int(round((seg["start"] + it.end_time) * 1000)), s_ms + 1)
                seg_out.append((s_ms, e_ms))
            # 塌缩检查：≥25% 相邻字起点间隔 <50ms → 视为该段失败（走均摊）
            sgaps = [seg_out[k + 1][0] - seg_out[k][0] for k in range(len(seg_out) - 1)]
            if len(sgaps) >= 5 and sum(1 for g in sgaps if g < 50) / len(sgaps) > 0.25:
                out.append(None)
                continue
            out.append(seg_out)
        return out

    def _align_batch(self, segments: list[dict]) -> list[list[tuple[int, int]] | None]:
        """segments: [{audio: np.float32, units: [str], start: float}] → 每段每单元 (start_ms, end_ms)。"""
        import torch

        if self._head is not None:
            return self._align_batch_head(segments)

        w = self._wrapper
        prompts, kept = [], []
        for seg in segments:
            idx = [i for i, u in enumerate(seg["units"]) if _clean(u)]
            kept.append(idx)
            body = "".join(_clean(seg["units"][i]) + "<timestamp><timestamp>" for i in idx)
            prompts.append("<|audio_start|><|audio_pad|><|audio_end|>" + body)

        inputs = w.processor(
            text=prompts, audio=[s["audio"] for s in segments],
            return_tensors="pt", padding=True,
        )
        inputs = inputs.to(w.model.device).to(w.model.dtype)
        with torch.inference_mode():
            output_ids = w.model.thinker(**inputs).logits.argmax(dim=-1)

        results: list[list[tuple[int, int]] | None] = []
        for seg, idx, in_ids, out_ids in zip(segments, kept, inputs["input_ids"], output_ids):
            ticks = out_ids[in_ids == w.timestamp_token_id].float() * w.timestamp_segment_time
            ms = self._fix(ticks.cpu().numpy()) if len(ticks) else []
            timings: list[tuple[int, int] | None] = [None] * len(seg["units"])
            for k, i in enumerate(idx):
                if 2 * k + 1 < len(ms):
                    timings[i] = (int(seg["start"] * 1000 + ms[2 * k]),
                                  int(seg["start"] * 1000 + ms[2 * k + 1]))
            results.append(timings)
        return results

    def align(self, segments: list[dict]) -> list[list[tuple[int, int]] | None]:
        out: list = []
        for i in range(0, len(segments), BATCH_SIZE):
            out.extend(self._align_batch(segments[i:i + BATCH_SIZE]))
        return out


# ---- 行窗构造 + 合理性校验 + 能量包络后处理（迁移自 pipeline.py） ----

def _rms(audio) -> "object":
    import numpy as np
    n = len(audio) // 160
    frames = audio[:n * 160].reshape(n, 160)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12)


def _line_window_end(start_ms: int, end_ms: int | None, n_units: int) -> float:
    """行窗终点：min(行 end, start + 1s + 每单元 1.2s)——长间奏自动裁剪。"""
    end_s = (end_ms or 0) / 1000.0 or start_ms / 1000.0 + n_units * 0.5
    return min(end_s, start_ms / 1000.0 + 1.0 + n_units * MAX_LINE_SECONDS_PER_UNIT)


def _slice(audio, start_s: float, end_s: float):
    a = int(max(start_s, 0.0) * SAMPLE_RATE)
    return max(start_s, 0.0), audio[a:max(a + 1, int(end_s * SAMPLE_RATE))]


def _segment(audio, start_s: float, end_s: float, units: list[str],
             pad_first: bool) -> dict:
    seg_start, seg_audio = _slice(audio, start_s, end_s)
    if pad_first:  # 首窗前置静音：首单元不贴窗边（否则对齐器钉到 0）
        import numpy as np
        seg_audio = np.concatenate([np.zeros(SAMPLE_RATE // 2, np.float32), seg_audio])
        seg_start -= 0.5
    return {"start": seg_start, "audio": seg_audio, "units": units}


def _plausible(timings: list[tuple[int, int]] | None, seg: dict,
               lrc_start_ms: int | None) -> bool:
    """对齐器明显追丢行时拒绝（迁移自 pipeline.py _plausible）。"""
    if not timings:
        return False
    seg_end_ms = int((seg["start"] + len(seg["audio"]) / SAMPLE_RATE) * 1000)
    if any(s < seg["start"] * 1000 - 10 or e > seg_end_ms + 10 for s, e in timings):
        return False
    collapsed = sum(1 for s, e in timings if e - s < MIN_DURATION * 1000 - 1)
    if len(timings) >= 3 and collapsed / len(timings) > 0.4:
        return False
    # 字起点间隔塌缩（快句 timestamp 全挤在一起的特征）：≥25% 相邻字间隔 <50ms
    starts = [s for s, _e in timings]
    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    if len(gaps) >= 5:
        tiny = sum(1 for g in gaps if g < 50)
        if tiny / len(gaps) > 0.25:
            return False
    if lrc_start_ms is not None and abs(timings[0][0] - lrc_start_ms) > MAX_ANCHOR_DEVIATION * 1000:
        # 允许首字搁浅在上一行尾（对齐器常见病），其余字贴近锚即接受
        if len(timings) < 2 or abs(timings[1][0] - lrc_start_ms) > MAX_ANCHOR_DEVIATION * 1000:
            return False
    return True


def _last_onset(rms, start_s: float, end_s: float) -> float | None:
    """[start, end) 内最后一个"发声起始"（能量上穿），用于首字重锚。"""
    import numpy as np
    a, b = max(int(start_s * 100), 0), int(end_s * 100)
    if b - a < 3:
        return None
    ref = float(np.percentile(rms[max(a - 100, 0):b + 100], 95))
    loud = rms[a:b] > 0.25 * ref
    rises = np.nonzero(loud[1:] & ~loud[:-1])[0]
    return start_s + (rises[-1] + 1) / 100 if len(rises) else None


def _fix_line_start(words: list[Word], rms) -> None:
    """首字(s)粘上行尾的修复：Case1 首字被拉长过停顿→找 onset 重锚；
    Case2 首字搁浅在远处→按能量 onset 均分回行内（迁移自 pipeline.py）。"""
    durations = [w.end - w.start for w in words]
    median = float(sorted(durations)[len(durations) // 2]) if durations else 0.0
    first = words[0]
    if first.end - first.start > max(1000, 4 * median):
        limit = words[1].start + 150 if len(words) > 1 else first.end
        onset = _last_onset(rms, first.start / 1000, limit / 1000 + 0.15)
        if onset is not None:
            first.start = int(onset * 1000)
            first.end = max(first.end, first.start + MIN_DURATION * 1000)
            if len(words) > 1 and words[1].start < first.end:
                words[1].start = first.end
                words[1].end = max(words[1].end, words[1].start + MIN_DURATION * 1000)
            first.confidence = 0.5
        return
    for k in range(min(2, len(words) - 1)):
        if words[k + 1].start - words[k].end <= STRANDED_GAP * 1000:
            continue
        n = k + 1
        anchor = words[n].start
        onset = _last_onset(rms, anchor / 1000 - 0.3 * n - 0.4, anchor / 1000 - MIN_DURATION * n)
        if onset is None:
            onset = anchor / 1000 - 0.2 * n
        step = (anchor / 1000 - onset) / n
        for j in range(n):
            words[j].start = int((onset + j * step) * 1000)
            words[j].end = int((onset + (j + 1) * step) * 1000)
            words[j].confidence = 0.5
        return


def _postprocess_line(words: list[Word], rms) -> None:
    """单调化 → 首字修复 → 最短时长 → 行区间静音降级（迁移自 pipeline.py）。"""
    for prev, cur in zip(words, words[1:]):
        cur.start = max(cur.start, prev.start)
        cur.end = max(cur.end, cur.start)
    _fix_line_start(words, rms)
    min_ms = MIN_DURATION * 1000
    for i, w in enumerate(words):
        if w.end - w.start >= min_ms - 1:
            continue
        w.confidence = min(w.confidence, 0.4)
        prev = words[i - 1] if i > 0 else None
        nxt = words[i + 1] if i + 1 < len(words) else None
        if prev and prev.end - prev.start >= 2 * min_ms - 1:
            w.start = min(w.start, w.end - min_ms)
            prev.end = min(prev.end, w.start)
        elif nxt and nxt.end - nxt.start >= 2 * min_ms:
            w.end = w.start + min_ms
            nxt.start = max(nxt.start, w.end)
        else:
            w.end = w.start + min_ms
    a, b = int(words[0].start / 1000 * 100), int(words[-1].end / 1000 * 100) + 1
    line_rms = rms[a:b]
    if len(line_rms):
        ref = float(sorted(line_rms)[len(line_rms) // 2])
        for w in words:
            seg = rms[int(w.start / 1000 * 100):int(w.end / 1000 * 100) + 1]
            if len(seg) and float(seg.mean()) < 0.15 * ref:
                w.confidence = min(w.confidence, 0.4)


def _fix_line_overlaps(lines: list) -> None:
    """行按演唱顺序裁剪尾部侵入（迁移自 pipeline.py）。"""
    for cur, nxt in zip(lines, lines[1:]):
        if cur.words[-1].end > nxt.words[0].start:
            cur.words[-1].end = max(cur.words[-1].start + MIN_DURATION * 1000,
                                    nxt.words[0].start)
            cur.end = min(cur.end, cur.words[-1].end)


def align_lrc_lines(wav_path: str, rows: list[dict], models_root=None,
                    notes: list | None = None, progress=None,
                    repo_id: str = MODEL_ID, use_head: bool = False,
                    language: str = "zh") -> list[list[Word]] | None:
    """LRC 行锚 + Qwen3-ForcedAligner 行窗精对齐（迁移 AutoKaraoke 主流程）。

    rows: parse_lrc 产物（text/start_ms/end_ms）。返回每行字级 Word；整轮
    失败（模型不可用等）返回 None 交由调用方回退。
    """
    import numpy as np

    rows = [r for r in rows if r.get("text")]
    if not rows:
        return None
    audio = load_audio(wav_path)
    duration = len(audio) / SAMPLE_RATE
    rms = _rms(audio)

    try:
        aligner = QwenAligner(models_root=models_root, repo_id=repo_id,
                              use_head=use_head, language=language)
    except Exception as exc:  # noqa: BLE001 — 模型不可用交调用方回退
        if notes is not None:
            notes.append(f"Qwen3-ForcedAligner 加载失败：{exc}")
        return None

    units_rows = [split_units(str(r["text"])) for r in rows]
    anchors = [(r["start_ms"] / 1000.0,
                _line_window_end(r["start_ms"], r.get("end_ms"), len(u) or 1))
               for r, u in zip(rows, units_rows)]

    # Pass A：每行带 ±1 行上下文（首窗前置静音 pad）
    segments, spans = [], []
    for i, (row, units) in enumerate(zip(rows, units_rows)):
        if not units:
            segments.append(None)
            spans.append((0, 0))
            continue
        lo, hi = max(0, i - CONTEXT_LINES), min(len(rows), i + CONTEXT_LINES + 1)
        ctx_units = [u for row2 in rows[lo:hi] for u in split_units(str(row2["text"]))]
        offset = sum(len(split_units(str(row2["text"]))) for row2 in rows[lo:i])
        segments.append(_segment(
            audio, anchors[lo][0] - PAD_BEFORE, anchors[hi - 1][1] + PAD_AFTER,
            ctx_units, pad_first=lo == 0))
        spans.append((offset, offset + len(units)))
    raw = [seg for seg in segments if seg is not None]
    results = [None] * len(raw)
    raw_total = len(raw)
    for k, res in enumerate(aligner.align(raw)):
        results[k] = res
        if progress and k % 4 == 0:
            # 对齐批次推进（主要耗时阶段），映射到行进度区间
            done_rows = min(len(rows), int((k + 1) / max(raw_total, 1) * len(rows)))
            progress(done_rows, len(rows) * 2)
    primary: list[list[tuple[int, int]] | None] = []
    ri = 0
    for seg in segments:
        if seg is None:
            primary.append(None)
        else:
            a, b = spans[ri] if ri < len(spans) else (0, 0)
            primary.append(results[ri][a:b] if results[ri] else None)
            ri += 1

    ok = [_plausible(r, seg, int(rows[i]["start_ms"]))
          for i, (r, seg) in enumerate(zip(primary, segments)) if seg is not None]
    ok_full: list[bool] = []
    ki = 0
    for seg in segments:
        if seg is None:
            ok_full.append(False)
        else:
            ok_full.append(ok[ki])
            ki += 1

    # 失败行单行重试（紧窗 + pad）
    failed = [i for i in range(len(rows)) if units_rows[i] and not ok_full[i]]
    retried: dict[int, list[tuple[int, int]]] = {}
    if failed:
        tight = [_segment(audio, anchors[i][0] - 0.5, anchors[i][1] + 0.3,
                          units_rows[i], pad_first=True) for i in failed]
        for i, res in zip(failed, aligner.align(tight)):
            if res and _plausible(res, tight[failed.index(i)], int(rows[i]["start_ms"])):
                retried[i] = res
        if progress:
            progress(len(rows), len(rows) * 2)

    # 组装字级
    out: list[list[Word]] = []
    for i, (row, units) in enumerate(zip(rows, units_rows)):
        line_start = int(row["start_ms"])
        line_end = int(_line_window_end(row["start_ms"], row.get("end_ms"),
                                        len(units) or 1) * 1000)
        span_ms = max(line_end - line_start, 200)
        timings = primary[i] if ok_full[i] else retried.get(i)
        conf = 0.9 if ok_full[i] else 0.6
        if timings and len([t for t in timings if t]) >= max(1, int(0.6 * len(units))):
            words = []
            last = line_start
            for u, t in zip(units, timings):
                if t is None:
                    t = (last, last + MIN_DURATION * 1000)
                # s 双向钳入行窗：防止模型对哼唱/无匹配段输出漂移时间戳
                # （否则 max(line_end, s+1) 会被 s 撑大，钳制逃逸）
                s = max(min(int(t[0]), line_end), line_start, last)
                e = max(min(int(t[1]), line_end, s + span_ms), s + 1)
                words.append((u, s, e, conf))
                last = e
            wl = units_to_words([w[0] for w in words], [(w[1], w[2]) for w in words], conf)
            for w in wl:
                w.confidence = conf
        else:
            # 均摊兜底（调用方也可整体回退 MMS）
            toks = units
            span = max(line_end - line_start, len(toks))
            step = span / max(len(toks), 1)
            wl = units_to_words(toks, [(int(line_start + k * step),
                                        int(line_start + (k + 1) * step))
                                       for k in range(len(toks))], 0.2)
        _postprocess_line(wl, rms)
        out.append(wl)
        if progress:
            progress(len(rows) + i + 1, len(rows) * 2)
    # 行尾侵入裁剪
    for cur, nxt in zip(out, out[1:]):
        if cur and nxt and cur[-1].end > nxt[0].start:
            cur[-1].end = max(cur[-1].start + MIN_DURATION * 1000, nxt[0].start)
    # 均摊率统计：大面积失败（常见于原曲带伴奏、模型匹配不到人声）时明确警告，
    # 提示用户开启人声分离串联，而不是静默产出低质量结果
    ok_count = sum(1 for i in range(len(rows)) if ok_full[i] or i in retried)
    if rows and ok_count / len(rows) < 0.5:
        msg = (f"Qwen3 仅 {ok_count}/{len(rows)} 行对齐成功（其余为均摊估计）——"
               "原曲伴奏干扰或 LRC 时间轴与音频版本不匹配可能性大，"
               "请确认已开启「人声分离串联」且 LRC 与音频为同一版本")
        if notes is not None:
            notes.append(msg)
        if progress is not None:
            try:
                progress(-1, msg)  # 负值标记：调用方可作为警告展示
            except Exception:  # noqa: BLE001
                pass
    return out


# ---- 纯文本定位：Qwen3-ASR-1.7B 词级转写 + 模糊字符匹配（迁移自 AutoKaraoke） ----

_ASR_LANG = {
    "zh": "Chinese", "en": "English", "yue": "Cantonese", "ja": "Japanese",
    "ko": "Korean", "de": "German", "fr": "French", "es": "Spanish",
    "ru": "Russian", "it": "Italian", "th": "Thai", "vi": "Vietnamese",
    "multi": None,  # 多语言混合：Qwen3 原生支持 code-switching，不锁定语言
}
_CHUNK_SECONDS = 30.0


def transcribe_qwen(wav_path: str, language: str = "zh", models_root=None):
    """Qwen3-ASR-1.7B 词级时间戳转写（按人声停顿切段，长唱段不漏重复）。

    返回 [(word_text, start_ms, end_ms), ...]。失败抛异常交调用方回退。
    """
    import numpy as np

    os.environ["HF_HOME"] = str(_hf_home(models_root))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import torch
    from qwen_asr import Qwen3ASRModel

    audio = load_audio(wav_path)
    # 按最静 0.3s 切段（迁移自 split_at_pauses）
    n = len(audio) // 160
    rms = np.sqrt((audio[:n * 160].reshape(n, 160) ** 2).mean(axis=1) + 1e-12)
    smooth = np.convolve(rms, np.ones(30) / 30, mode="same")
    max_frames = int(_CHUNK_SECONDS * SAMPLE_RATE / 160)
    bounds, a = [], 0
    while n - a > max_frames:
        lo, hi = a + max_frames * 2 // 3, a + max_frames
        cut = lo + int(np.argmin(smooth[lo:hi]))
        bounds.append((a * 160, cut * 160))
        a = cut
    bounds.append((a * 160, len(audio)))

    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    model = Qwen3ASRModel.from_pretrained(
        _model_dir(models_root, ASR_MODEL_ID),
        # forced_aligner 必须传本地目录：传 hub id 会让 qwen_asr 走 HF 下载
        # （HF_HUB_OFFLINE=1 下直接连接失败——模型实际在 l2t/models 本地目录）
        forced_aligner=_model_dir(models_root, MODEL_ID),
        forced_aligner_kwargs={"dtype": dtype, "device_map": device},
        max_inference_batch_size=4, max_new_tokens=1024,
        dtype=dtype, device_map=device,
    )
    lang = _ASR_LANG.get((language or "zh").split("_")[0].lower())
    chunks = [(audio[lo:hi], SAMPLE_RATE) for lo, hi in bounds]
    results = model.transcribe(chunks, context="", language=lang,
                               return_time_stamps=True)
    words: list[tuple[str, int, int]] = []
    for (lo, _hi), res in zip(bounds, results):
        if res.time_stamps is None:
            continue
        offset_ms = lo / SAMPLE_RATE * 1000
        for item in res.time_stamps.items:
            words.append((item.text, int(offset_ms + item.start_time * 1000),
                          int(offset_ms + item.end_time * 1000)))
    return words


def _dp_align(l_chars: list[str], t_chars: list[str]) -> dict[int, int]:
    """字符级全局单调 DP 对齐（Needleman-Wunsch）→ {歌词字符下标: 转写字符下标}。

    为什么不用 difflib（贪心最长块）：重复段落（副歌×N）会被最长块一次性
    吃掉或错配到错误的出现位置——爱在西元前实测 27 行漏配、部分行锚点
    跨 33 秒、整块被挤进 1.3 秒窗口。DP 在全局单调约束下求最大匹配分，
    每个转写字符至多用一次，重复歌词行自然按出现顺序分配到各次转写。
    """
    import numpy as np

    M, N = len(l_chars), len(t_chars)
    if not M or not N:
        return {}
    MATCH, MISS, SKIP = 2.0, -1.0, -1.0

    vocab: dict[str, int] = {}

    def _enc(chars: list[str]) -> np.ndarray:
        out = np.empty(len(chars), np.int32)
        for k, c in enumerate(chars):
            out[k] = vocab.setdefault(c, len(vocab))
        return out

    la, ta = _enc(l_chars), _enc(t_chars)

    # dp[i][j] = l[:i] 与 t[:j] 的最优对齐分；转移：对角(配对/错配)、
    # 上(跳过歌词字)、左(跳过转写字)
    dp = np.empty((M + 1, N + 1), np.float64)
    dp[0, 0] = 0.0
    dp[0, 1:] = np.arange(1, N + 1, dtype=np.float32) * SKIP
    dp[1:, 0] = np.arange(1, M + 1, dtype=np.float32) * SKIP
    row_idx = np.arange(1, N + 1, dtype=np.float64)
    base_left = row_idx * SKIP  # 行内连续跳转写字的前缀变换基底
    for i in range(1, M + 1):
        eq = ta == la[i - 1]
        score = np.where(eq, np.float64(MATCH), np.float64(MISS))
        cand_diag = dp[i - 1, :N] + score
        cand_up = dp[i - 1, 1:] + SKIP
        row = np.maximum(cand_diag, cand_up)
        # 行内左移链：row[j] = max(row[j], row[j-1] + SKIP) → 前缀最大值变换
        row = np.maximum.accumulate(row - base_left) + base_left
        dp[i, 1:] = row

    # 回溯（浮点容差比较；优先对角 → 跳歌词 → 跳转写）
    j_of_i: dict[int, int] = {}
    i, j = M, N
    tol = 1e-6
    while i > 0 and j > 0:
        s = MATCH if la[i - 1] == ta[j - 1] else MISS
        if abs(dp[i, j] - (dp[i - 1, j - 1] + s)) <= tol:
            if s == MATCH:
                j_of_i[i - 1] = j - 1
            i -= 1
            j -= 1
        elif abs(dp[i, j] - (dp[i - 1, j] + SKIP)) <= tol:
            i -= 1
        else:
            j -= 1
    return j_of_i


def _locate_rows(transcript_words: list[tuple[str, int, int]],
                 rows: list[dict]) -> list[dict]:
    """转写词流 ↔ 歌词行字符流全局匹配 → 每行 (start_ms, end_ms)。

    字符级全局单调 DP（_dp_align）：重复行按出现顺序分配，行 start =
    行首匹配字符所在转写词的 start；未匹配行夹在相邻匹配行之间插值。
    """
    # 转写字符流（词内字符共享词时间）
    t_chars: list[str] = []
    t_times: list[tuple[int, int]] = []
    for text, ws, we in transcript_words:
        clean = _clean(text)
        if not clean:
            continue
        for ch in clean:
            t_chars.append(ch.lower())
            t_times.append((ws, we))
    l_chars: list[str] = []
    for row in rows:
        for ch in split_units(str(row["text"])):
            l_chars.extend(ch.lower())

    j_of_i = _dp_align(l_chars, t_chars)

    return _assign_times(rows, t_times, j_of_i, l_chars)


def _assign_times(rows, t_times, j_of_i, l_chars):
    """按行顺序累计字符区间，匹配字符 → 时间；未匹配行插值。"""
    out: list[dict] = []
    idx = 0
    matched_rows: list[tuple[int, int, int, int]] = []  # (row_idx, start, end, n_chars)
    row_chars: list[int] = []
    for ri, row in enumerate(rows):
        units = split_units(str(row["text"]))
        n = len(units) and sum(len(u.lower()) for u in units) or len(str(row["text"]))
        row_chars.append(max(n, 1))
        first_j = None
        last_j = None
        for k in range(idx, idx + n):
            if k in j_of_i:
                if first_j is None:
                    first_j = j_of_i[k]
                last_j = j_of_i[k]
        if first_j is not None and last_j is not None:
            matched_rows.append((ri, t_times[first_j][0], t_times[last_j][1], n))
        else:
            matched_rows.append((ri, None, None, n))
        idx += max(n, 1)

    # ---- 行窗防御：部分字符匹配（转写残缺/含混）→ 行窗远小于实际演唱时长，
    # 精对齐被迫把整句挤进几百毫秒（爱在西元前实测 8 字 0.32s）。按匹配行
    # 每字时长中位数估计期望时长，不足 70% 时向两端扩展（不越过邻行）。 ----
    _known = [(m[2] - m[1]) / max(m[3], 1) for m in matched_rows
              if m[1] is not None and m[2] > m[1]]
    if _known:
        _known.sort()
        cps_ms = _known[len(_known) // 2]
        _mi = [k for k, m in enumerate(matched_rows) if m[1] is not None]
        for cnt, k in enumerate(_mi):
            ri, s, e, n = matched_rows[k]
            expected = n * cps_ms
            actual = e - s
            if actual >= expected * 0.7:
                continue
            need = expected - actual
            nxt_s = matched_rows[_mi[cnt + 1]][1] if cnt + 1 < len(_mi) else None
            prv_e = matched_rows[_mi[cnt - 1]][2] if cnt else None
            room_end = (nxt_s - 300 - e) if nxt_s is not None else need
            room_start = (s - 300 - prv_e) if prv_e is not None else need
            ext_end = min(need * 0.7, max(room_end, 0))
            e += int(ext_end)
            ext_start = min(need - ext_end, max(room_start, 0))
            s -= int(ext_start)
            matched_rows[k] = (ri, s, e, n)

    # 未匹配行夹在相邻匹配行之间线性插值；首尾外推用最近匹配行时长
    for pos, (ri, s, e, _nc) in enumerate(matched_rows):
        if s is not None:
            out.append({"text": rows[ri]["text"], "start_ms": s, "end_ms": e})
            continue
        prev_m = next(((m[1], m[2]) for m in reversed(matched_rows[:pos])
                       if m[1] is not None), None)
        next_m = next(((m[1], m[2]) for m in matched_rows[pos + 1:]
                       if m[1] is not None), None)
        if prev_m and next_m:
            span = max(next_m[0] - prev_m[1], 0)  # 乱序匹配防御：负跨度钳 0
            near = [m for m in matched_rows[:pos] if m[1] is not None]
            k_near = len(near) + 1
            total_unmatched = pos - (len(near) - 1)
            frac = k_near / max(total_unmatched, 1)
            out.append({"text": rows[ri]["text"],
                        "start_ms": int(prev_m[1] + span * frac * 0.8),
                        "end_ms": int(prev_m[1] + span * frac)})
        elif next_m:
            dur = next_m[1] - next_m[0] or 3000
            out.append({"text": rows[ri]["text"], "start_ms": max(next_m[0] - dur, 0),
                        "end_ms": next_m[0]})
        elif prev_m:
            out.append({"text": rows[ri]["text"], "start_ms": prev_m[1] + 200,
                        "end_ms": prev_m[1] + 200 + (prev_m[1] - prev_m[0] or 3000)})
        else:
            out.append({"text": rows[ri]["text"], "start_ms": 0, "end_ms": 3000})
    # 单调防护
    for a, b in zip(out, out[1:]):
        if b["start_ms"] < a["end_ms"]:
            a["end_ms"] = b["start_ms"]
    return out




def _fill_anchors(texts: list[str],
                  anchors: list[tuple[int, int] | None]) -> list[dict]:
    """锚点补全：未匹配的歌词行在相邻锚点间按字符占比插值（头部/尾部外推）。

    用户歌词一行都不能丢——定位器（Whisper/Qwen3-ASR 转写）与歌词的模糊匹配
    失败只说明"找不到这行的粗位置"，用邻行插值兜住，行窗精对齐仍会尝试。
    """
    n = len(texts)
    known = [i for i, a in enumerate(anchors) if a]
    if not known:
        return []
    out: list[tuple[int, int] | None] = list(anchors)
    # 每字符时长估计（已知行的中位数）
    durs = sorted((anchors[i][1] - anchors[i][0]) / max(len(texts[i]), 1)
                  for i in known)
    cps = durs[len(durs) // 2] if durs else 200.0
    for i in range(n):
        if out[i] is not None:
            continue
        pj = next((j for j in range(i - 1, -1, -1) if out[j]), None)
        nj = next((j for j in range(i + 1, n) if out[j]), None)
        if pj is not None and nj is not None:
            # 在 pj.end 与 nj.start 之间按字符占比分配
            between = list(range(pj + 1, nj))
            span0, span1 = out[pj][1], out[nj][0]
            total = sum(max(len(texts[k]), 1) for k in between)
            acc = span0
            for k in between:
                d = int((span1 - span0) * max(len(texts[k]), 1) / max(total, 1))
                out[k] = (acc, acc + max(d, 200))
                acc += max(d, 200)
        elif pj is not None:
            start = out[pj][1] + 300
            out[i] = (start, start + max(int(len(texts[i]) * cps), 300))
        elif nj is not None:
            end = max(out[nj][0] - 300, 200)
            out[i] = (max(end - int(len(texts[i]) * cps), 0), end)
    # 全局 sanitize：≥0 且 end ≥ start+200（模糊匹配乱序时的负跨度防御）
    filled: list[dict] = []
    for t, pair in zip(texts, out):
        if pair is None:
            continue
        s0 = max(int(pair[0]), 0)
        e0 = max(int(pair[1]), s0 + 200)
        filled.append({"text": t, "start_ms": s0, "end_ms": e0})
    return filled


def align_text_qwen(wav_path: str, rows: list[dict], *, models_root=None,
                    language: str = "zh", progress=None,
                    whisper_skeleton=None, repo_id: str = MODEL_ID,
                    use_head: bool = False):
    """纯文本：Qwen3-ForcedAligner 对齐（整曲一次 或 超长分段精对齐）。

    ≤290s：整曲一次对齐（_whole_song_pass），行边界 = 行首/末字时间。
    >290s：**破除模型时长限制**——用 Whisper DP 行级骨架（whisper 转写无
    时长限制）拿到每行粗锚，再交给 align_lrc_lines 逐行窗精对齐（行级切片
    每窗仅几秒，天然无限制）。whisper_skeleton：调用方预计算的行级骨架
    [(text, start_ms, end_ms)]，缺省时本函数内部不做转写（需调用方传入）。

    返回 (tifa_rows, words_rows, model_tag)。
    """
    import numpy as np

    rows = [{"text": str(r["text"])} for r in rows]

    # ---- 超长分支：whisper 行骨架 → 锚点补全 → Qwen3 行窗精对齐（分段无限制） ----
    if whisper_skeleton:
        # ① 骨架锚按文本顺序映射到歌词行（重复行按出现顺序消费）
        anchors: list[tuple[int, int] | None] = [None] * len(rows)
        si = 0
        for t, s, e in whisper_skeleton:
            if e <= s:
                continue
            key = str(t).strip()
            hit = next((i for i in range(si, len(rows))
                        if str(rows[i]["text"]).strip() == key and anchors[i] is None), None)
            if hit is None:
                hit = next((i for i in range(len(rows))
                            if str(rows[i]["text"]).strip() == key and anchors[i] is None), None)
            if hit is not None:
                anchors[hit] = (int(s), int(e))
                si = hit + 1
        # ② 锚点补全：未匹配的行在相邻锚点间插值，绝不丢用户歌词行
        base_rows = _fill_anchors([str(r["text"]) for r in rows], anchors)
        if base_rows:
            # 全部行都有锚 → 行窗精对齐（align_lrc_lines 逐行切片无限制）
            words_rows = align_lrc_lines(wav_path, base_rows,
                                         models_root=models_root, progress=progress,
                                         repo_id=repo_id)
            if words_rows:
                out_rows = [{"text": str(r["text"]),
                             "start_ms": int(w[0].start),
                             "end_ms": int(max(w[-1].end, w[0].start + 200))}
                            for r, w in zip(base_rows, words_rows) if w]
                if len(out_rows) >= max(3, int(0.5 * len(rows))):
                    return (out_rows, words_rows, "imported-text+qwen3-fa-seg")
        # 骨架不可用 → 落到下面整曲路径（若仍在限制内）或抛异常回退

    audio = load_audio(wav_path)
    duration = len(audio) / SAMPLE_RATE
    if duration > MAX_SECONDS:
        raise ImportError(
            f"纯文本整曲对齐仅支持 ≤290 秒的音频（当前 {duration:.0f}s）；"
            "无法取得可用的行级骨架。请提供 LRC 歌词或改用 MMS 引擎")

    aligner = QwenAligner(models_root=models_root, repo_id=repo_id,
                          use_head=use_head, language=language)
    units_rows = [split_units(str(r["text"])) for r in rows]
    units = [u for row in units_rows for u in row]
    if not units:
        raise ImportError("歌词没有可对齐的文本单元")

    # 整曲一次：前置 1s 静音 pad（首单元不贴窗边，模型习惯从 0 开始钉）
    seg_audio = np.concatenate([np.zeros(SAMPLE_RATE, np.float32), audio])
    seg = {"start": -1.0, "audio": seg_audio, "units": units}
    if progress:
        progress(0.1, "Qwen3-FA 整曲对齐")
    timings_all = aligner.align([seg])[0]

    # 行边界 = 该行首/末字时间；失败字（None）跳过
    out_rows: list[dict] = []
    words_rows: list[list[Word]] = []
    k = 0
    for row, urow in zip(rows, units_rows):
        n = len(urow)
        seg_t = timings_all[k:k + n]
        k += n
        valid = [t for t in seg_t if t]
        if not valid:
            continue  # 整行无时间 → 丢弃（调用方无该行）
        start_ms = max(int(valid[0][0]), 0)
        end_ms = max(int(valid[-1][1]), start_ms + 200)
        words: list[Word] = []
        for u, t in zip(urow, seg_t):
            if t is None:
                continue
            chars = list(u)
            span = max(t[1] - t[0], 20 * len(chars))
            step = span / len(chars)
            for kk, ch in enumerate(chars):
                words.append(Word(ch, int(t[0] + kk * step),
                                  int(t[0] + (kk + 1) * step), 0.9))
        if not words:
            continue
        rms = _rms(audio)
        _postprocess_line(words, rms)
        out_rows.append({"text": str(row["text"]),
                         "start_ms": int(words[0].start),
                         "end_ms": int(max(words[-1].end, start_ms + 200))})
        words_rows.append(words)
        if progress:
            progress(len(out_rows), len(rows) + 1)
    # 行尾侵入裁剪
    for cur, nxt in zip(words_rows, words_rows[1:]):
        if cur and nxt and cur[-1].end > nxt[0].start:
            cur[-1].end = max(cur[-1].start + MIN_DURATION * 1000, nxt[0].start)
    if not out_rows:
        raise ImportError("Qwen3-FA 整曲对齐没有产出任何行")
    return out_rows, words_rows, "imported-text+qwen3-fa"


# 兼容别名（import_align 的 _lyrics_from_qwen_lrc 引用）
align_line_windows_qwen = align_lrc_lines
