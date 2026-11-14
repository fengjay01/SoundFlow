# -*- coding: utf-8 -*-
r"""TIFA 歌声音素对齐适配器（迁移自 lrc-ttml，实验性可选引擎）。

TIFA（openvpi/TIFA，MIT 源码）用歌声音素对齐替代 whisper 词级时间戳，
lrc-ttml 实测逐字误差 0.06~0.11s（whisper 0.14~0.27s）。但：

- **TIFA-1.0-ST 模型为 CC BY-NC-SA 4.0（非商业许可）**，约 163MB；
- 需要独立外部 Python 环境（g2pflow / lightning / librosa 等重依赖）；
- 需要逐句 LRC。

因此本适配器设计为**完全可选**：未配置时一切走 whisperx + DP 主路径，
绝不阻塞默认流程。启用方式（设置外部环境后自动生效）：

    set LYRICS_TIFA_PYTHON=E:\path\to\tifa-env\python.exe
    set LYRICS_TIFA_MODEL=E:\path\to\tifa-model-dir

失败（未配置 / 外部环境报错）自动回退 whisperx 主路径。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional


class TifaUnavailable(RuntimeError):
    """TIFA 未配置或运行失败（调用方应回退 whisperx 主路径）。"""


def tifa_available() -> bool:
    """是否满足 TIFA 运行条件（外部 Python + 模型目录）。"""
    return bool(tifa_python() and tifa_model_dir())


def tifa_python() -> Optional[str]:
    value = os.environ.get("LYRICS_TIFA_PYTHON", "")
    return value if value and Path(value).is_file() else None


def tifa_model_dir() -> Optional[str]:
    value = os.environ.get("LYRICS_TIFA_MODEL", "")
    return value if value and Path(value).is_dir() else None


def align_with_tifa(wav: str, rows: list[dict], language: str,
                    timeout: int = 600) -> Optional[dict]:
    """逐行调用外部 TIFA 环境（子进程隔离），返回 {行号: [(char, start_ms, end_ms)]}。

    rows 为 LRC rows（需要逐行时间戳）。失败抛 TifaUnavailable，由调用方回退。
    适配器以子进程调用外部 Python 执行 TIFA 推理脚本（lrc-ttml 的
    timing_align 等价物），主进程不引入 g2pflow 等重依赖。
    """
    python = tifa_python()
    model_dir = tifa_model_dir()
    if not python or not model_dir:
        raise TifaUnavailable("TIFA 未配置（需要 LYRICS_TIFA_PYTHON / LYRICS_TIFA_MODEL）")
    if not rows:
        raise TifaUnavailable("TIFA 需要逐句 LRC 时间戳")
    worker_script = Path(__file__).with_name("_tifa_worker.py")
    if not worker_script.is_file():
        raise TifaUnavailable("缺少 _tifa_worker.py（TIFA 子进程脚本未随包安装）")
    import json
    import tempfile

    payload = {"wav": wav, "modelDir": model_dir, "language": language,
               "rows": rows}
    payload_path = Path(tempfile.gettempdir()) / f"tifa_req_{os.getpid()}.json"
    out_path = Path(tempfile.gettempdir()) / f"tifa_res_{os.getpid()}.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        proc = subprocess.run(
            [python, str(worker_script), "--req", str(payload_path),
             "--out", str(out_path)],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            raise TifaUnavailable(f"TIFA 外部环境失败: {proc.stderr[-400:]}")
        return json.loads(out_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TifaUnavailable(f"TIFA 外部 Python 不可用: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TifaUnavailable("TIFA 推理超时") from exc
    finally:
        for p in (payload_path, out_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
