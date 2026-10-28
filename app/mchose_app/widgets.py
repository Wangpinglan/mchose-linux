"""widgets — 自绘小部件（电量圆环、DPI 档位圆点）"""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402


def battery_rgb(pct):
    """电量配色，和顶栏/扩展保持一致。"""
    if pct is None:
        return (0.60, 0.60, 0.60)
    if pct <= 15:
        return (0.88, 0.11, 0.14)
    if pct <= 35:
        return (0.90, 0.65, 0.04)
    return (0.20, 0.82, 0.48)


class BatteryRing(Gtk.DrawingArea):
    """电量圆环：底圈 + 进度弧 + 中间百分比。"""

    __gtype_name__ = "MchoseBatteryRing"

    def __init__(self, size=76):
        super().__init__()
        self.set_content_width(size)
        self.set_content_height(size)
        self._pct = None
        self.set_draw_func(self._draw)

    def set_value(self, pct):
        pct = None if pct is None else int(pct)
        if pct == self._pct:
            return
        self._pct = pct
        self.queue_draw()

    # ------------------------------------------------------------ 绘制
    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 5
        stroke = 6

        # 底圈
        cr.set_line_width(stroke)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.20)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        pct = self._pct
        if pct is not None:
            frac = max(0.0, min(1.0, pct / 100.0))
            cr.set_line_width(stroke)
            cr.set_line_cap(1)                      # cairo.LINE_CAP_ROUND
            cr.set_source_rgba(*battery_rgb(pct), 1.0)
            start = -math.pi / 2                    # 12 点方向起
            cr.arc(cx, cy, radius, start, start + 2 * math.pi * frac)
            cr.stroke()

        # 中间数字。用 Pango 排版，别用 cr.show_text（没有字体回退）
        text = "—" if pct is None else str(pct)
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Sans Bold 17"))
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        cr.move_to(cx - tw / 2, cy - th / 2)

        color = self.get_color()
        alpha = 1.0 if pct is not None else 0.5
        cr.set_source_rgba(color.red, color.green, color.blue, alpha)
        PangoCairo.show_layout(cr, layout)


class StageDot(Gtk.DrawingArea):
    """DPI 档位圆点：当前档实心，其它档空心；点一下切到该档。"""

    __gtype_name__ = "MchoseStageDot"

    def __init__(self, size=24, on_activate=None):
        super().__init__()
        self.set_content_width(size)
        self.set_content_height(size)
        self._size = size
        self._color = (0.62, 0.62, 0.62)
        self._active = False
        self._hover = False
        self._on_activate = on_activate

        self.set_draw_func(self._draw)
        self.set_tooltip_text("设为当前档位")
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        except Exception:
            pass

        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("released", self._on_click)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._set_hover(True))
        motion.connect("leave", lambda *_: self._set_hover(False))
        self.add_controller(motion)

    def set_state(self, color=None, active=False):
        changed = False
        if color is not None and tuple(color) != tuple(self._color):
            self._color = tuple(color)
            changed = True
        if active != self._active:
            self._active = active
            changed = True
        if changed:
            self.queue_draw()

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self.queue_draw()

    def _on_click(self, *_args):
        if self._on_activate:
            self._on_activate()

    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        r, g, b = self._color
        if self._active:
            cr.set_source_rgba(r, g, b, 1.0)
            cr.arc(cx, cy, 6.5, 0, 2 * math.pi)
            cr.fill()
            # 外面补一圈淡的同色光晕，看起来像"选中"
            cr.set_source_rgba(r, g, b, 0.28)
            cr.set_line_width(2)
            cr.arc(cx, cy, 9.5, 0, 2 * math.pi)
            cr.stroke()
        else:
            alpha = 0.85 if self._hover else 0.55
            cr.set_source_rgba(r, g, b, alpha)
            cr.set_line_width(2)
            cr.arc(cx, cy, 6.0, 0, 2 * math.pi)
            cr.stroke()
