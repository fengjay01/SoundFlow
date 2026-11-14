"""SoundFlow 歌词处理包（M1：lyrics.json -> ASS -> 带字幕 MP4）。

模块：
- schema    统一歌词 JSON（PRD 第六章）：数据结构、校验、时间轴 lint
- ass_gen   ASS KTV 字幕生成（\\k/\\kf 逐字高亮、6 样式模板、下句预显）
- compose   FFmpeg + libass 视频合成挂载（音轨三选一）
"""
from .schema import Word, Line, Lyrics, load_lyrics, save_lyrics, validate, lint
from .ass_gen import build_ass, TEMPLATES
from .compose import build_ffmpeg_command, run_ffmpeg

__all__ = [
    "Word", "Line", "Lyrics", "load_lyrics", "save_lyrics", "validate", "lint",
    "build_ass", "TEMPLATES", "build_ffmpeg_command", "run_ffmpeg",
]
