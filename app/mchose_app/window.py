"""window — 主窗口（libadwaita）"""

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

from .backend import Controller, NoDevice  # noqa: E402
from .widgets import BatteryRing, StageDot  # noqa: E402

POLL_SECONDS = 20
DPI_STAGES = 6
DPI_MIN, DPI_MAX, DPI_STEP = 50, 32000, 25
SLEEP_MIN, SLEEP_MAX = 1, 30          # 设备支持 60..1800 秒
DEBOUNCE_MAX = 10

GITHUB_URL = "https://github.com/Wangpinglan/mchose-linux"


def hz_text(hz):
    if hz is None:
        return "—"
    if hz >= 1000:
        v = hz / 1000
        return f"{int(v)}K" if v == int(v) else f"{v:g}K"
    return str(hz)


def link_text(d):
    if d.get("link") == "wired":
        return "有线"
    hz = d.get("dongle_hz")
    return f"2.4G · {hz_text(hz)} 接收器" if hz else "2.4G"


def fmt_seconds(sec):
    if sec is None:
        return "—"
    m, s = divmod(int(sec), 60)
    return f"{m} 分 {s} 秒" if s else f"{m} 分钟"


class MchoseWindow(Adw.ApplicationWindow):
    __gtype_name__ = "MchoseWindow"

    def __init__(self, app):
        super().__init__(application=app, title="MCHOSE")
        self.set_default_size(520, 860)
        # Wayland 下窗口图标由 app-id 决定，X11 下靠这个
        self.set_icon_name("io.github.wangpinglan.Mchose")

        self._ctl = Controller()
        self._state = None
        self._last_ok = 0.0
        self._busy = False
        self._pending = None
        self._syncing = False
        self._apply_timer = None
        self._dpi_timers = {}
        self._typing_rows = set()

        self._build_ui()
        self.connect("close-request", self._on_close)
        self.connect("map", self._on_map)

        self._refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self._tick)

    # ------------------------------------------------------------ 构建界面
    def _build_ui(self):
        header = Adw.HeaderBar()
        self._title = Adw.WindowTitle(title="MCHOSE", subtitle="正在连接…")
        header.set_title_widget(self._title)

        self._refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_btn.set_tooltip_text("立即刷新")
        self._refresh_btn.connect("clicked", lambda *_: self._refresh(force=True))
        header.pack_end(self._refresh_btn)

        menu = Gio.Menu()
        menu.append("关于 MCHOSE", "app.about")
        menu.append("GitHub 项目页", "app.homepage")
        menu.append("退出", "app.quit")
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        menu_btn.set_tooltip_text("主菜单")
        header.pack_end(menu_btn)

        self._page = Adw.PreferencesPage()
        self._page.add(self._build_overview())
        self._page.add(self._build_dpi())
        self._page.add(self._build_polling())
        self._page.add(self._build_advanced())

        self._status = Adw.StatusPage(icon_name="input-mouse-symbolic")
        retry = Gtk.Button(label="重试")
        retry.add_css_class("pill")
        retry.add_css_class("suggested-action")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self._refresh(force=True))
        self._status.set_child(retry)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(160)
        self._stack.add_named(self._page, "page")
        self._stack.add_named(self._status, "status")
        self._stack.set_visible_child_name("status")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._stack)

        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

    def _build_overview(self):
        group = Adw.PreferencesGroup()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        row.set_margin_top(14)
        row.set_margin_bottom(14)
        row.set_margin_start(14)
        row.set_margin_end(14)

        self._ring = BatteryRing(78)
        self._ring.set_valign(Gtk.Align.CENTER)
        row.append(self._ring)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)

        self._model_label = Gtk.Label(label="未连接", xalign=0)
        self._model_label.add_css_class("title-3")
        self._model_label.set_ellipsize(3)          # Pango.EllipsizeMode.END

        self._meta_label = Gtk.Label(label="插上接收器或数据线", xalign=0)
        self._meta_label.add_css_class("dim-label")
        self._meta_label.add_css_class("caption")
        self._meta_label.set_ellipsize(3)

        self._battery_label = Gtk.Label(label="", xalign=0)
        self._battery_label.add_css_class("caption")

        texts.append(self._model_label)
        texts.append(self._meta_label)
        texts.append(self._battery_label)
        row.append(texts)

        group.add(row)
        return group

    def _build_dpi(self):
        self._dpi_group = Adw.PreferencesGroup(
            title="DPI 档位",
            description="点左侧圆点切换当前档位，右侧数字可直接修改",
        )
        self._dpi_rows = []
        self._dpi_dots = []
        self._dpi_badges = []

        for i in range(DPI_STAGES):
            adj = Gtk.Adjustment(
                value=800, lower=DPI_MIN, upper=DPI_MAX,
                step_increment=DPI_STEP, page_increment=DPI_STEP * 10,
            )
            row = Adw.SpinRow.new(adj, 8, 0)
            row.set_title(f"档位 {i + 1}")
            row.connect("notify::value", self._on_dpi_value_changed, i)
            row.connect("notify::has-focus", self._on_dpi_focus_changed, i)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_dpi_key_pressed, i)
            row.add_controller(keys)

            dot = StageDot(on_activate=lambda i=i: self._activate_dpi(i))
            row.add_prefix(dot)

            badge = Gtk.Label(label="当前")
            badge.add_css_class("accent")
            badge.add_css_class("caption")
            badge.set_visible(False)
            row.add_suffix(badge)

            self._dpi_group.add(row)
            self._dpi_rows.append(row)
            self._dpi_dots.append(dot)
            self._dpi_badges.append(badge)

        return self._dpi_group

    def _build_polling(self):
        group = Adw.PreferencesGroup(title="回报率")
        self._poll_row = Adw.ComboRow(title="USB 报告频率")
        self._poll_row.set_subtitle("—")
        self._poll_row.connect("notify::selected", self._on_polling_selected)
        self._poll_opts = []
        group.add(self._poll_row)
        return group

    def _build_advanced(self):
        group = Adw.PreferencesGroup(title="高级")

        self._lod_row = Adw.ComboRow(title="静默高度（LOD）")
        self._lod_row.set_model(Gtk.StringList.new(["1 mm", "2 mm"]))
        self._lod_row.connect("notify::selected", self._on_lod_selected)
        group.add(self._lod_row)

        self._sleep_row = Adw.SpinRow.new(
            Gtk.Adjustment(value=15, lower=SLEEP_MIN, upper=SLEEP_MAX,
                           step_increment=1, page_increment=5), 1, 1)
        self._sleep_row.set_title("休眠时间")
        self._sleep_row.set_subtitle("单位：分钟")
        self._sleep_row.connect("notify::value", self._on_sleep_changed)
        group.add(self._sleep_row)

        self._debounce_row = Adw.SpinRow.new(
            Gtk.Adjustment(value=0, lower=0, upper=DEBOUNCE_MAX,
                           step_increment=1, page_increment=2), 1, 0)
        self._debounce_row.set_title("按键消抖")
        self._debounce_row.set_subtitle("单位：毫秒")
        self._debounce_row.connect("notify::value", self._on_debounce_changed)
        group.add(self._debounce_row)

        self._snap_row = Adw.SwitchRow(title="直线修正")
        self._snap_row.set_subtitle("把抖动抹平，画直线更稳")
        self._snap_row.connect("notify::active", self._on_snap_toggled)
        group.add(self._snap_row)

        # 配置文件 1..5，做成联动按钮
        self._profile_row = Adw.ActionRow(title="配置文件")
        buttons = Gtk.Box(spacing=0)
        buttons.add_css_class("linked")
        buttons.set_valign(Gtk.Align.CENTER)

        self._profile_buttons = []
        first = None
        for p in range(1, 6):
            btn = Gtk.ToggleButton(label=str(p))
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            btn.connect("toggled", self._on_profile_toggled, p)
            buttons.append(btn)
            self._profile_buttons.append(btn)

        self._profile_row.add_suffix(buttons)
        group.add(self._profile_row)

        return group

    # ------------------------------------------------------------ 状态更新
    def _apply_state(self, d):
        self._busy = False
        connected = bool(d.get("connected"))

        now = time.monotonic()
        if connected:
            self._last_ok = now
        elif self._last_ok and now - self._last_ok < 4.0 \
                and self._stack.get_visible_child_name() == "page":
            # 改回报率会让 USB 链路重枚举，短暂读不到——别把界面弹回空状态，
            # 也保留上一次的数据，不然这段时间里设置项会全部变灰。
            self._title.set_subtitle("连接中断，正在重试…")
            return

        self._state = d

        self._syncing = True
        try:
            if connected:
                self._stack.set_visible_child_name("page")

                model = d.get("model") or "MCHOSE"
                self._title.set_subtitle(f"{model} · {link_text(d)}")
                self._model_label.set_text(model)
                self._meta_label.set_text(
                    f"固件 {d.get('firmware') or '—'} · PID {d.get('pid') or '—'}")
                self._ring.set_value(d.get("battery"))
                self._battery_label.set_text(self._battery_text(d))

                self._update_dpi(d)
                self._update_polling(d)
                self._update_advanced(d)
            else:
                err = (d.get("error") or "").strip()
                if d.get("need_auth"):
                    self._status.set_title("缺少设备权限")
                    self._status.set_description(
                        f"{err or '打不开 /dev/hidraw 设备'}\n\n"
                        "需要 70-mchose.rules 这条 udev 规则，见项目 README。")
                elif err:
                    self._status.set_title("读取失败")
                    self._status.set_description(err)
                else:
                    self._status.set_title("未检测到 MCHOSE 鼠标")
                    self._status.set_description(
                        "插上接收器或数据线，或者动一下鼠标把它唤醒。")
                self._stack.set_visible_child_name("status")
                self._title.set_subtitle("未连接")
        finally:
            self._syncing = False

    def _battery_text(self, d):
        pct = d.get("battery")
        if pct is None:
            return ""
        charging = d.get("charge_status") == 1
        return f"剩余电量 {pct}%" + ("（充电中）" if charging else "")

    def _update_dpi(self, d):
        stages = [int(s) for s in (d.get("dpi_stages") or [])]
        active = d.get("dpi_active")
        colors = d.get("dpi_colors") or []

        for i, (row, dot, badge) in enumerate(
                zip(self._dpi_rows, self._dpi_dots, self._dpi_badges)):
            has = i < len(stages)
            row.set_visible(has)
            if not has:
                continue
            # 用户正在这一格里打字就别抢值（只看按键，不看焦点——
            # 窗口打开时第一行会自动获得焦点，那种情况还是要同步）
            if i not in self._typing_rows:
                row.set_value(stages[i])
            color = None
            if i < len(colors) and len(colors[i]) >= 3:
                color = tuple(c / 255.0 for c in colors[i][:3])
            dot.set_state(color=color, active=(i == active))
            badge.set_visible(i == active)

        if stages:
            self._dpi_group.set_description(
                f"共 {len(stages)} 档 · 当前第 {active + 1 if active is not None else '—'} 档")
        else:
            self._dpi_group.set_description("读取中…")

    def _update_polling(self, d):
        opts = list(d.get("polling_options") or [])
        cur = d.get("polling")

        if opts != self._poll_opts:
            self._poll_opts = opts
            self._poll_row.set_model(
                Gtk.StringList.new([f"{h} Hz" for h in opts]))

        if cur in opts:
            self._poll_row.set_selected(opts.index(cur))
            note = "（设备未回读，显示上次设置值）" if d.get("polling_estimated") else ""
            self._poll_row.set_subtitle(f"当前 {cur} Hz{note}")
        else:
            self._poll_row.set_selected(Gtk.INVALID_LIST_POSITION)
            if d.get("polling_unset"):
                self._poll_row.set_subtitle("设备还没设过回报率，选一档写入")
            else:
                self._poll_row.set_subtitle("读取失败")

    def _update_advanced(self, d):
        lod = d.get("lod")
        self._lod_row.set_selected(0 if lod == 1 else 1 if lod == 2
                                   else Gtk.INVALID_LIST_POSITION)

        sec = d.get("sleep_sec")
        if sec is not None:
            self._sleep_row.set_value(round(sec / 60, 1))
            self._sleep_row.set_subtitle(f"当前 {fmt_seconds(sec)}")
        else:
            self._sleep_row.set_subtitle("读取失败")

        ms = d.get("debounce")
        if ms is not None:
            self._debounce_row.set_value(float(ms))
            self._debounce_row.set_subtitle(f"当前 {ms} ms")
        else:
            self._debounce_row.set_subtitle("读取失败")

        self._snap_row.set_active(bool(d.get("angle_snap")))
        prof = d.get("profile")
        for p, btn in enumerate(self._profile_buttons, start=1):
            btn.set_active(p == prof)

    # ------------------------------------------------------------ 交互
    def _connected(self):
        return bool(self._state and self._state.get("connected"))

    def _on_dpi_value_changed(self, row, _pspec, index):
        if self._syncing or not self._connected():
            return
        stages = list(self._state.get("dpi_stages") or [])
        if index >= len(stages):
            return
        val = int(round(row.get_value() / DPI_STEP) * DPI_STEP)
        val = max(DPI_MIN, min(DPI_MAX, val))
        if val != int(row.get_value()):
            self._syncing = True
            row.set_value(val)
            self._syncing = False
        if val == stages[index]:
            return

        if index in self._dpi_timers:
            GLib.source_remove(self._dpi_timers.pop(index))

        def commit():
            self._dpi_timers.pop(index, None)
            stages_now = list(self._state.get("dpi_stages") or [])
            if index < len(stages_now):
                stages_now[index] = int(self._dpi_rows[index].get_value())
                self._apply("dpi_stages", stages_now, quiet=True)
            return GLib.SOURCE_REMOVE

        self._dpi_timers[index] = GLib.timeout_add(450, commit)

    def _on_dpi_key_pressed(self, _ctrl, _keyval, _keycode, _state, index):
        self._typing_rows.add(index)
        return False

    def _on_dpi_focus_changed(self, row, _pspec, index):
        if not row.has_focus():
            self._typing_rows.discard(index)

    def _activate_dpi(self, index):
        if not self._connected():
            return
        if index == self._state.get("dpi_active"):
            return
        if index >= len(self._state.get("dpi_stages") or []):
            return
        self._apply("dpi_active", index)

    def _on_polling_selected(self, row, _pspec):
        if self._syncing or not self._connected():
            return
        idx = row.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self._poll_opts):
            return
        hz = self._poll_opts[idx]
        if hz == self._state.get("polling"):
            return
        self._apply("polling", hz)

    def _on_lod_selected(self, row, _pspec):
        if self._syncing or not self._connected():
            return
        idx = row.get_selected()
        if idx not in (0, 1):
            return
        mm = idx + 1
        if mm == self._state.get("lod"):
            return
        self._apply("lod", mm)

    def _on_sleep_changed(self, row, _pspec):
        if self._syncing or not self._connected():
            return
        sec = int(round(row.get_value() * 60))
        sec = max(60, min(1800, sec))
        if sec == self._state.get("sleep_sec"):
            return
        self._queue_apply("sleep", sec)

    def _on_debounce_changed(self, row, _pspec):
        if self._syncing or not self._connected():
            return
        ms = int(row.get_value())
        if ms == self._state.get("debounce"):
            return
        self._queue_apply("debounce", ms)

    def _on_snap_toggled(self, row, _pspec):
        if self._syncing or not self._connected():
            return
        on = row.get_active()
        if on == bool(self._state.get("angle_snap")):
            return
        self._apply("angle_snap", on)

    def _on_profile_toggled(self, btn, profile):
        if self._syncing or not self._connected():
            return
        if not btn.get_active():
            return
        if profile == self._state.get("profile"):
            return
        self._apply("profile", profile)

    def _queue_apply(self, key, value, delay=500):
        if self._apply_timer:
            GLib.source_remove(self._apply_timer)

        def commit():
            self._apply_timer = None
            self._apply(key, value)
            return GLib.SOURCE_REMOVE

        self._apply_timer = GLib.timeout_add(delay, commit)

    # ------------------------------------------------------------ 后台读写
    def _tick(self):
        self._refresh()
        return GLib.SOURCE_CONTINUE

    def _refresh(self, force=False):
        if self._busy:
            return
        self._busy = True
        self._refresh_btn.set_sensitive(False)
        threading.Thread(target=self._read_worker, daemon=True).start()

    def _read_worker(self):
        try:
            state = self._ctl.read()
        except PermissionError as e:
            state = {"connected": False, "need_auth": True, "error": str(e)}
        except NoDevice:
            state = {"connected": False}
        except Exception as e:
            state = {"connected": False, "error": f"{type(e).__name__}: {e}"}
        GLib.idle_add(self._on_read_done, state)

    def _on_read_done(self, state):
        self._refresh_btn.set_sensitive(True)
        self._apply_state(state)
        return GLib.SOURCE_REMOVE

    def _apply(self, key, value, quiet=False):
        if self._busy:
            # 上一个还没写完，记下来等它结束再发，避免写入被吞掉
            self._pending = (key, value, quiet)
            return
        self._busy = True
        threading.Thread(target=self._apply_worker,
                         args=(key, value, quiet), daemon=True).start()

    def _apply_worker(self, key, value, quiet):
        try:
            self._ctl.apply(key, value)
            state = self._ctl.read()
            GLib.idle_add(self._on_apply_done, state, None, quiet, key)
        except Exception as e:
            GLib.idle_add(self._on_apply_done, None, str(e), quiet, key)

    def _on_apply_done(self, state, error, quiet, key):
        if error:
            self._busy = False
            self._toast(f"设置失败：{error}")
            self._refresh(force=True)
            return GLib.SOURCE_REMOVE

        self._apply_state(state)
        if not quiet:
            self._toast("已生效" if key == "polling" else "已保存")
        if key == "polling":
            # 接收器重新枚举要一两秒，稍后再读一次把最新状态拉回来
            GLib.timeout_add(2000, self._delayed_refresh)

        if self._pending:
            k, v, q = self._pending
            self._pending = None
            self._apply(k, v, q)
        return GLib.SOURCE_REMOVE

    def _delayed_refresh(self):
        self._refresh(force=True)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ 杂项
    def _toast(self, text):
        t = Adw.Toast.new(text)
        t.set_timeout(2)
        self._toasts.add_toast(t)

    def _on_map(self, *_args):
        # 默认焦点会落进第一档 DPI 的输入框，整行套一圈蓝框，看着像被选中了。
        # 把焦点挪到刷新按钮（focus-visible 还是 false，所以不画焦点圈）。
        GLib.idle_add(self._steal_focus)

    def _steal_focus(self):
        self.set_focus_visible(False)
        self._refresh_btn.grab_focus()
        return GLib.SOURCE_REMOVE

    def _on_close(self, *_args):
        try:
            self._ctl.close()
        except Exception:
            pass
        return False
