#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B 站音频提取 - Android App (Kivy)
输入 BV 号,下载音频并把歌词内嵌进文件,保存到手机。

输出位置:
- 优先写到公共「音乐」目录(需存储权限,Android 9 及以下)
- Android 10+ 用 scoped storage,退回到 App 私有目录 music/ 下,
  并在界面显示实际路径(文件管理器可找到)
"""

import os
import threading
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

import mobile_core

IS_ANDROID = platform == "android"


def resolve_output_dir():
    """确定音频输出目录,并尽量使用公共音乐目录。"""
    app = App.get_running_app()
    private = os.path.join(app.user_data_dir, "music")
    os.makedirs(private, exist_ok=True)

    if not IS_ANDROID:
        return private, "调试模式(私有目录)"

    # Android:尝试请求存储权限并写到公共 Music 目录
    try:
        from android.permissions import request_permissions, Permission, check_permission
        perms = [Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE]
        request_permissions(perms)
        # 权限是异步授予的,这里先按已有权限判断;若未授予则回落私有目录
        if check_permission(Permission.WRITE_EXTERNAL_STORAGE):
            try:
                from android.storage import primary_external_storage_path
                pub = os.path.join(primary_external_storage_path(), "Music", "BiliAudio")
                os.makedirs(pub, exist_ok=True)
                return pub, "公共音乐目录 Music/BiliAudio"
            except Exception:
                pass
    except Exception:
        pass

    # Android 10+  scoped storage:公共目录不可直接写,退回私有目录
    return private, "应用私有目录 music/(文件管理器 → Android/data/包名/files/music)"


class LogView(ScrollView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.label = TextInput(
            readonly=True,
            font_name="ChineseFont",
            font_size="13sp",
            multiline=True,
            background_color=(0.12, 0.12, 0.14, 1),
            foreground_color=(0.85, 0.85, 0.88, 1),
            size_hint_y=None,
        )
        self.label.bind(texture_size=self._update_height)
        self.add_widget(self.label)
        self._lines = []

    def _update_height(self, instance, size):
        self.label.height = max(size[1], self.height)

    def add(self, text: str):
        self._lines.append(str(text))
        if len(self._lines) > 300:
            self._lines = self._lines[-300:]
        self.label.text = "\n".join(self._lines)
        # 滚到底部
        self.scroll_y = 0

    def clear(self):
        self._lines = []
        self.label.text = ""


class RootWidget(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=dp(10), spacing=dp(8), **kwargs)

        title = Label(
            text="B 站音频提取 · 带歌词",
            font_name="ChineseFont", font_size="20sp", size_hint_y=None, height=dp(38),
        )
        self.add_widget(title)

        self.path_info = Label(
            text="输出目录检测中...",
            font_name="ChineseFont", font_size="11sp", size_hint_y=None, height=dp(30),
            halign="left",
        )
        self.path_info.bind(size=self.path_info.setter("text_size"))
        self.add_widget(self.path_info)

        hint = Label(
            text="输入 BV 号或视频链接,多个用空格/换行分隔",
            font_name="ChineseFont", font_size="12sp", size_hint_y=None, height=dp(24),
        )
        self.add_widget(hint)

        self.input = TextInput(
            font_name="ChineseFont", font_size="14sp", multiline=True,
            size_hint_y=None, height=dp(90), hint_text="例如: BV1GJ411x7hD",
        )
        self.add_widget(self.input)

        # 选项行 1:音质
        row1 = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        row1.add_widget(Label(text="音质", font_name="ChineseFont", font_size="13sp", size_hint_x=None, width=dp(40)))
        self.quality = Spinner(
            text="高音质", values=("高音质", "标准", "省空间"),
            font_name="ChineseFont", font_size="13sp", size_hint_x=None, width=dp(110),
        )
        row1.add_widget(self.quality)
        row1.add_widget(Label(text="歌词", font_name="ChineseFont", font_size="13sp", size_hint_x=None, width=dp(40)))
        self.lyric_chk = CheckBox(active=True, size_hint_x=None, width=dp(40))
        row1.add_widget(self.lyric_chk)
        row1.add_widget(Label(text="下载并内嵌", font_name="ChineseFont", font_size="12sp"))
        self.add_widget(row1)

        # 选项行 2:歌词搜索词(可选)
        row2 = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        row2.add_widget(Label(text="歌词搜索词", font_name="ChineseFont", font_size="12sp", size_hint_x=None, width=dp(90)))
        self.keyword = TextInput(
            font_name="ChineseFont", font_size="13sp", multiline=False,
            hint_text="留空=自动识别,可填『歌名 歌手』",
        )
        row2.add_widget(self.keyword)
        self.add_widget(row2)

        # 操作按钮
        row3 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.start_btn = Button(text="开始下载", font_name="ChineseFont", font_size="15sp")
        self.start_btn.bind(on_press=self.on_start)
        self.clear_btn = Button(text="清空日志", font_name="ChineseFont", font_size="14sp", size_hint_x=None, width=dp(100))
        self.clear_btn.bind(on_press=lambda *_: self.log.clear())
        row3.add_widget(self.start_btn)
        row3.add_widget(self.clear_btn)
        self.add_widget(row3)

        self.log = LogView(size_hint_y=1)
        self.add_widget(self.log)

        self._cancel = threading.Event()
        self._running = False

    def set_path_info(self, path, desc):
        self.path_info.text = f"保存到: {desc}"

    def log_threadsafe(self, text):
        Clock.schedule_once(lambda dt: self.log.add(text), 0)

    def on_start(self, *_):
        if self._running:
            self._cancel.set()
            self.start_btn.text = "正在取消..."
            return
        text = self.input.text.strip()
        if not text:
            self.log.add("请先输入 BV 号或链接")
            return

        self._cancel.clear()
        self._running = True
        self.start_btn.text = "取消"
        self.log.clear()

        qmap = {"高音质": "high", "标准": "normal", "省空间": "low"}
        quality = qmap.get(self.quality.text, "high")
        want_lyric = self.lyric_chk.active
        keyword = self.keyword.text.strip()

        t = threading.Thread(
            target=self._worker,
            args=(text, quality, want_lyric, keyword),
            daemon=True,
        )
        t.start()

    def _worker(self, text, quality, want_lyric, keyword):
        try:
            def prog(i, n):
                Clock.schedule_once(lambda dt: setattr(self.start_btn, "text", f"处理中 {i}/{n} (点此取消)"), 0)

            results = mobile_core.download_batch(
                text, quality, want_lyric, keyword,
                log_cb=self.log_threadsafe,
                cancel_event=self._cancel,
                progress_cb=prog,
            )
            ok = sum(1 for r in results if r["ok"])
            Clock.schedule_once(
                lambda dt: self.log.add(f"\n全部结束: 成功 {ok}/{len(results)}"), 0
            )
        except Exception:
            Clock.schedule_once(
                lambda dt: self.log.add("异常: " + traceback.format_exc()[-300:]), 0
            )
        finally:
            Clock.schedule_once(lambda dt: self._finish(), 0)

    def _finish(self):
        self._running = False
        self.start_btn.text = "开始下载"


class BiliAudioApp(App):
    def build(self):
        # 注册中文字体(Buildozer 会把 NotoSansSC.ttf 打进资源)
        try:
            from kivy.core.text import LabelBase
            LabelBase.register(name="ChineseFont", fn_asset="NotoSansSC.ttf")
        except Exception:
            pass

        root = RootWidget()
        path, desc = resolve_output_dir()
        mobile_core.set_output_dir(path)
        root.set_path_info(path, desc)
        return root


if __name__ == "__main__":
    BiliAudioApp().run()
