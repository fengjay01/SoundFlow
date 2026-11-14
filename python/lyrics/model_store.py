# -*- coding: utf-8 -*-
"""lrc-ttml 模型仓库移植（MIT，来源 6hdj/lrc-ttml desktop/services/model_manager.py
与 desktop/resources/model_manifest.json）：完整模型目录 + 下载（魔搭直链/HTTP
多源回退/GitHub Release 镜像，Range 续传）+ 字节/SHA256 校验 + 清理。

缓存根目录：``<models_root>``（即 ``_tool_models/lyrics/models``，永远在 E 盘
项目内，绝不落 C 盘）。

目录布局（2026-11-06 起按分类一级目录组织，新下载自动遵循）：
- ``asr/``：Qwen3-ASR、whisper 系 HF 快照（``asr/hf_cache/models--<repo>/snapshots/``）
- ``alignment/``：Qwen3-FA 原版/Ultra、MMS、STARS、TIFA、Paraformer（funasr/）
- ``diarization/``：CAM++ 三变体
- ``separation/``：Demucs
旧布局（``l2t/models/<name>``、根级 Systran--* 等）在读取端均有兜底。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from worker_proxy import configure_process_proxy, load_proxy_config

_MANIFEST = Path(__file__).with_name("l2t_model_manifest.json")
_ROOT_HINT = ""  # 2026-11-06 分类化布局：仓库根即 lyrics/models（旧 l2t/ 嵌套已取消）


class ModelStoreError(RuntimeError):
    pass


def load_manifest() -> list[dict]:
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    return data["models"] if isinstance(data, dict) else data


def store_root(models_root: str | Path) -> Path:
    root = Path(models_root)
    return root / _ROOT_HINT if _ROOT_HINT else root


def _hf_cache_dir(root: Path, cache_key: str) -> Path:
    # hf 类条目目前只有 whisper 系（语音识别分类）→ 缓存归入 asr/hf_cache
    return root / "asr" / "hf_cache" / cache_key


def _flat_asr_dir(root: Path, cache_key: str) -> Path:
    """同一模型的另一套登记位置：分类直连目录 <root>/asr/<owner--model>。

    两个下载入口（model_store / worker_lyrics_models）各自写入一种布局，
    状态检查、复用与删除都必须同时认识这两种布局，避免同一模型存两份。
    """
    if not cache_key.startswith("models--"):
        return root / "asr" / "_none_"
    return root / "asr" / cache_key.removeprefix("models--")


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _download_url(url: str, dest: Path, expected_size: int,
                  on_bytes: Callable[[int], None], timeout: int = 60) -> None:
    """单 URL 下载，Range 断点续传，超长/损坏丢弃重下。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.is_file() else 0
    if expected_size and done > expected_size:
        part.unlink(missing_ok=True)
        done = 0
    headers = {"User-Agent": "pymss-studio-model-store"}
    if done:
        headers["Range"] = f"bytes={done}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = getattr(resp, "status", 200)
        if done and code != 206:
            part.unlink(missing_ok=True)  # 服务器不支持续传，干净重下
            done = 0
        with part.open("ab") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                on_bytes(len(chunk))
    size = part.stat().st_size
    if expected_size and size != expected_size:
        part.unlink(missing_ok=True)
        raise ModelStoreError(f"字节数不符 {dest.name}: 期望 {expected_size}，实际 {size}")
    part.replace(dest)


# 模型页可选下载源 → URL 域名白名单（未选时保留全部 URL，选中后严格过滤）
SOURCE_DOMAIN_PREF: dict[str, tuple[str, ...]] = {
    "modelscope": ("modelscope.cn",),
    "hf": ("huggingface.co",),
    "hf-mirror": ("hf-mirror.com",),
    "github": ("github.com", "gh-proxy.com", "github.boki.moe", "codeload.github.com",
               "objects.githubusercontent.com", "raw.githubusercontent.com"),
}


def _prefer_urls(urls: list[str], source: str | None) -> list[str]:
    """按用户选择的下载源**严格过滤** URL（选什么渠道就只用什么渠道）。

    未选（None）→ 按清单默认顺序全部可用；选中 → 只保留匹配域名的 URL，
    该渠道失败不偷偷换源（由用户自己决定是否切换）。
    """
    if not source:
        return urls
    pref = SOURCE_DOMAIN_PREF.get(source)
    if not pref:
        return urls
    return [u for u in urls if any(d in u for d in pref)]


def _fetch_model_file(entry: dict, rel: str, dest: Path,
                      expected: int, on_bytes: Callable[[int], None],
                      source: str | None = None) -> None:
    """按 source.kind 选源下载单文件（多 URL 依次回退）。

    kind=http_files：entry["files"] 里每文件自带 urls 列表（ModelScope 优先、
    hf-mirror 回退），按 rel 匹配。source：模型页手动选择的下载源
    （modelscope/hf/hf-mirror/github），由 _prefer_urls() 按域名严格过滤 URL；
    未选（None）时保留清单全部 URL，选中后该渠道失败也不偷偷换源。
    dest 是下载前算好的本地路径，只决定落在 _tool_models 的哪个位置，与 source 无关。
    """
    src = entry.get("source") or {}
    kind = src.get("kind")
    urls: list[str] = []
    if kind == "http_files":
        for spec in entry.get("files") or []:
            if spec.get("rel") == rel:
                urls = list(spec.get("urls") or [])
                break
        if not urls:
            raise ModelStoreError(f"http_files 清单缺少文件 {rel}")
    elif kind == "http":
        urls = list(src.get("urls") or [])
    elif kind == "hf":
        base = (entry.get("direct") or {}).get("base") or ""
        if base:
            urls = [base.rstrip("/") + "/" + rel]
        repo = src.get("repository") or ""
        if repo:
            for ep in ("https://hf-mirror.com", "https://huggingface.co"):
                urls.append(f"{ep}/{repo}/resolve/main/{rel}")
    elif kind == "github_release":
        mirror = entry.get("mirror") or {}
        repo, tag = mirror.get("repo") or src.get("repo"), mirror.get("tag") or src.get("tag")
        for tpl in mirror.get("urls") or []:
            for name in mirror.get("files") or []:
                urls.append(tpl.format(repo=repo, tag=tag, name=name))
    last: Exception | None = None
    urls = _prefer_urls(urls, source)
    if source and not urls:
        raise ModelStoreError(
            f"所选下载源「{source}」没有 {dest.name} 的镜像地址。"
            "请切换其它下载源（或用「默认」）后重试")
    for url in urls:
        try:
            _download_url(url, dest, expected, on_bytes)
            return
        except Exception as exc:  # noqa: BLE001 — 多源回退
            last = exc
            time.sleep(0.5)
    # 多轮重试（指数退避）：SSL EOF / 连接重置等瞬时网络抖动常在重试后成功
    for attempt in range(1, 4):
        time.sleep(min(2 ** attempt, 8))
        for url in urls:
            try:
                _download_url(url, dest, expected, on_bytes)
                return
            except Exception as exc:  # noqa: BLE001 — 逐源重试
                last = exc
    raise ModelStoreError(
        f"{dest.name} 所有下载源均失败（已重试 3 轮）: {last}。"
        "网络不稳定时可切换下载源（HF-Mirror 镜像）后重试，断点续传不会重头下载")


def _verify(entry: dict, root: Path) -> tuple[bool, list[str]]:
    """按 verify.kind 校验，返回 (完整, 缺失/损坏列表)。"""
    v = entry.get("verify") or {}
    kind = v.get("kind")
    problems: list[str] = []
    if kind == "hf_snapshot":
        cache = _hf_cache_dir(root, (entry.get("source") or {}).get("cache_key", ""))
        snaps = sorted((cache / "snapshots").glob("*")) if (cache / "snapshots").is_dir() else []
        snap = snaps[-1] if snaps else cache
        for rel, size in (v.get("files") or {}).items():
            p = snap / rel
            if not p.is_file():
                problems.append(rel)
            elif size and p.stat().st_size != size:
                problems.append(f"{rel}(字节不符)")
    elif kind == "hf_presence":
        cache_key = (entry.get("source") or {}).get("cache_key", "")
        cache = _hf_cache_dir(root, cache_key)
        flat = _flat_asr_dir(root, cache_key)
        # 两种布局任一份存在即视为已下载：删掉重复副本后不再误报「未下载」。
        if not any((cache / "snapshots").glob("*/model.bin")) and not (flat / "model.bin").is_file():
            problems.append("model.bin")
    elif kind == "file_sha256":
        p = root / v.get("target", "")
        if not p.is_file():
            problems.append(v.get("target", ""))
        elif v.get("sha256") and _sha256(p) != v["sha256"]:
            problems.append(f"{v.get('target')}(SHA256 不符)")
    elif kind == "files_sha256":
        base = root / v.get("target", "")
        for rel, meta in (v.get("files") or {}).items():
            p = base / rel
            if not p.is_file():
                problems.append(rel)
            elif meta.get("sha256") and _sha256(p) != meta["sha256"]:
                problems.append(f"{rel}(SHA256 不符)")
    elif kind == "files_exist":
        for rel in v.get("files") or []:
            if not (root / rel).is_file():
                problems.append(rel)
    elif kind == "dir_exists":
        d = root / v.get("target", "")
        if not d.is_dir() or not any(d.iterdir()):
            problems.append(v.get("target", ""))
    return (not problems), problems


_LICENSE_RE = None


def _license_short(license_text: str) -> str:
    """从长协议说明中提取短协议名（如 MIT / Apache-2.0 / CC BY-NC-SA 4.0）。"""
    import re

    m = re.search(
        r"MIT|Apache-2\.0|CC[- ]BY[- ]NC[- ]SA 4\.0|CC[- ]BY[- ]NC 4\.0|"
        r"CC[- ]BY 4\.0|CC[- ]BY-NC|研究用途", license_text or "")
    return m.group(0) if m else (license_text or "").split("；")[0][:20]


def _source_label(entry: dict) -> str:
    """模型主要来源（显示用）。"""
    src = entry.get("source") or {}
    kind = src.get("kind")
    if kind == "github_release":
        return "GitHub"
    if kind == "modelscope":
        return "ModelScope"
    if kind == "hf":
        return "HuggingFace"
    if kind == "bundled":
        return "内置"
    if kind == "http_files":
        urls = ((entry.get("files") or [{}])[0].get("urls")) or []
        if any("modelscope.cn" in u for u in urls):
            return "ModelScope"
        return "HuggingFace"
    if kind == "http":
        urls = list(src.get("urls") or [])
        u = urls[0] if urls else ""
        if "modelscope.cn" in u:
            return "ModelScope"
        if "github.com" in u or "gh-proxy" in u:
            return "GitHub"
        if "huggingface.co" in u:
            return "HuggingFace"
        if "fbaipublicfiles.com" in u:
            return "Meta 官方"
        return "HTTP"
    return str(kind or "未知")


def status(models_root: str | Path) -> dict:
    """全部模型条目的真实状态（文件级检查）。"""
    root = store_root(models_root)
    out = []
    for entry in load_manifest():
        ok, problems = _verify(entry, root)
        approx = int(entry.get("approx_size") or 0)
        out.append({
            "id": entry["id"],
            "name": entry.get("name"),
            "category": entry.get("category"),
            "license": entry.get("license"),
            "licenseShort": _license_short(str(entry.get("license") or "")),
            "source": _source_label(entry),
            "note": entry.get("note"),
            "approxSize": approx,
            "complete": ok,
            "problems": problems,
            # verify 目标目录（共享目录的模型下载互斥：Ultra/Ultra+Head 共用同一 .part）
            "target": (entry.get("verify") or {}).get("target", ""),
            "cacheDir": str(root / ((entry.get("verify") or {}).get("target", "")).split("/")[0]) if entry.get("verify") else str(root),
        })
    return {"root": str(root), "models": out}


def _emit(task_id: Optional[str], stage: str, message: str) -> None:
    from worker_protocol import emit
    emit("task_stage", {"stage": stage, "message": message}, task_id=task_id)


class _Progress:
    """按字节聚合的百分比进度（整数百分比变化才上报，避免事件洪泛）。"""

    def __init__(self, total: int, cb: Optional[Callable[[int, str], None]]):
        self.total = max(total, 1)
        self.done = 0
        self.cb = cb
        self._last = -1
        self._msg = ""

    def add(self, n: int, message: str = "") -> None:
        if message:
            self._msg = message
        self.done += n
        pct = min(99, int(self.done * 100 / self.total))
        if pct != self._last:
            self._last = pct
            if self.cb:
                self.cb(pct, self._msg or f"{pct}%")

    def finish(self, message: str) -> None:
        if self.cb:
            self.cb(100, message)


def _plan_bytes(entry: dict) -> int:
    """预估下载总量（按 source.kind）。"""
    src = entry.get("source") or {}
    kind = src.get("kind")
    v = entry.get("verify") or {}
    if kind == "hf":
        files = dict((entry.get("direct") or {}).get("files") or {})
        if not files:
            files = dict(v.get("files") or {})
        return sum(int(s) for s in files.values())
    if kind == "http_files":
        return sum(int(spec.get("size") or 0) for spec in entry.get("files") or [])
    if kind == "http":
        return int(v.get("size") or entry.get("approx_size") or 0)
    if kind == "github_release":
        return int((entry.get("mirror") or {}).get("size") or 0)
    return 0


def download(models_root: str | Path, model_id: str,
             task_id: Optional[str] = None,
             progress: Optional[Callable[[int, str], None]] = None,
             source: str | None = None) -> dict:
    """下载单个模型（断点续传 + 校验 + 百分比进度），返回 {files, bytes, cacheDir}。

    source：模型页手动选择的下载源（modelscope/hf/hf-mirror/github）；
    None = 按清单默认优先级（ModelScope 优先、hf-mirror 回退）。
    """
    root = store_root(models_root)
    root.mkdir(parents=True, exist_ok=True)
    entry = next((m for m in load_manifest() if m["id"] == model_id), None)
    if entry is None:
        raise ModelStoreError(f"未知模型: {model_id}")
    src = entry.get("source") or {}
    kind = src.get("kind")
    tracker = _Progress(_plan_bytes(entry), progress)
    downloaded: list[str] = []
    total_bytes = 0

    _emit(task_id, "downloading", f"开始下载 {entry.get('name')}")
    if kind == "hf":
        flat = _flat_asr_dir(root, src.get("cache_key", ""))
        if (flat / "model.bin").is_file() and (flat / "config.json").is_file():
            # 另一个入口已把同一模型下到分类目录：直接复用，绝不再下第二份。
            _emit(task_id, "verifying", f"复用分类目录已有模型 {flat.name}，跳过下载")
            return {"files": [], "bytes": 0, "cacheDir": str(root), "reused": str(flat)}
        cache = _hf_cache_dir(root, src.get("cache_key", ""))
        snap = cache / "snapshots" / "master"
        files = dict((entry.get("direct") or {}).get("files") or {})
        if not files:  # 无清单时按 verify.files 下载
            files = dict((entry.get("verify") or {}).get("files") or {})
        for i, (rel, size) in enumerate(sorted(files.items()), 1):
            dest = snap / rel
            label = f"[{i}/{len(files)}] {rel} ({(size or 0) / 1048576:.1f} MB)"
            if dest.is_file() and (not size or dest.stat().st_size == size):
                _emit(task_id, "downloading", f"{label} 已存在")
                continue
            _emit(task_id, "downloading", label)
            _fetch_model_file(entry, rel, dest, int(size or 0),
                              lambda n, _l=label: (progress and tracker.add(n, _l)),
                              source=source)
            downloaded.append(rel)
    elif kind == "http_files":
        v = entry.get("verify") or {}
        base = root / v.get("target", "")
        specs = entry.get("files") or []
        for i, spec in enumerate(specs, 1):
            rel = spec.get("rel") or ""
            size = int(spec.get("size") or 0)
            dest = base / rel
            label = f"[{i}/{len(specs)}] {rel} ({size / 1048576:.1f} MB)"
            if dest.is_file() and (not size or dest.stat().st_size == size):
                _emit(task_id, "downloading", f"{label} 已存在")
                continue
            _emit(task_id, "downloading", label)
            _fetch_model_file(entry, rel, dest, size,
                              lambda n, _l=label: (progress and tracker.add(n, _l)),
                              source=source)
            downloaded.append(f"{v.get('target', '')}/{rel}")
    elif kind == "http":
        v = entry.get("verify") or {}
        target = v.get("target") or ""
        dest = root / target
        if not (dest.is_file() and v.get("sha256") and _sha256(dest) == v["sha256"]):
            _emit(task_id, "downloading", f"{target} ({(v.get('size') or 0) / 1048576:.1f} MB)")
            _fetch_model_file(entry, Path(target).name, dest,
                              int(v.get("size") or 0),
                              lambda n: (progress and tracker.add(n, target)),
                              source=source)
            downloaded.append(target)
    elif kind == "github_release":
        mirror = entry.get("mirror") or {}
        archive_name = (mirror.get("files") or ["tifa-aligner.tar"])[0]
        tmp = root / "_archives" / archive_name
        _emit(task_id, "downloading", f"{archive_name} ({(mirror.get('size') or 0) / 1048576:.1f} MB)")
        _fetch_model_file(entry, archive_name, tmp,
                          int(mirror.get("size") or 0),
                          lambda n: (progress and tracker.add(n, archive_name)),
                          source=source)
        downloaded.append(archive_name)
        dest_base = root / (mirror.get("dest") or "alignment/tifa")
        dest_base.mkdir(parents=True, exist_ok=True)
        _emit(task_id, "extracting", "下载完成，解包归档…")
        with tarfile.open(tmp, "r") as tf:
            members = tf.getmembers()
            files_only = [m for m in members if m.isfile()]
            # 上游归档自带 models/tifa/ 顶层前缀：直接解到 root，避免
            # root/models/tifa/models/tifa 双重嵌套导致校验失败。
            strip_prefix = False
            if files_only and mirror.get("dest"):
                dest_posix = dest_base.relative_to(root).as_posix()
                strip_prefix = all(
                    m.name == dest_posix or m.name.startswith(dest_posix + "/")
                    for m in files_only)
            if strip_prefix:
                tf.extractall(root)  # noqa: S202 — 上游受信归档
            else:
                tf.extractall(dest_base)  # noqa: S202 — 上游受信归档
        tmp.unlink(missing_ok=True)
    elif kind == "bundled":
        raise ModelStoreError(f"{entry['id']} 随上游仓库提供，本移植版请手动放置或改用 HTTP 源")
    else:
        raise ModelStoreError(f"未知下载类型: {kind}")

    # 事后校验（SHA256/字节）
    _emit(task_id, "verifying", "校验文件完整性…")
    ok, problems = _verify(entry, root)
    if not ok:
        raise ModelStoreError(f"下载后校验失败: {'; '.join(problems[:4])}")
    if progress:
        tracker.finish("校验通过")
    _emit(task_id, "verifying", "校验通过")
    return {"files": downloaded, "bytes": total_bytes, "cacheDir": str(root)}


def delete(models_root: str | Path, model_id: str) -> int:
    """清理模型缓存，返回释放的字节数。"""
    root = store_root(models_root)
    entry = next((m for m in load_manifest() if m["id"] == model_id), None)
    if entry is None:
        raise ModelStoreError(f"未知模型: {model_id}")
    freed = 0
    targets: list[Path] = []
    v = entry.get("verify") or {}
    if entry.get("source", {}).get("kind") == "hf":
        cache_key = entry["source"].get("cache_key", "")
        targets.append(_hf_cache_dir(root, cache_key))
        if cache_key.startswith("models--"):
            # 两种布局一起清，否则删完仍因另一份而显示「已下载」。
            targets.append(_flat_asr_dir(root, cache_key))
    elif v.get("target"):
        targets.append(root / v["target"])
    for t in targets:
        if t.is_dir():
            for p in t.rglob("*"):
                if p.is_file():
                    freed += p.stat().st_size
            shutil.rmtree(t, ignore_errors=True)
        elif t.is_file():
            freed += t.stat().st_size
            t.unlink(missing_ok=True)
    return freed


def ensure_proxy() -> None:
    """worker 进程统一代理设置（与其它下载链路一致）。"""
    try:
        configure_process_proxy(load_proxy_config())
    except Exception:  # noqa: BLE001
        pass
