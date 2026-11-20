#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
移动端下载核心(Android)
复用 lyric_helper / embed_helper,针对手机环境做了适配:
- 输出到 App 私有可写目录(Android 无需存储权限)
- 不依赖 ffmpeg,直接 m4a + 内嵌歌词
- 通过回调把进度/日志抛给 UI 线程
"""

import os
import re
import threading

import yt_dlp

import lyric_helper
import embed_helper

# Android 上由 main.py 传入 App 的可写目录;桌面调试时回退到当前目录
OUTPUT_DIR = None


def set_output_dir(path: str):
    global OUTPUT_DIR
    OUTPUT_DIR = path
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def normalize_to_url(raw: str):
    raw = (raw or "").strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    if m:
        return f"https://www.bilibili.com/video/{m.group(1)}"
    m = re.search(r"(?:av|AV)(\d+)", raw)
    if m:
        return f"https://www.bilibili.com/video/av{m.group(1)}"
    if raw.startswith("http"):
        return raw
    return None


class _ProgressLogger:
    """把 yt-dlp 的日志转成 UI 回调。"""

    def __init__(self, log_cb):
        self.log_cb = log_cb or (lambda *_: None)
        self._last_pct = -1

    def debug(self, msg):
        # 过滤掉 yt-dlp 的进度噪音行,保留关键状态
        if msg.startswith("[download]") and "%" in msg:
            m = re.search(r"(\d+(?:\.\d+)?)%", msg)
            if m:
                pct = int(float(m.group(1)))
                if pct // 10 > self._last_pct // 10:
                    self._last_pct = pct
                    self.log_cb(f"  下载进度 {pct}%")
            return
        if any(k in msg for k in ("Downloading", "Extracting", "format", "Destination")):
            self.log_cb(msg if len(msg) < 100 else msg[:100] + "...")

    def info(self, msg):
        if len(msg) < 120:
            self.log_cb(msg)

    def warning(self, msg):
        self.log_cb(f"[警告] {msg[:120]}")

    def error(self, msg):
        self.log_cb(f"[错误] {msg[:120]}")


def download_one(url: str, quality: str = "high", want_lyric: bool = True,
                 lyric_keyword: str = "", log_cb=None, cancel_event=None) -> dict:
    """下载单个视频(音频+内嵌歌词)。返回 {ok, title, audio_path, lrc_path, msg}。"""
    result = {"ok": False, "title": "", "audio_path": "", "lrc_path": "", "msg": ""}
    log = log_cb or (lambda *_: None)

    # 标准化 URL(兼容直接传 BV 号的情况)
    normalized = normalize_to_url(url)
    if normalized:
        url = normalized

    out_dir = OUTPUT_DIR or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    fmt_map = {
        "high": "bestaudio[abr>=180]/bestaudio/best",
        "normal": "bestaudio[abr>=120]/bestaudio/best",
        "low": "bestaudio/best",
    }
    fmt = fmt_map.get(quality, fmt_map["high"])

    ydl_opts = {
        "format": fmt,
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "noplaylist": True,
        "concurrent_fragment_downloads": 4,
        "retries": 3,
        "socket_timeout": 20,
        "quiet": True,          # 用 logger 接管输出
        "no_warnings": False,
        "nocheckcertificate": True,   # Android 上证书链常缺失,跳过校验避免误报
        "logger": _ProgressLogger(log),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        },
    }

    def _hook(d):
        if cancel_event is not None and cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("已取消")

    ydl_opts["progress_hooks"] = [_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = (info.get("title") or "") if info else ""
            result["title"] = title
            log(f"完成: {title}")
    except Exception as e:
        msg = str(e)
        result["msg"] = msg
        if "取消" in msg:
            log("已取消下载")
        else:
            log(f"下载失败: {msg[:150]}")
            if any(k in msg.lower() for k in ("login", "会员", "cookie", "403", "付费", "版权")):
                log("提示: 该内容可能需要登录/大会员,手机端暂不支持 cookie")
        return result

    # 定位生成的文件
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as probe:
            audio_path = probe.prepare_filename(info)
    except Exception:
        audio_path = None
    if not audio_path or not os.path.exists(audio_path):
        # 兜底:按标题前缀在目录里找
        candidates = [f for f in os.listdir(out_dir)
                      if title and f.startswith(title[:15]) and f.lower().endswith(".m4a")]
        audio_path = os.path.join(out_dir, max(candidates, key=lambda f: os.path.getmtime(os.path.join(out_dir, f)))) if candidates else None
    if not audio_path:
        result["msg"] = "未找到输出文件"
        return result
    result["audio_path"] = audio_path

    if want_lyric:
        keyword = lyric_keyword.strip() or lyric_helper.clean_title(title)
        if keyword:
            log(f"搜索歌词: {keyword}")
            lrc, song, artist = lyric_helper.fetch_lyric(keyword, verbose=False)
            if lrc:
                lrc_path = lyric_helper.write_lrc(audio_path, lrc)
                result["lrc_path"] = lrc_path
                ok = embed_helper.embed_metadata(
                    audio_path, lrc,
                    title=re.sub(r"\s+", " ", title).strip()[:80] if title else song,
                    artist=artist,
                )
                log(f"歌词已内嵌 ({song} - {artist})" if ok else "歌词内嵌失败")
            else:
                log("未找到歌词,音频已保存")
        else:
            log("无法识别歌名,跳过歌词")

    result["ok"] = True
    return result


def download_batch(text: str, quality: str, want_lyric: bool, lyric_keyword: str,
                   log_cb=None, cancel_event=None, progress_cb=None) -> list:
    """解析输入(支持多行/空格分隔),逐个下载。"""
    log = log_cb or (lambda *_: None)
    urls = []
    for token in re.split(r"[\s,;，；]+", text):
        u = normalize_to_url(token)
        if u:
            urls.append(u)
    if not urls:
        log("没有识别到有效的 BV 号或链接")
        return []
    log(f"共 {len(urls)} 个视频")
    results = []
    for i, u in enumerate(urls, 1):
        if cancel_event is not None and cancel_event.is_set():
            break
        log(f"--- [{i}/{len(urls)}] ---")
        if progress_cb:
            progress_cb(i, len(urls))
        results.append(download_one(u, quality, want_lyric, lyric_keyword, log, cancel_event))
    return results
