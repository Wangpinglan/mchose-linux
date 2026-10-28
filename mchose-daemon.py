#!/usr/bin/env python3
"""mchose-daemon — 供 GNOME 扩展调用的 HID 辅助进程。

比扩展直接做 ioctl 更省事：GJS 没有暴露 open(2)/ioctl，而 Python 有。
进程常驻，通过 stdin 读 JSON 命令、stdout 回 JSON 结果，一行一个。

协议：
    请求  {"id": 1, "cmd": "read_all"}          -> 读所有状态
          {"id": 2, "cmd": "set", "key": "dpi", "value": [...] }
    响应  {"id": 1, "ok": true, "data": {...}}
          {"id": 2, "ok": false, "error": "..."}

实际的状态读写逻辑在 mchose_api.py，和 GTK 应用共用。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mchose  # noqa: E402
import mchose_api  # noqa: E402


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


def read_all():
    try:
        dev, m = ensure_device()
    except PermissionError as e:
        # 没权限时不要退出进程，把原因带回界面
        reset_handle()
        return {"connected": False, "need_auth": True, "error": str(e)}
    if dev is None:
        return {"connected": False}
    return mchose_api.read_all(m, dev, reset=reset_handle)


def do_set(key, value):
    dev, m = ensure_device()
    if m is None:
        return False, "设备未连接"

    try:
        mchose_api.do_set(m, key, value)
    except ValueError as e:
        return False, str(e)

    if key == "polling":
        # 改回报率会让接收器重置 USB 链路，节点会短时间消失，必须丢掉旧句柄
        reset_handle()
        time.sleep(1.2)
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
