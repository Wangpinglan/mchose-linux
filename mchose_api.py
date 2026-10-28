#!/usr/bin/env python3
"""mchose_api — 设备状态的读写封装。

守护进程（mchose-daemon.py）和 GTK 应用（app/）共用这里的逻辑，
避免两边各写一份、改了一边忘了另一边。

约定：
    read_all(m, dev)          -> dict，形状和守护进程返回 JSON 的 data 一致
    do_set(m, key, value)     -> None；参数非法时抛 ValueError
"""

import json
import os
import time

import mchose

STATE_PATH = os.path.expanduser("~/.cache/mchose/state.json")

DEFAULT_POLLING = [125, 250, 500, 1000]


# ---------------------------------------------------------------- 本地状态
# 有些接收器在无线模式下读不回回报率（固定返回 0x00），只能记住"上次设了多少"。

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(**kw):
    d = load_state()
    d.update(kw)
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def seconds_to_minutes(sec):
    return None if sec is None else round(sec / 60)


# ---------------------------------------------------------------- 读

def read_all(m, dev, reset=None):
    """读一遍设备所有状态。

    reset: 可选的回调，读取出错（fd 失效）时用来丢弃旧句柄。
    """
    def safe(fn, key, default=None):
        for attempt in (0, 1):
            try:
                val = fn()
            except OSError as e:
                # fd 失效（设备重枚举/休眠）——丢掉句柄，下次自动重连
                out.setdefault("errors", {})[key] = str(e)
                if reset:
                    reset()
                out[key] = default
                return
            except Exception as e:
                out.setdefault("errors", {})[key] = str(e)
                out[key] = default
                return
            if val is not None:
                out[key] = val
                return
            if attempt == 1:
                out[key] = default
                return
            time.sleep(0.06)      # 读回空值，多半是设备刚被唤醒，再试一次

    # 接收器的 VID/PID 不代表鼠标本体（F015 是通用 4K 接收器），
    # 优先用鼠标自报的 PID 来显示型号。
    try:
        real_pid = m.pid()
    except Exception:
        real_pid = None
    shown_pid = real_pid or dev.pid

    out = {
        "connected": True,
        "path": dev.path,
        "pid": f"{shown_pid:04X}",
        "model": mchose.KNOWN_PIDS.get(shown_pid, f"未知型号 {shown_pid:04X}"),
        "dongle_pid": f"{dev.pid:04X}",
        "name": dev.name,
    }

    safe(m.firmware, "firmware")
    safe(m.profile, "profile")
    safe(m.sleep_time, "sleep_sec")
    safe(lambda: seconds_to_minutes(out.get("sleep_sec")), "sleep")
    safe(m.debounce, "debounce")
    safe(m.lod, "lod")
    safe(m.angle_snap, "angle_snap")

    # 回报率：出厂没设过时设备回 0x00（不是 0 Hz）。
    # 写一档之后就能正常回读，所以 0x00 单独标成 unset 交给界面提示。
    safe(m.polling_code, "polling_code")
    code = out.get("polling_code")
    hz = mchose.POLLING_CODES.get(code) if code is not None else None
    out["polling_hz_read"] = hz
    out["polling_unset"] = (code == mchose.POLLING_UNSET)
    if hz is None:
        remembered = load_state().get("polling_hz")
        out["polling"] = remembered
        out["polling_estimated"] = remembered is not None
    else:
        out["polling"] = hz
        out["polling_estimated"] = False
        save_state(polling_hz=hz)

    # 可选档位 = 型号表列表 + 4K/1K 接收器的追加规则（驱动里的
    # GetGoldenDonglePollingRate）。所以 A5 Pro 插 4K 接收器会有 2K/4K。
    out["link"] = m.link_mode()
    out["dongle_hz"] = m.dongle_hz()
    safe(m.polling_supported, "polling_options")
    if not out.get("polling_options"):
        out["polling_options"] = list(DEFAULT_POLLING)
    out["polling_max"] = max(out["polling_options"])

    try:
        bat = m.battery()
        if bat:
            out["battery"] = bat[1]
            out["charge_status"] = bat[0]
        else:
            out["battery"] = None
    except Exception as e:
        out["battery"] = None
        out.setdefault("errors", {})["battery"] = str(e)

    try:
        active, stages = m.dpi_stages()
        out["dpi_active"] = active
        out["dpi_stages"] = list(stages)
        try:
            out["dpi_colors"] = [list(c) for c in m.dpi_colors()]
        except Exception:
            out["dpi_colors"] = []
    except Exception as e:
        out["dpi_stages"] = []
        out.setdefault("errors", {})["dpi"] = str(e)

    return out


# ---------------------------------------------------------------- 写

def do_set(m, key, value):
    """写一个设置项。参数非法时抛 ValueError。"""
    if key == "dpi_stages":
        vals = []
        for d in value:
            if d is None:
                raise ValueError("DPI 档位里有空值，已放弃这次写入")
            vals.append(int(d))
        m.set_dpi_stages(vals)
    elif key == "dpi_active":
        m.set_active_dpi(int(value))
    elif key == "dpi_colors":
        m.set_dpi_colors([tuple(c) for c in value])
    elif key == "polling":
        m.set_polling(int(value))
        # 设备不回读时靠这个记住用户的选择
        save_state(polling_hz=int(value))
    elif key == "sleep":
        # 单位是秒（设备原生存的就是秒，60..1800）
        sec = int(value)
        if not 60 <= sec <= 1800:
            raise ValueError(f"休眠时间必须在 60..1800 秒之间，收到 {sec}")
        m.set_sleep_time(sec)
    elif key == "debounce":
        m.set_debounce(int(value))
    elif key == "lod":
        m.set_lod(int(value))
    elif key == "angle_snap":
        m.set_angle_snap(bool(value))
    elif key == "profile":
        m.set_profile(int(value))
    elif key == "reset":
        m.reset_default()
    else:
        raise ValueError(f"未知设置项 {key}")
