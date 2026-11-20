#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
歌词获取模块
1) 从 B 站视频标题智能清洗出歌名/歌手
2) 通过网易云 / QQ 音乐双源搜索,获取带时间戳的 LRC 歌词(互为兜底)
3) 写出与音频同名的 .lrc 文件(本地播放器自动识别同步显示)
歌词源接口仅用标准库,无第三方依赖。
"""

import json
import os
import re
import ssl
import urllib.parse
import urllib.request

# Android 环境证书链常缺失,创建一个不校验证书的上下文(仅移动端副本使用)
try:
    _SSL_CTX = ssl._create_unverified_context()
except Exception:
    _SSL_CTX = None

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}
UA_QQ = {
    "User-Agent": UA["User-Agent"],
    "Referer": "https://y.qq.com/",
}

# 标题里常见的噪音词(与音乐本身无关)
NOISE_WORDS = [
    "官方", "Official", "official", "OFFICIAL", "MV", "mv", "Mv",
    "4K", "8K", "1080P", "720P", "60FPS", "60fps", "蓝光", "超清",
    "高清", "修复", "完整版", "字幕版", "中字", "双语", "自制字幕",
    "Music Video", "MUSIC VIDEO", "音源", "纯音频", "音频", "现场版",
    "LIVE", "Live", "live", "演唱会", "字幕", "转载", "搬运",
    "独家", "全网首发", "首播", "HD", "hd", "Full", "FULL",
    "官方版", "正式版", "录音室版", "版",
    "歌词纯享", "纯享版", "歌词版", "听力测试", "耳机聆听",
    "百万豪装录音棚", "录音棚大声听", "大声听", "豪装录音棚",
    "无损音质", "无损", "Hi-Res", "Hires", "hi-res", "𝐇𝐢-𝐑𝐞𝐬",
    "戴上耳机", "循环播放", "单曲循环", "每日推荐",
]

# 括号内容里若含这些词,整段删除
BRACKET_NOISE = re.compile(
    r"[（(\[【][^）)\]】]{0,40}(?:official|mv|4k|8k|1080p|720p|60fps|高清|超清|"
    r"完整版|字幕|修复|音源|音频|live|现场|蓝光|官方)[^）)\]】]{0,40}[）)\]】]",
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    """从视频标题中提取用于搜索的关键词。"""
    s = title or ""
    # 删除含噪音的括号段
    s = BRACKET_NOISE.sub(" ", s)
    # 删除所有【】[] 方括号标签(通常是画质/分类标签)
    s = re.sub(r"[【\[][^】\]]{0,30}[】\]]", " ", s)
    # 逐词删除噪音
    for w in NOISE_WORDS:
        s = s.replace(w, " ")
    # 分隔符统一为空格
    s = re.sub(r"[|｜—_：:/\\·・\-–]+", " ", s)
    # 去掉 emoji 及非常规符号,保留中日韩文字/字母/数字/常见标点
    s = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\s'&.,!?~-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _http_get_json(url: str, timeout: int = 15, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or UA)
    kwargs = {"timeout": timeout}
    if _SSL_CTX is not None:
        kwargs["context"] = _SSL_CTX
    with urllib.request.urlopen(req, **kwargs) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def search_netease(keyword: str, limit: int = 6):
    """搜索网易云歌曲,返回 [(id, name, artist), ...]。"""
    q = urllib.parse.quote(keyword)
    url = (
        f"https://music.163.com/api/search/get/web?s={q}"
        f"&type=1&offset=0&total=false&limit={limit}"
    )
    try:
        data = _http_get_json(url)
    except Exception:
        return []
    songs = (data.get("result") or {}).get("songs") or []
    out = []
    for s in songs:
        sid = s.get("id")
        name = s.get("name", "")
        artists = s.get("artists") or s.get("ar") or []
        artist = artists[0].get("name", "") if artists else ""
        out.append((sid, name, artist))
    return out


def get_lrc(song_id) -> str:
    """按歌曲 ID 获取 LRC 歌词文本。"""
    url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"
    try:
        data = _http_get_json(url)
    except Exception:
        return ""
    lrc = ((data.get("lrc") or {}).get("lyric") or "").strip()
    return lrc


def search_qq(keyword: str, limit: int = 6):
    """QQ 音乐搜索,返回 [(mid, name, artist), ...]。"""
    q = urllib.parse.quote(keyword)
    url = (
        f"https://c.y.qq.com/soso/fcgi-bin/client_search_cp?p=1&n={limit}"
        f"&w={q}&format=json&new_json=1"
    )
    try:
        data = _http_get_json(url, headers=UA_QQ)
    except Exception:
        return []
    songs = (data.get("data") or {}).get("song", {}).get("list") or []
    out = []
    for s in songs:
        mid = s.get("mid")
        name = s.get("name") or s.get("title") or ""
        singers = s.get("singer") or []
        artist = singers[0].get("name", "") if singers else ""
        out.append((mid, name, artist))
    return out


def get_lrc_qq(mid: str) -> str:
    """按 QQ 音乐 mid 获取 LRC 歌词。"""
    if not mid:
        return ""
    url = (
        f"https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
        f"?songmid={mid}&format=json&nobase64=1"
    )
    try:
        data = _http_get_json(url, headers=UA_QQ)
    except Exception:
        return ""
    return (data.get("lyric") or "").strip()


def has_timestamps(lrc: str) -> bool:
    """判断歌词是否带时间戳(有效 LRC)。"""
    return bool(re.search(r"\[\d{1,2}:\d{2}(?:\.\d+)?\]", lrc))


def _try_source(results, fetch_fn, source_name: str, verbose: bool):
    """遍历某源的搜索结果,返回第一个带时间轴的歌词。"""
    for sid, name, artist in results:
        lrc = fetch_fn(sid)
        if has_timestamps(lrc):
            if verbose:
                print(f"  [歌词] {source_name}命中: {name} - {artist} (共 {len(lrc.splitlines())} 行)")
            return lrc, name, artist
    return "", "", ""


def fetch_lyric(keyword: str, verbose: bool = True):
    """
    根据关键词搜索并抓取歌词(网易云优先,失败自动切 QQ 音乐兜底)。
    返回 (lrc文本, 歌曲名, 歌手);失败返回 ("", "", "")。
    """
    if verbose:
        print(f"  [歌词] 搜索关键词: {keyword}")

    # 源 1:网易云
    netease = search_netease(keyword)
    if netease:
        if verbose:
            for idx, (_, name, artist) in enumerate(netease[:5], 1):
                print(f"         [网易云] {idx}. {name} - {artist}")
        hit = _try_source(netease, get_lrc, "网易云", verbose)
        if hit[0]:
            return hit
    elif verbose:
        print("  [歌词] 网易云未返回结果,尝试备用源...")

    # 源 2:QQ 音乐兜底
    qq = search_qq(keyword)
    if qq:
        if verbose:
            for idx, (_, name, artist) in enumerate(qq[:5], 1):
                print(f"         [QQ音乐] {idx}. {name} - {artist}")
        hit = _try_source(qq, get_lrc_qq, "QQ音乐", verbose)
        if hit[0]:
            return hit

    if verbose:
        print("  [歌词] 两个歌词源都没有找到可用的时间轴歌词")
    return "", "", ""


def write_lrc(audio_path: str, lrc_text: str) -> str:
    """写出与音频同名(去掉扩展名)的 .lrc 文件。"""
    base = audio_path.rsplit(".", 1)[0] if "." in os.path.basename(audio_path) else audio_path
    lrc_path = base + ".lrc"
    with open(lrc_path, "w", encoding="utf-8") as f:
        f.write(lrc_text.strip() + "\n")
    return lrc_path
