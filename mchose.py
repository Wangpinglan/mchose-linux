#!/usr/bin/env python3
"""mchose-hid — MCHOSE A5 / AX5 Pro 系列鼠标的 Linux 配置工具。

协议来自厂商 Windows 驱动的逆向（见 PROTOCOL.md）。
使用标准 HID Feature Report，通过 /dev/hidraw* 直接通信，无需内核驱动。

用法示例：
    ./mchose.py list                    # 扫描已连接的 MCHOSE 设备
    ./mchose.py info                    # 读固件版本 / PID / profile
    ./mchose.py dpi                     # 读所有 DPI 档位
    ./mchose.py dpi --set 1:1600        # 把第 2 档设为 1600
    ./mchose.py polling                 # 读回报率
    ./mchose.py polling 1000            # 设回报率
    ./mchose.py sleep                   # 读休眠时间
    ./mchose.py sleep 15                # 设 15 分钟
    ./mchose.py debounce                # 读按键消抖
    ./mchose.py debounce 4              # 设 4ms
    ./mchose.py lod                     # 读静默高度
    ./mchose.py lod 1                   # 1mm / 2mm
    ./mchose.py anglesnap on|off
    ./mchose.py profile 2               # 切到 2 号配置文件
    ./mchose.py battery                 # 读电量
    ./mchose.py raw 2 1 0x81 0          # 发原始帧（调试用）
"""

import argparse
import fcntl
import glob
import os
import struct
import sys
import time

# ---------------------------------------------------------------- 常量

VID_MCHOSE = 0x2023

# 已知型号（PID -> 名称）
KNOWN_PIDS = {
    0xF017: "A5 Pro",
    0xF013: "A5 Pro / Pro Max",
    0xF019: "A5 Pro Max",
    0xF026: "AX5 Pro",
    0xF024: "AX5 Pro / Pro Max",
    0xF022: "AX5 Pro Max",
    0xF016: "A5 Pro (Bootloader)",
    0xF012: "A5 (Bootloader 无线)",
    0xF018: "A5 Pro Max (Bootloader)",
    0xF025: "AX5 Pro (Bootloader)",
    0xF023: "AX5 (Bootloader 无线)",
    0xF021: "AX5 Pro Max (Bootloader)",
    0xF015: "通用 4K 接收器",
}

# hidraw ioctl
HIDIOCGFEATURE = lambda n: (0xC0000000 | (n << 16) | (ord("H") << 8) | 0x07)
HIDIOCSFEATURE = lambda n: (0xC0000000 | (n << 16) | (ord("H") << 8) | 0x06)
HIDIOCGRAWINFO = 0x80084803
HIDIOCGRAWNAME = lambda n: (0x80000000 | (n << 16) | (ord("H") << 8) | 0x04)

PACKET_LEN = 65          # Feature Report 总长（含 report id 占位）
CMD_DELAY = 0.030        # SetFeature 后等 30ms 再读，与驱动一致
MAX_DPI_STAGES = 6       # 固件固定 6 档

# 回报率代码 <-> Hz
# 来自反编译的 HomePageViewModel.Check1K4K8K：125=8, 250=4, 500=2,
# 1000=1, 2000=0x20, 4000=0x40, 8000=0x80。低位那组是**反的**，别按直觉写。
POLLING_CODES = {
    0x08: 125, 0x04: 250, 0x02: 500, 0x01: 1000,
    0x20: 2000, 0x40: 4000, 0x80: 8000,
}
POLLING_HZ_TO_CODE = {v: k for k, v in POLLING_CODES.items()}
POLLING_UNSET = 0x00      # 设备从没设置过回报率时回这个码，不是 0 Hz
# 每型号支持的回报率档位，抄自驱动自带的 Config/xvi_models.xlsx
# （有线 PollingRate / 无线 PollingRate 两列）。这是权威来源：
# 接收器是"通用"的（F015 同时服务 A5 Pro 和 A5 Pro Max），
# 能跑多高取决于鼠标型号，不是接收器名字。所以 4K 接收器插 A5 Pro
# 也只有 125/250/500/1000，**不该出现 2K/4K/8K**。
MODEL_POLLING = {
    0xF017: {"wired": [125, 500, 1000],
             "wireless": [125, 250, 500, 1000]},                    # A5 Pro
    0xF019: {"wired": [125, 500, 1000],
             "wireless": [125, 250, 500, 1000]},                    # A5 Pro Max
    0xF026: {"wired": [125, 500, 1000],
             "wireless": [125, 500, 1000, 2000, 4000]},             # AX5 Pro
    0xF022: {"wired": [125, 500, 1000, 2000, 4000, 8000],
             "wireless": [125, 500, 1000, 2000, 4000, 8000]},       # AX5 Pro Max
}

# 这些 PID 是接收器/无线链路，枚举到它们说明鼠标走的是 2.4G
DONGLE_PIDS = {0xF012, 0xF013, 0xF015, 0xF023, 0xF024}

# 接收器 PID -> 它本身支持的回报率（型号表 T/U 两列：1K;4K / F013;F015）
# F024 在表里没有对应的 1K/4K/8K 名，说明 AX5 系列不走"金接收器"追加逻辑。
DONGLE_RATE_BY_PID = {
    0xF013: 1000,     # 1K 通用接收器
    0xF015: 4000,     # 4K 通用接收器
}

DEFAULT_SLEEP_MIN = 15       # 全系默认休眠 15 分钟（型号表 W 列）


def golden_polling(base, dongle_hz):
    """复刻驱动 HomePageViewModel.GetGoldenDonglePollingRate。

    规则（原样照抄，别"优化"）：
      1. 基础列表里**严格小于**接收器上限的项全部保留；
      2. 上限高于 2000 且列表里没有 2000，就补一个 2000；
      3. 最后把接收器上限自己追加进去。

    所以 A5 Pro 的无线基础列表 125;250;500;1000 配上 4K 接收器，
    结果是 125;250;500;1000;2000;4000 —— 2000/4000 是接收器带来的，
    不在鼠标型号自己的列表里。这也解释了为什么 Windows 上能选到 4K。
    """
    if not dongle_hz:
        return list(base)
    out = [h for h in base if h < dongle_hz]
    if dongle_hz > 2000 and 2000 not in out:
        out.append(2000)
    out.append(dongle_hz)
    return sorted(set(out))


def polling_options(model_pid=None, link="wireless", dongle_hz=None):
    """返回该型号 + 该接收器组合下支持的回报率列表。"""
    row = MODEL_POLLING.get(model_pid or 0)
    base = list(row.get(link) or row["wireless"]) if row else [125, 250, 500, 1000]
    if link == "wireless" and dongle_hz:
        base = golden_polling(base, dongle_hz)
    return base


def max_polling_from_name(name):
    """从产品名推断接收器上限，拿不到 PID 映射时的兜底。"""
    n = (name or "").upper()
    for tag, hz in (("8K", 8000), ("4K", 4000), ("2K", 2000)):
        if tag in n:
            return hz
    return 1000


def fmt_polling(hz):
    return "未知" if hz is None else f"{hz} Hz"


def fmt_sleep(sec):
    """休眠时间：设备存的是秒，人看的是分钟。"""
    if sec is None:
        return "未知"
    sec = int(sec)
    m, s = divmod(sec, 60)
    if s == 0:
        return f"{m} 分钟"
    return f"{m} 分 {s} 秒"


# ---------------------------------------------------------------- 设备发现

class Device:
    def __init__(self, path, vid, pid, name):
        self.path = path
        self.vid = vid
        self.pid = pid
        self.name = name

    @property
    def label(self):
        model = KNOWN_PIDS.get(self.pid, "未知型号")
        return f"{self.path}  VID:PID={self.vid:04X}:{self.pid:04X}  {model}  ({self.name})"

    def open(self):
        return Hijack(self.path)


def _read_sys_attr(hidraw, attr):
    p = f"/sys/class/hidraw/{os.path.basename(hidraw)}/device/{attr}"
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def scan(verbose=False):
    """枚举所有 MCHOSE 的 hidraw 节点，并标出哪个是 65 字节的配置接口。"""
    out = []
    for path in sorted(glob.glob("/dev/hidraw*")):
        info = _read_sys_attr(path, "uevent") or ""
        vid = pid = None
        for line in info.splitlines():
            if line.startswith("HID_ID="):
                # 格式: BUS:VID:PID
                parts = line.split("=", 1)[1].split(":")
                if len(parts) == 3:
                    vid, pid = int(parts[1], 16), int(parts[2], 16)
        if vid != VID_MCHOSE:
            continue
        name = ""
        for line in info.splitlines():
            if line.startswith("HID_NAME="):
                name = line.split("=", 1)[1]
        dev = Device(path, vid, pid, name)
        dev.feature_len = feature_report_len(path)
        out.append(dev)
    return out


def feature_report_len(path):
    """用 report descriptor 里声明的长度判断；失败则返回 None。"""
    p = f"/sys/class/hidraw/{os.path.basename(path)}/device/report_descriptor"
    try:
        with open(p, "rb") as f:
            desc = f.read()
    except OSError:
        return None
    # 粗略解析：找 Feature(0xB1/0xB2) 项，取其 report size/count
    # 这里只做启发式判断：是否存在 65*8=520 位的 feature 字段
    i = 0
    size_bits = 0
    count = 1
    max_feature = 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item
            if i + 1 >= len(desc):
                break
            dsize = desc[i + 1]
            i += 3 + dsize
            continue
        bSize = b & 0x03
        bType = (b >> 2) & 0x03
        bTag = b >> 4
        n = 0 if bSize == 3 else bSize
        if bSize == 3:
            val = 0
        else:
            val = int.from_bytes(desc[i + 1:i + 1 + n], "little") if n else 0
        if bType == 1:  # Global
            if bTag == 7:
                size_bits = val
            elif bTag == 9:
                count = val or 1
            elif bTag == 8:
                rid = val
        elif bType == 0 and bTag == 0xC:  # Feature
            total = size_bits * count // 8
            if total > max_feature:
                max_feature = total
        i += 1 + n
    return max_feature or None


def pick_device(prefer_path=None):
    devs = scan()
    if not devs:
        return None
    if prefer_path:
        for d in devs:
            if d.path == prefer_path:
                return d
        return None
    # 优先选 feature report 长度 == 64 的那个（配置接口）
    for d in devs:
        if getattr(d, "feature_len", 0) >= 64:
            return d
    # 退而求其次：忽略 bootloader
    for d in devs:
        if d.pid not in (0xF016, 0xF012, 0xF018, 0xF025, 0xF021, 0xF023):
            return d
    return devs[0]


# ---------------------------------------------------------------- HID 收发

class Hijack:
    """一个 hidraw 句柄。"""

    def __init__(self, path):
        self.path = path
        try:
            self.fd = os.open(path, os.O_RDWR)
        except PermissionError:
            sys.exit(
                f"没有权限打开 {path}。\n"
                f"把当前用户加入 input 组并重登，或装一条 udev 规则：\n"
                f'  echo \'SUBSYSTEM=="hidraw", ATTRS{{idVendor}}=="2023", MODE="0660", GROUP="input"\' \\\n'
                f"    | sudo tee /etc/udev/rules.d/99-mchose.rules\n"
                f"  sudo udevadm control --reload && sudo udevadm trigger"
            )
        self.lock()

    def lock(self):
        try:
            fcntl.ioctl(self.fd, 0x4004480B)  # HIDIOCREQ? 保守起见跳过
        except OSError:
            pass

    def send(self, buf):
        b = bytes(buf).ljust(PACKET_LEN, b"\x00")[:PACKET_LEN]
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(b)), b)

    def recv(self, length=PACKET_LEN):
        buf = bytearray(length)
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(length), buf, True)
        return bytes(buf)

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ---------------------------------------------------------------- 协议层

class Mchose:
    def __init__(self, dev: Device):
        self.dev = dev
        self.h = dev.open()

    # ---------- 底层帧 ----------

    def xfer(self, group, code, params=b"", length=None, retries=1):
        """发一帧写命令，再读回应答。

        params 从 [7] 开始填。length 默认 = len(params)。
        """
        if length is None:
            length = len(params)
        cmd = [0] * PACKET_LEN
        cmd[3] = 2                     # 方向：需应答
        cmd[4] = length
        cmd[5] = group
        cmd[6] = code
        for i, v in enumerate(params):
            cmd[7 + i] = v

        resp = None
        for _ in range(retries):
            self.h.send(cmd)
            time.sleep(CMD_DELAY)
            resp = self.h.recv()
            if resp[1] == 0xA1:
                return resp
        return resp

    def xfer_read(self, group, code, params=b"", length=None):
        """读命令：命令码自动 |0x80。"""
        return self.xfer(group, code | 0x80, params, length)

    def xfer_write(self, group, code, params=b"", length=None):
        return self.xfer(group, code, params, length)

    # ---------- 高层操作 ----------

    def firmware(self):
        r = self.xfer_read(0, 0x01, length=1)
        if not r:
            return None
        return f"{r[7]:02d}.{r[8]:02d}.{r[9]:02d}.{r[10]:02d}"

    def pid(self):
        """设备 PID（鼠标自己的，不是接收器的）。

        固件应答的 [11][12] 是可靠来源；0x86 在部分接收器上返回无意义值，
        所以只把它当备选，且必须能在型号表里对上。
        """
        cands = []
        r = self.xfer_read(0, 0x01, length=16)
        if r and r[1] == 0xA1:
            cands.append((r[11] << 8) | r[12])
        r = self.xfer_read(0, 0x06, length=2)
        if r and r[1] == 0xA1:
            cands.append((r[7] << 8) | r[8])
        for v in cands:
            if v in KNOWN_PIDS:
                return v
        return cands[0] if cands and cands[0] else None

    def link_mode(self):
        """wired / wireless —— 看枚举到的 PID 是鼠标本体还是接收器。"""
        return "wireless" if self.dev.pid in DONGLE_PIDS else "wired"

    def polling_code(self):
        """回报率的原始代码。读不到返回 None。

        实测：出厂没设过回报率时设备回 0x00（不是 0 Hz，也不在码表里）；
        随便写一档之后就能正常回读（写 1000 后回 0x01）。所以 0x00 一律
        当"未设置"处理。
        """
        r = self.xfer_read(1, 0x00, length=1)
        if not r or r[1] != 0xA1:
            return None
        return r[7]

    def polling(self):
        """当前回报率 Hz。设备没回报有效代码时返回 None。"""
        code = self.polling_code()
        return POLLING_CODES.get(code) if code is not None else None

    def dongle_hz(self):
        """当前接收器自身的回报率上限；有线直连或认不出时返回 None。"""
        if self.dev.pid not in DONGLE_PIDS:
            return None
        hz = DONGLE_RATE_BY_PID.get(self.dev.pid)
        if hz:
            return hz
        # 表里没有的接收器 PID（如 F024）就走产品名推断
        return max_polling_from_name(self.dev.name)

    def polling_supported(self):
        model_pid = None
        try:
            model_pid = self.pid()
        except Exception:
            pass
        link = self.link_mode()
        return polling_options(model_pid or self.dev.pid, link,
                               self.dongle_hz() if link == "wireless" else None)

    def max_polling(self):
        return max(self.polling_supported())

    def set_polling(self, hz):
        code = POLLING_HZ_TO_CODE.get(hz)
        if code is None:
            raise ValueError(f"不支持的回报率 {hz}，可选: {sorted(POLLING_HZ_TO_CODE)}")
        allowed = self.polling_supported()
        if hz not in allowed:
            raise ValueError(
                f"该型号{self.link_mode() == 'wired' and '有线' or '无线'}模式"
                f"只支持 {allowed}，不能设 {hz}")
        self.xfer_write(1, 0x00, bytes([code]), length=1)

    def profile(self):
        """当前配置文件，**1 基**（1..5）。

        注意是组 0 的 0x85，不是组 1；读到组 1 会永远返回 0。
        """
        r = self.xfer_read(0, 0x05, length=1)
        return r[7] if r else None

    def set_profile(self, p):
        if not 1 <= p <= 5:
            raise ValueError("配置文件编号是 1..5")
        r = self.xfer_write(0, 0x05, bytes([p]), length=1)
        return r is not None and r[1] == 0xA1

    def sleep_time(self):
        """休眠时间，单位**秒**（驱动里 trackbar 范围 60..1800）。"""
        r = self.xfer_read(0, 0x07, length=2)
        if not r or r[1] != 0xA1:
            return None
        return (r[7] << 8) | r[8]

    def set_sleep_time(self, seconds):
        """写休眠时间，单位秒。"""
        seconds = max(60, min(1800, int(seconds)))
        self.xfer_write(0, 0x07,
                        bytes([(seconds >> 8) & 0xFF, seconds & 0xFF]), length=2)

    def debounce(self, profile=None):
        """按键消抖，单位 ms。"""
        p = self.profile() if profile is None else profile
        r = self.xfer_read(0, 0x08, bytes([p, 0]), length=2)
        if not r or r[1] != 0xA1:
            return None
        return r[8]

    def set_debounce(self, ms, profile=None):
        p = self.profile() if profile is None else profile
        self.xfer_write(0, 0x08, bytes([p, int(ms)]), length=2)

    def lod(self):
        """静默高度，单位**毫米**（1 或 2），不是 0/1 索引。"""
        r = self.xfer_read(1, 0x08, length=1)
        if not r or r[1] != 0xA1:
            return None
        return r[7]

    def set_lod(self, mm):
        self.xfer_write(1, 0x08, bytes([int(mm)]), length=1)

    def angle_snap(self):
        r = self.xfer_read(1, 0x04, length=1)
        return r[7] if r else None

    def set_angle_snap(self, on):
        self.xfer_write(1, 0x04, bytes([1 if on else 0]), length=1)

    def dpi_stages(self, profile=None):
        """读 DPI 档位。

        返回 (当前激活档位, [dpi, dpi, ...])。

        请求帧（对照驱动 GetDPIStageInfo）：
            [4]=10  [5]=1  [6]=0x81  [7]=profile  [8]=要读的档数(6)
        应答帧：
            [7]            = profile
            [8]            = 实际档数 N
            [9+4i][10+4i]  = 第 i 档 DPI，大端 16 位
            [11+4i][12+4i] = 同一个 DPI 值的重复（不是颜色）
        颜色是另一条命令，见 dpi_colors()。
        """
        p = self.profile() if profile is None else profile
        r = self.xfer_read(1, 0x01, bytes([p, MAX_DPI_STAGES]), length=10)
        if not r or r[1] != 0xA1:
            return None, []
        count = min(r[8], MAX_DPI_STAGES)
        stages = []
        for i in range(count):
            base = 9 + 4 * i
            dpi = (r[base] << 8) | r[base + 1]
            if dpi:                       # 空档位是 0，跳过
                stages.append(dpi)
        r2 = self.xfer_read(1, 0x02, bytes([p]), length=2)
        # 硬件返回的是 1 基档号，这里统一转成 0 基给上层用
        active = (r2[8] - 1) if r2 and r2[1] == 0xA1 and r2[8] >= 1 else None
        return active, stages

    def dpi_colors(self, profile=None):
        """读各档颜色。返回 [(r,g,b), ...]，最多 6 组。

        组 2、码 0x81，[4]=19（1 + 6*3），颜色从 [8] 起每档 3 字节。
        """
        p = self.profile() if profile is None else profile
        r = self.xfer_read(2, 0x01, bytes([p]), length=19)
        if not r or r[1] != 0xA1:
            return []
        out = []
        for i in range(MAX_DPI_STAGES):
            base = 8 + 3 * i
            out.append((r[base], r[base + 1], r[base + 2]))
        return out

    def set_dpi_stages(self, stages, profile=None):
        """写入 DPI 档位。

        stages: [dpi, ...]，1..6 档。
        写帧：组 1、码 0x01，载荷 = [profile, N, (dpi 大端 2 字节) * 2 * N]
        每档 4 字节，值是重复两遍（驱动里两个相同数组各填一次）。
        """
        p = self.profile() if profile is None else profile
        if not 1 <= len(stages) <= MAX_DPI_STAGES:
            raise ValueError(f"DPI 档位数量必须在 1..{MAX_DPI_STAGES} 之间")
        payload = bytearray([p, len(stages)])
        for dpi in stages:
            dpi = int(dpi)
            if not 0 < dpi <= 0xFFFF:
                raise ValueError(f"DPI 超出范围: {dpi}")
            pair = bytes([(dpi >> 8) & 0xFF, dpi & 0xFF])
            payload += pair + pair      # 每档重复两次，共 4 字节
        self.xfer_write(1, 0x01, bytes(payload), length=len(payload))

    def set_dpi_colors(self, colors, profile=None):
        """写入各档颜色。colors: [(r,g,b), ...]，按档数补齐到 6 组。"""
        p = self.profile() if profile is None else profile
        payload = bytearray([p])
        for i in range(MAX_DPI_STAGES):
            c = list(colors[i]) + [0, 0, 0] if i < len(colors) else [0, 0, 0]
            payload += bytes(c[:3])
        self.xfer_write(2, 0x01, bytes(payload), length=len(payload))

    def set_active_dpi(self, stage, profile=None):
        """stage 是 0 基索引，硬件要 1 基。"""
        p = self.profile() if profile is None else profile
        self.xfer_write(1, 0x02, bytes([p, stage + 1]), length=2)

    def battery(self):
        """读电量。返回 (充电状态字节, 电量百分比) 或 None。

        命令组 0、码 0x83。
        """
        r = self.xfer_read(0, 0x03, length=2)
        if not r or r[1] != 0xA1:
            return None
        return r[7], r[8]

    def reset_default(self):
        self.xfer_write(2, 0xC0, bytes([0x01]), length=1)

    def close(self):
        self.h.close()


# ---------------------------------------------------------------- CLI

def fmt_bool(v):
    return "开" if v else "关"


def cmd_list(args):
    devs = scan()
    if not devs:
        print("没有找到 MCHOSE 设备（VID 2023）。")
        print("检查：鼠标是否插好/已唤醒、是否在 2.4G 模式。")
        return 1
    print(f"找到 {len(devs)} 个 hidraw 节点：")
    for d in devs:
        flen = getattr(d, "feature_len", None)
        mark = "  <== 配置接口" if flen == PACKET_LEN else ""
        print(f"  {d.label}  feature={flen}{mark}")
    return 0


def cmd_info(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        print(f"设备: {dev.label}")
        bat = m.battery()
        print(f"  固件版本: {m.firmware()}")
        print(f"  设备 PID: {m.pid():04X}" if m.pid() else "  设备 PID: ?")
        print(f"  电量: {bat[1]}%" if bat else "  电量: N/A")
        print(f"  当前配置文件: {m.profile()}")
        hz = m.polling()
        print(f"  回报率: {fmt_polling(hz)}（{m.link_mode()}，可选 "
              f"{'/'.join(str(x) for x in m.polling_supported())}）")
        sec = m.sleep_time()
        print(f"  睡眠时间: {fmt_sleep(sec)}")
        print(f"  按键消抖: {m.debounce()} ms")
        print(f"  静默高度: {m.lod()}mm")
        print(f"  直线修正: {fmt_bool(m.angle_snap())}")
    with_dev(dev, run)
    return 0


def cmd_dpi(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        active, stages = m.dpi_stages()
        if args.set:
            if not stages:
                print("读取现有档位失败，为安全起见不写入。")
                return
            new = list(stages)
            for item in args.set:
                stage_s, dpi_s = item.split(":")
                idx, val = int(stage_s), int(dpi_s)
                if not 0 <= idx < len(new):
                    print(f"档位 {idx} 不存在（当前共 {len(new)} 档）")
                    return
                new[idx] = val
            m.set_dpi_stages(new)
            print("已写入 DPI 档位：")
            active, stages = m.dpi_stages()
        if args.switch is not None:
            m.set_active_dpi(args.switch)
            print(f"已切换到第 {args.switch + 1} 档")
            time.sleep(0.1)
            active, stages = m.dpi_stages()
        if active is None and not stages:
            print("读取失败 —— 设备可能休眠，动一下鼠标再试。")
            return
        colors = m.dpi_colors()
        print(f"配置文件 {m.profile()}  当前激活档位: 第 {(active or 0) + 1} 档")
        for i, dpi in enumerate(stages):
            mark = " <== 当前" if i == active else ""
            cs = "#%02X%02X%02X" % colors[i] if i < len(colors) else "?"
            print(f"  第 {i + 1} 档: {dpi:5d} DPI   颜色 {cs}{mark}")
    with_dev(dev, run)
    return 0


def cmd_polling(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        if args.value:
            m.set_polling(args.value)
            print(f"已设置回报率 {args.value} Hz")
            time.sleep(0.8)
        hz = m.polling()
        print(f"当前回报率: {fmt_polling(hz)}")
        if hz is None and m.polling_code() == POLLING_UNSET:
            print("  设备回报代码 0x00：还没设置过回报率（不是 0 Hz）。"
                  "选一档写进去就能正常回读了。")
        print(f"  可选: {'/'.join(str(x) for x in m.polling_supported())}"
              f"（{m.link_mode()}）")
    with_dev(dev, run)
    return 0


def cmd_sleep(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        if args.value is not None:
            m.set_sleep_time(int(args.value) * 60)
            print(f"已设置睡眠时间 {args.value} 分钟")
            time.sleep(0.1)
        sec = m.sleep_time()
        print(f"当前睡眠时间: {fmt_sleep(sec)}"
              + (f"（{sec} 秒）" if sec is not None else ""))
    with_dev(dev, run)
    return 0


def cmd_debounce(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        if args.value is not None:
            m.set_debounce(args.value)
            print(f"已设置按键消抖 {args.value} ms")
            time.sleep(0.1)
        print(f"当前按键消抖: {m.debounce()} ms")
    with_dev(dev, run)
    return 0


def cmd_lod(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        if args.value is not None:
            m.set_lod(args.value)
            print(f"已设置静默高度 {args.value}mm")
            time.sleep(0.1)
        print(f"当前静默高度: {m.lod()}mm")
    with_dev(dev, run)
    return 0


def cmd_anglesnap(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        if args.state:
            m.set_angle_snap(args.state == "on")
            time.sleep(0.1)
        print(f"直线修正: {fmt_bool(m.angle_snap())}")
    with_dev(dev, run)
    return 0


def cmd_profile(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        if args.value is not None:
            m.set_profile(args.value)
            time.sleep(0.1)
        print(f"当前配置文件: {m.profile()}")
    with_dev(dev, run)
    return 0


def cmd_battery(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def run(m):
        r = m.battery()
        if r:
            status, pct = r
            print(f"电量: {pct}%   (状态字节 0x{status:02x})")
        else:
            print("读不到电量 —— 可能有线模式下不供电量，或者设备休眠了。")
    with_dev(dev, run)
    return 0


def cmd_raw(args):
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)
    params = bytes(int(x, 0) for x in args.params) if args.params else b""
    length = args.len if args.len is not None else len(params)

    def run(m):
        print(f"发送: group={args.group} code=0x{args.code:02x} len={length} params={params.hex()}")
        r = m.xfer(args.group, args.code, params, length=length)
        print(f"应答: {r.hex()}")
        print(f"  [1]={r[1]:02x} (A1=成功)  [7..]={r[7:].hex()}")
    with_dev(dev, run)
    return 0


def cmd_probe(args):
    """把 DPI 相关的收发帧全部原样打印，用于排查协议不符。"""
    dev = pick_device(args.dev)
    if not dev:
        return cmd_list(args)

    def hexdump(tag, r):
        if not r:
            print(f"{tag}: <无应答>")
            return
        print(f"{tag}: status=0x{r[1]:02x}")
        for off in range(0, 36, 12):
            chunk = r[off:off + 12]
            print(f"   [{off:02d}] " + " ".join(f"{b:02x}" for b in chunk))

    def run(m):
        p = m.profile()
        print(f"当前配置文件: {p}\n")
        hexdump("DPI 档位 (g1 c0x81, len=10, [8]=6)",
                m.xfer_read(1, 0x01, bytes([p, MAX_DPI_STAGES]), length=10))
        print()
        hexdump("激活档   (g1 c0x82, len=2)",
                m.xfer_read(1, 0x02, bytes([p]), length=2))
        print()
        hexdump("档位颜色 (g2 c0x81, len=19)",
                m.xfer_read(2, 0x01, bytes([p]), length=19))
        print()
        hexdump("回报率   (g1 c0x80, len=1)",
                m.xfer_read(1, 0x00, length=1))
        print()
        hexdump("休眠时间 (g0 c0x87, len=2)",
                m.xfer_read(0, 0x07, length=2))
        print()
        code = m.polling_code()
        sec = m.sleep_time()
        print(f"解析: 回报率代码=0x{code:02x} -> {fmt_polling(m.polling())}"
              if code is not None else "解析: 回报率读取失败")
        print(f"      休眠={sec} 秒（{fmt_sleep(sec)}）")
        print(f"      链路={m.link_mode()}  可选回报率="
              f"{m.polling_supported()}")
        active, stages = m.dpi_stages()
        print(f"解析: 激活={active}  档位={stages}")
        print(f"颜色: {m.dpi_colors()}")
    with_dev(dev, run)
    return 0


def with_dev(dev, fn):
    m = Mchose(dev)
    try:
        fn(m)
    except OSError as e:
        print(f"HID 通信失败: {e}")
        print("可能原因：设备休眠（动一下鼠标）、接口被独占、或 PID 不是配置接口。")
    finally:
        m.close()


def main():
    ap = argparse.ArgumentParser(
        description="MCHOSE A5/AX5 Pro 系列鼠标 Linux 配置工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--dev", help="指定 hidraw 节点，如 /dev/hidraw5")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list", help="扫描设备").set_defaults(func=cmd_list)
    sub.add_parser("info", help="读取设备信息").set_defaults(func=cmd_info)

    p = sub.add_parser("dpi", help="DPI 档位")
    p.add_argument("--set", nargs="+", metavar="档位:DPI")
    p.add_argument("--switch", type=int, metavar="档位")
    p.set_defaults(func=cmd_dpi)

    p = sub.add_parser("polling", help="回报率")
    p.add_argument("value", nargs="?", type=int)
    p.set_defaults(func=cmd_polling)

    p = sub.add_parser("sleep", help="休眠时间（分钟）")
    p.add_argument("value", nargs="?", type=int)
    p.set_defaults(func=cmd_sleep)

    p = sub.add_parser("debounce", help="按键消抖（ms）")
    p.add_argument("value", nargs="?", type=int)
    p.set_defaults(func=cmd_debounce)

    p = sub.add_parser("lod", help="静默高度，单位 mm（1 或 2）")
    p.add_argument("value", nargs="?", type=int)
    p.set_defaults(func=cmd_lod)

    p = sub.add_parser("anglesnap", help="直线修正")
    p.add_argument("state", nargs="?", choices=["on", "off"])
    p.set_defaults(func=cmd_anglesnap)

    p = sub.add_parser("profile", help="配置文件切换")
    p.add_argument("value", nargs="?", type=int)
    p.set_defaults(func=cmd_profile)

    sub.add_parser("battery", help="电量").set_defaults(func=cmd_battery)

    p = sub.add_parser("raw", help="发原始帧")
    p.add_argument("group", type=int)
    p.add_argument("code", type=lambda x: int(x, 0))
    p.add_argument("params", nargs="*")
    p.add_argument("--len", type=int)
    p.set_defaults(func=cmd_raw)

    sub.add_parser("dpiprobe", help="打印 DPI 收发原始帧（排查用）").set_defaults(
        func=cmd_probe)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
