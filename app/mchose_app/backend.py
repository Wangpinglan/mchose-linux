"""backend — 把 mchose 的 HID 操作包成线程安全的小接口。

界面线程永远不直接碰 ioctl：读取和写入都丢到后台线程，
结果再通过 GLib.idle_add 回到主循环。
"""

import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# 源码树：app/mchose_app -> 仓库根；安装后：~/.local/lib/mchose-app/mchose_app -> 同目录
for _p in (
    os.environ.get("MCHOSE_LIB"),
    os.path.dirname(_HERE),
    os.path.dirname(os.path.dirname(_HERE)),
):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import mchose          # noqa: E402
import mchose_api      # noqa: E402


class NoDevice(Exception):
    """没有找到（或没插）MCHOSE 设备。"""


class Controller:
    """持有 hidraw 句柄，设备换节点（有线/无线切换）时自动重连。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._m = None
        self._dev = None

    # ------------------------------------------------------------ 内部
    def _close_locked(self):
        if self._m is not None:
            try:
                self._m.close()
            except Exception:
                pass
        self._m = None
        self._dev = None

    def _ensure_locked(self):
        dev = mchose.pick_device()
        if dev is None:
            self._close_locked()
            raise NoDevice()
        if self._dev is None or self._dev.path != dev.path:
            self._close_locked()
            self._dev = dev
            # 没权限时 Mchose() 会抛 PermissionError，消息里带节点路径
            self._m = mchose.Mchose(dev)
        return self._m, self._dev

    # ------------------------------------------------------------ 对外
    def read(self):
        with self._lock:
            m, dev = self._ensure_locked()
            return mchose_api.read_all(m, dev, reset=self._close_locked)

    def apply(self, key, value):
        with self._lock:
            m, _dev = self._ensure_locked()
            mchose_api.do_set(m, key, value)
            if key == "polling":
                # 改回报率会让接收器重置 USB 链路，节点会短暂消失
                self._close_locked()
                time.sleep(1.2)

    def close(self):
        with self._lock:
            self._close_locked()
