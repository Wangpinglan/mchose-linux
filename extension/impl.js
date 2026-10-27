// extension.js — MCHOSE 鼠标控制面板

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';


import {Daemon} from './daemon.js';

const POLL_INTERVAL = 20000;   // 面板轮询间隔（毫秒）
const DEBOUNCE_MS = 400;       // 设置写入去抖
const DPI_STAGES_UI = 6;       // 固件固定 6 档，界面按 3 列 × 2 行铺

// ---------------------------------------------------------------- 工具

/** DPI 档位值。兼容旧格式 [dpi, color]，新格式就是数字。 */
function stageValue(v) {
    return Array.isArray(v) ? v[0] : v;
}

function batteryColor(pct) {
    if (pct === null || pct === undefined) return '#9a9996';
    if (pct <= 15) return '#e01b24';   // 红
    if (pct <= 35) return '#e5a50a';   // 琥珀
    return '#33d17a';                  // 绿
}

function batteryIconName(pct, charging) {
    if (pct === null || pct === undefined) return 'battery-missing-symbolic';
    if (charging) return 'battery-good-charging-symbolic';
    if (pct <= 15) return 'battery-caution-symbolic';
    if (pct <= 35) return 'battery-low-symbolic';
    if (pct <= 70) return 'battery-good-symbolic';
    return 'battery-full-symbolic';
}

/** 1000 -> "1K"，250 -> "250" */
function hzText(hz) {
    if (hz === null || hz === undefined) return '—';
    return hz >= 1000 ? `${hz / 1000}K` : `${hz}`;
}

/** 一个带标题的分组容器 */
function makeSection(title) {
    const item = new PopupMenu.PopupBaseMenuItem({
        reactive: false, can_focus: false,
    });
    const box = new St.BoxLayout({
        vertical: true,
        x_expand: true,
        style_class: 'mchose-section',
    });

    const headerRow = new St.BoxLayout({
        x_expand: true,
        style_class: 'mchose-section-header',
    });
    const header = new St.Label({
        text: title,
        style_class: 'mchose-section-title',
        x_expand: true,
    });
    headerRow.add_child(header);
    box.add_child(headerRow);

    item.add_child(box);
    item._content = box;
    item._header = headerRow;
    return item;
}

// 每次载入本模块都用一个新的 GType 名：外壳会用带时间戳的 URL 多次 import
// 本文件，GJS 默认按"路径+类名"生成类型名，重复 import 就会撞
// "Type name ... is already registered"。
const TYPE_SUFFIX = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;

/** 主界面按钮 */
var MchoseButton = GObject.registerClass({
    GTypeName: `MchoseButton_${TYPE_SUFFIX}`,
},
class MchoseButton extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'MCHOSE 鼠标', false);
        this._ext = extension;
        this._state = null;
        this._timer = null;
        this._busy = false;
        this._busyTimer = null;
        this._lastGoodAt = 0;
        this._syncing = false;
        this._destroyed = false;
        this._editingSleep = false;
        this._sleepShownText = '';
        this._dpiPending = null;

        // ---------- 面板部分 ----------
        const panelBox = new St.BoxLayout({
            style_class: 'panel-status-indicators-box',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._icon = new St.Icon({
            icon_name: 'input-mouse-symbolic',
            style_class: 'system-status-icon',
        });
        this._batteryLabel = new St.Label({
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'mchose-panel-label',
        });
        this._dpiLabel = new St.Label({
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'mchose-panel-label mchose-panel-dim',
        });
        panelBox.add_child(this._icon);
        panelBox.add_child(this._batteryLabel);
        panelBox.add_child(this._dpiLabel);
        this.add_child(panelBox);

        this._buildMenu();
        this._refresh();
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, POLL_INTERVAL, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    // ---------------------------------------------------------- 菜单构建

    _buildMenu() {
        const menu = this.menu;

        // 设备概览卡片
        this._overviewItem = new PopupMenu.PopupBaseMenuItem({
            reactive: false, can_focus: false,
        });
        const ov = new St.BoxLayout({
            vertical: false, x_expand: true, style_class: 'mchose-overview',
        });
        this._ovIcon = new St.Icon({
            icon_name: 'input-mouse-symbolic',
            icon_size: 40,
            style_class: 'mchose-overview-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        const ovText = new St.BoxLayout({
            vertical: true, x_expand: true,
            style_class: 'mchose-overview-text',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._ovModel = new St.Label({text: '正在连接…', style_class: 'mchose-model'});
        this._ovFw = new St.Label({text: '', style_class: 'mchose-dim'});
        ovText.add_child(this._ovModel);
        ovText.add_child(this._ovFw);

        // 电池圆环（用 St.DrawingArea 画）
        this._batteryArea = new St.DrawingArea({
            style_class: 'mchose-battery-ring',
            width: 56, height: 56,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._batteryArea.connect('repaint', area => this._drawBattery(area));

        ov.add_child(this._ovIcon);
        ov.add_child(ovText);
        ov.add_child(this._batteryArea);
        this._overviewItem.add_child(ov);
        menu.addMenuItem(this._overviewItem);

        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // ---------- DPI ----------
        this._dpiSection = makeSection('DPI');
        this._dpiCurrent = new St.Label({
            text: '—',
            style_class: 'mchose-dpi-current',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._dpiSection._header.add_child(this._dpiCurrent);

        // 六档全部可直接改数字：圆点切当前档，输入框改数值
        this._dpiCells = [];
        this._editingStage = null;
        const dpiRows = [];
        for (let r = 0; r < 2; r++) {
            const row = new St.BoxLayout({
                x_expand: true, style_class: 'mchose-dpi-row',
            });
            dpiRows.push(row);
            this._dpiSection._content.add_child(row);
        }
        for (let i = 0; i < DPI_STAGES_UI; i++) {
            const cell = new St.BoxLayout({style_class: 'mchose-dpi-cell'});
            const dotBtn = new St.Button({
                can_focus: false, style_class: 'mchose-dot-btn',
            });
            const dot = new St.Bin({style_class: 'mchose-stage-dot'});
            dotBtn.set_child(dot);
            dotBtn.connect('clicked', () => this._set('dpi_active', i));

            const entry = new St.Entry({
                text: '—',
                can_focus: true,
                style_class: 'mchose-dpi-input',
            });
            entry.clutter_text.connect('activate', () => this._commitStage(i));
            // Clutter 里没有 focus-in/focus-out，只有 key-focus-in/key-focus-out
            entry.clutter_text.connect('key-focus-in', () => {
                this._editingStage = i;
            });
            entry.clutter_text.connect('key-focus-out', () => {
                if (this._editingStage === i)
                    this._editingStage = null;
            });

            cell.add_child(dotBtn);
            cell.add_child(entry);
            dpiRows[Math.floor(i / 3)].add_child(cell);
            this._dpiCells.push({cell, dot, entry});
        }

        // DPI 滑块：在当前档位的范围内调
        this._dpiSlider = new Slider(0.0);
        this._dpiSlider.x_expand = true;
        this._dpiSlider.connect('notify::value', () => this._onDpiSliderChanged());
        this._dpiSection._content.add_child(this._dpiSlider);

        this._dpiHint = new St.Label({
            text: '改数字回车生效',
            style_class: 'mchose-hint',
        });
        this._dpiSection._content.add_child(this._dpiHint);
        menu.addMenuItem(this._dpiSection);

        // ---------- 回报率 ----------
        this._pollSection = makeSection('回报率');
        this._pollBox = new St.BoxLayout({
            x_expand: true, style_class: 'mchose-chip-row',
        });
        this._pollSection._content.add_child(this._pollBox);
        this._pollHint = new St.Label({text: '', style_class: 'mchose-hint'});
        this._pollSection._content.add_child(this._pollHint);
        menu.addMenuItem(this._pollSection);

        // ---------- 其他设置 ----------
        this._miscSection = makeSection('其他');
        this._buildMiscRows(this._miscSection._content);
        menu.addMenuItem(this._miscSection);

        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // 刷新 / 断开提示
        this._statusItem = new PopupMenu.PopupMenuItem('立即刷新');
        this._statusItem.connect('activate', () => this._refresh(true));
        menu.addMenuItem(this._statusItem);
    }

    _buildMiscRows(parent) {
        const mkRow = (label, buildControl) => {
            const row = new St.BoxLayout({
                x_expand: true, style_class: 'mchose-row',
            });
            const l = new St.Label({
                text: label, x_expand: true, style_class: 'mchose-row-label',
                y_align: Clutter.ActorAlign.CENTER,
            });
            const ctl = buildControl();
            row.add_child(l);
            row.add_child(ctl);
            parent.add_child(row);
            return ctl;
        };

        const chip = (text, onClick) => {
            const b = new St.Button({
                style_class: 'mchose-chip',
                child: new St.Label({text, style_class: 'mchose-chip-label'}),
                can_focus: true,
            });
            b.connect('clicked', onClick);
            return b;
        };

        // 静默高度（值就是毫米数，1 或 2）
        this._lodBox = new St.BoxLayout({style_class: 'mchose-chip-row'});
        this._lodBox.add_child(chip('1mm', () => this._set('lod', 1)));
        this._lodBox.add_child(chip('2mm', () => this._set('lod', 2)));
        mkRow('静默高度 (LOD)', () => this._lodBox);

        // 直线修正
        this._snapBox = new St.BoxLayout({style_class: 'mchose-chip-row'});
        this._snapBox.add_child(chip('开', () => this._set('angle_snap', true)));
        this._snapBox.add_child(chip('关', () => this._set('angle_snap', false)));
        mkRow('直线修正', () => this._snapBox);

        // 按键消抖
        this._debounceBox = new St.BoxLayout({style_class: 'mchose-chip-row'});
        for (const v of [0, 2, 4, 8]) {
            this._debounceBox.add_child(chip(`${v}ms`, () => this._set('debounce', v)));
        }
        mkRow('按键消抖', () => this._debounceBox);

        // 休眠时间：快选 + 手动输入（设备存的是秒，分钟可以有小数）
        this._sleepBox = new St.BoxLayout({style_class: 'mchose-chip-row'});
        for (const v of [1, 5, 15, 30]) {
            this._sleepBox.add_child(chip(`${v}分`, () => this._set('sleep', v * 60)));
        }
        mkRow('休眠时间', () => this._sleepBox);

        const sleepRow = new St.BoxLayout({
            x_expand: true, style_class: 'mchose-row',
        });
        this._sleepEntry = new St.Entry({
            text: '',
            can_focus: true,
            style_class: 'mchose-mini-input',
        });
        this._sleepEntry.clutter_text.connect('activate', () => this._commitSleep());
        this._sleepEntry.clutter_text.connect('key-focus-in', () => {
            this._editingSleep = true;
        });
        this._sleepEntry.clutter_text.connect('key-focus-out', () => {
            this._editingSleep = false;
        });
        sleepRow.add_child(this._sleepEntry);
        sleepRow.add_child(new St.Label({
            text: '分钟',
            style_class: 'mchose-unit',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        this._sleepHint = new St.Label({
            text: '',
            x_expand: true,
            x_align: Clutter.ActorAlign.END,
            style_class: 'mchose-hint',
            y_align: Clutter.ActorAlign.CENTER,
        });
        sleepRow.add_child(this._sleepHint);
        parent.add_child(sleepRow);

        // 配置文件（1 基，1..5）
        this._profileBox = new St.BoxLayout({style_class: 'mchose-chip-row'});
        for (let i = 1; i <= 5; i++)
            this._profileBox.add_child(chip(`${i}`, () => this._set('profile', i)));
        mkRow('配置文件', () => this._profileBox);
    }

    // ---------------------------------------------------------- 绘制

    _drawBattery(area) {
        const cr = area.get_context();
        if (!cr) return;
        const [w, h] = area.get_surface_size();
        const pct = this._state?.battery;
        const cx = w / 2, cy = h / 2;
        const r = Math.min(w, h) / 2 - 4;
        const lw = 5;

        // 底圈
        cr.setLineWidth(lw);
        cr.setSourceRGBA(0.5, 0.5, 0.5, 0.22);
        cr.arc(cx, cy, r, 0, 2 * Math.PI);
        cr.stroke();

        // 进度弧
        if (pct !== null && pct !== undefined) {
            const frac = Math.max(0, Math.min(1, pct / 100));
            const rgb = pct <= 15 ? [0.88, 0.11, 0.14]
                : pct <= 35 ? [0.90, 0.65, 0.04]
                : [0.20, 0.82, 0.48];
            cr.setSourceRGBA(rgb[0], rgb[1], rgb[2], 0.95);
            cr.setLineCap(1); // ROUND
            const start = -Math.PI / 2;   // 从 12 点方向开始
            cr.arc(cx, cy, r, start, start + 2 * Math.PI * frac);
            cr.stroke();
        }

        // 中心文字。Cairo 用的是 toy API，不会给 emoji 做字体回退，
        // 直接写 "⚡" 只会画出一个方框（tofu），所以充电闪电用路径画。
        const charging = this._state?.charge_status === 1;
        const hasPct = pct !== null && pct !== undefined;
        const txt = hasPct ? `${pct}` : '—';
        const showBolt = charging && hasPct;

        cr.selectFontFace('Sans', 0, 1);   // NORMAL, BOLD
        cr.setFontSize(showBolt ? 12 : 15);
        const ext = cr.textExtents(txt);

        const boltW = 6, boltH = 9, gap = 2;
        const totalW = ext.width + (showBolt ? boltW + gap : 0);
        let tx = cx - totalW / 2;

        if (showBolt) {
            this._drawBolt(cr, tx, cy - boltH / 2, boltW, boltH);
            tx += boltW + gap;
        }

        cr.moveTo(tx - ext.xBearing, cy - ext.height / 2 - ext.yBearing);
        const dim = !hasPct;
        cr.setSourceRGBA(dim ? 0.6 : 0.98, dim ? 0.6 : 0.98, dim ? 0.6 : 0.98, 1.0);
        cr.showText(txt);

        // Cairo context 由 St 管理，不手动 $dispose
    }

    /** 手绘充电闪电：emoji 字符在 Cairo 里会变成方框 */
    _drawBolt(cr, x, y, w, h) {
        cr.moveTo(x + w * 0.60, y);
        cr.lineTo(x + w * 0.08, y + h * 0.56);
        cr.lineTo(x + w * 0.44, y + h * 0.56);
        cr.lineTo(x + w * 0.32, y + h);
        cr.lineTo(x + w * 0.92, y + h * 0.42);
        cr.lineTo(x + w * 0.55, y + h * 0.42);
        cr.closePath();
        cr.setSourceRGBA(0.96, 0.79, 0.09, 1.0);
        cr.fill();
    }

    // ---------------------------------------------------------- 状态更新

    _applyState(d) {
        if (this._destroyed)
            return;
        const connected = d?.connected;

        // 刚连上过的设备短暂掉线（改回报率会重置 USB 链路）不要立刻清空界面，
        // 否则整块面板闪一下，看起来像“UI 重启”。
        if (!connected) {
            const since = this._lastGoodAt ? Date.now() - this._lastGoodAt : 1e9;
            if (since < 4000)
                return;
        } else {
            this._lastGoodAt = Date.now();
        }

        this._state = d;

        // 分区更新，每块独立兜底：一块画坏了不影响别的区域
        for (const [tag, fn] of [
            ['panel', () => this._updatePanel(d, connected)],
            ['overview', () => this._updateOverview(d, connected)],
            ['dpi', () => this._updateDpi(d, connected)],
            ['polling', () => this._updatePolling(d)],
            ['misc', () => this._updateMisc(d)],
        ]) {
            try {
                fn();
            } catch (e) {
                logError(e, `mchose: update ${tag}`);
            }
        }
    }

    _updatePanel(d, connected) {
        if (!connected) {
            this._icon.icon_name = 'input-mouse-symbolic';
            this._icon.set_style('opacity: 0.45;');
            this._batteryLabel.text = '未连接';
            this._batteryLabel.set_style('color: #9a9996;');
            this._dpiLabel.text = '';
            return;
        }
        const pct = d.battery;
        const charging = d.charge_status === 1;
        // 图标用电池形状，文字带百分比，颜色区分电量高低
        this._icon.icon_name = batteryIconName(pct, charging);
        this._icon.set_style(`color: ${batteryColor(pct)}; opacity: 1.0;`);
        if (pct === null || pct === undefined) {
            this._batteryLabel.text = '—';
            this._batteryLabel.set_style('color: #9a9996;');
        } else {
            this._batteryLabel.text = charging ? `${pct}%⚡` : `${pct}%`;
            this._batteryLabel.set_style(`color: ${batteryColor(pct)};`);
        }
        const dpi = stageValue(d.dpi_stages?.[d.dpi_active]);
        this._dpiLabel.text = dpi ? `· ${dpi}` : '';
    }

    _updateOverview(d, connected) {
        // 没权限 / 打不开设备：把原因直接写出来，别只说「未连接」
        if (!connected && d?.error) {
            const t = String(d.error).replace(/\s+/g, ' ').trim();
            this._ovModel.text = d.need_auth ? '缺少设备权限' : '读取失败';
            this._ovFw.text = t.length > 28 ? `${t.slice(0, 28)}…` : t;
            this._ovFw.set_style('color: #e01b24;');
            this._ovIcon.set_style('opacity: 0.4;');
            return;
        }
        this._ovModel.text = connected ? d.model : '鼠标未连接';
        this._ovFw.text = connected
            ? `固件 ${d.firmware ?? '—'}   PID ${d.pid ?? '—'}`
            : '插上接收器或唤醒鼠标';
        // 清掉 _showFatal 可能留下的红色
        this._ovFw.set_style('');
        // 左侧图标表示设备本身，右侧圆环显示电量
        this._ovIcon.icon_name = 'input-mouse-symbolic';
        this._ovIcon.set_style(connected
            ? 'color: -st-accent-color; opacity: 0.9;'
            : 'opacity: 0.4;');
        this._batteryArea.queue_repaint();
    }

    _updateDpi(d, connected) {
        const stages = d?.dpi_stages ?? [];
        const stageColors = d?.dpi_colors ?? [];
        const active = d?.dpi_active;

        for (let i = 0; i < this._dpiCells.length; i++) {
            const {cell, dot, entry} = this._dpiCells[i];
            const has = i < stages.length;
            cell.visible = has;
            if (!has)
                continue;
            // 正在这个格子里打字就别抢字
            if (this._editingStage !== i) {
                const val = stageValue(stages[i]);
                entry.text = val ? `${val}` : '—';
            }
            const c = stageColors[i];
            dot.set_style(c && (c[0] || c[1] || c[2])
                ? `background-color: rgb(${c[0]},${c[1]},${c[2]});`
                : '');
            cell.set_style_class_name(i === active
                ? 'mchose-dpi-cell mchose-dpi-cell-active'
                : 'mchose-dpi-cell');
        }

        const curDpi = stageValue(stages[active]);
        this._dpiCurrent.text = curDpi ? `${curDpi} DPI` : '—';
        this._dpiHint.text = stages.length
            ? '改数字回车生效；点左边圆点切换当前档'
            : (connected ? '没有读到 DPI 档位，点下方「立即刷新」重试'
                         : '鼠标未连接');

        // 滑块映射：把 400..32000 用对数刻度更符合直觉
        this._sliderMin = 400;
        this._sliderMax = 32000;
        if (!curDpi)
            return;
        const frac = Math.log(curDpi / this._sliderMin) /
                     Math.log(this._sliderMax / this._sliderMin);
        if (this._dragging)
            return;
        // 程序同步位置时别被当成用户拖动，否则会反写设备
        this._syncing = true;
        this._dpiSlider.value = Math.max(0, Math.min(1, frac));
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._syncing = false;
            return GLib.SOURCE_REMOVE;
        });
    }

    _updatePolling(d) {
        this._pollBox.destroy_all_children();
        // 档位来自型号表（守护进程已按型号+有线/无线裁剪过），
        // 不要在这里按"接收器名字"再猜上限
        const rates = d?.polling_options ?? [125, 250, 500, 1000];
        const cur = d?.polling;
        for (const hz of rates) {
            const isActive = cur === hz;
            const b = new St.Button({
                style_class: isActive
                    ? 'mchose-chip mchose-chip-active'
                    : 'mchose-chip',
                can_focus: false,
            });
            b.set_child(new St.Label({
                text: hzText(hz),
                style_class: 'mchose-chip-label',
            }));
            b.connect('clicked', () => this._set('polling', hz));
            this._pollBox.add_child(b);
        }

        if (cur === null || cur === undefined) {
            if (d?.connected === false)
                this._pollHint.text = '鼠标未连接';
            else if (d?.polling_unset)
                this._pollHint.text = '设备还没设过回报率，点上面任一档写入';
            else if (d?.polling_estimated)
                this._pollHint.text = '设备未回读，显示上次设置值';
            else
                this._pollHint.text = '读取失败，点下方「立即刷新」重试';
        } else if (d.polling_estimated) {
            this._pollHint.text = `当前 ${hzText(cur)}（上次设置值，设备未回读）`;
        } else {
            this._pollHint.text = `当前 ${hzText(cur)}`;
        }
    }

    _updateMisc(d) {
        this._markChips(this._lodBox, String(d?.lod ?? ''), ['1', '2']);
        this._markChips(this._snapBox, d?.angle_snap ? '1' : '0', ['1', '0']);
        this._markChips(this._debounceBox, String(d?.debounce ?? ''), ['0', '2', '4', '8']);
        this._markChips(this._sleepBox, String(d?.sleep ?? ''), ['1', '5', '15', '30']);
        this._markChips(this._profileBox, String(d?.profile ?? ''), ['1', '2', '3', '4', '5']);

        const sec = d?.sleep_sec;
        if (sec === null || sec === undefined) {
            this._sleepHint.text = '读取失败，点下方「立即刷新」重试';
        } else {
            const mm = Math.floor(sec / 60);
            const ss = sec % 60;
            this._sleepHint.text = ss
                ? `当前 ${mm} 分 ${ss} 秒`
                : `当前 ${mm} 分钟`;
        }
        // 正在打字就别覆盖
        if (!this._editingSleep)
            this._showSleep(sec);
    }

    /** 根据当前值高亮 chip */
    _markChips(box, current, values) {
        let i = 0;
        for (const child of box.get_children()) {
            const want = values[i++] === current;
            if (want)
                child.add_style_class_name('mchose-chip-active');
            else
                child.remove_style_class_name('mchose-chip-active');
        }
    }

    // ---------------------------------------------------------- 交互

    _onDpiSliderChanged() {
        if (!this._state?.connected) return;
        if (this._syncing) return;          // 程序在同步位置，不是用户拖动
        const stages = this._state.dpi_stages ?? [];
        const idx = this._state.dpi_active;
        if (idx === undefined || idx === null || !stages[idx]) return;
        // 还没拿到有效范围就先把滑块锁住，否则会算出 NaN 再写进设备
        if (!Number.isFinite(this._sliderMin) || !Number.isFinite(this._sliderMax))
            return;

        const frac = this._dpiSlider.value;
        const raw = this._sliderMin *
            Math.pow(this._sliderMax / this._sliderMin, frac);
        if (!Number.isFinite(raw)) return;

        // 圆整到 25 的倍数，手感更像厂商软件
        const dpi = Math.max(50, Math.min(32000, Math.round(raw / 25) * 25));
        const cell = this._dpiCells[idx];
        if (cell && this._editingStage !== idx)
            cell.entry.text = `${dpi}`;
        this._dpiCurrent.text = `${dpi} DPI`;

        // 算出来的值跟当前一致（回程误差），没必要写设备
        const cur = stageValue(stages[idx]);
        if (cur && Math.abs(dpi - cur) <= Math.max(25, cur * 0.01))
            return;

        this._dragging = true;
        // 去抖：拖动过程中只更新显示，停手后才写入
        if (this._dpiTimer) GLib.source_remove(this._dpiTimer);
        this._dpiTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, DEBOUNCE_MS, () => {
            this._dpiTimer = null;
            this._dragging = false;
            this._writeDpi(idx, dpi);
            return GLib.SOURCE_REMOVE;
        });
    }

    /** 某一档的输入框手打 DPI，回车提交 */
    _commitStage(i) {
        const stages = this._state?.dpi_stages;
        const cell = this._dpiCells[i];
        if (!stages || !cell || stages[i] === undefined) return;
        const cur = stageValue(stages[i]);
        const v = Number.parseInt(cell.entry.get_text().trim(), 10);
        if (!Number.isFinite(v)) {
            // 输入不合法就退回原值
            cell.entry.text = cur ? `${cur}` : '\u2014';
            return;
        }
        const dpi = Math.max(50, Math.min(32000, Math.round(v / 25) * 25));
        cell.entry.text = `${dpi}`;
        if (cur && dpi === cur) return;
        this._writeDpi(i, dpi);
    }

    /** 休眠时间输入框：填分钟，可带小数，回车生效 */
    _commitSleep() {
        const cur = this._state?.sleep_sec;
        const txt = this._sleepEntry.get_text().trim();
        // 没改过就别写：104 秒显示成 1.7 分，回车会变成 102 秒，白白漂移
        if (txt === this._sleepShownText)
            return;
        const minutes = Number.parseFloat(txt);
        if (!Number.isFinite(minutes)) {
            this._showSleep(cur);
            return;
        }
        // 设备范围 60..1800 秒
        const sec = Math.max(60, Math.min(1800, Math.round(minutes * 60)));
        this._showSleep(sec);
        if (cur !== null && cur !== undefined && sec === cur)
            return;
        this._set('sleep', sec);
    }

    /** 把秒数写进输入框（整分钟就不带小数） */
    _showSleep(sec) {
        if (!this._sleepEntry)
            return;
        if (sec === null || sec === undefined) {
            this._sleepEntry.text = '';
            return;
        }
        const m = sec / 60;
        const txt = Number.isInteger(m)
            ? `${m}` : `${Math.round(m * 10) / 10}`;
        this._sleepEntry.text = txt;
        this._sleepShownText = txt;
    }

    _writeDpi(stageIdx, dpi) {
        const stages = this._state?.dpi_stages;
        if (!stages) return;
        if (!Number.isFinite(dpi)) return;
        const next = [...stages];
        next[stageIdx] = dpi;
        this._set('dpi_stages', next);
    }

    _set(key, value) {
        if (this._destroyed || this._busy) return;
        this._busy = true;
        // 兜底：无论守护进程回不回话，5 秒后都解锁，避免整个面板卡死
        const unlock = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5000, () => {
            this._busyTimer = null;
            this._busy = false;
            return GLib.SOURCE_REMOVE;
        });
        this._busyTimer = unlock;

        this._ext.daemon.set(key, value).then(res => {
            this._clearBusy();
            if (res.ok && res.data) {
                this._applyState(res.data);
            } else if (!res.ok) {
                Main.notifyError('MCHOSE', res.error ?? '设置失败');
            }
            // 改回报率后接收器要重新枚举，补一次刷新把界面拉回最新
            if (key === 'polling') {
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2000, () => {
                    this._refresh(true);
                    return GLib.SOURCE_REMOVE;
                });
            }
        }).catch(e => {
            this._clearBusy();
            Main.notifyError('MCHOSE', `${e.message ?? e}`);
        });
    }

    _clearBusy() {
        if (this._busyTimer) {
            GLib.source_remove(this._busyTimer);
            this._busyTimer = null;
        }
        this._busy = false;
    }

    _refresh(force = false) {
        if (this._destroyed || (this._busy && !force)) return;
        this._ext.daemon.readAll().then(res => {
            if (this._destroyed)
                return;
            if (!res.ok) {
                // 读失败沿用上一次的数据；但一次都没成功过就必须把原因说出来，
                // 否则界面永远停在「正在连接…」，看不出是没插鼠标还是进程挂了
                if (!this._state)
                    this._showFatal(res.error || '辅助进程没有响应');
                return;
            }
            this._applyState(res.data);
        }).catch(e => {
            if (!this._destroyed && !this._state)
                this._showFatal(e?.message ?? String(e));
            else
                logError(e, 'mchose refresh');
        });
    }

    /** 一次都没连上过时，把失败原因直接写在概览卡上 */
    _showFatal(msg) {
        const text = String(msg).replace(/\s+/g, ' ').trim();
        // 同时写进 journal，方便直接 grep
        logError(new Error(text), 'mchose: 守护进程无响应');
        this._ovModel.text = '连接失败';
        this._ovFw.text = text.length > 28 ? `${text.slice(0, 28)}…` : text;
        this._ovFw.set_style('color: #e01b24;');
        this._ovIcon.set_style('opacity: 0.4;');
        this._dpiHint.text = '守护进程没有响应，点「立即刷新」重试';
    }

    destroy() {
        // 先立旗：异步回调（守护进程应答、GLib 定时器）可能在销毁之后才回来，
        // 那时控件已经 dispose，再写就会报 "has been already disposed"
        this._destroyed = true;
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        if (this._dpiTimer) {
            GLib.source_remove(this._dpiTimer);
            this._dpiTimer = null;
        }
        this._clearBusy();
        super.destroy();
    }
});

// ---------------------------------------------------------------- 入口

// ---------------------------------------------------------------- 生命周期
// 这里不是 Extension 子类：入口在 extension.js 里，它用带版本号的动态
// import 载入本文件。这样改界面只要 disable/enable 扩展就能生效，不用注销。

let _button = null;
let _daemon = null;
let _restartTimer = null;

export function start(ext) {
    const root = ext.dir.get_path();
    // 守护脚本随扩展一起安装，自带 mchose.py 协议实现
    const daemonPath = GLib.build_filenamev([root, 'mchose-daemon.py']);

    if (!GLib.file_test(daemonPath, GLib.FileTest.EXISTS)) {
        logError(new Error(`找不到 ${daemonPath}`), 'mchose');
        Main.notifyError('MCHOSE Mouse', '缺少 mchose-daemon.py，请重新安装扩展');
        return;
    }

    _daemon = new Daemon(daemonPath, pickPython());
    _daemon.onDisconnect = () => {
        // 辅助进程意外退出，稍后重试
        _restartTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            _restartTimer = null;
            if (_daemon) {
                _daemon.start();
                _button?._refresh(true);
            }
            return GLib.SOURCE_REMOVE;
        });
    };
    _daemon.start();

    _button = new MchoseButton({daemon: _daemon});
    Main.panel.addToStatusArea('mchose-mouse', _button, 1, 'right');
}

export function stop() {
    if (_restartTimer) {
        GLib.source_remove(_restartTimer);
        _restartTimer = null;
    }
    _daemon?.destroy();
    _daemon = null;
    _button?.destroy();
    _button = null;
}

function pickPython() {
    for (const p of ['/usr/bin/python3', '/usr/local/bin/python3']) {
        if (GLib.file_test(p, GLib.FileTest.EXISTS))
            return p;
    }
    return 'python3';
}
