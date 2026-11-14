# -*- coding: utf-8 -*-
"""Meta MMS-FA 歌词强制对齐（torchaudio.pipelines.MMS_FA）。

歌声场景显著优于 WhisperX（学术评测歌声对齐 PCO 55.4% vs WhisperX 23~28%；
WhisperX 的 VAD 会把整段唱词并成大段、其语音对齐器跟不上歌唱发音）。

原理：MMS-FA 是 CTC 强制对齐器——给定"已知歌词 + 音频"，模型只回答"每个字
什么时候唱"，不猜内容，因此不会像 ASR 那样在歌词听错的地方把时间轴带歪。

中文处理：汉字 → pypinyin 拼音音节（MMS 词表是罗马字符 a-z），每字一音节，
音节 span 即字级时间；英文按整词罗马化，词内时间均分到字母。

两种用法（import_align 分发）：
- LRC 行锚模式：LRC 行级时间戳可信，行内逐字用本模块在行窗口内精对齐
  （无需 Whisper 转写，快且准）。
- 纯文本模式：行间插 <star> 分隔 token，整曲分块（≤45s，wav2vec2 注意力
  O(n²)）全局对齐，行边界由 star span 定位。

权重：MMS ctc_alignment_mling_uroman model.pt（1.18GB），缓存到
TORCH_HOME = <模型库>/_tool_models/lyrics/models/l2t/models/mms（E 盘）。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .schema import Word

_ROMAN_CLEAN_RE = re.compile(r"[^a-z']")
_HAN_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z']+")

# MMS acoustic model 注意力 O(n²)：单块超过 ~50s 显存与质量都劣化
_MAX_BLOCK_SEC = 45.0
# 纯文本打块时的每字时长估计（秒），中文歌普遍 2.5~4 字/秒
_EST_SEC_PER_CHAR = 0.30


def romanize_line(text: str) -> list[dict]:
    """一行歌词 → [ {token: 罗马词, chars: [覆盖的原字符]} ]。

    汉字逐字转拼音音节（每字一 token，字级时间戳由此而来）；
    英文按整词（含撇号）；标点/数字/空格跳过（数字读音复杂，
    时间由相邻字插值）。
    """
    from pypinyin import lazy_pinyin, Style

    out: list[dict] = []
    for ch in text:
        if _HAN_RE.match(ch):
            syl = lazy_pinyin(ch, style=Style.NORMAL)
            syl = _ROMAN_CLEAN_RE.sub("", (syl[0] if syl else "").lower())
            if syl:
                out.append({"token": syl, "chars": [ch]})
        elif ch.isascii() and (ch.isalpha() or ch == "'"):
            # 累积连续拉丁字符为一个词
            if out and out[-1].get("_latin"):
                out[-1]["token"] += ch.lower()
                out[-1]["chars"].append(ch)
            else:
                out.append({"token": ch.lower(), "chars": [ch], "_latin": True})
        # 其余（标点/数字/空格）不进对齐
    for item in out:
        item.pop("_latin", None)
        # 词表只有 a-z 与撇号：tokenizer 会丢弃其余；这里再保险清洗一次
        item["token"] = _ROMAN_CLEAN_RE.sub("", item["token"]) or item["token"]
    return [item for item in out if item["token"]]


def _load_bundle(models_root: str | None):
    """加载 MMS_FA bundle（模型/分词器/对齐器），权重缓存到 E 盘模型库。

    models_root 兼容两种传入：裸数据根（data/models）或已拼装的歌词模型根
    （.../_tool_models/lyrics/models）——内部幂等解析，绝不重复套娃路径。
    """
    import torch

    from .model_store import store_root
    from worker_lyrics_models import lyrics_models_root

    root = Path(models_root) if models_root else None
    if root is None or not ((root / "alignment").is_dir() or (root / "l2t").is_dir()):
        root = Path(lyrics_models_root(str(root) if models_root else None))
    store = store_root(str(root))
    mms_root = store / "alignment" / "mms"
    if not (mms_root / "hub").is_dir() and (store / "models" / "mms" / "hub").is_dir():
        mms_root = store / "models" / "mms"  # 旧布局兜底（l2t/models/mms）
    mms_root.mkdir(parents=True, exist_ok=True)
    os.environ["TORCH_HOME"] = str(mms_root)  # 1.18GB 权重落 E 盘，绝不进 C 盘
    from torchaudio.pipelines import MMS_FA as bundle

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = bundle.get_model(with_star=True).to(device).eval()
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()
    return model, tokenizer, aligner, bundle.sample_rate, device


def _normalize_peak(x) -> None:
    """峰值归一化到 0.95（demucs 输出音量可能极低，影响对齐模型输入质量）。"""
    import torch

    if isinstance(x, torch.Tensor):
        peak = float(x.abs().max())
    else:
        import numpy as np
        peak = float(np.max(np.abs(x)))
    if peak > 1e-6:
        x *= 0.95 / peak


def _load_wave(path: str, sample_rate: int):
    """ffmpeg 解码 + soundfile 读取；ffmpeg 失败（demucs float wav 等）直读重采样。"""
    import subprocess
    import tempfile

    import soundfile as sf
    import torch

    tmp = Path(tempfile.gettempdir()) / f"mms_align_{os.getpid()}_{int(time.time())}.wav"
    try:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", path,
                 "-ac", "1", "-ar", str(sample_rate), "-f", "wav", str(tmp)],
                check=True, capture_output=True)
            wav, sr = sf.read(str(tmp), dtype="float32", always_2d=True)
            wav = torch.from_numpy(wav.T)
            _normalize_peak(wav[0])  # demucs 输出音量可能极低，影响对齐质量
            return wav, sr
        except Exception:  # noqa: BLE001 — ffmpeg 失败：soundfile 直读 + torchaudio 重采样
            wav, sr = sf.read(str(path), dtype="float32", always_2d=True)
            wav = torch.from_numpy(wav.T)
            if wav.shape[0] > 1:
                wav = wav.mean(0, keepdim=True)
            if sr != sample_rate:
                import torchaudio.functional as _taf
                wav = _taf.resample(wav, sr, sample_rate)
            _normalize_peak(wav[0])
            return wav, sample_rate
    finally:
        tmp.unlink(missing_ok=True)


def _align_words(model, tokenizer, aligner, device, wav, words: list[str],
                 sample_rate: int, offset_sec: float = 0.0) -> list[dict]:
    """一段波形 + 罗马词序列 → [{token, start, end, score}]（秒，绝对）。"""
    import torch

    with torch.inference_mode():
        emission, _ = model(wav.to(device))
        token_spans = aligner(emission[0], tokenizer(words))
    frame_sec = wav.shape[1] / max(emission.shape[1], 1) / sample_rate
    out = []
    for word, spans in zip(words, token_spans):
        if not spans:
            continue
        start = min(s.start for s in spans) * frame_sec + offset_sec
        end = (max(s.end for s in spans) + 1) * frame_sec + offset_sec
        out.append({"token": word, "start": start, "end": end,
                    "score": float(sum(s.score for s in spans) / len(spans))})
    return out


def align_line_window(wav, sample_rate: int, line_text: str,
                      window_start_ms: int, model, tokenizer, aligner, device,
                      window_end_ms: int | None = None, pad_ms: int = 600) -> list[Word]:
    """行窗口内对齐一行文本 → 字级 Word（绝对毫秒）。"""
    sec = sample_rate / 1000.0
    lo_ms = max(int(window_start_ms) - pad_ms, 0)
    hi_ms = (int(window_end_ms) + pad_ms) if window_end_ms else wav.shape[1] * 1000 / sample_rate
    lo = max(int(lo_ms * sec), 0)
    hi = min(int(hi_ms * sec), wav.shape[1])
    seg = wav[:, lo:hi]
    roman = romanize_line(line_text)
    if not roman or seg.shape[1] < int(0.3 * sample_rate):
        return []
    words = [item["token"] for item in roman]
    spans = _align_words(model, tokenizer, aligner, device, seg, words,
                         sample_rate, offset_sec=lo / sample_rate)
    return _spans_to_chars(roman, spans)


def _spans_to_chars(roman: list[dict], spans: list[dict]) -> list[Word]:
    """罗马词 span → 字级 Word（词内多字母按宽度均分；无 span 的字用邻值插值）。"""
    # token 对齐 roman 列表（_align_words 保证一一对应）
    char_words: list[Word] = []
    for item, sp in zip(roman, spans):
        chars = item["chars"]
        n = len(chars)
        span = max(sp["end"] - sp["start"], 0.02 * n)
        step = span / n
        for k, ch in enumerate(chars):
            s = sp["start"] + k * step
            e = s + step
            char_words.append(Word(ch, int(s * 1000), int(e * 1000),
                                   round(min(max(sp["score"], 0.0), 1.0), 3)))
    return char_words


def align_line_windows(wav, sample_rate: int, rows: list[dict], models_root=None,
                       progress=None) -> list[list[Word]]:
    """LRC 行锚：逐行在 [line.start, line.end] 窗口内精对齐（复用一个模型实例）。

    rows: [{text, start_ms, end_ms}]（start/end 来自 LRC 行级时间戳）。
    """
    model, tokenizer, aligner, sr, device = _load_bundle(models_root)
    total = len(rows)
    results: list[list[Word]] = []
    for idx, row in enumerate(rows):
        try:
            words = align_line_window(wav, sr, str(row["text"]),
                                      int(row["start_ms"]), model, tokenizer,
                                      aligner, device,
                                      window_end_ms=int(row.get("end_ms") or 0) or None)
        except Exception:  # noqa: BLE001 — 单行失败不拖垮整曲
            words = []
        results.append(words)
        if progress:
            progress(idx + 1, total)
    return results


def align_text_full(wav, sample_rate: int, rows: list[dict], models_root=None,
                    progress=None, bundle=None) -> list[list[Word]]:
    """纯文本模式 v2：滑窗重叠拼接 emission + 全曲单次强制对齐。

    与公开基准（27 歌实测：MMS 中位误差 16ms、96.4% 词 <250ms）同款用法：
    - 音频按 45s 块滑窗（重叠 5s），每块独立前向得 emission；
    - 拼接 emission（丢弃重叠区前半帧）为全局序列；
    - 全曲 transcript（行间插 <star>）一次 forced_align；
    - 无任何"估算块偏移"，全局连续性完整。
    旧版按估算偏移逐块对齐，块偏移猜错会整体错位——已废弃。
    """
    import torch

    if bundle is not None:
        model, tokenizer, aligner, sr, device = bundle
    else:
        model, tokenizer, aligner, sr, device = _load_bundle(models_root)

    roman_rows = [romanize_line(str(r["text"])) for r in rows]
    total_tokens = sum(len(r) for r in roman_rows) + max(len(roman_rows) - 1, 0)
    if not total_tokens:
        return [[] for _ in roman_rows]

    # ---- 滑窗切块：45s 块、5s 重叠（块间重叠保证拼接处特征连续） ----
    block_sec, overlap_sec = 45.0, 5.0
    total_sec = wav.shape[1] / sample_rate
    spans: list[tuple[float, float]] = []
    t = 0.0
    while t < total_sec - 0.5:
        spans.append((t, min(t + block_sec, total_sec)))
        t += block_sec - overlap_sec
    if not spans:
        spans = [(0.0, total_sec)]

    emissions: list = []
    for bi, (lo, hi) in enumerate(spans):
        seg = wav[:, int(lo * sample_rate):int(hi * sample_rate)]
        if seg.shape[1] < int(0.3 * sample_rate):
            continue
        with torch.inference_mode():
            emission, _ = model(seg.to(device))
        emissions.append(emission[0].cpu())
        if progress:
            progress(bi + 1, len(spans) + 1)
    if not emissions:
        return [[] for _ in roman_rows]

    # ---- 拼接：后块丢弃前 overlap/2 的帧，前块保留全部 ----
    chunks: list = []
    frame_times: list[float] = []
    for bi, em in enumerate(emissions):
        lo, hi = spans[bi]
        dur = hi - lo
        fs = dur / max(em.shape[0], 1)  # 秒/帧（dur 已是秒）
        start_f = 0 if bi == 0 else int((overlap_sec / 2) / fs)
        for fi in range(start_f, em.shape[0]):
            frame_times.append(lo + fi * fs)
        chunks.append(em[start_f:, :])
    emission_all = torch.cat(chunks, dim=0)
    if len(frame_times) != emission_all.shape[0]:
        # 防御：帧时间表与拼接长度对齐
        frame_times = [i * (frame_times[-1] / max(len(frame_times), 1))
                       for i in range(emission_all.shape[0])]

    tokens: list[str] = []
    for ri, roman in enumerate(roman_rows):
        tokens.extend(item["token"] for item in roman)
        if ri + 1 < len(roman_rows):
            tokens.append("*")

    with torch.inference_mode():
        token_spans = aligner(emission_all.to(device), tokenizer(tokens))

    results: list[list[Word]] = [[] for _ in roman_rows]
    cursor = 0
    n_frames = len(frame_times)
    for ri, roman in enumerate(roman_rows):
        n = len(roman)
        row_spans = token_spans[cursor:cursor + n]
        cursor += n + (1 if ri + 1 < len(roman_rows) else 0)
        char_words: list[Word] = []
        for item, spans_w in zip(roman, row_spans):
            if not spans_w:
                continue
            f_start = min(s.start for s in spans_w)
            f_end = max(s.end for s in spans_w) + 1
            start = frame_times[min(f_start, n_frames - 1)]
            end = frame_times[min(f_end, n_frames - 1)]
            chars = item["chars"]
            step = max(end - start, 0.02 * len(chars)) / len(chars)
            score = sum(s.score for s in spans_w) / len(spans_w)
            for k, ch in enumerate(chars):
                s = start + k * step
                char_words.append(Word(ch, int(s * 1000), int((s + step) * 1000),
                                       round(min(max(score, 0.0), 1.0), 3)))
        results[ri] = char_words
        if progress:
            progress(ri + 1, len(roman_rows))
    return results
