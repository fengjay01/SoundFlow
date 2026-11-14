# -*- coding: utf-8 -*-
"""M2：WhisperX 自动识别 + 字级时间戳 → 统一歌词 JSON。

管线：ffmpeg 抽 16k 单声道 wav → whisperx 转写（faster-whisper）→
whisperx 强制对齐（wav2vec2 CTC，return_char_alignments=True）→ chars 聚合成 words → Lyrics。

设计决策：
- WhisperX 的 char 级对齐直接给中文逐字时间戳（SF-004/005/006 前半程）；
- 拉丁字符 run 合并成整词（tokenize_pronounceable 的语义），标点/空白不是 word；
- 对齐失败的段落退化为区间均摊并打低置信度（confidence=0.1，标记 fallback-spread），
  仅供 M2 冒烟闭环；M3 的质检/局部重对齐会替换该退化路径。
"""
from __future__ import annotations

import re
import subprocess
from typing import Optional

from lyrics.schema import Line, Lyrics, Word, validate, tokenize_pronounceable, WORD_TOKEN_RE

class ModelMissingError(RuntimeError):
    """模型既不在歌词管理目录，也不在系统缓存（错误码 LYRICS_MODEL_MISSING）。"""

# 管理目录 cache_name（<owner--model>）与 HF repo id 映射，与
# worker_lyrics_models.LYRICS_MODELS 保持一致（lyrics 包不依赖 worker_*）。
_ALIGN_CACHE_NAMES = {
    "zh": "jonatasgrosman--wav2vec2-large-xlsr-53-chinese-zh-cn",
    "en": "facebook--wav2vec2-base-960h",
}
_ASR_CACHE_NAMES = {
    "tiny": "Systran--faster-whisper-tiny",
    "base": "Systran--faster-whisper-base",
    "small": "Systran--faster-whisper-small",
    "medium": "Systran--faster-whisper-medium",
}
_ALIGN_HF_IDS = {
    "zh": "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
    "en": "facebook/wav2vec2-base-960h",
}

def _lang_key(lang: str) -> str:
    return "zh" if lang.startswith("zh") else ("en" if lang.startswith("en") else "")

def resolve_asr_model_source(model_size: str, models_root: Optional[str] = None) -> str:
    """解析 ASR 模型来源：管理目录优先，否则用名字探测系统缓存。

    额外支持 lrc-ttml 模型仓库（<models_root>/l2t/hf_cache/models--*/snapshots/*），
    large-v3 等大模型从那里解析。
    """
    from pathlib import Path

    if model_size and Path(model_size).is_dir():
        return model_size
    key = str(model_size).removeprefix("whisper-")
    if models_root:
        names: list[str] = []
        mapped = _ASR_CACHE_NAMES.get(key) or _ASR_CACHE_NAMES.get(key.removeprefix("faster-whisper-"))
        if mapped:
            names.append(mapped)
        names.append(key.replace("/", "--"))
        for name in names:
            # 分类布局：<root>/asr/<name> 优先，旧布局（根级 <name>）兜底
            for base in (Path(models_root) / "asr", Path(models_root)):
                candidate = base / name
                if candidate.is_dir() and (candidate / "config.json").is_file():
                    return str(candidate)
        # lrc-ttml 模型仓库：asr/hf_cache/models--<repo>/snapshots/<rev>（旧 l2t/hf_cache 兜底）
        repo_id = _ASR_HF_REPO.get(key) or (key if "/" in key else "")
        if repo_id:
            cache_key = "models--" + repo_id.replace("/", "--")
            for hf_base in (Path(models_root) / "asr" / "hf_cache",
                            Path(models_root) / "l2t" / "hf_cache"):
                snaps = hf_base / cache_key / "snapshots"
                if snaps.is_dir():
                    for snap in sorted(snaps.iterdir()):
                        if (snap / "model.bin").is_file():
                            return str(snap)
    return model_size


# 尺寸名 → HF 仓库（lrc-ttml 模型仓库布局解析用）
_ASR_HF_REPO = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
}

def extract_wav(input_path: str, out_wav: str, sr: int = 16000,
                start: Optional[float] = None, duration: Optional[float] = None,
                ffmpeg: str = "ffmpeg") -> str:
    """用 ffmpeg 抽 16k 单声道 wav（WhisperX 输入要求）。"""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", input_path]
    if start is not None:
        cmd += ["-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), out_wav]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg extract_wav failed:\n{r.stderr[-2000:]}")
    return out_wav

def _ms(sec) -> int:
    return int(round(float(sec) * 1000))

def _iter_tokens(text: str):
    """行文本 → token 序列。

    必须与 schema.tokenize_pronounceable 完全一致（同一正则，单一事实来源）：
    CJK 单字成词；拉丁/数字 run 含词中撇号（you're / I'm）合并成整词；
    其余标点与空白不构成 token。历史上这里与 schema 各写了一套规则，
    撇号处理不一致导致英文歌词每行都校验失败（LYRICS_INVALID）。
    """
    return iter(WORD_TOKEN_RE.findall(text))

def _chars_to_words(chars: list) -> list:
    """whisperx char 列表 → schema.Word 列表。

    分组用与 _iter_tokens 完全相同的正则扫过拼接后的字符流，从构造上保证
    words 序列 == text 分词：CJK 单字成词；拉丁/数字 run（含词中撇号）合并
    成整词；标点与空白不属于任何 token，直接丢弃（无时长，符合 PRD）。
    未对齐（start/end 缺失）的字符仍参与分组以保持 token 序列稳定，时间戳
    取该 token 内已对齐字符的范围；整 token 均未对齐时挂在前一个词末尾。
    """
    entries: list = []
    for c in chars:
        ch = c.get("char") or ""
        start = c.get("start")
        end = c.get("end")
        for sub in ch:  # 防御：whisperx 常规为单字符，这里按字符展开保证下标对齐
            entries.append((sub,
                            _ms(start) if start is not None else None,
                            _ms(end) if end is not None else None,
                            c.get("score")))
    text = "".join(ch for ch, *_rest in entries)
    words: list = []
    for m in WORD_TOKEN_RE.finditer(text):
        span = entries[m.start():m.end()]
        timed = [(s, e) for _ch, s, e, _sc in span if s is not None and e is not None]
        if timed:
            start = min(s for s, _e in timed)
            end = max(e for _s, e in timed)
        elif words:
            start = end = words[-1].end
        else:
            start = end = 0
        scores = [sc for _ch, _s, _e, sc in span if sc is not None]
        score = sum(scores) / len(scores) if scores else 0.0
        words.append(Word(m.group(), start, end, round(min(max(score, 0.0), 1.0), 3)))
    return words

def _fallback_spread_words(text: str, start_s: float, end_s: float) -> list:
    """对齐缺失时的退化路径：区间均摊 + 低置信度（M3 将以局部重对齐替换）。"""
    toks = list(_iter_tokens(text))
    if not toks:
        return []
    dur = max(end_s - start_s, 0.001) / len(toks)
    out = []
    for i, t in enumerate(toks):
        s = start_s + i * dur
        out.append(Word(t, _ms(s), _ms(s + dur), 0.1))
    return out

def resolve_align_model_source(align_model: Optional[str], language: str,
                               models_root: Optional[str] = None) -> Optional[str]:
    """解析对齐模型来源：显式指定 > 管理目录 > HF id（探测系统缓存）> whisperx 默认。

    返回值直接作为 whisperx.load_align_model(model_name=...)：本地目录路径或
    HF repo id 均可；None 时由 whisperx 按语言查默认表。
    """
    from pathlib import Path

    if align_model:
        return align_model
    key = _lang_key(language)
    if key and key in _ALIGN_CACHE_NAMES and models_root:
        # 分类布局 <root>/asr/<name> 优先，旧布局（根级）兜底
        for base in (Path(models_root) / "asr", Path(models_root)):
            candidate = base / _ALIGN_CACHE_NAMES[key]
            if candidate.is_dir() and (candidate / "config.json").is_file():
                return str(candidate)
    return _ALIGN_HF_IDS.get(key)  # zh/en → HF id 探测系统缓存；其他语言 None 走默认

def _kwarg_safe(model_call, *args, **kwargs):
    """调用 whisperx transcribe，兼容旧版本不认识的参数（TypeError 时逐个剔除重试）。"""
    while True:
        try:
            return model_call(*args, **kwargs)
        except TypeError as exc:
            msg = str(exc)
            m = re.search(r"unexpected keyword argument '(\w+)'", msg)
            if m and m.group(1) in kwargs:
                kwargs = {k: v for k, v in kwargs.items() if k != m.group(1)}
                continue
            raise


def transcribe(audio_path: str, model_size: str = "small", language: Optional[str] = None,
               device: str = "cpu", compute_type: str = "int8", batch_size: int = 8,
               align_model: Optional[str] = None,
               models_root: Optional[str] = None,
               initial_prompt: Optional[str] = None) -> dict:
    """whisperx 转写 + 字级强制对齐，返回带 chars 的 segments dict。

    models_root：歌词工具模型根（<root>/_tool_models/lyrics/models 的父级 models 根），
    传入后优先使用已下载的管理目录模型；均缺失时 local_files_only 探测系统缓存，
    仍失败抛 ModelMissingError（不静默联网下载，符 SF-014 模型管理语义）。
    initial_prompt：歌词文本提示词（迁移自 lrc-ttml：把歌词喂给 whisper，
    转写主动向正确文本靠拢）；condition_on_previous_text=False 抑制复读幻觉
    （lrc-ttml 实测同一经验）。
    """
    try:
        import whisperx  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "whisperx 未安装。请先: python -m pip install --break-system-packages whisperx"
        ) from e

    if language == "multi":
        language = None  # 多语言混合：交给模型自动检测/原生混语
    asr_source = resolve_asr_model_source(model_size, models_root)
    try:
        model = whisperx.load_model(asr_source, device, compute_type=compute_type,
                                    language=language, asr_options={"without_timestamps": True},
                                    vad_options={"vad_onset": 0.001, "vad_offset": 0.999},  # 歌声骗过 VAD，阈值压到极低等效关闭
                                    local_files_only=True)
    except Exception as e:
        raise ModelMissingError(
            f"Whisper {model_size} 模型未下载，请到「模型管理」页下载后重试"
            f"（工作台只从本地加载模型）。原始错误: {e}"
        ) from e
    audio = whisperx.load_audio(audio_path)
    # VAD 必须关闭：歌声不是语音，pyannote/silero VAD 会把人声段整段吞掉
    # （lrc-ttml 实测漏掉 70%+ 人声 → "No active speech found" → 时间轴为空）
    result = _kwarg_safe(
        model.transcribe, audio, batch_size=batch_size,
        initial_prompt=initial_prompt or None,
        condition_on_previous_text=False,
        vad_filter=False,
    )
    lang = result.get("language") or language or "zh"
    del model  # 及时释放（PRD 资源管理）

    # 歌声切片的自动检测语言不可靠（会把歌词窗判成 nn/si 等小语种）：
    # 用户显式选择语言时锁定之；对齐模型加载失败再回退 zh 重试一次
    if language:
        lang = language
    align_source = resolve_align_model_source(align_model, lang, models_root)
    try:
        model_a, metadata = whisperx.load_align_model(
            language_code=lang, device=device,
            model_name=align_source, model_cache_only=True)
    except Exception as e:
        if lang != "zh":
            lang = "zh"
            align_source = resolve_align_model_source(align_model, lang, models_root)
            model_a, metadata = whisperx.load_align_model(
                language_code=lang, device=device,
                model_name=align_source, model_cache_only=True)
        else:
            raise ModelMissingError(
                f"对齐模型不可用（语言 {lang}，来源 {align_source or 'whisperx 默认'}）。"
                f"请先下载对应 align 模型后重试。原始错误: {e}"
            ) from e
    aligned = whisperx.align(result["segments"], model_a, metadata, audio, device,
                             return_char_alignments=True)
    return {"language": lang, "segments": aligned["segments"]}


def transcribe_slices(audio_path: str, windows: list[tuple[float, float]],
                      prompts: list[Optional[str]], *,
                      model_size: str = "small", language: Optional[str] = None,
                      device: str = "cpu", compute_type: str = "int8",
                      align_model: Optional[str] = None,
                      models_root: Optional[str] = None) -> dict:
    """LRC 锚点切片转写（迁移自 lrc-ttml）：按行时间窗逐片转写 + 对齐。

    windows/prompts 按下标一一对应；每片以该行歌词文本为 initial_prompt，
    转写主动向正确歌词靠拢。模型只加载一次；片段时间戳统一加回 offset。
    vad_filter 保持关闭——歌曲的人声+伴奏混合会骗过 silero VAD
    （lrc-ttml 实测会漏掉 70% 以上的人声段）。
    返回 {"language", "segments"}，segments 已按窗口顺序合并。
    """
    try:
        import whisperx  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "whisperx 未安装。请先: python -m pip install --break-system-packages whisperx"
        ) from e

    asr_source = resolve_asr_model_source(model_size, models_root)
    model = whisperx.load_model(asr_source, device, compute_type=compute_type,
                                language=language, asr_options={"without_timestamps": True},
                                vad_options={"vad_onset": 0.001, "vad_offset": 0.999},  # 歌声骗过 VAD，阈值压到极低等效关闭
                                local_files_only=True)
    audio = whisperx.load_audio(audio_path)
    sr = 16000
    merged: list[dict] = []
    lang = language or "zh"
    for (start_s, end_s), prompt in zip(windows, prompts):
        a = max(0, int(start_s * sr))
        b = min(len(audio), int(max(end_s, start_s + 0.3) * sr))
        if b - a < int(0.15 * sr):
            continue
        chunk = audio[a:b]
        result = _kwarg_safe(
            model.transcribe, chunk, batch_size=8,
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        # 用户显式选择语言时锁定（歌声切片自动检测不可靠，易判成小语种）
        if not language and result.get("language"):
            lang = result["language"]
        for seg in result.get("segments", []):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            merged.append({"start": float(seg["start"]) + start_s,
                           "end": float(seg["end"]) + start_s,
                           "text": text})
    del model

    # 同 transcribe：用户显式语言优先，加载失败回退 zh 重试
    if language:
        lang = language
    align_source = resolve_align_model_source(align_model, lang, models_root)
    try:
        model_a, metadata = whisperx.load_align_model(
            language_code=lang, device=device,
            model_name=align_source, model_cache_only=True)
    except Exception as e:
        if lang != "zh":
            lang = "zh"
            align_source = resolve_align_model_source(align_model, lang, models_root)
            model_a, metadata = whisperx.load_align_model(
                language_code=lang, device=device,
                model_name=align_source, model_cache_only=True)
        else:
            raise ModelMissingError(
                f"对齐模型不可用（语言 {lang}，来源 {align_source or 'whisperx 默认'}）。"
                f"请先下载对应 align 模型后重试。原始错误: {e}"
            ) from e
    aligned = whisperx.align(merged, model_a, metadata, audio, device,
                             return_char_alignments=True)
    return {"language": lang, "segments": aligned["segments"]}

def segments_to_lyrics(result: dict, source: str, model_info: str) -> Lyrics:
    """把 whisperx 对齐结果转成统一歌词 JSON（schema 校验保证 words==text 分词）。"""
    lines: list = []
    fallback_used = False
    for i, seg in enumerate(result["segments"]):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        chars = seg.get("chars") or []
        words = _chars_to_words(chars) if chars else []
        expected = list(_iter_tokens(text))
        got = [w.char for w in words]
        if got != expected:
            # 对齐不完整：整段退化均摊（低置信度），保证 JSON 结构合法
            words = _fallback_spread_words(text, seg["start"], seg["end"])
            fallback_used = True
        # 保证词区间单调落在行区间内
        start_ms, end_ms = _ms(seg["start"]), max(_ms(seg["end"]), _ms(seg["start"]) + 1)
        for w in words:
            w.start = max(w.start, start_ms)
            w.end = min(max(w.end, w.start + 1), end_ms)
        lines.append(Line(id=f"L{i + 1}", text=text, start=start_ms, end=end_ms, words=words))

    tag = model_info + ("+fallback-spread" if fallback_used else "")
    ly = Lyrics(version=1, language=result.get("language", "zh"), source=source,
                alignment_model=tag, lines=lines)
    validate(ly)  # 出厂前自检，失败抛 ValueError
    return ly

def transcribe_to_lyrics(media_path: str, out_json: Optional[str] = None,
                         model_size: str = "small", language: Optional[str] = None,
                         device: str = "cpu", compute_type: str = "int8",
                         start: Optional[float] = None, duration: Optional[float] = None,
                         align_model: Optional[str] = None,
                         models_root: Optional[str] = None) -> Lyrics:
    """端到端：媒体文件 → lyrics.json（媒体可以是视频或音频）。"""
    import os
    import tempfile
    from lyrics.schema import save_lyrics

    wav = os.path.join(tempfile.gettempdir(), "lyrics_asr_input.wav")
    extract_wav(media_path, wav, start=start, duration=duration)
    raw = transcribe(wav, model_size=model_size, language=language,
                     device=device, compute_type=compute_type,
                     align_model=align_model, models_root=models_root)
    ly = segments_to_lyrics(raw, source=os.path.basename(media_path),
                            model_info=f"whisperx/{model_size}+ctc-align")
    if out_json:
        save_lyrics(ly, out_json)
    return ly
