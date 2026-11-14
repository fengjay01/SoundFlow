"""Lyrics tool-model management: catalog, status, and download.

Storage layout mirrors the ASR tool-model convention (see worker_models.py):

    <model_root>/_tool_models/lyrics/models/asr/<owner--model>/<flat model files>

where ``<owner--model>`` is a HuggingFace repo id with ``/`` replaced by ``--``
and the directory holds the repository files verbatim. A flat directory is what
whisperx accepts directly (``load_model(whisper_arch=<dir>)`` and
``load_align_model(model_name=<dir>, model_cache_only=True)``), so no HuggingFace
cache-layout reimplementation is needed at inference time.

The download event protocol mirrors ``worker_download.cmd_download_model``:
``download_started`` / ``download_stage`` / ``download_file`` /
``download_progress`` / ``download_log`` / ``download_done``, each tagged with
``task_id``. Cancellation is process-level (the Rust side kills the worker),
the same as ``download_model``.

Transfer uses the HuggingFace tree/resolve HTTP API via stdlib urllib only, so
this module works in the bootstrap Python without pymss or huggingface_hub.
The transfer layer is resilience-first (see the download section below):
mirror endpoints fail over automatically (hf-mirror.com → huggingface.co by
default, overridable via ``payload["endpoint"]`` or ``$HF_ENDPOINT``), partial
files resume with HTTP Range requests, transient network/SSL errors retry with
backoff, and the studio proxy setting plus host CA trust (worker_https /
worker_proxy, when importable) are honoured.
"""

from __future__ import annotations

import http.client
import json
import os
import ssl
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker_protocol import emit, emit_error

LYRICS_TOOL = "lyrics"

# Weight suffixes used for completeness checks (any one real weight file is
# enough). ``model.bin`` (faster-whisper) matches ``*.bin``.
_WEIGHT_SUFFIXES = (".bin", ".safetensors", ".pt", ".pth", ".ckpt", ".onnx")
# 其他推理框架的权重副本/历史格式：正常下载不需要（whisperx 走 pytorch/safetensors）。
_SKIP_FRAMEWORK_FILES = {"flax_model.msgpack", "tf_model.h5", "rust_model.ot"}
_MIN_WEIGHT_BYTES = 1024
_LFS_POINTER_PREFIX = b"version https://git-lfs"


@dataclass(frozen=True)
class LyricsModelEntry:
    """Catalog metadata for one downloadable lyrics model."""

    key: str
    repo: str
    role: str  # "recognition" | "alignment"
    language: str | None  # None = multilingual ASR; "zh"/"en" for align models
    required_files: tuple[str, ...]
    size_bytes: int  # approximate expected size for UI display; 0 = unknown

    @property
    def cache_name(self) -> str:
        return self.repo.replace("/", "--")


# Catalog of lyrics-pipeline models. ASR entries mirror the model sizes offered
# in LyricsView; align entries mirror whisperx DEFAULT_ALIGN_MODELS for zh/en.
LYRICS_MODELS: dict[str, LyricsModelEntry] = {
    entry.key: entry
    for entry in (
        LyricsModelEntry(
            key="faster-whisper-tiny",
            repo="Systran/faster-whisper-tiny",
            role="recognition",
            language=None,
            required_files=("config.json", "model.bin", "tokenizer.json"),
            size_bytes=75 * 1024 * 1024,
        ),
        LyricsModelEntry(
            key="faster-whisper-base",
            repo="Systran/faster-whisper-base",
            role="recognition",
            language=None,
            required_files=("config.json", "model.bin", "tokenizer.json"),
            size_bytes=145 * 1024 * 1024,
        ),
        LyricsModelEntry(
            key="faster-whisper-small",
            repo="Systran/faster-whisper-small",
            role="recognition",
            language=None,
            required_files=("config.json", "model.bin", "tokenizer.json"),
            size_bytes=484 * 1024 * 1024,
        ),
        LyricsModelEntry(
            key="faster-whisper-medium",
            repo="Systran/faster-whisper-medium",
            role="recognition",
            language=None,
            required_files=("config.json", "model.bin", "tokenizer.json"),
            size_bytes=1500 * 1024 * 1024,
        ),
        LyricsModelEntry(
            key="align-zh",
            repo="jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
            role="alignment",
            language="zh",
            required_files=(
                "config.json",
                "preprocessor_config.json",
                "vocab.json",
                "special_tokens_map.json",
            ),
            size_bytes=1200 * 1024 * 1024,
        ),
        LyricsModelEntry(
            key="align-en",
            repo="facebook/wav2vec2-base-960h",
            role="alignment",
            language="en",
            required_files=("config.json", "preprocessor_config.json", "vocab.json"),
            size_bytes=360 * 1024 * 1024,
        ),
    )
}


def lyrics_model_role(name: str) -> str:
    lowered = name.lower()
    return "alignment" if "wav2vec2" in lowered or "align" in lowered else "recognition"


def cache_name_for_repo(repo: str) -> str:
    return repo.replace("/", "--")


def repo_for_cache_name(cache_name: str) -> str:
    owner, separator, model = cache_name.partition("--")
    return f"{owner}/{model}" if separator and owner and model else cache_name


def _default_model_dir() -> Path:
    """Resolve the pymss model root without requiring pymss.

    Mirrors pymss.model_registry._default_model_dir: prefer pymss itself when
    importable (runtime Python), else the same env/cache fallbacks so the
    bootstrap Python lands on the same directory. The last-resort fallback is
    the project-local ``data/models`` (NEVER the user home — C-drive is
    forbidden for model storage by project policy).
    """
    try:
        from pymss.model_registry import model_root  # type: ignore

        return Path(model_root(None))
    except Exception:
        pass
    env_value = os.environ.get("PYMSS_MODEL_DIR")
    if env_value:
        return Path(env_value).expanduser()
    # Project-local fallback (dev layout: python/worker_lyrics_models.py →
    # project root). Keeps everything off the system drive.
    return Path(__file__).resolve().parents[1] / "data" / "models"


def lyrics_models_root(model_dir: str | None = None) -> Path:
    root = Path(model_dir).expanduser() if model_dir else _default_model_dir()
    return root / "_tool_models" / LYRICS_TOOL / "models"


def find_lyrics_model(key_or_repo: str) -> LyricsModelEntry | None:
    key = str(key_or_repo or "").strip()
    if key in LYRICS_MODELS:
        return LYRICS_MODELS[key]
    cache = cache_name_for_repo(key)
    for entry in LYRICS_MODELS.values():
        if entry.cache_name == cache or entry.repo == key:
            return entry
    return None


def _has_weight_file(model_dir: Path) -> bool:
    if not model_dir.is_dir():
        return False
    for path in model_dir.iterdir():
        if not path.is_file():
            continue
        if not path.name.lower().endswith(_WEIGHT_SUFFIXES):
            continue
        try:
            size = path.stat().st_size
            if size < _MIN_WEIGHT_BYTES:
                continue
            with path.open("rb") as file:
                head = file.read(len(_LFS_POINTER_PREFIX))
            if head.startswith(_LFS_POINTER_PREFIX):
                continue
            return True
        except OSError:
            continue
    return False


def is_complete_lyrics_model_cache(model_dir: Path | str) -> bool:
    """A lyrics model cache is complete when it is usable offline.

    Requirements: no ``.part`` remnants, every required file present, and at
    least one real weight file (>= 1 KiB, not a git-lfs pointer). Unknown cache
    names fall back to ``config.json`` + one weight file.
    """
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        return False
    for path in model_dir.rglob("*"):
        if path.is_file() and path.name.endswith((".part", ".incomplete")):
            return False
    entry = LYRICS_MODELS.get(model_dir.name)
    if entry is None:
        for candidate in LYRICS_MODELS.values():
            if candidate.cache_name == model_dir.name:
                entry = candidate
                break
    required = entry.required_files if entry is not None else ("config.json",)
    for name in required:
        path = model_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            return False
    return _has_weight_file(model_dir)


def missing_files_for(model_dir: Path | str, entry: LyricsModelEntry) -> list[str]:
    model_dir = Path(model_dir)
    missing = []
    for name in entry.required_files:
        path = model_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(name)
    if not _has_weight_file(model_dir):
        missing.append("<weight file>")
    return missing


def _dir_stats(model_dir: Path) -> tuple[int, int]:
    size = 0
    count = 0
    if model_dir.is_dir():
        for path in model_dir.rglob("*"):
            if path.is_file():
                try:
                    size += path.stat().st_size
                    count += 1
                except OSError:
                    pass
    return size, count


def status_payload(model_dir: str | None = None) -> dict[str, Any]:
    root = lyrics_models_root(model_dir)
    asr_root = root / "asr"  # 分类布局：whisper/wav2vec2 归语音识别目录
    models = []
    for entry in LYRICS_MODELS.values():
        path = asr_root / entry.cache_name
        if not is_complete_lyrics_model_cache(path):
            legacy = root / entry.cache_name
            if is_complete_lyrics_model_cache(legacy):
                path = legacy  # 旧布局兜底
            else:
                # 模型管理页的 HF 快照布局：同一模型的另一份登记位置。
                snaps = (asr_root / "hf_cache" / ("models--" + entry.cache_name)
                         / "snapshots")
                if snaps.is_dir():
                    for snap in sorted(snaps.iterdir()):
                        if is_complete_lyrics_model_cache(snap):
                            path = snap
                            break
        complete = is_complete_lyrics_model_cache(path)
        size, count = _dir_stats(path)
        models.append({
            "id": entry.cache_name,
            "key": entry.key,
            "name": entry.repo,
            "tool": LYRICS_TOOL,
            "role": entry.role,
            "language": entry.language,
            "path": str(path),
            "complete": complete,
            "sizeBytes": size,
            "expectedSizeBytes": entry.size_bytes,
            "fileCount": count,
            "missingFiles": [] if complete else missing_files_for(path, entry),
        })
    return {
        "tool": LYRICS_TOOL,
        "modelDir": str(root),
        "models": models,
        "completeCount": sum(1 for item in models if item["complete"]),
    }


def cmd_lyrics_model_status(payload: dict[str, Any]) -> int:
    try:
        emit("lyrics_model_status", status_payload(payload.get("modelDir") or None))
        return 0
    except Exception as exc:
        return emit_error("LYRICS_MODEL_STATUS_FAILED", str(exc), traceback.format_exc())


# ---------------------------------------------------------------------------
# Download (event flow mirrors worker_download.cmd_download_model, but the
# transfer engine below is self-contained: this module stays independent from
# worker_download / worker_models and works in the bootstrap Python.)
#
# Robustness contract (what the first revision lacked):
#   * endpoint failover  — explicit endpoint → $HF_ENDPOINT → hf-mirror.com →
#     huggingface.co; every network failure moves to the next candidate;
#   * resume             — partial data lands in ``<dest>.part`` and the next
#     attempt continues with ``Range: bytes=<n>-`` (206) or restarts cleanly
#     when a server ignores the range;
#   * retries            — transient socket/SSL/HTTP errors retry with
#     backoff; only unexpected HTTP client errors abort immediately;
#   * proxy + TLS        — the studio proxy setting (worker_proxy) and the
#     host CA trust bootstrap (worker_https, when importable) both apply.
# ---------------------------------------------------------------------------


def _emit_download_log(task_id: str | None, level: str, message: str, **extra: Any) -> None:
    payload: dict[str, Any] = {"level": level, "message": message}
    payload.update(extra)
    emit("download_log", payload, task_id=task_id)


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


DEFAULT_ENDPOINT = "https://hf-mirror.com"
FALLBACK_ENDPOINTS = ("https://hf-mirror.com", "https://huggingface.co")
_USER_AGENT = "pymss-studio-lyrics/2"
_CHUNK_SIZE = 256 * 1024
_MAX_FILE_ATTEMPTS = 6
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 8.0
# Transient HTTP statuses worth retrying; other 4xx are caller mistakes.
_RETRYABLE_HTTP_STATUS = {408, 429}


def candidate_endpoints(explicit: str | None = None) -> list[str]:
    """Ordered, de-duplicated mirror candidates for this download."""
    ordered: list[str] = []
    for item in (explicit, os.environ.get("HF_ENDPOINT"), *FALLBACK_ENDPOINTS):
        value = str(item or "").strip().rstrip("/")
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def _ssl_context():
    """Host CA trust when worker_https is available, stdlib defaults otherwise."""
    try:
        import worker_https  # type: ignore

        return worker_https.build_ssl_context()
    except Exception:
        return ssl.create_default_context()


def _build_opener(proxy_url: str | None, force_direct: bool, task_id: str | None):
    """Build a urllib opener honouring the studio proxy setting.

    ``force_direct`` (proxy mode "none") bypasses even environment proxies.
    SOCKS proxies need PySocks which stdlib urllib cannot honour; in that case
    the default opener is returned and the limitation is logged.
    """
    handlers: list[urllib.request.BaseHandler] = []
    if force_direct:
        handlers.append(urllib.request.ProxyHandler({}))
    elif proxy_url:
        scheme = urllib.parse.urlsplit(proxy_url).scheme.lower()
        if scheme.startswith("socks"):
            try:
                import socks  # type: ignore  # noqa: F401
            except ImportError:
                _emit_download_log(
                    task_id, "warning",
                    f"SOCKS proxy {proxy_url} requires PySocks (not installed); downloading directly",
                )
                return urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=_ssl_context()))
        else:
            handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    handlers.append(urllib.request.HTTPSHandler(context=_ssl_context()))
    return urllib.request.build_opener(*handlers)


def _proxy_settings(task_id: str | None) -> tuple[Any, str | None, bool]:
    """(opener, proxy_url, force_direct) from the studio proxy configuration."""
    try:
        from worker_proxy import effective_proxy_url, load_proxy_config

        config = load_proxy_config()
        proxy_url = effective_proxy_url(config)
        return _build_opener(proxy_url or None, config.mode == "none", task_id), proxy_url, config.mode == "none"
    except Exception:
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context())), None, False


class _TransientTransferError(RuntimeError):
    """A recoverable transfer problem: keep the .part file and retry."""


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _TransientTransferError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_HTTP_STATUS or exc.code >= 500
    # URLError (DNS, refused, wrapped SSL), raw SSL errors, timeouts, and
    # mid-stream breaks (http.client.IncompleteRead is an HTTPException).
    return isinstance(
        exc,
        (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException),
    )


def _urlopen_with_trust():
    """urlopen carrying host CA trust when worker_https is importable."""
    try:
        import worker_https  # type: ignore

        return worker_https.urlopen
    except Exception:
        return urllib.request.urlopen


def list_repo_files(repo: str, endpoint: str, timeout: int) -> list[dict[str, Any]]:
    """List repository files through the HuggingFace tree API.

    Returns ``[{path, size}]`` with ``size`` being the real blob size (the
    ``lfs`` sub-object size for LFS files). Pagination is followed via the
    ``Link: <...>; rel="next"`` header.
    """
    files: list[dict[str, Any]] = []
    urlopen = _urlopen_with_trust()
    url = f"{endpoint}/api/models/{urllib.parse.quote(repo)}/tree/main?recursive=true"
    pages = 0
    while url and pages < 10:
        pages += 1
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            link = response.headers.get("Link") or ""
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected tree response for {repo}")
        for item in data:
            if item.get("type") != "file":
                continue
            lfs = item.get("lfs") or {}
            size = lfs.get("size") or item.get("size") or 0
            files.append({"path": str(item.get("path") or ""), "size": int(size)})
        url = ""
        for part in link.split(","):
            if 'rel="next"' in part:
                start = part.find("<")
                end = part.find(">", start + 1)
                if start >= 0 and end > start:
                    url = part[start + 1:end]
    return files


def list_repo_files_with_failover(
    repo: str, endpoints: list[str], timeout: int, task_id: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Try every endpoint until one lists the repo; return (files, endpoint)."""
    last_error: BaseException | None = None
    for endpoint in endpoints:
        try:
            files = list_repo_files(repo, endpoint, timeout)
            if files:
                return files, endpoint
            last_error = RuntimeError(f"Empty file listing for {repo} via {endpoint}")
        except Exception as exc:
            last_error = exc
            _emit_download_log(task_id, "warning", f"Metadata via {endpoint} failed: {exc}")
    raise RuntimeError(f"Cannot list files for {repo}: {last_error}")


def _download_one(
    repo: str,
    rel_path: str,
    dest: Path,
    expected_size: int,
    timeout: int,
    endpoints: list[str],
    open_request,
    on_bytes,
    task_id: str | None = None,
) -> str:
    """Fetch one repo file into ``dest``; returns the endpoint that served it.

    ``open_request(request, timeout)`` must return an HTTPResponse-like object.
    Partial bytes accumulate in ``<dest>.part``; every retry resumes from the
    bytes already on disk via ``Range`` when the server supports it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    quote = urllib.parse.quote
    last_error: BaseException | None = None
    for attempt in range(1, _MAX_FILE_ATTEMPTS + 1):
        endpoint = endpoints[(attempt - 1) % len(endpoints)]
        url = f"{endpoint}/{repo}/resolve/main/{quote(rel_path)}"
        done = part.stat().st_size if part.is_file() else 0
        headers = {"User-Agent": _USER_AGENT}
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with open_request(request, timeout) as response:
                status = getattr(response, "status", 200) or 200
                resumed = done and status == 206
                if done and not resumed:
                    # Server ignored the Range header; start this file over.
                    done = 0
                with part.open("ab" if resumed else "wb") as file:
                    while True:
                        chunk = response.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        file.write(chunk)
                        on_bytes(len(chunk))
            size = part.stat().st_size
            if expected_size and size > expected_size:
                # Oversized means corrupted state; only then discard progress.
                part.unlink(missing_ok=True)
                raise _TransientTransferError(
                    f"corrupt partial file ({size} > {expected_size}); restarted")
            if expected_size and size < expected_size:
                # Truncated stream — keep .part so the next attempt resumes.
                raise _TransientTransferError(
                    f"truncated ({size}/{expected_size} bytes); will resume")
            part.replace(dest)
            return endpoint
        except Exception as exc:  # noqa: BLE001 - classified below
            if not _is_transient(exc):
                raise
            last_error = exc
            if attempt < _MAX_FILE_ATTEMPTS:
                delay = min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
                next_endpoint = endpoints[attempt % len(endpoints)]
                _emit_download_log(
                    task_id, "warning",
                    f"[retry {attempt}/{_MAX_FILE_ATTEMPTS - 1}] {rel_path}: {exc}"
                    f" — retrying via {next_endpoint} in {delay:.0f}s",
                )
                time.sleep(delay)
    raise RuntimeError(
        f"Failed to download {rel_path} after {_MAX_FILE_ATTEMPTS} attempts: {last_error}")


def cmd_lyrics_model_download(payload: dict[str, Any]) -> int:
    model_key = str(payload.get("model") or "").strip()
    entry = find_lyrics_model(model_key)
    if entry is None:
        return emit_error("LYRICS_MODEL_NOT_FOUND", f"Unknown lyrics model: {model_key or 'missing'}")

    task_id = payload.get("taskId") or f"lyrics_model_{entry.key}"
    model_dir = payload.get("modelDir") or None
    endpoints = candidate_endpoints(str(payload.get("endpoint") or "") or None)
    force = bool(payload.get("force", False))
    timeout = _safe_int(payload.get("timeout"), 60)

    opener, proxy_url, force_direct = _proxy_settings(task_id)

    def open_request(request: urllib.request.Request, timeout_value: int):
        return opener.open(request, timeout=timeout_value)

    root = lyrics_models_root(model_dir)
    target = root / "asr" / entry.cache_name  # 分类布局：语音识别模型归 asr/

    try:
        emit("download_started", {
            "model": entry.key,
            "name": entry.repo,
            "source": "huggingface",
            "endpoint": endpoints[0],
            "mirrorCandidates": endpoints,
            "proxy": proxy_url or "",
            "force": force,
            "totalFiles": 0,
            "completedFiles": 0,
            "progress": 0,
        }, task_id=task_id)
        emit("download_stage", {"stage": "resolving_files", "progress": 0}, task_id=task_id)

        listing, active_endpoint = list_repo_files_with_failover(
            entry.repo, endpoints, timeout, task_id)
        # Put the endpoint that just worked in front for the file transfers.
        endpoints = [active_endpoint] + [item for item in endpoints if item != active_endpoint]
        _emit_download_log(task_id, "info", f"Mirror in use: {active_endpoint}")
        present = {item["path"] for item in listing}
        kept: list[dict[str, Any]] = []
        for item in listing:
            name = item["path"]
            if name in _SKIP_FRAMEWORK_FILES:
                _emit_download_log(task_id, "info", f"[skip-file] {name} (framework weight copy)")
                continue
            if name == "pytorch_model.bin" and "model.safetensors" in present:
                _emit_download_log(task_id, "info", f"[skip-file] {name} (prefer model.safetensors)")
                continue
            kept.append(item)
        listing = kept
        if not listing:
            raise RuntimeError(f"Empty file listing after filtering for {entry.repo}")
        total_files = len(listing)
        total_bytes = sum(item["size"] for item in listing)

        pending: list[dict[str, Any]] = []
        skipped: list[str] = []
        for item in listing:
            dest = target / item["path"]
            if not force and dest.is_file() and (not item["size"] or dest.stat().st_size == item["size"]):
                skipped.append(item["path"])
                _emit_download_log(task_id, "info", f"[skip] {item['path']}")
                continue
            pending.append(item)
            _emit_download_log(
                task_id,
                "info",
                f"[{len(pending)}/{total_files}] {item['path']}"
                + (f", {item['size'] / 1048576:.1f} MB" if item["size"] else ""),
            )

        already_bytes = sum(item["size"] for item in listing if item["path"] in set(skipped))
        downloaded_bytes = 0
        completed_files = len(skipped)
        window_started = time.monotonic()
        window_start_bytes = already_bytes
        speed = 0.0

        emit("download_stage", {
            "stage": "downloading_files",
            "progress": 0,
            "message": "Downloading model files",
        }, task_id=task_id)

        last_progress = -1
        last_emitted_at = 0.0

        def emit_progress(*, force: bool = False) -> None:
            nonlocal speed, window_started, window_start_bytes
            nonlocal last_progress, last_emitted_at
            now = time.monotonic()
            elapsed = now - window_started
            if elapsed >= 0.8:
                current = already_bytes + downloaded_bytes
                speed = max(0.0, (current - window_start_bytes) / elapsed)
                window_started = now
                window_start_bytes = current
            aggregate = already_bytes + downloaded_bytes
            if total_bytes > 0:
                progress = min(95, int(aggregate / total_bytes * 95))
            else:
                progress = min(95, int((completed_files / total_files) * 95))
            # Bar and file transitions are immediate; metric-only changes are
            # sampled at most once a second (mirrors worker_download).
            if not force and progress == last_progress and now - last_emitted_at < 1.0:
                return
            last_progress = progress
            last_emitted_at = now
            emit("download_progress", {
                "model": entry.key,
                "completedFiles": completed_files,
                "totalFiles": total_files,
                "aggregateDownloadedBytes": aggregate,
                "aggregateTotalBytes": total_bytes,
                "speedBytesPerSecond": speed,
                "progress": progress,
            }, task_id=task_id)

        def on_bytes(delta: int) -> None:
            nonlocal downloaded_bytes
            downloaded_bytes += delta
            emit_progress()

        downloaded: list[str] = []
        for item in pending:
            rel = item["path"]
            dest = target / rel
            emit("download_file", {
                "model": entry.key,
                "path": rel,
                "completedFiles": completed_files,
                "totalFiles": total_files,
            }, task_id=task_id)
            served_by = _download_one(
                entry.repo, rel, dest, item["size"], timeout,
                endpoints, open_request, on_bytes, task_id,
            )
            if served_by != active_endpoint:
                _emit_download_log(task_id, "info", f"{rel}: served by mirror {served_by}")
            downloaded.append(rel)
            completed_files += 1
            emit_progress(force=True)

        emit("download_stage", {
            "stage": "verifying",
            "progress": 97,
            "message": "Verifying downloaded files",
        }, task_id=task_id)
        if not is_complete_lyrics_model_cache(target):
            missing = missing_files_for(target, entry)
            raise RuntimeError(
                f"Downloaded model is incomplete: {', '.join(missing) or 'unknown files missing'}"
            )
        emit_progress(force=True)
        emit("download_done", {
            "model": entry.key,
            "name": entry.repo,
            "downloaded": downloaded,
            "skipped": skipped,
            "modelDir": str(root),
            "path": str(target),
            "source": "huggingface",
            "endpoint": active_endpoint,
            "progress": 100,
        }, task_id=task_id)
        return 0
    except Exception as exc:
        return emit_error(
            "LYRICS_MODEL_DOWNLOAD_FAILED",
            str(exc),
            traceback.format_exc(),
            task_id=task_id,
        )
