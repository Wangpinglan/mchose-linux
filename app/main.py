#!/usr/bin/env python3
"""MCHOSE 鼠标设置 — GTK4 + libadwaita 前端。

直接读写 /dev/hidraw*，不需要扩展、不需要守护进程。
udev 规则见仓库根目录的 70-mchose.rules。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from mchose_app.window import MchoseWindow  # noqa: E402

APP_ID = "io.github.wangpinglan.Mchose"
VERSION = "1.0.0"
GITHUB_URL = "https://github.com/Wangpinglan/mchose-linux"


class MchoseApplication(Adw.Application):
    __gtype_name__ = "MchoseApplication"

    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._window = None

    # ------------------------------------------------------------ 生命周期
    def do_startup(self):
        Adw.Application.do_startup(self)
        self._add_actions()

    def do_activate(self):
        if self._window is None:
            self._window = MchoseWindow(self)
            self._window.connect("destroy", self._on_window_destroy)
        self._window.present()

    def _on_window_destroy(self, *_args):
        self._window = None

    # ------------------------------------------------------------ 动作
    def _add_actions(self):
        for name, handler in (("about", self._on_about),
                              ("homepage", self._on_homepage),
                              ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def _on_about(self, *_args):
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display)
        icon = APP_ID if theme.has_icon(APP_ID) else "input-mouse-symbolic"

        dialog = Adw.AboutDialog(
            application_name="MCHOSE 鼠标设置",
            application_icon=icon,
            version=VERSION,
            developer_name="Wangpinglan",
            comments="在 Linux 下配置 MCHOSE A5 / AX5 Pro 系列鼠标："
                     "DPI、回报率、静默高度、休眠、按键消抖等。\n"
                     "通过逆向原厂 Windows 驱动实现，不需要 Wine。",
            website=GITHUB_URL,
            issue_url=f"{GITHUB_URL}/issues",
            developers=["Wangpinglan"],
            license_type=Gtk.License.MIT_X11,
        )
        dialog.present(self._window)

    def _on_homepage(self, *_args):
        Gtk.show_uri(self._window, GITHUB_URL, Gdk.CURRENT_TIME)


def main():
    return MchoseApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
