"""SoundFlow 歌词管线命令行（M1 冒烟用；后续注册进 worker.py run_command）。

用法（在 SoundFlow/SoundFlow/python 目录下）：
  python -m lyrics.cli validate  lyrics_demo.json
  python -m lyrics.cli ass        lyrics_demo.json -t classic_ktv -o out/lyrics.ass
  python -m lyrics.cli compose    -i video.mp4 --ass out/lyrics.ass -o out/karaoke.mp4
  python -m lyrics.cli pipeline   lyrics_demo.json -i video.mp4 -o out/karaoke.mp4 -t classic_ktv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ass_gen import TEMPLATES, build_ass, save_ass
from .compose import AUDIO_TRACKS, build_ffmpeg_command, run_ffmpeg
from .schema import load_lyrics, lint, save_lyrics


def _cmd_asr(args) -> int:
    from .asr import transcribe_to_lyrics
    print(f"[asr] whisperx 转写+对齐：{args.input}（model={args.model}）")
    ly = transcribe_to_lyrics(
        args.input, out_json=args.output, model_size=args.model, language=args.language,
        device=args.device, compute_type=args.compute_type,
        start=args.start, duration=args.duration,
    )
    for w in lint(ly):
        print(f"  [lint] {w}")
    print(f"歌词已写出：{args.output}（{len(ly.lines)} 行 / {sum(len(l.words) for l in ly.lines)} 字）")
    return 0


def _cmd_auto(args) -> int:
    """全自动雏形：媒体 → WhisperX 歌词 → ASS → KTV MP4（SF-001…SF-011 主链）。"""
    from .asr import transcribe_to_lyrics
    out_mp4 = Path(args.output)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    lyrics_json = args.out_lyrics or out_mp4.with_suffix(".json")
    ass_path = args.out_ass or out_mp4.with_suffix(".ass")

    print(f"[1/3] WhisperX 识别+对齐：{args.input}")
    ly = transcribe_to_lyrics(
        args.input, out_json=str(lyrics_json), model_size=args.model, language=args.language,
        device=args.device, compute_type=args.compute_type,
        start=args.start, duration=args.duration,
    )
    print(f"      → {lyrics_json}（{len(ly.lines)} 行 / {sum(len(l.words) for l in ly.lines)} 字）")

    print(f"[2/3] 生成 ASS：{ass_path}（模板 {args.template}）")
    save_ass(build_ass(ly, template=args.template,
                       next_preview=None if not args.no_next_preview else False), ass_path)

    print(f"[3/3] 烧录合成：{out_mp4}")
    cmd = build_ffmpeg_command(
        input_video=args.input, output=str(out_mp4), ass_path=ass_path,
        audio_path=args.audio, audio_track=args.track,
        height=args.height, crf=args.crf, preset=args.preset,
        video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
        duration=args.duration, start=args.start,
    )
    run_ffmpeg(cmd, timeout=args.timeout)
    print(f"输出完成：{out_mp4}")
    return 0


def _cmd_validate(args) -> int:
    try:
        lyrics = load_lyrics(args.lyrics)
    except ValueError as e:
        print(e)
        return 1
    print(f"校验通过：{len(lyrics.lines)} 行 / {sum(len(l.words) for l in lyrics.lines)} 字")
    for w in lint(lyrics, media_duration_ms=args.media_duration):
        print(f"  [lint] {w}")
    return 0


def _cmd_ass(args) -> int:
    try:
        lyrics = load_lyrics(args.lyrics)
    except ValueError as e:
        print(e)
        return 1
    text = build_ass(lyrics, template=args.template,
                     next_preview=None if not args.no_next_preview else False)
    save_ass(text, args.output)
    print(f"ASS 已生成：{args.output}（模板 {args.template}）")
    return 0


def _cmd_compose(args) -> int:
    cmd = build_ffmpeg_command(
        input_video=args.input, output=args.output, ass_path=args.ass,
        audio_path=args.audio, audio_track=args.track,
        height=args.height, crf=args.crf, preset=args.preset,
        video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
        duration=args.duration, start=args.start,
    )
    print("执行：", " ".join(cmd))
    run_ffmpeg(cmd, timeout=args.timeout)
    print(f"输出完成：{args.output}")
    return 0


def _cmd_pipeline(args) -> int:
    try:
        lyrics = load_lyrics(args.lyrics)
    except ValueError as e:
        print(e)
        return 1
    ass_path = Path(args.out_ass) if args.out_ass else Path(args.output).with_suffix(".ass")
    save_ass(build_ass(lyrics, template=args.template,
                       next_preview=None if not args.no_next_preview else False), ass_path)
    print(f"[1/2] ASS 已生成：{ass_path}")
    cmd = build_ffmpeg_command(
        input_video=args.input, output=args.output, ass_path=ass_path,
        audio_path=args.audio, audio_track=args.track,
        height=args.height, crf=args.crf, preset=args.preset,
        video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
        duration=args.duration, start=args.start,
    )
    run_ffmpeg(cmd, timeout=args.timeout)
    print(f"[2/2] 输出完成：{args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lyrics.cli", description="SoundFlow 歌词管线 CLI")
    sub = ap.add_subparsers(dest="command", required=True)

    def add_media_opts(p):
        p.add_argument("--media-duration", type=float, default=None,
                       help="媒体时长（毫秒），用于超时长检查")

    def add_compose_opts(p, with_start=True):
        p.add_argument("-i", "--input", required=True, help="输入视频")
        p.add_argument("-o", "--output", required=True, help="输出 MP4")
        p.add_argument("--audio", default=None, help="伴奏文件（--track instrumental 时必填）")
        p.add_argument("--track", default="original", choices=list(AUDIO_TRACKS),
                       help="音轨：instrumental=伴奏 / original=原唱 / source=原始")
        p.add_argument("--height", type=int, default=None, help="输出高度（如 720/1080），默认保持")
        p.add_argument("--crf", type=int, default=23)
        p.add_argument("--preset", default="medium")
        p.add_argument("--video-bitrate", default=None)
        p.add_argument("--audio-bitrate", default="192k")
        if with_start:
            p.add_argument("--start", type=float, default=None, help="起始秒（截取用）")
        p.add_argument("--duration", type=float, default=None, help="截取时长（秒，冒烟测试用）")
        p.add_argument("--timeout", type=float, default=1800)

    p = sub.add_parser("validate", help="校验 lyrics.json")
    p.add_argument("lyrics")
    add_media_opts(p)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("ass", help="生成 ASS KTV 字幕")
    p.add_argument("lyrics")
    p.add_argument("-t", "--template", default="classic_ktv", choices=list(TEMPLATES))
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--no-next-preview", action="store_true")
    add_media_opts(p)
    p.set_defaults(func=_cmd_ass)

    p = sub.add_parser("compose", help="ASS 烧录进视频（FFmpeg+libass）")
    p.add_argument("--ass", required=True)
    add_compose_opts(p)
    p.set_defaults(func=_cmd_compose)

    p = sub.add_parser("pipeline", help="lyrics.json → ASS → 带字幕 MP4")
    p.add_argument("lyrics")
    p.add_argument("-t", "--template", default="classic_ktv", choices=list(TEMPLATES))
    p.add_argument("--out-ass", default=None, help="ASS 中间文件输出路径（默认与输出同名 .ass）")
    p.add_argument("--no-next-preview", action="store_true")
    add_media_opts(p)
    add_compose_opts(p)
    p.set_defaults(func=_cmd_pipeline)

    def add_asr_opts(p, with_duration=True):
        p.add_argument("--model", default="small", help="whisper 模型（tiny/base/small/medium/large-v3）")
        p.add_argument("--language", default=None, help="语言代码（默认自动检测）")
        p.add_argument("--device", default="cpu")
        p.add_argument("--compute-type", default="int8")
        p.add_argument("--start", type=float, default=None, help="起始秒（冒烟测试用）")
        if with_duration:
            p.add_argument("--duration", type=float, default=None, help="截取秒（冒烟测试用）")

    p = sub.add_parser("asr", help="WhisperX 识别+字级对齐 → lyrics.json")
    p.add_argument("-i", "--input", required=True, help="媒体（音频或视频）")
    p.add_argument("-o", "--output", required=True, help="lyrics.json 输出路径")
    add_asr_opts(p)
    p.set_defaults(func=_cmd_asr)

    p = sub.add_parser("auto", help="全自动：媒体 → lyrics.json → ASS → KTV MP4")
    # -i/--input、-o/--output、--duration 由 add_compose_opts 提供，勿重复定义
    p.add_argument("-t", "--template", default="classic_ktv", choices=list(TEMPLATES))
    p.add_argument("--out-lyrics", default=None)
    p.add_argument("--out-ass", default=None)
    p.add_argument("--no-next-preview", action="store_true")
    add_asr_opts(p, with_duration=False)  # --duration/--start 复用 compose 的（同为截取语义）
    add_compose_opts(p, with_start=False)
    p.set_defaults(func=_cmd_auto)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
