"""歌词处理 worker 命令：识别（asr）/ 生成 ASS（ass）/ 合成视频（compose）/ 全自动（pipeline）。

复用 python/lyrics 包（schema/ass_gen/compose/asr），事件范式仿 worker_infer.py：
task_started → task_stage/task_progress → task_done；错误 emit_error 返回 1。

payload（camelCase，与前端一致）：
  taskId      : str   任务 id（必填，缺省自生成）
  action      : str   asr | ass | compose | pipeline（默认 pipeline）
  input       : str   媒体文件（asr/pipeline）或 lyrics.json（ass）
  output      : str   输出目录
  modelSize   : str   whisper 模型 tiny/base/small/medium（默认 small）
  modelDir    : str   模型根目录（可选，缺省 pymss model_root；用于解析歌词工具模型）
  language    : str   zh/en/...（默认 auto=whisper 自动）
  device      : str   cpu/cuda（默认 cpu）
  computeType : str   int8/float16（默认 int8）
  template    : str   ASS 模板（默认 classic_ktv）
  nextPreview : bool  下句预显（默认 true）
  videoOutput : str   compose/pipeline 的 MP4 输出路径（默认 <output>/<stem>_ktv.mp4）
  audioTrack  : str   instrumental/original/source（默认 original）
  audioPath   : str   instrumental 时的伴奏文件
  height      : int   输出高度（默认 720）
  crf         : int   默认 23
  preset      : str   默认 medium
  start/duration : float  截取（秒）
"""
from __future__ import annotations

import sys
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

from worker_protocol import emit, emit_error


def _stage(task_id: str, stage: str, message: str) -> None:
    emit("task_stage", {"stage": stage, "message": message}, task_id=task_id)


def _progress(task_id: str, stage: str, percent: float, message: str) -> None:
    emit("task_progress", {"stage": stage, "percent": round(percent, 1),
                           "message": message}, task_id=task_id)


# ---------------------------------------------------------------------------
# 辅助动作：联网搜索歌词 / 拉取 LRC / 人工编辑应用（无需媒体输入）
# ---------------------------------------------------------------------------

def _aux_search_lyrics(payload: dict[str, Any], task_id: str) -> int:
    try:
        from lyrics.lyrics_search import SearchError, search
        query = str(payload.get("query") or "")
        results = search(query, limit=int(payload.get("limit", 10)))
        emit("task_done", {"results": results, "query": query}, task_id=task_id)
        return 0
    except SearchError as exc:
        return emit_error("LYRICS_SEARCH_FAILED", str(exc), task_id=task_id)
    except Exception as exc:  # noqa: BLE001
        return emit_error("LYRICS_SEARCH_FAILED", str(exc), traceback.format_exc(),
                          task_id=task_id)


def _aux_fetch_lyrics(payload: dict[str, Any], task_id: str) -> int:
    try:
        import re
        from pathlib import Path

        from lyrics.lyrics_search import SearchError, fetch_lrc
        info = fetch_lrc(int(payload.get("songId") or 0))
        song_name = str(payload.get("songName") or "")
        info.update({"songName": song_name})
        # 立即落盘：优先保存到歌曲所在目录（saveDir 由前端传音频目录），
        # 文件名 = <歌名 - 歌手>.lrc（清洗非法字符），让"下载的歌词"有据可查。
        save_dir = str(payload.get("saveDir") or "").strip()
        if save_dir and info.get("lyricsText"):
            base = re.sub(r'[\\/:*?"<>|]+', "_", song_name or "在线歌词").strip() or "在线歌词"
            dest = Path(save_dir) / f"{base}.lrc"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(info["lyricsText"]), encoding="utf-8")
            info["savedPath"] = str(dest)
        emit("task_done", info, task_id=task_id)
        return 0
    except (SearchError, ValueError) as exc:
        return emit_error("LYRICS_FETCH_FAILED", str(exc), task_id=task_id)
    except Exception as exc:  # noqa: BLE001
        return emit_error("LYRICS_FETCH_FAILED", str(exc), traceback.format_exc(),
                          task_id=task_id)


def _fit_edited_line(line, new_text: str, new_start: int, new_end: int) -> None:
    """把人工编辑应用到一行：文本不变→词时间线性映射到新窗口；文本变了→均摊重建。"""
    from lyrics.schema import Word, tokenize_pronounceable

    new_end = max(new_end, new_start + 1)
    toks = tokenize_pronounceable(new_text)
    same_text = [w.char for w in line.words] == toks
    old_start, old_end = line.start, line.end
    line.text = new_text
    line.start = new_start
    line.end = new_end
    if same_text and line.words:
        span_old = max(old_end - old_start, 1)
        span_new = max(new_end - new_start, 1)
        for w in line.words:
            rel_s = (w.start - old_start) / span_old
            rel_e = (w.end - old_start) / span_old
            w.start = max(int(new_start + rel_s * span_new), new_start)
            w.end = min(max(int(new_start + rel_e * span_new), w.start + 1), new_end)
    else:
        step = max(new_end - new_start, len(toks)) / max(len(toks), 1)
        line.words = [Word(t, int(new_start + k * step), int(new_start + (k + 1) * step), 0.5)
                      for k, t in enumerate(toks)]
    for w in line.words:
        w.start = max(w.start, new_start)
        w.end = min(max(w.end, w.start + 1), new_end)


def _run_edit_apply(payload: dict[str, Any], task_id: str) -> int:
    """人工编辑应用：校验编辑后的歌词 → 重新生成 ASS/TTML/LRC（可选合成视频）。"""
    try:
        from lyrics.ass_gen import build_ass, save_ass
        from lyrics.compose import build_ffmpeg_command, run_ffmpeg
        from lyrics.exporters import to_lrc
        from lyrics.schema import Lyrics, save_lyrics, validate

        data = payload.get("lyrics")
        if not isinstance(data, dict):
            return emit_error("LYRICS_INVALID", "缺少编辑后的歌词数据", task_id=task_id)
        ly = Lyrics.from_dict(data)
        edits = payload.get("edits") or []
        by_id = {line.id: line for line in ly.lines}
        for e in edits:
            if e.get("delete"):
                drop_id = str(e.get("id") or "")
                ly.lines = [l for l in ly.lines if l.id != drop_id]
                continue
            line = by_id.get(str(e.get("id") or ""))
            if line is None:
                continue
            if "translation" in e:
                line.translation = str(e.get("translation") or "")
            _fit_edited_line(line, str(e.get("text") or line.text),
                             int(e.get("start") or line.start),
                             int(e.get("end") or line.end))
        try:
            validate(ly)
        except ValueError as exc:
            return emit_error("LYRICS_INVALID", f"编辑后的歌词校验失败: {exc}",
                              traceback.format_exc(), task_id=task_id)

        target = payload.get("target") or {}
        lyrics_json = target.get("lyricsJson")
        if not lyrics_json:
            return emit_error("OUTPUT_DIR_FAILED", "缺少歌词 JSON 路径", task_id=task_id)
        save_lyrics(ly, lyrics_json)

        out_dir = Path(lyrics_json).parent
        stem = Path(lyrics_json).stem.replace(".lyrics", "")
        _stage(task_id, "generating_ass", "重新生成 KTV 字幕")
        ass_path = Path(target.get("assPath") or out_dir / f"{stem}.ass")
        ass_text = build_ass(ly, template=payload.get("template") or "classic_ktv",
                             next_preview=payload.get("nextPreview", True),
                             countdown_gap_ms=int(payload.get("countdownGapMs", 0) or 0))
        save_ass(ass_text, ass_path)

        files = [str(lyrics_json), str(ass_path)]
        outputs: dict[str, Any] = {"lyricsJson": str(lyrics_json), "ass": str(ass_path)}
        if payload.get("exportLyrics", True):
            lrc_path = Path(target.get("lrcPath") or out_dir / f"{stem}.lrc")
            lrc_path.write_text(to_lrc(ly), encoding="utf-8")
            files.append(str(lrc_path))
            outputs["lrc"] = str(lrc_path)
        if payload.get("exportTtml", True):
            from lyrics.ttml import build_ttml, validate_ttml
            ttml_path = Path(target.get("ttmlPath") or out_dir / f"{stem}.ttml")
            ttml_text = build_ttml(ly, title=stem)
            issues = validate_ttml(ttml_text)
            if not issues:
                ttml_path.write_text(ttml_text, encoding="utf-8")
                files.append(str(ttml_path))
                outputs["ttml"] = str(ttml_path)

        video_source = target.get("videoSource")
        video_out = target.get("videoOut")
        if video_source and Path(video_source).exists() and video_out:
            _stage(task_id, "compositing", "重新合成 KTV 视频")
            cmd = build_ffmpeg_command(
                video_source, video_out, str(ass_path),
                audio_path=target.get("audioPath"),
                audio_track=target.get("audioTrack") or "original",
                height=_out_height(target.get("height")),
                crf=int(target.get("crf") or 23),
                preset=target.get("preset") or "medium",
                start=target.get("start"), duration=target.get("duration"),
            )
            run_ffmpeg(cmd)
            files.append(str(video_out))
            outputs["video"] = str(video_out)

        emit("lyrics_ready", {"lyrics": ly.to_dict(), "path": str(lyrics_json)},
             task_id=task_id)
        emit("task_done", {"files": files, "outputs": outputs,
                           "outputDir": str(out_dir.resolve())}, task_id=task_id)
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("LYRICS_EDIT_FAILED", str(exc), traceback.format_exc(),
                          task_id=task_id)


def _resolve_device(value) -> str:
    """设备选择解析：'auto' → 有 CUDA 用 cuda，否则 cpu（设置页 runtimeDevice）。"""
    v = str(value or "auto").lower()
    if v in ("cuda", "cpu"):
        return v
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001 — torch 不可用时一律 CPU
        return "cpu"


def _out_height(value, default: int = 720) -> int:
    """输出高度：0 = 原视频清晰度（不缩放）；缺省 720。"""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


def _asr_source_audio(payload: dict[str, Any], task_id: str | None = None) -> str:
    """识别/对齐用的音频来源：启用人声分离时优先用分离出的人声轨。

    人声分离串联（lrc-ttml 三段式第一步，实测混合音轨是全链路精度瓶颈）：
    payload.vocalsPath 指向分离产物（如 Demucs/MDX 的 Vocals 轨）时，
    ASR 与对齐都改用人声轨，伴奏不再干扰识别。未提供产物时自动调用
    Demucs 在线分离（模型 payload.separateModel，默认 htdemucs）。
    """
    vocals = str(payload.get("vocalsPath") or "")
    if payload.get("useVocals"):
        if vocals and Path(vocals).exists():
            return vocals
        auto = _auto_separate_vocals(payload, task_id)
        if auto:
            return auto
    return str(payload.get("input"))


def _auto_separate_vocals(payload: dict[str, Any], task_id: str | None = None) -> str:
    """Demucs 两轨分离（vocal/instrumental），返回人声轨路径；失败返回空串。

    权重查找：TORCH_HOME 指向 l2t 模型库的 models/demucs（htdemucs 权重
    955717e8 随模型管理下载就绪），缺权重时 demucs 会自动联网补下。
    """
    input_path = str(payload.get("input") or "")
    if not input_path or not Path(input_path).exists():
        return ""
    try:
        import os
        import time

        import torch
        from worker_lyrics_models import lyrics_models_root

        from lyrics.model_store import store_root

        model = str(payload.get("separateModel") or "htdemucs")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        out_dir = Path(payload.get("output") or Path(input_path).parent) / "separated"
        track_dir = out_dir / model / Path(input_path).stem
        vocals = track_dir / "vocals.wav"
        if vocals.is_file():
            _stage(task_id, "separating", f"人声分离产物已存在: {vocals}")
            return str(vocals)
        torch_home = store_root(lyrics_models_root(payload.get("modelDir"))) / "models" / "demucs"
        torch_home.mkdir(parents=True, exist_ok=True)
        os.environ["TORCH_HOME"] = str(torch_home)
        # huggingface_hub（demucs 拉 yaml 配置）缓存同样必须留 E 盘
        hf_home = torch_home / "hf"
        hf_home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(hf_home))
        os.environ["HF_HOME"] = str(hf_home)
        # htdemucs 权重/yaml 已随模型管理就绪：离线模式跳过 HF 在线探测
        # （每次 HEAD 超时重试要白等 ~25s）；htdemucs_ft 需联网补权重则不设
        if model == "htdemucs":
            os.environ["HF_HUB_OFFLINE"] = "1"
        else:
            os.environ.pop("HF_HUB_OFFLINE", None)
        _stage(task_id, "separating", f"人声分离中（Demucs {model} · {device}）…")
        _progress(task_id, "separating", 6, f"人声分离（{model}）")
        t0 = time.monotonic()
        from demucs.separate import main as demucs_main

        demucs_main([
            "-n", model, "--two-stems", "vocals",
            "-d", device, "-o", str(out_dir), input_path,
        ])
        if not vocals.is_file():
            raise RuntimeError(f"分离完成但未找到人声轨: {vocals}")
        _stage(task_id, "separating",
               f"人声分离完成（{time.monotonic() - t0:.0f}s）: {vocals}")
        _progress(task_id, "separating", 10, "人声分离完成")
        return str(vocals)
    except Exception as exc:  # noqa: BLE001 — 分离失败不阻断主流程，回退混合音轨
        from worker_protocol import emit_error  # noqa: F401 — 仅记录，不终止
        _stage(task_id, "separating", f"人声分离失败，回退混合音轨: {exc}")
        return ""


def _apply_tail_extend(ly, extend_ms: int, overlap: bool = False) -> None:
    """尾音延长（lrc-ttml 同款，默认开）：每行结束与末字收尾向后延长。

    默认钳制到下一行开始（和声行不参与钳制基准），避免覆盖下一句咬字；
    ``overlap=True``（同一歌手拖音重叠）时放开钳制，允许行尾拖音跨入下一行。
    """
    if not extend_ms or extend_ms <= 0:
        return
    main = [l for l in ly.lines if not l.is_background]
    for i, line in enumerate(ly.lines):
        limit = None
        if not overlap and not line.is_background and i + 1 < len(main):
            idx = main.index(line)
            if idx + 1 < len(main):
                limit = main[idx + 1].start
        new_end = line.end + extend_ms
        if limit is not None:
            new_end = min(new_end, max(limit - 50, line.end))
        line.end = max(line.end, int(new_end))


def _compress_same_agent_overlap(ly) -> None:
    """压缩同一声部（agent/主唱）相邻行的首尾重叠（分层歌词关闭时调用）。

    lrc-ttml 同款语义：分层歌词开启时允许主歌词/和声/附加句同时出现
    （保留拖音重叠）；关闭时把同声部重叠压回下一行开始前 50ms。
    和声（is_background）行不参与压缩。
    """
    main = [l for l in ly.lines if not l.is_background]
    for a, b in zip(main, main[1:]):
        if (a.agent or "") != (b.agent or ""):
            continue
        if a.end > b.start:
            a.end = max(b.start - 50, a.start + 1)
            for w in reversed(a.words):
                if w.end > a.end:
                    w.end = max(a.end, w.start + 1)
                else:
                    break


def _words_to_lyrics(words, *, source, language, gap_ms=650):
    """听写词序列 → Lyrics（词间隙 ≥ gap_ms 切行）。"""
    from lyrics.schema import Lyrics, Line, Word, validate
    from lyrics.import_align import _clamp_line_words

    lines = []
    cur = []
    for c, a, b in words:
        if cur and a - cur[-1][2] > gap_ms:
            lines.append(cur)
            cur = []
        cur.append((c, a, b))
    if cur:
        lines.append(cur)
    ly_lines = [
        Line(id=f"L{i + 1}",
             text="".join(c for c, _a, _b in ws),
             start=ws[0][1],
             end=max(ws[-1][2], ws[0][1] + 200),
             words=[Word(c, a, max(b, a + 40)) for c, a, b in ws])
        for i, ws in enumerate(lines) if ws
    ]
    ly = Lyrics(version=1, language=language, source=source,
                alignment_model="transcribed+qwen3-asr", lines=ly_lines)
    try:
        validate(ly)
    except ValueError:
        _clamp_line_words(ly)
        validate(ly)
    return ly


def _run_asr(payload: dict[str, Any], task_id: str) -> Any:
    """媒体 → Lyrics（含阶段事件），返回 Lyrics。"""
    from lyrics.asr import extract_wav, segments_to_lyrics, transcribe
    from worker_lyrics_models import lyrics_models_root

    input_path = payload.get("input")
    model_size = payload.get("modelSize") or "small"
    language = payload.get("language") or None
    device = _resolve_device(payload.get("device"))
    compute_type = payload.get("computeType") or ("float16" if device == "cuda" else "int8")
    start = payload.get("start")
    duration = payload.get("duration")
    models_root = str(lyrics_models_root(payload.get("modelDir")))
    asr_input = _asr_source_audio(payload, task_id)

    _stage(task_id, "extracting_audio", "提取音频轨（16kHz 单声道）")
    _progress(task_id, "extracting_audio", 5, "提取音频轨")
    import os
    import tempfile
    wav = os.path.join(tempfile.gettempdir(), f"lyrics_worker_{task_id}.wav")
    try:
        extract_wav(asr_input, wav, start=start, duration=duration)

        _stage(task_id, "loading_model", f"加载识别模型 {model_size}")
        _progress(task_id, "loading_model", 12, f"加载识别模型 {model_size}")
        # 无歌词模式 · Qwen3-ASR-1.7B：听写转写 → 按停顿分组行 → 字级时间轴
        if model_size == "qwen3-asr":
            from lyrics.qwen_align import transcribe_qwen
            _stage(task_id, "transcribing", "语音识别（Qwen3-ASR-1.7B）")
            _progress(task_id, "transcribing", 25, "语音识别中")
            words = transcribe_qwen(wav, language or "zh", models_root=models_root)
            words = [(str(c).strip(), int(a), int(b)) for c, a, b in words if str(c).strip()]
            _stage(task_id, "aligning", "按停顿整理行结构")
            _progress(task_id, "aligning", 80, "整理时间轴")
            return _words_to_lyrics(words, source=Path(input_path).name,
                                    language=language or "zh")
        _stage(task_id, "transcribing", "语音识别（faster-whisper）")
        _progress(task_id, "transcribing", 25, "语音识别中")
        # transcribe 内部：load_model → 转写 → load_align_model → 字级对齐
        raw = transcribe(wav, model_size=model_size, language=language,
                         device=device, compute_type=compute_type,
                         models_root=models_root)

        _stage(task_id, "aligning", "字级强制对齐完成，整理时间轴")
        _progress(task_id, "aligning", 80, "整理时间轴")
        ly = segments_to_lyrics(raw, source=Path(input_path).name,
                                model_info=f"whisperx/{model_size}+ctc-align")
        return ly
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def _run_import(payload: dict[str, Any], task_id: str) -> Any:
    """导入歌词（LRC/纯文本）→ 模型对齐（歌词文本以用户导入为准）。"""
    from lyrics.import_align import ImportLyricsError, align_imported
    from lyrics.lrc import looks_like_lrc
    from lyrics.asr import extract_wav
    from worker_lyrics_models import lyrics_models_root

    text = str(payload.get("lyricsText") or "")
    if not text.strip():
        raise ValueError("导入歌词为空：请先选择 LRC 或文本歌词文件")
    fmt = payload.get("lyricsFormat") or ("lrc" if looks_like_lrc(text) else "text")
    input_path = payload.get("input")
    # modelSize=null 表示用户未选识别模型（不定位）；不能 or 'small'——否则误判为已选
    model_size = payload.get("modelSize") or None
    language = payload.get("language") or None
    device = _resolve_device(payload.get("device"))
    compute_type = payload.get("computeType") or ("float16" if device == "cuda" else "int8")
    models_root = str(lyrics_models_root(payload.get("modelDir")))
    asr_input = _asr_source_audio(payload, task_id)
    import os  # noqa: F401 — env 设置需要
    # lrc-ttml 模型仓库集成：仓库里的 TIFA 模型自动作为 LYRICS_TIFA_MODEL
    store_tifa = Path(models_root) / "alignment" / "tifa"
    if not (store_tifa / "model.pt").is_file():
        store_tifa = Path(models_root) / "l2t" / "models" / "tifa"  # 旧布局兜底
    if (store_tifa / "model.pt").is_file() and not os.environ.get("LYRICS_TIFA_MODEL"):
        os.environ["LYRICS_TIFA_MODEL"] = str(store_tifa)
    # 对齐引擎（lrc-ttml 工作台的引擎选择）：tifa → 允许 TIFA（需 LYRICS_TIFA_PYTHON
    # 环境或 payload.tifaPython）；whisper → 强制跳过 TIFA
    engine = (payload.get("engine") or "whisper").lower()
    tifa_python = str(payload.get("tifaPython") or "")
    if engine == "tifa":
        if tifa_python and Path(tifa_python).is_file():
            os.environ["LYRICS_TIFA_PYTHON"] = tifa_python
        elif not os.environ.get("LYRICS_TIFA_PYTHON"):
            # TIFA 依赖（g2pflow 等）已内置到 worker 自身环境：
            # 选 TIFA 引擎即零配置，直接用本 worker 的 Python 跑推理子进程
            os.environ["LYRICS_TIFA_PYTHON"] = sys.executable

    _stage(task_id, "extracting_audio", "提取音频轨（16kHz 单声道）")
    _progress(task_id, "extracting_audio", 5, "提取音频轨")
    if payload.get("evenSpread"):
        _stage(task_id, "export",
               "已开启均摊估计模式：完成后将额外产出 .even. 版 player / ASS / JSON（每行字级均分时间窗）")
    import os
    import tempfile
    import wave
    wav = os.path.join(tempfile.gettempdir(), f"lyrics_import_{task_id}.wav")
    try:
        extract_wav(asr_input, wav, start=payload.get("start"),
                    duration=payload.get("duration"))
        try:
            with wave.open(wav, "rb") as _w:
                _dur = _w.getnframes() / max(_w.getframerate(), 1)
            _stage(task_id, "extracting_audio", f"音频轨就绪（{_dur:.1f}s）")
        except Exception:  # noqa: BLE001
            pass
        if fmt == "lrc" and model_size:
            _stage(task_id, "loading_model",
                   f"已选识别模型 {model_size}：LRC 模式行位置由 LRC 自带时间轴提供，"
                   "识别模型不参与对齐（仅 Whisper 引擎会用它做切片转写）")
        _stage(task_id, "loading_model", f"加载对齐模型（引擎 {engine}）")
        _progress(task_id, "loading_model", 12, "加载对齐模型")
        _stage(task_id, "transcribing", "歌词对齐中（导入歌词 + 模型时间轴）")
        _progress(task_id, "transcribing", 25, "歌词对齐中")
        try:
            notes: list[str] = []
            def _num(v):
                try:
                    f = float(v)
                    return f if f == f else None
                except (TypeError, ValueError):
                    return None

            def _on_align_progress(done, total=None, msg=None):
                # 兼容三种进度调用形式：
                #   行计数：progress(done_rows, total_rows)
                #   比例：  progress(0.35, "描述文本")
                #   负值：  progress(-1, "警告文本")（msg 落在 total 位）
                d, t = _num(done), _num(total)
                if d is not None and d < 0:
                    _progress(task_id, "transcribing", 40,
                              str(total or msg or "警告"))
                    return
                if t is not None and t > 8 and d is not None and 0 <= d <= t:
                    _progress(task_id, "transcribing", 25 + int(d / t * 60),
                              f"歌词对齐中 {int(d)}/{int(t)} 行")
                    return
                if d is not None and 0 <= d <= 1:
                    _progress(task_id, "transcribing", 25 + int(d * 60),
                              str(total or msg or "歌词对齐中"))
                    return
                # 其他（如 progress(-1, "警告")）：展示文本，进度保持
                _progress(task_id, "transcribing", 40, str(msg or done or "歌词对齐中"))
            ly = align_imported(
                wav, text, fmt, source=Path(input_path).name,
                model_size=model_size, language=language, device=device,
                compute_type=compute_type, models_root=models_root,
                auto_harmony=bool(payload.get("autoHarmony", False)),
                engine=engine, notes=notes, progress=_on_align_progress)
            for msg in notes:
                _stage(task_id, "transcribing", msg)
            _stage(task_id, "aligning",
                   f"对齐完成：共 {len(ly.lines)} 行 · "
                   f"{sum(len(l.words) for l in ly.lines)} 字")
        except ImportLyricsError as exc:
            raise ValueError(str(exc)) from exc
        return ly
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def _export_side_files(ly, out_dir: Path, stem: str,
                       payload: dict[str, Any], task_id: str) -> dict[str, str]:
    """按开关导出 LRC / 增强逐字 LRC / TTML（失败不阻断主流程）。"""
    out: dict[str, str] = {}
    if payload.get("exportLyrics", True):
        try:
            from lyrics.exporters import to_lrc
            enhanced = bool(payload.get("enhancedLrc", False))
            suffix = ".enhanced.lrc" if enhanced else ".lrc"
            lrc_path = out_dir / f"{stem}{suffix}"
            lrc_path.write_text(to_lrc(ly, enhanced=enhanced), encoding="utf-8")
            out["lrc" if not enhanced else "enhancedLrc"] = str(lrc_path)
            _stage(task_id, "export",
                   f"已导出 {'增强逐字 LRC' if enhanced else 'LRC'}")
        except Exception as exc:  # noqa: BLE001
            _stage(task_id, "export", f"LRC 导出跳过: {exc}")
    if payload.get("exportTtml", True):
        try:
            from lyrics.ttml import build_ttml, validate_ttml
            ttml_text = build_ttml(ly, title=stem,
                                   artist=str(payload.get("artist") or ""))
            issues = validate_ttml(ttml_text)
            if issues:
                raise ValueError("; ".join(issues[:3]))
            ttml_path = out_dir / f"{stem}.ttml"
            ttml_path.write_text(ttml_text, encoding="utf-8")
            out["ttml"] = str(ttml_path)
            _stage(task_id, "export", "已导出 TTML")
        except Exception as exc:  # noqa: BLE001
            _stage(task_id, "export", f"TTML 导出跳过: {exc}")
    if payload.get("exportSrt", True):
        try:
            from lyrics.exporters import to_srt
            srt_path = out_dir / f"{stem}.srt"
            srt_path.write_text(to_srt(ly), encoding="utf-8")
            out["srt"] = str(srt_path)
            _stage(task_id, "export", "已导出 SRT")
        except Exception as exc:  # noqa: BLE001
            _stage(task_id, "export", f"SRT 导出跳过: {exc}")
    if payload.get("exportKrc", True):
        try:
            from lyrics.exporters import to_krc
            krc_path = out_dir / f"{stem}.krc"
            krc_path.write_bytes(to_krc(ly))
            out["krc"] = str(krc_path)
            _stage(task_id, "export", "已导出 KRC")
        except Exception as exc:  # noqa: BLE001
            _stage(task_id, "export", f"KRC 导出跳过: {exc}")
    if payload.get("exportFurigana", False):
        try:
            from lyrics.ttml import build_ttml_furigana
            fp = out_dir / f"{stem}.furigana.ttml"
            fp.write_text(build_ttml_furigana(ly, title=stem,
                                              artist=str(payload.get("artist") or "")),
                          encoding="utf-8")
            out["furiganaTtml"] = str(fp)
        except Exception as exc:  # noqa: BLE001
            _stage(task_id, "export", f"注音 TTML 导出跳过: {exc}")
    if payload.get("exportPlayer", True):
        try:
            from lyrics.player import save_player, save_lines_player
            from lyrics.exporters import to_lrc
            from lyrics.lrc import parse_lrc
            # 音频输入 → base64 内嵌（双击即播）；视频输入 → 同名相对引用
            audio_src = payload.get("audioFile") or payload.get("input")
            # ① 逐字填充版（模型字级时间轴）
            pp = out_dir / f"{stem}.player.html"
            save_player(ly, audio_src, pp, mode="karaoke",
                        title=stem, artist=str(payload.get("artist") or ""))
            out["player"] = str(pp)
            # ② 仅逐行版（模型导出 LRC 的行级时间）
            model_rows = parse_lrc(to_lrc(ly))
            plp = out_dir / f"{stem}.lyric.player.html"
            save_lines_player(model_rows, audio_src, plp,
                              title=stem, artist=str(payload.get("artist") or ""))
            out["lyricPlayer"] = str(plp)
            _stage(task_id, "export", "已导出歌词播放器（逐字 / 逐行）")
            # ③ 仅逐行版（源 LRC 原始时间轴，LRC 模式才有）
            src_lrc = Path(str(payload.get("sourceLyricsPath") or ""))
            if src_lrc.is_file():
                try:
                    src_rows = parse_lrc(src_lrc.read_text(encoding="utf-8"))
                    if src_rows:
                        psp = out_dir / f"{stem}.source.player.html"
                        save_lines_player(src_rows, audio_src, psp,
                                          title=stem, artist=str(payload.get("artist") or ""))
                        out["sourcePlayer"] = str(psp)
                except Exception as exc:  # noqa: BLE001
                    _stage(task_id, "export", f"源 LRC 播放器跳过: {exc}")
        except Exception as exc:  # noqa: BLE001
            _stage(task_id, "export", f"播放器生成跳过: {exc}")

        # 均摊估计模式（evenSpread 开关，三模式通用）：额外产出一份每行字级
        # 均摊时间轴的 player / ASS / JSON（文件名含 .even.），用于对比模型
        # 对齐精度——行内每个字均分 [行起点, 行终点] 的时间窗
        if payload.get("evenSpread"):
            try:
                from lyrics.schema import Lyrics, Line, save_lyrics
                from lyrics.ass_gen import build_ass, save_ass
                from lyrics.player import save_player
                audio_src = payload.get("audioFile") or payload.get("input")
                even_template = payload.get("template") or "classic_ktv"
                lines = []
                for ln in ly.lines:
                    if ln.words:
                        s = ln.start
                        # 行终点取 max(行 end, 末字 end)：均摊字不得超出行窗（validate 约束）
                        e = max(ln.end, ln.words[-1].end)
                        n = len(ln.words)
                        step = max(e - s, n * 100) / n
                        words = [w.__class__(w.char, int(s + k * step),
                                             int(s + (k + 1) * step), 0.5)
                                 for k, w in enumerate(ln.words)]
                        lines.append(Line(id=ln.id, text=ln.text, start=ln.start,
                                          end=int(e), words=words,
                                          translation=ln.translation,
                                          is_background=ln.is_background,
                                          agent=ln.agent))
                    else:
                        lines.append(Line(id=ln.id, text=ln.text, start=ln.start,
                                          end=ln.end, words=[],
                                          translation=ln.translation,
                                          is_background=ln.is_background,
                                          agent=ln.agent))
                even = Lyrics(version=ly.version, language=ly.language,
                              source=ly.source,
                              alignment_model=(ly.alignment_model or "even") + "+even",
                              lines=lines)
                ej = out_dir / f"{stem}.even.lyrics.json"
                save_lyrics(even, str(ej))
                ea = out_dir / f"{stem}.even.ass"
                save_ass(build_ass(even, template=even_template,
                                   next_preview=payload.get("nextPreview", True)), ea)
                ep = out_dir / f"{stem}.even.player.html"
                save_player(even, audio_src, ep,
                            title=f"{stem} · 均摊",
                            artist=str(payload.get("artist") or ""))
                out["evenJson"] = str(ej)
                out["evenAss"] = str(ea)
                out["evenPlayer"] = str(ep)
                _stage(task_id, "export", "已导出均摊版 player / ASS / JSON（.even.）")
            except Exception as exc:  # noqa: BLE001 — 均摊版失败不阻断主流程
                _stage(task_id, "export", f"均摊版导出跳过: {exc}")
    return out


def cmd_lyrics(payload: dict[str, Any]) -> int:
    task_id = payload.get("taskId") or f"lyr_{int(time.time() * 1000)}"
    action = (payload.get("action") or "pipeline").lower()
    # 辅助动作：不需要媒体输入，先于输入校验分发
    if action == "search_lyrics":
        return _aux_search_lyrics(payload, task_id)
    if action == "fetch_lyrics":
        return _aux_fetch_lyrics(payload, task_id)
    if action == "edit_apply":
        return _run_edit_apply(payload, task_id)
    if action == "save_lyrics_json":
        try:
            from lyrics.schema import Lyrics
            data = payload.get("data")
            path = str(payload.get("path") or "")
            if not data or not path:
                return emit_error("LYRICS_INVALID", "缺少 path 或 data", task_id=task_id)
            ly = Lyrics.from_dict(data)
            from lyrics.schema import save_lyrics as _save_lyrics
            _save_lyrics(ly, path)
            emit("task_done", {"saved": path}, task_id=task_id)
            return 0
        except Exception as exc:  # noqa: BLE001
            return emit_error("LYRICS_SAVE_FAILED", str(exc), traceback.format_exc(), task_id=task_id)
    if action == "tifa_status":
        # TIFA 已零配置内置（依赖在本环境、模型在模型库即用）：
        # available = 模型目录就绪（python 用 worker 自身）
        from lyrics.tifa_align import tifa_model_dir, tifa_python
        from worker_lyrics_models import lyrics_models_root

        model_dir = tifa_model_dir() or str(
            Path(lyrics_models_root(payload.get("modelDir")))
            / "alignment" / "tifa")
        if not Path(model_dir).is_dir():
            model_dir = str(Path(lyrics_models_root(payload.get("modelDir")))
                            / "l2t" / "models" / "tifa")  # 旧布局兜底
        available = (Path(model_dir) / "model.pt").is_file() \
            and (Path(model_dir) / "runtime" / "infer.py").is_file()
        emit("task_done", {"available": available,
                           "python": tifa_python() or sys.executable,
                           "modelDir": model_dir if available else ""}, task_id=task_id)
        return 0
    if action == "l2t_status":
        from lyrics.model_store import ensure_proxy, status
        from worker_lyrics_models import lyrics_models_root
        ensure_proxy()
        emit("task_done", status(str(lyrics_models_root(payload.get("modelDir")))),
             task_id=task_id)
        return 0
    if action == "l2t_delete":
        from lyrics.model_store import delete
        from worker_lyrics_models import lyrics_models_root
        freed = delete(str(lyrics_models_root(payload.get("modelDir"))),
                       str(payload.get("modelId") or ""))
        emit("task_done", {"freed": freed, "modelId": payload.get("modelId")},
             task_id=task_id)
        return 0
    if action == "l2t_download":
        from lyrics.model_store import ModelStoreError, download, ensure_proxy
        from worker_lyrics_models import lyrics_models_root
        ensure_proxy()
        root_str = str(lyrics_models_root(payload.get("modelDir")))

        def on_progress(pct: int, message: str) -> None:
            emit("task_progress", {"stage": "downloading", "percent": pct,
                                   "message": message}, task_id=task_id)

        try:
            result = download(root_str, str(payload.get("modelId") or ""),
                              task_id=task_id, progress=on_progress,
                              source=(str(payload["source"]) if payload.get("source") else None))
        except ModelStoreError as exc:
            return emit_error("L2T_MODEL_DOWNLOAD_FAILED", str(exc),
                              traceback.format_exc(), task_id=task_id)
        emit("task_done", {"result": result, "modelId": payload.get("modelId")},
             task_id=task_id)
        return 0

    input_path = payload.get("input")
    output_dir = payload.get("output") or str(Path(input_path).parent) if input_path else None

    if not input_path:
        return emit_error("INPUT_NOT_FOUND", "Missing input path", task_id=task_id)
    if not Path(input_path).exists():
        return emit_error("INPUT_NOT_FOUND", f"Input path does not exist: {input_path}",
                          task_id=task_id)

    # 默认输出 = 以文件名命名的子文件夹（<同目录>/<歌名>/），并把原始
    # 音视频复制一份进去——所有产物集中在一个以歌名命名的文件夹内。
    if not payload.get("output"):
        src = Path(input_path)
        output_dir = str(src.parent / src.stem)
        try:
            (Path(output_dir) / src.name).parent.mkdir(parents=True, exist_ok=True)
            dest = Path(output_dir) / src.name
            if not dest.is_file():
                shutil.copy2(src, dest)
            # 源歌词归档：LRC → <stem>.source.lrc；TXT → <stem>.source.txt
            # （与打轴产物 <stem>.lrc / 识别产物区分，保留原始参考）
            src_ly = Path(str(payload.get("sourceLyricsPath") or ""))
            if src_ly.is_file():
                ext = src_ly.suffix.lower()
                if ext == ".lrc":
                    src_dest = Path(output_dir) / f"{src.stem}.source.lrc"
                elif ext in (".txt", ".text"):
                    src_dest = Path(output_dir) / f"{src.stem}.source.txt"
                else:
                    src_dest = None
                if src_dest is not None and (
                        not src_dest.is_file() or src_dest.read_bytes() != src_ly.read_bytes()):
                    shutil.copy2(src_ly, src_dest)
        except OSError as exc:
            return emit_error("OUTPUT_DIR_FAILED",
                              f"Cannot copy source into output folder: {exc}",
                              task_id=task_id)

    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return emit_error("OUTPUT_DIR_FAILED", f"Cannot create output dir: {exc}",
                          task_id=task_id)

    stem = Path(input_path).stem
    out_dir = Path(output_dir)
    lyrics_json = out_dir / f"{stem}.lyrics.json"
    lyrics_ass = out_dir / f"{stem}.ass"
    video_out = payload.get("videoOutput") or str(out_dir / f"{stem}_ktv.mp4")

    try:
        emit("task_started", {"action": action, "input": input_path,
                              "output": str(out_dir)}, task_id=task_id)
        _stage(task_id, "validating_input", "校验输入")
        _progress(task_id, "validating_input", 2, "校验输入")

        ly = None
        side_files: dict[str, str] = {}
        if action in ("asr", "pipeline"):
            if (payload.get("lyricsSource") or "auto") == "import":
                ly = _run_import(payload, task_id)
            else:
                ly = _run_asr(payload, task_id)
            from lyrics.schema import save_lyrics
            save_lyrics(ly, lyrics_json)
            # 尾音延长（lrc-ttml 默认开）：行尾与末字收尾向后延伸；
            # dragOverlap（同一歌手拖音重叠）时放开"钳到下一行"的限制；
            # 分层歌词关闭时把同声部重叠压回（开启则保留重叠同时显示）
            _apply_tail_extend(ly, int(payload.get("tailExtendMs", 400) or 0),
                               overlap=bool(payload.get("dragOverlap", False)))
            if not payload.get("layeredLyrics", False):
                _compress_same_agent_overlap(ly)
            # LRC / TTML 导出（exportLyrics / exportTtml 可关）
            side_files = _export_side_files(ly, out_dir, stem, payload, task_id)
            # 提前把歌词数据发给前端，UI 可在合成阶段就开始渲染
            emit("lyrics_ready", {"lyrics": ly.to_dict(), "path": str(lyrics_json)},
                 task_id=task_id)
            _progress(task_id, "asr_done", 85, "识别完成")

        if action == "ass":
            from lyrics.schema import load_lyrics
            ly = load_lyrics(input_path)
            lyrics_json = Path(input_path)

        if action in ("ass", "pipeline"):
            from lyrics.ass_gen import build_ass, save_ass
            _stage(task_id, "generating_ass", "生成 KTV 字幕（ASS）")
            _progress(task_id, "generating_ass", 88, "生成 KTV 字幕")
            next_preview = payload.get("nextPreview", True)
            ass_text = build_ass(ly, template=payload.get("template") or "classic_ktv",
                                 next_preview=next_preview,
                                 countdown_gap_ms=int(payload.get("countdownGapMs", 0) or 0))
            save_ass(ass_text, lyrics_ass)
            # 纯逐行版（播放器风）：与逐字 KTV 版并存，命名 <stem>.lyric.ass
            try:
                lyric_ass = out_dir / f"{stem}.lyric.ass"
                save_ass(build_ass(ly, template="lyric_line",
                                   next_preview=payload.get("nextPreview", True)),
                         lyric_ass)
                if action == "ass":
                    side_files["lyricAss"] = str(lyric_ass)
            except Exception as exc:  # noqa: BLE001 — 逐行版失败不阻断
                _stage(task_id, "generating_ass", f"逐行版字幕跳过: {exc}")
            if action == "ass":
                side_files = _export_side_files(ly, out_dir, stem, payload, task_id)

        burn_video = payload.get("burnVideo", False)  # 打轴优先：默认不烧视频
        if action in ("compose", "pipeline") and burn_video:
            from lyrics.compose import build_ffmpeg_command, run_ffmpeg
            _stage(task_id, "compositing", "烧录字幕合成 KTV 视频")
            _progress(task_id, "compositing", 92, "合成视频")
            # compose 动作的 input 即 ASS 旁边的媒体路径由 videoSource 指定
            video_source = payload.get("videoSource") or (
                input_path if action == "pipeline" else None)
            if not video_source:
                return emit_error("INPUT_NOT_FOUND",
                                  "compose 动作需要 videoSource（待烧录的媒体文件）",
                                  task_id=task_id)
            cmd = build_ffmpeg_command(
                video_source, video_out, lyrics_ass,
                audio_path=payload.get("audioPath"),
                audio_track=payload.get("audioTrack") or "original",
                height=_out_height(payload.get("height")),
                crf=payload.get("crf") or 23,
                preset=payload.get("preset") or "medium",
                start=payload.get("start"),
                duration=payload.get("duration"),
            )
            run_ffmpeg(cmd)

        _stage(task_id, "done", "完成")
        files = []
        if action in ("asr", "pipeline"):
            files.append(str(lyrics_json))
        files.extend(side_files.values())
        if action in ("ass", "pipeline"):
            files.append(str(lyrics_ass))
        if action in ("compose", "pipeline") and burn_video:
            files.append(str(video_out))
        emit("task_done", {
            "files": files,
            "outputs": {
                "lyricsJson": str(lyrics_json) if action in ("asr", "pipeline") else None,
                **side_files,
                "ass": str(lyrics_ass) if action in ("ass", "pipeline") else None,
                "video": str(video_out) if (action in ("compose", "pipeline") and burn_video) else None,
            },
            "outputDir": str(out_dir.resolve()),
            "outputRoot": str(out_dir.resolve()),
            "outputFormat": "mp4" if (action in ("compose", "pipeline") and burn_video) else (
                "ass" if action == "ass" else "json"),
            # 打轴历史卡片：行数 + 歌词预览（前 3 行主歌词）
            "lineCount": len([l for l in ly.lines if not l.is_background]),
            "preview": [l.text for l in ly.lines if not l.is_background][:3],
        }, task_id=task_id)
        return 0
    except ValueError as exc:
        return emit_error("LYRICS_INVALID", str(exc), traceback.format_exc(), task_id=task_id)
    except Exception as exc:  # noqa: BLE001
        from lyrics.asr import ModelMissingError
        if isinstance(exc, ModelMissingError):
            return emit_error("LYRICS_MODEL_MISSING", str(exc), traceback.format_exc(),
                              task_id=task_id)
        if isinstance(exc, RuntimeError):
            # transcribe 的 ImportError 包装 / ffmpeg 失败
            code = "COMPOSE_FAILED" if "FFmpeg" in str(exc) else "ASR_FAILED"
            return emit_error(code, str(exc), traceback.format_exc(), task_id=task_id)
        return emit_error("LYRICS_FAILED", str(exc), traceback.format_exc(), task_id=task_id)
