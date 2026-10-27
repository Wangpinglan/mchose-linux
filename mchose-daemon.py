#!/usr/bin/env python3
"""mchose-daemon — 供 GNOME 扩展调用的 HID 辅助进程。

比扩展直接做 ioctl 更省事：GJS 没有暴露 open(2)/ioctl，而 Python 有。
进程常驻，通过 stdin 读 JSON 命令、stdout 回 JSON 结果，一行一个。

协议：
    请求  {"id": 1, "cmd": "read_all"}          -> 读所有状态
          {"id": 2, "cmd": "set", "key": "dpi", "value": [...] }
    响应  {"id": 1, "ok": true, "data": {...}}
          {"id": 2, "ok": false, "error": "..."}
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mchose  # noqa: E402


_handle = None
_last_path = None


def reset_handle():
    """丢掉当前句柄。设备重枚举后旧 fd 会失效，必须重新打开。"""
    global _handle, _last_path
    h, _handle = _handle, None
    _last_path = None
    if h is not None:
        try:
            h.close()
        except Exception:
            pass


def ensure_device():
    """返回 (Device, Mchose)。设备换了会自动重连。"""
    global _handle, _last_path
    dev = mchose.pick_device()
    if dev is None:
        reset_handle()
        return None, None
    if _handle is None or _last_path != dev.path:
        reset_handle()
        _handle = mchose.Mchose(dev)
        _last_path = dev.path
    return dev, _handle


# ---------------------------------------------------------------- 本地状态
# 有些接收器在无线模式下读不回回报率（固定返回 0x00），只能记住"上次设了多少"。

STATE_PATH = os.path.expanduser("~/.cache/mchose/state.json")


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


def read_all():
    try:
        dev, m = ensure_device()
    except PermissionError as e:
        # 没权限时不要退出进程，把原因带回界面
        reset_handle()
        return {"connected": False, "need_auth": True, "error": str(e)}
    if dev is None:
        return {"connected": False}

    def _seconds_to_minutes(sec):
        return None if sec is None else round(sec / 60)

    # 接收器的 VID/PID 不代表鼠标本体（F015 是通用 4K 接收器），
    # 优先用鼠标自报的 PID 来显示型号。
    real_pid = None
    try:
        real_pid = m.pid()
    except Exception:
        pass
    shown_pid = real_pid or dev.pid
    out = {"connected": True, "path": dev.path,
           "pid": f"{shown_pid:04X}",
           "model": mchose.KNOWN_PIDS.get(shown_pid,
                                          f"未知型号 {shown_pid:04X}"),
           "dongle_pid": f"{dev.pid:04X}",
           "name": dev.name}

    def safe(fn, key, default=None):
        for attempt in (0, 1):
            try:
                val = fn()
            except OSError as e:
                # fd 失效（设备重枚举/休眠）——丢掉句柄，下次自动重连
                out.setdefault("errors", {})[key] = str(e)
                reset_handle()
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

    safe(m.firmware, "firmware")
    safe(m.profile, "profile")
    safe(m.sleep_time, "sleep_sec")
    safe(lambda: _seconds_to_minutes(out.get("sleep_sec")), "sleep")
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
        out["polling_options"] = [125, 250, 500, 1000]
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


def do_set(key, value):
    dev, m = ensure_device()
    if m is None:
        return False, "设备未连接"

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
        # 改回报率会让接收器重置 USB 链路，节点会短时间消失，必须丢掉旧句柄
        reset_handle()
        time.sleep(1.2)
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
        return False, f"未知设置项 {key}"
    return True, None


def main():
    # 确保 stdout 是行缓冲，否则扩展等不到响应
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"bad json: {e}"}), flush=True)
            continue

        rid = req.get("id")
        cmd = req.get("cmd")
        try:
            if cmd == "read_all":
                resp = {"id": rid, "ok": True, "data": read_all()}
            elif cmd == "set":
                ok, err = do_set(req["key"], req.get("value"))
                resp = {"id": rid, "ok": ok}
                if err:
                    resp["error"] = err
                else:
                    # 设置后立刻回读一次，让界面马上刷新
                    resp["data"] = read_all()
            elif cmd == "ping":
                resp = {"id": rid, "ok": True}
            elif cmd == "quit":
                print(json.dumps({"id": rid, "ok": True}), flush=True)
                break
            else:
                resp = {"id": rid, "ok": False, "error": f"unknown cmd {cmd}"}
        except Exception as e:
            resp = {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
