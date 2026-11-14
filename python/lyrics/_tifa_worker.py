# -*- coding: utf-8 -*-
r"""TIFA 子进程推理脚本（在"外部 Python 环境"内运行，自包含、不依赖本项目的包）。

由 lyrics/tifa_align.py 通过 subprocess 调用：
    <tifa_python> _tifa_worker.py --req <request.json> --out <result.json>

请求格式：
    {"wav": "...16k mono wav", "modelDir": "含 model.pt/config.yaml/vocabulary.json
     与 runtime/infer.py（openvpi/TIFA 源码）的目录", "language": "zh",
     "rows": [{"start_ms": 0, "end_ms": 0, "text": "..."}]}

结果格式：
    {"lines": {"<行下标>": [["字", start_ms, end_ms], ...]}, "language": "zh"}

流程（忠实移植 lrc-ttml timing_align.py 的做法）：
  1. 逐行切 clip（±0.2s 上下文），写 line_NNNN.wav / line_NNNN.txt；
  2. 子进程调用 openvpi/TIFA 的 infer.py（env 加固：UTF-8、TEMP 指向工作目录、
     各类缓存目录隔离）；
  3. 解析输出的 TextGrid（极简解析器），char 时间加回 clip 偏移；
  4. 一个 word 多字时用 phones tier 音素边界切分；模型字符序列与目标不一致时
     用内嵌的单调 DP 模糊映射（相似度 > 0.5 过滤，匹配率 < 60% 整行放弃）；
  5. 未匹配字夹在前后匹配字之间，行内保证 begin < end。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

CTX_BEFORE = 0.2   # 行首上下文（秒）
CTX_AFTER = 0.2    # 行尾上下文（秒）
MIN_CLIP = 0.3     # 最短 clip（秒）
SR = 16000

_LANG_ARGS = {
    "zh": ["-l", "zh"],
    "en": ["-l", "en"],
    "ja": ["-l", "ja"],
}


# --------------------------------------------------------------------------
# 音频
# --------------------------------------------------------------------------

def read_wav(path: str):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        width = w.getsampwidth()
        ch = w.getnchannels()
        raw = w.readframes(n)
    if width == 2:
        import array
        samples = array.array("h", raw)
        if ch > 1:
            samples = samples[::ch]
        return sr, [s / 32768.0 for s in samples]
    if width == 4:
        import array
        samples = array.array("f", raw)
        if ch > 1:
            samples = samples[::ch]
        return sr, list(samples)
    raise RuntimeError(f"不支持的 wav 位宽: {width * 8}bit（请先转 16/32bit PCM）")


def write_clip(samples, sr: float, start_s: float, end_s: float, path: Path) -> None:
    a = max(0, int(start_s * sr))
    b = min(len(samples), max(int(end_s * sr), a + int(0.1 * sr)))
    seg = samples[a:b]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        frames = b"".join(
            int(max(-1.0, min(1.0, s)) * 32767).to_bytes(2, "little", signed=True)
            for s in seg
        )
        w.writeframes(frames)


# --------------------------------------------------------------------------
# TextGrid 极简解析
# --------------------------------------------------------------------------

def parse_textgrid(path: Path) -> dict:
    """返回 {tier_name: [(xmin, xmax, text), ...]}（Praat TextGrid 极简解析）。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    tiers: dict[str, list] = {}
    cur: str | None = None
    xmin: float | None = None
    xmax: float | None = None
    label = ""
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r'name = "(.+?)"\s*:?\s*$', line)
        if m and not line.startswith("item"):
            cur = m.group(1)
            tiers.setdefault(cur, [])
            continue
        m = re.match(r'xmin = ([\d.eE+-]+)', line)
        if m:
            xmin = float(m.group(1))
            continue
        m = re.match(r'xmax = ([\d.eE+-]+)', line)
        if m:
            xmax = float(m.group(1))
            continue
        m = re.match(r'text = "(.*)"\s*$', line)
        if m:
            label = m.group(1)
            if cur is not None and xmin is not None and xmax is not None:
                tiers[cur].append((xmin, xmax, label))
            xmin = xmax = None
            label = ""
    return tiers


# --------------------------------------------------------------------------
# 字符映射（内嵌精简版：单调 DP + 音素切分）
# --------------------------------------------------------------------------

def _sim(a: str, b: str) -> float:
    a, b = a.casefold(), b.casefold()
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.6
    common = sum(1 for x, y in zip(a, b) if x == y)
    return 0.3 + 0.4 * common / max(len(a), len(b), 1) if common else -0.1


def _dp_align(targets: list[str], sources: list[str]) -> dict:
    """单调全局对齐（线性 gap 罚分），返回 {target 下标: source 下标}。"""
    n, m = len(targets), len(sources)
    if not n or not m:
        return {}
    gap = -0.5
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = gap * i
    for j in range(1, m + 1):
        dp[0][j] = gap * j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = max(dp[i - 1][j - 1] + _sim(targets[i - 1], sources[j - 1]),
                           dp[i - 1][j] + gap, dp[i][j - 1] + gap)
    out: dict = {}
    i, j = n, m
    while i > 0 and j > 0:
        cur = dp[i][j]
        if abs(cur - (dp[i - 1][j - 1] + _sim(targets[i - 1], sources[j - 1]))) < 1e-9:
            out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif abs(cur - (dp[i - 1][j] + gap)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return out


_KNPTN = re.compile(r"[^\W_]+", re.UNICODE)


def _kept(text: str) -> list[str]:
    """只保留可发音字符（Unicode L/N 类）。"""
    return [ch for ch in text if ch.isalnum()]


def map_line(text: str, tiers: dict, offset_s: float,
             clip_lo: float, clip_hi: float) -> list | None:
    """TextGrid → [[char, start_ms, end_ms], ...]；匹配率 < 60% 返回 None。"""
    texts = tiers.get("texts") or []
    phones = tiers.get("phones") or []
    if not texts:
        return None
    target = _kept(text)
    if not target:
        return None
    # TextGrid char 项 → (char, abs_start, abs_end)（同一 word 多字共享区间时分组）
    model: list[list] = []
    for a, b, t in texts:
        for ch in _kept(t or ""):
            model.append([ch, a + offset_s, b + offset_s])
    model_seq = [m[0] for m in model]
    if model_seq != target:
        # 字符不一致：单调 DP 模糊映射（相似度 > 0.5 过滤；音素边界已不可信，
        # 未匹配字夹在相邻匹配字之间——与 lrc-ttml 同语义）
        mapping = _dp_align(target, model_seq)
        pairs = {i: j for i, j in mapping.items()
                 if _sim(target[i], model_seq[j]) > 0.5}
        if len(pairs) < max(1, int(0.6 * len(target))):
            return None
        out: list = []
        prev_ms = int(clip_lo * 1000)
        for i, ch in enumerate(target):
            if i in pairs:
                j = pairs[i]
                a = max(model[j][1], clip_lo)
                b = min(model[j][2], clip_hi)
                if b <= a:
                    b = a + 0.001
            else:
                # 下一个匹配字的起点（无则 clip 终点），未匹配字夹在中间
                nxt = clip_hi
                for k in range(i + 1, len(target)):
                    if k in pairs:
                        nxt = max(model[pairs[k]][1], clip_lo)
                        break
                span = max(nxt - prev_s, 0.002)
                a = prev_s + span * 0.33
                b = prev_s + span * 0.66
                if b <= a:
                    b = a + 0.001
            # 全程毫秒：a/b（秒）×1000 后再与 prev_ms（毫秒）取 max——
            # 原实现 max(a秒, prev_s毫秒)×1000 每字指数×1000，时间轴爆炸
            s_ms = max(int(a * 1000), prev_ms)
            e_ms = max(int(b * 1000), s_ms + 1)
            out.append([ch, s_ms, e_ms])
            prev_ms = e_ms
            prev_s = e_ms / 1000.0
        return out
    # 完全一致路径：逐字独立区间（多字共享区间时用 phones tier 音素边界切分）
    groups: list[list[int]] = [[i] for i in range(len(model))]
    out: list = []
    for gi, grp in enumerate(groups):
        a = max(model[gi][1], clip_lo)
        b = min(model[gi][2], clip_hi)
        if b <= a:
            b = a + 0.001
        if len(grp) > 1:
            rel = [(max(a, pa + offset_s), min(b, pb + offset_s))
                   for pa, pb, pt in phones
                   if pb > a + 0.005 and pa < b - 0.005 and pb > pa and (pt or "").strip()]
            rel.sort()
            if len(rel) >= len(grp):
                cuts = [round(k * len(rel) / len(grp)) for k in range(len(grp) + 1)]
                cuts[0], cuts[-1] = 0, len(rel)
                for k, idx in enumerate(grp):
                    s = rel[cuts[k]][0]
                    e = rel[max(cuts[k], cuts[k + 1] - 1)][1]
                    out.append((target[idx], int(s * 1000), int(max(e, s + 0.001) * 1000)))
                continue
        span = (b - a) / len(grp)
        for k, idx in enumerate(grp):
            s = a + k * span
            e = a + (k + 1) * span
            out.append((target[idx], int(s * 1000), int(max(e, s + 0.001) * 1000)))
    # 单调化 + 保底 1ms
    prev_end = 0
    for k, item in enumerate(out):
        s = max(item[1], prev_end)
        e = max(item[2], s + 1)
        out[k] = [item[0], s, e]
        prev_end = e
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--req", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    req = json.loads(Path(args.req).read_text(encoding="utf-8"))
    model_dir = Path(req["modelDir"])
    runtime = model_dir / "runtime"
    infer = runtime / "infer.py"
    if not infer.is_file():
        raise RuntimeError(f"缺少 TIFA runtime: {infer}")
    language = str(req.get("language") or "zh")
    sr, samples = read_wav(req["wav"])
    duration = len(samples) / sr

    work = model_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    clips = work / "clips"
    out_dir = work / "grid"
    for d in (clips, out_dir):
        if d.exists():
            for f in d.iterdir():
                f.unlink(missing_ok=True)
        d.mkdir(parents=True, exist_ok=True)

    rows = req["rows"]
    offsets: list[float] = []
    for i, row in enumerate(rows):
        start_s = max(0.0, row["start_ms"] / 1000 - CTX_BEFORE)
        end_s = min(duration, max(row["end_ms"] / 1000, start_s + MIN_CLIP) + CTX_AFTER)
        write_clip(samples, sr, start_s, end_s, clips / f"line_{i:04d}.wav")
        (clips / f"line_{i:04d}.txt").write_text(row["text"], encoding="utf-8", newline="\n")
        offsets.append(start_s)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = str(work)
    env["MPLCONFIGDIR"] = str(work / "mpl")
    env["MPLBACKEND"] = "Agg"
    env["NUMBA_CACHE_DIR"] = str(work / "numba")
    env["XDG_CACHE_HOME"] = str(work / "xdg")
    for d in ("mpl", "numba", "xdg"):
        (work / d).mkdir(exist_ok=True)

    # lrc-ttml 同款：-m 传 model.pt 文件路径（不是目录）
    model_ckpt = model_dir / "model.pt"
    cmd = [sys.executable, "-X", "utf8", str(infer), str(clips),
           "-m", str(model_ckpt), "-o", str(out_dir),
           "--batch-size", "8", "--num-workers", "0", "--precision", "32-true"]
    cmd += _LANG_ARGS.get(language, ["-l", "zh"])
    proc = subprocess.run(cmd, cwd=str(runtime), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(f"TIFA infer.py 失败: {proc.stderr[-800:]}")

    lines: dict[str, list] = {}
    for i, row in enumerate(rows):
        grid = out_dir / f"line_{i:04d}.TextGrid"
        if not grid.is_file():
            continue
        tiers = parse_textgrid(grid)
        clip_lo = offsets[i]
        clip_hi = min(duration, max(row["end_ms"] / 1000, offsets[i] + MIN_CLIP) + CTX_AFTER)
        mapped = map_line(row["text"], tiers, offsets[i], clip_lo, clip_hi)
        if mapped:
            lines[str(i)] = mapped

    Path(args.out).write_text(
        json.dumps({"lines": lines, "language": language}, ensure_ascii=False),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
