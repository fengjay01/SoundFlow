# -*- coding: utf-8 -*-
r"""滚动歌词播放器（迁移自 Huhu AI player 页面形态）。

生成自包含 HTML：左侧封面/歌名/歌手/进度条，右侧逐行滚动歌词 +
字级渐进高亮（与 ASS \kf 同款语义）。音频用相对路径引用
（与播放器 HTML 同目录的音频文件），双击即用浏览器打开。
"""
from __future__ import annotations

import json
from pathlib import Path

from lyrics.schema import Lyrics

_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --hl: #e8b4bc; --fg: #f2f2f7; --dim: rgba(242,242,247,.35); --mid: rgba(242,242,247,.6); }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1c1c22; color: var(--fg);
         font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
         height: 100vh; display: flex; overflow: hidden; }
  .left { width: 40%; min-width: 280px; display: flex; flex-direction: column;
          justify-content: center; align-items: center; padding: 40px; gap: 18px; }
  .cover { width: min(320px, 60%); aspect-ratio: 1; background: #6a5c68;
           border-radius: 24px; display: flex; align-items: center; justify-content: center; }
  .cover svg { width: 55%; height: 55%; opacity: .55; }
  .meta { text-align: left; width: min(320px, 60%); }
  .meta .name { font-size: 24px; font-weight: 700; }
  .meta .artist { color: var(--mid); font-size: 16px; margin-top: 4px; }
  .controls { width: min(320px, 60%); display: flex; align-items: center; gap: 10px; }
  .time { color: var(--mid); font-size: 12px; min-width: 34px; text-align: center;
          font-variant-numeric: tabular-nums; }
  /* 平台式可点击/拖动进度条（网易云/Spotify 风） */
  .bar { flex: 1; position: relative; height: 18px; cursor: pointer; touch-action: none; }
  .bar-track { position: absolute; left: 0; right: 0; top: 50%; height: 4px;
               transform: translateY(-50%); background: rgba(242,242,247,.22);
               border-radius: 3px; transition: height .15s; }
  .bar:hover .bar-track, .bar.dragging .bar-track { height: 6px; }
  .bar-fill { position: absolute; left: 0; top: 0; bottom: 0; width: 0%;
              background: var(--hl); border-radius: 3px; }
  .bar-thumb { position: absolute; top: 50%; left: 0; width: 12px; height: 12px;
               border-radius: 50%; background: #fff;
               box-shadow: 0 1px 4px rgba(0,0,0,.45);
               transform: translate(-50%, -50%); opacity: 0; transition: opacity .15s;
               pointer-events: none; }
  .bar:hover .bar-thumb, .bar.dragging .bar-thumb { opacity: 1; }
  #play { width: 44px; height: 44px; border-radius: 50%; border: none; cursor: pointer;
          background: var(--hl); color: #fff; font-size: 18px; }
  .right { flex: 1; overflow: hidden; position: relative;
           background: linear-gradient(135deg, #242028 0%, #1c1c22 100%); }
  .scroll { position: absolute; inset: 0; overflow-y: auto; padding: 20vh 48px 60vh; }
  .line { font-size: clamp(20px, 2.6vw, 34px); font-weight: 700; margin: 28px 0;
          color: var(--dim); transition: color .3s, transform .3s; transform-origin: left center;
          cursor: pointer; }
  .line.past { color: var(--mid); }
  .line.active { color: var(--fg); transform: scale(1.06); }
  /* 字级 \kf 渐进填充：当前字从左到右流动填色（与 ASS 同语义） */
  .line .char { color: var(--dim); }
  .line.past .char { color: var(--mid); }
  .line.active .char {
    background: linear-gradient(to right, var(--hl) 0%, var(--hl) var(--p, 0%), var(--dim) var(--p, 0%));
    -webkit-background-clip: text; background-clip: text;
    color: transparent; -webkit-text-fill-color: transparent;
  }
  /* 仅逐行模式：当前行整行纯色高亮 + 柔和背景条 */
  body.mode-line .line.active {
    background: rgba(232, 180, 188, 0.10);
    border-radius: 12px; padding: 8px 14px; margin-left: -14px;
  }
  body.mode-line .line.active .char {
    background: none; -webkit-background-clip: initial; background-clip: initial;
    color: var(--fg); -webkit-text-fill-color: currentColor;
  }
  .bg-line { font-size: clamp(14px, 1.6vw, 20px); font-weight: 500; }
</style>
</head>
<body class="__MODE_CLS__">
  <div class="left">
    <div class="cover">
      <svg viewBox="0 0 24 24" fill="#8a7888"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/></svg>
    </div>
    <div class="meta">
      <div class="name">__TITLE__</div>
      <div class="artist">__ARTIST__</div>
    </div>
    <div class="controls">
      <span class="time" id="tCur">0:00</span>
      <div class="bar" id="bar">
        <div class="bar-track"><div class="bar-fill" id="barFill"></div></div>
        <div class="bar-thumb" id="barThumb"></div>
      </div>
      <span class="time" id="tEnd">0:00</span>
      <button id="play">▶</button>
    </div>
    __MEDIA_TAG__
  </div>
  <div class="right"><div class="scroll" id="scroll"></div></div>
<script>
const MODE = '__MODE__';  // karaoke=逐字填充 | line=仅逐行
const DATA = __DATA__;
const audio = document.getElementById('audio');
const scroll = document.getElementById('scroll');
const play = document.getElementById('play');
const bar = document.getElementById('bar');
const barFill = document.getElementById('barFill');
const barThumb = document.getElementById('barThumb');
const tCur = document.getElementById('tCur');
let els = [], activeIdx = -1, dragging = false;

DATA.lines.forEach((ln, i) => {
  const div = document.createElement('div');
  div.className = 'line' + (ln.bg ? ' bg-line' : '');
  div.dataset.i = i;
  ln.words.forEach(w => {
    const c = document.createElement('span');
    c.className = 'char'; c.textContent = w.char;
    div.appendChild(c);
  });
  div.addEventListener('click', () => { audio.currentTime = ln.start / 1000; audio.play(); });
  scroll.appendChild(div);
  els.push(div);
});

function fmt(s) { s = Math.max(0, s|0); return (s/60|0) + ':' + String(s%60).padStart(2,'0'); }

function _fill(el, p) {
  el.style.setProperty('--p', Math.min(100, Math.max(0, p)).toFixed(1) + '%');
}

function tick() {
  const t = audio.currentTime * 1000;
  if (!dragging) {
    tCur.textContent = fmt(audio.currentTime);
    if (audio.duration) _setBar(audio.currentTime / audio.duration);
  }
  let idx = -1;
  DATA.lines.forEach((ln, i) => { if (t >= ln.start && t < ln.end + 300) idx = i; });
  els.forEach((el, i) => {
    el.classList.toggle('active', i === idx);
    el.classList.toggle('past', i < idx && i !== idx);
    if (i === idx && activeIdx !== idx)
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (i === idx) {
      const chars = el.querySelectorAll('.char');
      if (MODE === 'line') {
        chars.forEach((c) => _fill(c, 100));  // 仅逐行：整行高亮
      } else {
        DATA.lines[i].words.forEach((w, k) => {
          const c = chars[k]; if (!c) return;
          if (t >= w.end) _fill(c, 100);
          else if (t >= w.start)
            _fill(c, (t - w.start) / Math.max(w.end - w.start, 1) * 100);
          else _fill(c, 0);
        });
      }
    }
  });
  activeIdx = idx;
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);


play.addEventListener('click', () => audio.paused ? audio.play() : audio.pause());
audio.addEventListener('play', () => play.textContent = '⏸');
audio.addEventListener('pause', () => play.textContent = '▶');
audio.addEventListener('loadedmetadata', () => document.getElementById('tEnd').textContent = fmt(audio.duration));

// ---- 平台式进度条：点击任意位置定位 + 按住拖动刮擦（松手才真正 seek） ----
function _setBar(ratio) {
  const p = Math.min(100, Math.max(0, ratio * 100));
  barFill.style.width = p + '%';
  barThumb.style.left = p + '%';
}
function _ratioFromEvent(e) {
  const rect = bar.getBoundingClientRect();
  return Math.min(1, Math.max(0, (e.clientX - rect.left) / Math.max(rect.width, 1)));
}
bar.addEventListener('pointerdown', (e) => {
  if (!audio.duration || !isFinite(audio.duration)) return;
  dragging = true;
  bar.classList.add('dragging');
  bar.setPointerCapture(e.pointerId);
  const r = _ratioFromEvent(e);
  _setBar(r);
  tCur.textContent = fmt(r * audio.duration);
});
bar.addEventListener('pointermove', (e) => {
  if (!dragging) return;
  const r = _ratioFromEvent(e);
  _setBar(r);
  tCur.textContent = fmt(r * audio.duration);
});
bar.addEventListener('pointerup', (e) => {
  if (!dragging) return;
  dragging = false;
  bar.classList.remove('dragging');
  if (audio.duration && isFinite(audio.duration))
    audio.currentTime = _ratioFromEvent(e) * audio.duration;
});
bar.addEventListener('pointercancel', () => {
  dragging = false;
  bar.classList.remove('dragging');
});

</script>
</body>
</html>
"""


def build_player_html(lyrics: Lyrics | None = None, *, lines_data: list | None = None,
                      audio_file: str | None = None,
                      title: str, artist: str, mode: str = "karaoke") -> str:
    """Lyrics → 自包含滚动歌词播放器 HTML。

    audio_file：音频文件名（相对引用，与 HTML 同目录）。
    """
    if lines_data is not None:
        data = {"lines": lines_data}
    else:
        data = {
            "lines": [
                {
                    "start": ln.start,
                    "end": max(ln.end, ln.start + 200),
                    "bg": bool(ln.is_background),
                    "words": [{"char": w.char, "start": w.start, "end": w.end}
                              for w in ln.words],
                }
                for ln in lyrics.lines if ln.words
            ],
        }
    html = _TEMPLATE
    html = html.replace("__TITLE__", title or "Untitled")
    html = html.replace("__ARTIST__", artist or "")
    # 媒体源：纯音频(≤30MB)→base64 内嵌（双击即播）；视频→同目录相对引用
    # （隐藏 <video> 播音轨）；都不可用→空源（仅歌词预览）
    media_tag = '<audio id="audio" src=""></audio>'
    if audio_file:
        src = Path(audio_file)
        ext = src.suffix.lstrip(".").lower()
        audio_exts = {"mp3", "wav", "m4a", "flac", "ogg"}
        video_exts = {"mp4", "mov", "webm", "mkv"}
        if ext in audio_exts and src.is_file() and src.stat().st_size <= 30 * 1024 * 1024:
            import base64
            mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
                    "flac": "audio/flac", "ogg": "audio/ogg"}.get(ext, "audio/mpeg")
            b64 = base64.b64encode(src.read_bytes()).decode("ascii")
            media_tag = f'<audio id="audio" src="data:{mime};base64,{b64}"></audio>'
        elif ext in video_exts and src.is_file():
            media_tag = (f'<video id="audio" src="{src.name}" style="display:none" '
                         f'preload="auto"></video>')
        elif src.is_file():
            media_tag = f'<audio id="audio" src="{src.name}"></audio>'
    html = html.replace("__MEDIA_TAG__", media_tag)
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__MODE__", "line" if mode == "line" else "karaoke")
    html = html.replace("__MODE_CLS__", "mode-line" if mode == "line" else "mode-karaoke")
    return html


def save_player(lyrics: Lyrics, audio_file: str | None, out_path: Path,
                *, title: str = "", artist: str = "", mode: str = "karaoke") -> Path:
    out_path = Path(out_path)
    out_path.write_text(build_player_html(lyrics, audio_file=audio_file,
                                          title=title, artist=artist, mode=mode),
                        encoding="utf-8")
    return out_path


def save_lines_player(rows: list[dict], audio_file: str | None, out_path: Path,
                      *, title: str = "", artist: str = "") -> Path:
    """LRC 行级时间（[{text,start_ms,end_ms}]）→ 仅逐行播放器。

    每行作为一个整体高亮单元（无字级渐变），行间平滑滚动。
    """
    lines_data = [
        {
            "start": int(r["start_ms"]),
            "end": max(int(r.get("end_ms") or 0), int(r["start_ms"]) + 500),
            "bg": False,
            "words": [{"char": str(r["text"]),
                       "start": int(r["start_ms"]),
                       "end": max(int(r.get("end_ms") or 0), int(r["start_ms"]) + 500)}],
        }
        for r in rows if str(r.get("text") or "").strip()
    ]
    out_path = Path(out_path)
    out_path.write_text(
        build_player_html(None, lines_data=lines_data, audio_file=audio_file,
                          title=title, artist=artist, mode="line"),
        encoding="utf-8")
    return out_path
