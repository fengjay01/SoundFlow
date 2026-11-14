"""HTTPS trust bootstrap for the worker process.

Problem: some Python distributions (MSYS2/ucrt64 builds, stripped embedded
runtimes) ship without any usable CA bundle and without the
``ssl.enum_certificates`` Windows-store API. Every HTTPS request then fails
with::

    SSL: CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate

even when the OS itself trusts the endpoint (PowerShell / curl work fine).

This module assembles the widest set of root CAs discoverable on the host
into one :class:`ssl.SSLContext` and exposes :func:`urlopen`, a drop-in
replacement for ``urllib.request.urlopen`` that applies that context to
HTTPS requests. Trust sources are consulted in order:

1. the interpreter's own defaults (honours ``SSL_CERT_FILE`` /
   ``SSL_CERT_DIR``);
2. ``certifi`` when importable (including ``pip._vendor.certifi`` and the
   bundle vendored with ``requests``);
3. the Windows system store via ``ssl.enum_certificates`` when exposed;
4. well-known bundle paths (python.org Windows, MSYS2/MinGW, Git for
   Windows, Linux distros, macOS, Homebrew).

Loading is best-effort: unusable candidates are skipped and requests fail
with the same clear SSL error as before when nothing is available. The
module never disables certificate verification.
"""

from __future__ import annotations

import importlib
import os
import ssl
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional

__all__ = [
    "build_ssl_context",
    "candidate_ca_files",
    "iter_candidate_paths",
    "reset_cache",
    "urlopen",
]

_ENV_PATH_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

_BUNDLE_RELATIVE_PATHS = (
    "etc/pki/ca-bundle.crt",
    "etc/pki/tls/certs/ca-bundle.crt",
    "etc/ssl/certs/ca-certificates.crt",
    "etc/ssl/certs/ca-bundle.crt",
    "etc/ssl/cert.pem",
    "ssl/cert.pem",
)

_BUNDLE_ABSOLUTE_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/ca-bundle.crt",
    "/etc/ssl/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
    "/etc/openssl/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/share/certs/ca-root-nss.crt",
    r"C:\Program Files\Git\mingw64\etc\ssl\certs\ca-bundle.crt",
    r"C:\Program Files\Git\mingw64\ssl\cert.pem",
    r"C:\Program Files (x86)\Git\mingw64\etc\ssl\certs\ca-bundle.crt",
)

_CERTIFI_SOURCES = (
    ("certifi", "where"),
    ("pip._vendor.certifi", "where"),
    ("requests.certs", "where"),
    ("requests.utils", "DEFAULT_CA_BUNDLE_PATH"),
)

_lock = threading.Lock()
_context: Optional[ssl.SSLContext] = None


def _certifi_candidates() -> List[Path]:
    out: List[Path] = []
    for mod_name, attr in _CERTIFI_SOURCES:
        try:
            mod = importlib.import_module(mod_name)
            val = getattr(mod, attr)
            out.append(Path(val() if callable(val) else val))
        except Exception:
            continue
    return out


def _install_roots() -> List[Path]:
    """Directory roots where a CA bundle may live, e.g. the MSYS2 root."""
    roots: List[Path] = []

    def add(raw: object) -> None:
        try:
            path = Path(str(raw))
        except Exception:
            return
        if str(path) and path not in roots:
            roots.append(path)

    for raw in (os.environ.get("MINGW_PREFIX"), sys.prefix, getattr(sys, "base_prefix", None)):
        if raw:
            add(raw)
    for prefix in list(roots):
        add(prefix.parent)
    try:
        exe = Path(sys.executable).resolve()
    except Exception:
        exe = Path(sys.executable)
    add(exe.parent)               # <root>/python.exe (python.org Windows)
    add(exe.parent.parent)        # <root>/<pkg>/bin/python.exe -> <root>/<pkg>
    add(exe.parent.parent.parent)  # -> install root (MSYS2, Git for Windows)
    return roots


def iter_candidate_paths() -> Iterable[Path]:
    """All CA bundle paths worth probing, existing or not (test seam)."""
    for var in _ENV_PATH_VARS:
        raw = os.environ.get(var)
        if raw:
            yield Path(raw)
    yield from _certifi_candidates()
    for root in _install_roots():
        for rel in _BUNDLE_RELATIVE_PATHS:
            yield root / rel
    for raw in _BUNDLE_ABSOLUTE_PATHS:
        yield Path(raw)


def candidate_ca_files() -> List[Path]:
    seen = set()
    out: List[Path] = []
    for path in iter_candidate_paths():
        try:
            if not path.is_file():
                continue
            key = str(path)
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _ca_count(ctx: ssl.SSLContext) -> int:
    try:
        return int(ctx.cert_store_stats().get("x509_ca", 0))
    except Exception:
        return -1  # unknown: keep loading candidates


def _load_windows_store(ctx: ssl.SSLContext) -> int:
    enum_certificates = getattr(ssl, "enum_certificates", None)
    if enum_certificates is None:
        return 0
    loaded = 0
    for store in ("ROOT", "CA"):
        try:
            entries = enum_certificates(store)
        except Exception:
            continue
        for entry in entries:
            der = entry.get("cert") if isinstance(entry, dict) else None
            if not der:
                continue
            try:
                ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
            except Exception:
                continue
            loaded += 1
    return loaded


def _fresh_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if _ca_count(ctx) > 0:
        return ctx
    for ca_file in candidate_ca_files():
        try:
            ctx.load_verify_locations(cafile=str(ca_file))
        except Exception:
            continue
        if _ca_count(ctx) > 0:
            return ctx
    _load_windows_store(ctx)
    return ctx


def build_ssl_context() -> ssl.SSLContext:
    """Return (and cache) an SSLContext with the widest trust available."""
    global _context
    ctx = _context
    if ctx is not None:
        return ctx
    with _lock:
        if _context is None:
            _context = _fresh_context()
        return _context


def reset_cache() -> None:
    """Drop the cached context (used by tests)."""
    global _context
    with _lock:
        _context = None


def urlopen(url, data=None, timeout=object()):
    """Drop-in for ``urllib.request.urlopen`` that carries host CA trust."""
    import socket as _socket

    if timeout is object():
        timeout = _socket._GLOBAL_DEFAULT_TIMEOUT  # type: ignore[attr-defined]
    return urllib.request.urlopen(
        url, data=data, timeout=timeout, context=build_ssl_context()
    )
