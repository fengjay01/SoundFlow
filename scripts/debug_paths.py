# -*- coding: utf-8 -*-
"""SoundFlow 统一调试产物路径工具（随仓库分发，完全可移植）。

调试产物落在 ``<repo>/_debug_output/``；该目录被仓库根 ``.gitignore`` 的
``_debug_output/`` 规则排除，不会被提交 / 推送。本文件位于 ``scripts/``，
会随仓库分发，因此新开发者 clone 后即可使用，**不需要任何绝对路径**。

用法（在仓库根目录执行，任意 clone 路径都适用）：

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.cwd() / "scripts"))
    from debug_paths import debug_out, repo_path

    debug_out("screenshots", "check_01.png").write_bytes(data)
    debug_out("logs", "probe.json").write_text(text, encoding="utf-8")

也可以直接 ``from scripts.debug_paths import debug_out``（命名空间包）。
产物根目录可用环境变量 ``SOUNDFLOW_DEBUG_OUTPUT`` 覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEBUG_ROOT = REPO_ROOT / "_debug_output"

CATEGORIES = ("screenshots", "test-media", "logs", "archive", "diag", "tmp")


def debug_root() -> Path:
    """返回调试产物根目录（不存在则创建）。"""
    override = os.environ.get("SOUNDFLOW_DEBUG_OUTPUT")
    root = Path(override).expanduser() if override else DEFAULT_DEBUG_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def debug_out(category: str, name: str) -> Path:
    """返回某个分类下的目标文件路径，父目录自动创建。"""
    if category not in CATEGORIES:
        raise ValueError(
            "unknown category %r; use one of %s" % (category, ", ".join(CATEGORIES)))
    target_dir = debug_root() / category
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / name


def repo_path(*parts: str) -> Path:
    """只读定位仓库内源码文件；不要用它写文件。"""
    return REPO_ROOT.joinpath(*parts)


if __name__ == "__main__":
    print("debug root :", debug_root())
    print("repo root  :", REPO_ROOT)
    print("categories :", ", ".join(CATEGORIES))
