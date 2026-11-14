# -*- coding: utf-8 -*-
"""联网歌词搜索：网易云音乐公开接口（无需 API Key）。

- ``search(query)``：关键词 → 歌曲列表（id / 歌名 / 歌手 / 专辑 / 时长）
- ``fetch_lrc(song_id)``：歌曲 id → 歌词文本。优先抓取 **YRC 逐字歌词**
  （平台级字级时间轴，人工校对），转成增强 LRC（<start,dur> 逐字标签）；
  无逐字则回退普通 LRC。两者都可直接喂给导入对齐管线（MMS 行锚）。

接口无官方保证，失败抛 SearchError（调用方展示为可重试错误）。
所有请求走 worker 进程环境（代理设置已在 configure_process_proxy 统一处理）。

weapi 加密参数为网易云音乐前端公开常量（NeteaseCloudMusicApi / musicbox
等开源项目通用实现）：AES-CBC 两次加密 + RSA（secKey 倒序模幂）。
"""
from __future__ import annotations

import base64
import json
import random
import re
import string
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_BASE = "https://music.163.com"

# weapi 固定公开常量（前端 JS 内置，全社区通用）
_WEAPI_MODULUS = (
    '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7'
    'b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280'
    '104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932'
    '575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b'
    '3ece0462db0a22b8e7'
)
_WEAPI_NONCE = "0CoJUm6Qyw8W8jud"
_WEAPI_PUBKEY = "010001"
_WEAPI_IV = "0102030405060708"


class SearchError(RuntimeError):
    """搜索/拉取失败（网络或接口变更）。"""


def _get(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://music.163.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — 统一转为可展示错误
        raise SearchError(f"歌词服务请求失败: {exc}") from exc


def _post_form(url: str, form: dict, timeout: int = 12) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": _UA,
        "Referer": "https://music.163.com/",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — 统一转为可展示错误
        raise SearchError(f"歌词服务请求失败: {exc}") from exc


def search(query: str, limit: int = 10) -> list[dict]:
    """关键词搜索歌曲。返回 [{songId, name, artist, album, durationMs}]。

    主端点为 ``api/search/get/``（POST 表单）；GET 的 ``api/search/pc``
    在部分网络下会被风控（code -462），仅作兜底。
    """
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(limit, 20))
    data = _post_form(f"{_BASE}/api/search/get/",
                      {"s": query, "type": 1, "limit": limit, "offset": 0})
    songs = ((data.get("result") or {}).get("songs")) or []
    if not songs:
        # 兜底：GET 端点（可能触发风控，失败即抛 SearchError）
        url = (f"{_BASE}/api/search/pc?s={urllib.parse.quote(query)}"
               f"&type=1&limit={limit}")
        data = _get(url)
        songs = ((data.get("result") or {}).get("songs")) or []
    out: list[dict] = []
    for s in songs:
        artists = " / ".join(a.get("name", "") for a in (s.get("artists") or [])[:3])
        out.append({
            "songId": int(s.get("id") or 0),
            "name": str(s.get("name") or ""),
            "artist": artists,
            "album": str((s.get("album") or {}).get("name") or ""),
            "durationMs": int(s.get("duration") or 0),
        })
    return out


def fetch_lrc(song_id: int) -> dict:
    """拉取歌词。优先 YRC 逐字（转增强 LRC），无逐字回退普通 LRC。"""
    # 1) weapi lyric/v1：带 yv 声明拿 YRC 逐字歌词（平台级字级时间轴）
    try:
        data = _weapi_post("/api/song/lyric/v1", {
            "id": int(song_id), "cp": False, "tv": -1, "lv": -1,
            "rv": -1, "kv": -1, "yv": 1, "ytv": -1, "yrv": -1,
        })
        yrc = str((data.get("yrc") or {}).get("lyric") or "")
        if yrc.strip():
            lrc = yrc_to_lrc(yrc)
            if lrc.strip():
                return {
                    "lyricsText": lrc,
                    "format": "lrc",
                    "wordLevel": True,  # 含逐字时间戳（平台人工校对）
                    "translationIncluded": bool(str((data.get("tlyric") or {}).get("lyric") or "").strip()),
                }
        lrc = str((data.get("lrc") or {}).get("lyric") or "")
        if lrc.strip():
            return {
                "lyricsText": lrc,
                "format": "lrc",
                "translationIncluded": bool(str((data.get("tlyric") or {}).get("lyric") or "").strip()),
            }
    except SearchError:
        raise
    except Exception:  # noqa: BLE001 — weapi 失败回退公开 GET 端点
        pass
    # 2) 公开 GET 端点（旧行为）
    url = f"{_BASE}/api/song/lyric?id={int(song_id)}&lv=1&kv=1&tv=-1"
    data = _get(url)
    lrc = str(data.get("lrc", {}).get("lyric") or "")
    if not lrc.strip():
        raise SearchError("该歌曲没有可用的 LRC 歌词")
    return {
        "lyricsText": lrc,
        "format": "lrc",
        "translationIncluded": bool(str(data.get("tlyric", {}).get("lyric") or "").strip()),
    }


# ---- weapi 加密（网易云前端公开算法） ----

def _aes_cbc_b64(text: str, key: str) -> str:
    from Crypto.Cipher import AES

    pad = 16 - len(text.encode()) % 16
    data = (text + chr(pad) * pad).encode()
    cipher = AES.new(key.encode(), AES.MODE_CBC, _WEAPI_IV.encode())
    return base64.b64encode(cipher.encrypt(data)).decode()


def _rsa_no_pad(sec_key: str) -> str:
    """secKey 倒序 → hex 整数模幂 → 零填 256 位 hex（网易云特殊 RSA 用法）。"""
    text = "".join(reversed(sec_key))
    value = int(text.encode().hex(), 16) if text.encode().hex()[0:2] != "00" \
        else int(text.encode().hex(), 16)
    result = pow(value, int(_WEAPI_PUBKEY, 16), int(_WEAPI_MODULUS, 16))
    return f"{result:0256x}"


def _weapi_post(path: str, payload: dict, timeout: int = 12) -> dict:
    text = json.dumps(payload)
    sec_key = "".join(random.choice(string.ascii_letters + string.digits)
                      for _ in range(16))
    params = _aes_cbc_b64(_aes_cbc_b64(text, _WEAPI_NONCE), sec_key)
    form = urllib.parse.urlencode({
        "params": params,
        "encSecKey": _rsa_no_pad(sec_key),
    }).encode()
    # weapi 端点：/api/xxx → /weapi/xxx
    url = f"{_BASE}{path.replace('/api/', '/weapi/', 1)}"
    req = urllib.request.Request(url, data=form, headers={
        "User-Agent": _UA,
        "Referer": "https://music.163.com/",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": "os=pc; appver=2.9.7",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — 统一转为可展示错误
        raise SearchError(f"weapi 请求失败: {exc}") from exc


# ---- YRC → 增强 LRC ----

_YRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_YRC_WORD_RE = re.compile(r"\((\d+),(\d+),\d+\)([^()]*)")


def yrc_to_lrc(yrc: str) -> str:
    """网易云 YRC（明文逐字）→ 增强 LRC（[行起]<字起>字<字起>…）。

    YRC 行格式：[line_start,line_dur](w_start,w_dur,0)字(w_start,w_dur,0)字…
    时间均为毫秒。空行/无字行保留为普通空行。
    """
    out: list[str] = []
    for raw in yrc.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _YRC_LINE_RE.match(raw)
        if not m:
            out.append(raw)  # 非时间轴行（元数据）原样保留
            continue
        line_start, _dur, body = int(m.group(1)), int(m.group(2)), m.group(3)
        pieces: list[str] = []
        for wm in _YRC_WORD_RE.finditer(body):
            w_start = int(wm.group(1))
            text = wm.group(3)
            if not text:
                continue
            cs = w_start // 10  # 毫秒 → 厘秒
            pieces.append(f"<{cs // 100 // 60}:{(cs // 100) % 60:02d}.{cs % 100:02d}>{text}")
        if pieces:
            cs = line_start // 10
            out.append(f"[{cs // 100 // 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}]"
                       + "".join(pieces))
        else:
            cs = line_start // 10
            out.append(f"[{cs // 100 // 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}]")
    return "\n".join(out)
