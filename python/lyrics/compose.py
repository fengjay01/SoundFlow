"""FFmpeg + libass 视频合成（PRD SF-011）。

输入：原视频 + 音轨（伴奏/原唱/原始）+ ASS → KTV MP4。
libass 由 FFmpeg `ass` 滤镜提供；不自研渲染引擎（SF-009）。

纯音频输入（MP3/FLAC 等无视频流的文件）：自动生成黑色背景视频后烧录字幕，
否则 `-map 0:v:0` 会因输入没有视频流而失败（Stream map '' matches no streams）。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# SF-011 音轨三选一
AUDIO_TRACKS = {
    "instrumental": "伴奏（需提供 audio 文件，分离产物 instrumental.wav）",
    "original": "原唱（源视频自带音轨）",
    "source": "原始（与 original 同：源视频音轨）",
}


def _escape_filter_path(path: str | Path) -> str:
    """FFmpeg 滤镜参数里的路径转义：反斜杠→正斜杠，冒号→\\\\:（双反斜杠，
    实测单反斜杠会被滤镜图解析层吃掉导致按冒号切分选项）。"""
    s = str(Path(path).resolve()).replace("\\", "/")
    return s.replace(":", "\\\\:")


def _resolve_ffprobe(ffmpeg_bin: str) -> str | None:
    """与 ffmpeg 同目录优先，其次 PATH；找不到返回 None（探测不可用）。"""
    candidate = str(Path(ffmpeg_bin).with_name("ffprobe"))
    if Path(candidate).is_file():
        return candidate
    return "ffprobe" if shutil.which("ffprobe") else None


def has_video_stream(media: str | Path, ffmpeg_bin: str = "ffmpeg") -> bool:
    """探测媒体是否含视频流；探测工具不可用时按有视频处理（保持旧行为）。"""
    ffprobe = _resolve_ffprobe(ffmpeg_bin)
    if ffprobe is None:
        return True
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(media)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return bool(r.stdout.strip())


def build_ffmpeg_command(
    input_video: str | Path,
    output: str | Path,
    ass_path: str | Path,
    audio_path: str | Path | None = None,
    audio_track: str = "original",
    height: int | None = None,
    crf: int = 23,
    preset: str = "medium",
    video_bitrate: str | None = None,
    audio_bitrate: str = "192k",
    duration: float | None = None,
    start: float | None = None,
    ffmpeg: str = "ffmpeg",
    has_video: bool | None = None,
) -> list[str]:
    if audio_track not in AUDIO_TRACKS:
        raise ValueError(f"未知音轨选项 {audio_track!r}，可选：{list(AUDIO_TRACKS)}")
    if audio_track == "instrumental" and audio_path is None:
        raise ValueError("audio_track='instrumental' 时必须提供 audio_path（伴奏文件）")

    # 纯音频输入：has_video=None 时自动探测；测试可显式传入
    audio_only = not has_video_stream(input_video, ffmpeg) if has_video is None else not has_video

    if audio_only:
        # 黑色背景（默认与 ASS PlayRes 同比例 16:9），libass 会按输出尺寸缩放脚本
        bg_h = height or 720
        bg_w = int(round(bg_h * 16 / 9))
        cmd = [ffmpeg, "-y",
               "-f", "lavfi", "-i", f"color=c=black:s={bg_w}x{bg_h}:r=25"]
        # 输入端截取：与 asr --start/--duration 截取的歌词时间轴一致
        if start:
            cmd += ["-ss", f"{start:g}"]
        if duration:
            cmd += ["-t", f"{duration:g}"]
        cmd += ["-i", str(input_video)]
        if audio_track == "instrumental":
            cmd += ["-i", str(audio_path)]
        # 背景源无限长，用 -shortest 随音频结束
        cmd += [
            "-vf", f"ass={_escape_filter_path(ass_path)}",
            "-shortest",
            "-map", "0:v:0",
            "-map", "2:a:0" if audio_track == "instrumental" else "1:a:0",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        ]
        if video_bitrate:
            cmd += ["-b:v", video_bitrate]
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart", str(output)]
        return cmd

    vf_parts = []
    if height:
        vf_parts.append(f"scale=-2:{height}")
    vf_parts.append(f"ass={_escape_filter_path(ass_path)}")

    cmd = [ffmpeg, "-y"]
    if start:
        # 输入端 seek：输出时间轴从 0 起，与 asr --start 截取的歌词时间轴一致
        cmd += ["-ss", f"{start:g}"]
    if duration:
        cmd += ["-t", f"{duration:g}"]
    cmd += ["-i", str(input_video)]
    if audio_track == "instrumental":
        cmd += ["-i", str(audio_path)]

    cmd += [
        "-vf", ",".join(vf_parts),
        "-map", "0:v:0",
        "-map", "1:a:0" if audio_track == "instrumental" else "0:a:0",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
    ]
    if video_bitrate:
        cmd += ["-b:v", video_bitrate]
    cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart", str(output)]
    return cmd


def run_ffmpeg(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    """执行 FFmpeg 命令；失败抛 RuntimeError（带 stderr 尾部，便于日志追踪 SF-008）。"""
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-12:])
        raise RuntimeError(f"FFmpeg 失败 (exit {proc.returncode}):\n{tail}")
    return proc
