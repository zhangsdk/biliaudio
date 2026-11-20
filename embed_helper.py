#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
歌词/元数据内嵌模块
把 LRC 歌词与歌曲信息写入音频文件内部,实现"单文件携带歌词":
- mp3 -> ID3 USLT(unsynced lyrics)标签,兼容性最好(手机/车机/iTunes 都认)
- m4a -> ©lyr 原子
同时写入标题/歌手/专辑等基础元数据,让本地音乐库正确归类。

依赖:mutagen(纯 Python,无需 ffmpeg)
"""

import os

try:
    from mutagen.id3 import ID3, USLT, TIT2, TPE1, TALB, APIC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen import File as MutagenFile
except ImportError:
    ID3 = None  # 由调用方提示安装


def _strip_lrc_metadata(lrc: str) -> str:
    """去掉 LRC 头部的 [ti:][ar:][al:][by:][offset:] 等元信息行,保留时间轴歌词。"""
    lines = []
    for line in lrc.splitlines():
        if line.strip().startswith("[ti:") or line.strip().startswith("[ar:") \
           or line.strip().startswith("[al:") or line.strip().startswith("[by:") \
           or line.strip().startswith("[offset:") or line.strip().startswith("[length:"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def embed_into_mp3(path: str, lrc: str, title: str = "", artist: str = "",
                   album: str = "", cover_path: str | None = None) -> bool:
    """把歌词和元数据写入 mp3 的 ID3 标签。用 ID3() 直接操作标签,不依赖音频帧解析。"""
    try:
        tags = ID3(path)
    except Exception:
        tags = ID3()  # 文件还没有 ID3 标签时新建
    try:
        # 歌词:USLT,lang=chi,desc 留空便于播放器识别为默认歌词
        if lrc:
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="chi", desc="", text=_strip_lrc_metadata(lrc)))
        if title:
            tags.setall("TIT2", [TIT2(encoding=3, text=title)])
        if artist:
            tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
        if album:
            tags.setall("TALB", [TALB(encoding=3, text=album)])
        if cover_path and os.path.exists(cover_path):
            with open(cover_path, "rb") as f:
                data = f.read()
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data))
        tags.save(path)
        return True
    except Exception as e:
        print(f"  [内嵌] mp3 写入失败: {e}")
        return False


def embed_into_m4a(path: str, lrc: str, title: str = "", artist: str = "",
                   album: str = "", cover_path: str | None = None) -> bool:
    """把歌词和元数据写入 m4a 的 MP4 标签。"""
    try:
        audio = MP4(path)
        tags = audio.tags
        if tags is None:
            print("  [内嵌] m4a 无标签,可能不是标准 MP4 容器")
            return False
        if lrc:
            tags["\xa9lyr"] = [_strip_lrc_metadata(lrc)]
        if title:
            tags["\xa9nam"] = [title]
        if artist:
            tags["\xa9ART"] = [artist]
        if album:
            tags["\xa9alb"] = [album]
        if cover_path and os.path.exists(cover_path):
            with open(cover_path, "rb") as f:
                data = f.read()
            tags["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        return True
    except Exception as e:
        print(f"  [内嵌] m4a 写入失败: {e}")
        return False


def embed_metadata(path: str, lrc: str, title: str = "", artist: str = "",
                   album: str = "", cover_path: str | None = None) -> bool:
    """按文件扩展名自动选择内嵌方式。"""
    if ID3 is None:
        print("  [内嵌] 缺少 mutagen,请运行: python -m pip install mutagen")
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        return embed_into_mp3(path, lrc, title, artist, album, cover_path)
    elif ext in (".m4a", ".mp4", ".aac"):
        return embed_into_m4a(path, lrc, title, artist, album, cover_path)
    else:
        # 尝试 mutagen 通用识别
        print(f"  [内嵌] 未识别的扩展名 {ext},跳过内嵌")
        return False


def verify_embed(path: str) -> dict:
    """读取音频标签,验证歌词是否写入成功(用于调试/自检)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        try:
            id3 = ID3(path)
            uslt = id3.get("USLT::chi") or id3.get("USLT:")
            return {"ok": bool(uslt), "lyric_len": len(str(uslt)) if uslt else 0}
        except Exception as e:
            return {"ok": False, "reason": f"ID3 读取失败: {e}"}
    try:
        f = MutagenFile(path)
        if f is None or f.tags is None:
            return {"ok": False, "reason": "无标签"}
        lyr = f.tags.get("\xa9lyr")
        return {"ok": bool(lyr), "lyric_len": len(str(lyr[0])) if lyr else 0}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
