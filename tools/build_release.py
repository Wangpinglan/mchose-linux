#!/usr/bin/env python3
"""把扩展打包成单文件安装脚本（内嵌 zip）+ 独立 zip。

用法：
    python3 tools/build_release.py

产物：
    dist/install-mchose.sh                     ← 单文件，拷到别的机器直接跑
    dist/mchose-mouse@local.shell-extension.zip ← 标准扩展包，可手动安装
"""
import base64
import os
import shutil
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dist" / ".src"
DIST = ROOT / "dist"
UUID = "mchose-mouse@local"
EXTENSION = ROOT / "extension"
EXTENSION = ROOT / "extension"

# 扩展运行必需的文件（README 之类不进包）
FILES = [
    "metadata.json",
    "extension.js",
    "impl.js",
    "daemon.js",
    "stylesheet.css",
    "mchose.py",
    "mchose-daemon.py",
]
EXECUTABLE = {"mchose.py", "mchose-daemon.py"}

UDEV_RULE = (
    'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2023", '
    'MODE="0660", GROUP="input", TAG+="uaccess"'
)

TEMPLATE = r"""#!/usr/bin/env bash
# MCHOSE 鼠标控制面板 —— Fedora / GNOME 一键安装
#
# 用法：
#   bash install-mchose.sh            安装或更新
#   bash install-mchose.sh --no-udev  只装扩展，不动系统（没有 sudo 时用）
#   bash install-mchose.sh --uninstall 卸载（配 --no-udev 则保留 udev 规则）
#
# 内含扩展本体（zip，base64 内嵌）与 hidraw udev 规则，不需要联网。

set -euo pipefail

UUID="@@UUID@@"
PY_MIN="3.6"
VER="@@VER@@"

C_R=$'\033[0m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_E=$'\033[31m'; C_B=$'\033[1m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$C_G" "$C_R" "$*"; }
warn() { printf '%s  ! %s%s\n' "$C_Y" "$C_R" "$*" >&2; }
die()  { printf '%s错误：%s%s\n' "$C_E" "$C_R" "$*" >&2; exit 1; }
step() { printf '\n%s%s%s\n' "$C_B" "$*" "$C_R"; }

DO_UDEV=1
UNINSTALL=0
NEED_RELOGIN=0
for arg in "$@"; do
    case "$arg" in
        --no-udev)   DO_UDEV=0 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)   sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$arg" ;;
    esac
done

# ---------------------------------------------------------- sudo 误用保护
# 用户常常直接 `sudo ./install-mchose.sh`，此时 HOME=/root，扩展会装进 root 家目录，
# 桌面会话完全看不到（还把 enabled-extensions 写进 root 的 dconf）。
# 处理方式：先以 root 做完系统级动作，再降回真实用户重跑文件安装。
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] \
   && [ "${MCHOSE_REEXEC:-0}" != "1" ]; then
    step "检测到用 sudo 运行整个脚本"
    if [ ! -f "$0" ]; then
        die "请用普通用户运行，不要加 sudo。脚本只会在写 udev 规则时自己提权。"
    fi

    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)
    REAL_UID=$(id -u "$SUDO_USER" 2>/dev/null || true)
    if [ -z "${REAL_HOME:-}" ] || [ -z "${REAL_UID:-}" ] || [ ! -d "$REAL_HOME" ]; then
        die "找不到用户 $SUDO_USER 的家目录。请改用普通用户直接运行：bash $0"
    fi
    say "不需要 sudo 整个脚本：扩展要装进 $REAL_HOME，不是 /root。"
    say "先以 root 处理系统级配置，再切回 $SUDO_USER 装扩展。"

    if [ "$DO_UDEV" -eq 1 ]; then
        if [ "$UNINSTALL" -eq 1 ]; then
            if [ -f "$RULE" ]; then
                rm -f "$RULE"
                udevadm control --reload-rules 2>/dev/null || true
                ok "已删除 udev 规则"
            fi
        else
            printf '%s\n' "$RULE_TEXT" > "$RULE"
            udevadm control --reload-rules 2>/dev/null || true
            udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
            ok "udev 规则已写入 $RULE"
            # 兜底：部分系统 logind 不给 seat 会话补 uaccess ACL，
            # 只靠规则还是打不开设备，把用户加进 input 组（规则里 GROUP="input"）
            if id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx input; then
                ok "$SUDO_USER 已在 input 组"
            elif getent group input >/dev/null 2>&1; then
                if usermod -aG input "$SUDO_USER"; then
                    NEED_RELOGIN=1
                    ok "已把 $SUDO_USER 加入 input 组（重登后生效）"
                else
                    warn "加入 input 组失败，请手动执行：sudo usermod -aG input $SUDO_USER"
                fi
            else
                warn "系统没有 input 组，只能靠 uaccess 规则；若读不到设备请检查 udev"
            fi
        fi
    else
        say "已指定 --no-udev，跳过系统配置"
    fi

    say "切换到 $SUDO_USER 继续…"
    say ""
    sudo -u "$SUDO_USER" -H \
        env MCHOSE_REEXEC=1 \
            XDG_RUNTIME_DIR="/run/user/$REAL_UID" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$REAL_UID/bus" \
        bash "$0" "$@" --no-udev
    exit $?
fi

DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"
BIN="$HOME/.local/bin/mchose"
RULE=/etc/udev/rules.d/99-mchose.rules
# 单引号包裹：内容里有双引号，绝不能放进双引号字符串里，否则引号会被 shell 吃掉
RULE_TEXT='@@RULE@@'

# ---------------------------------------------------------------- 卸载
if [ "$UNINSTALL" -eq 1 ]; then
    step "卸载 MCHOSE 鼠标控制面板"
    if command -v gnome-extensions >/dev/null 2>&1; then
        gnome-extensions disable "$UUID" 2>/dev/null || true
    fi
    rm -rf "$DEST"
    ok "已删除扩展目录"
    if [ -f "$BIN" ]; then
        rm -f "$BIN"
        ok "已删除命令行工具"
    fi
    if [ "$DO_UDEV" -eq 1 ] && [ -f "$RULE" ]; then
        if [ "$(id -u)" -eq 0 ]; then
            rm -f "$RULE"
            if command -v udevadm >/dev/null 2>&1; then
                udevadm control --reload-rules 2>/dev/null || true
            fi
            ok "已删除 udev 规则"
        elif command -v sudo >/dev/null 2>&1; then
            sudo rm -f "$RULE"
            sudo udevadm control --reload-rules 2>/dev/null || true
            ok "已删除 udev 规则"
        else
            warn "没有权限删除 $RULE，请手动执行："
            warn "  sudo rm -f $RULE"
        fi
    fi
    say ""
    say "注销重登后图标才会消失。"
    exit 0
fi

# ---------------------------------------------------------------- 环境检查
step "检查环境"

[ -n "${HOME:-}" ] || die "HOME 未设置"
command -v python3 >/dev/null 2>&1 || die "找不到 python3（守护进程需要它）"
PY_VER=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
PY_OK=$(python3 -c "import sys;print(1 if sys.version_info[:2] >= tuple(int(x) for x in '$PY_MIN'.split('.')) else 0)")
[ "$PY_OK" = "1" ] || die "Python 版本过低（$PY_VER），需要 $PY_MIN 以上"
ok "Python $PY_VER"

if command -v gnome-shell >/dev/null 2>&1; then
    SHELL_VER=$(gnome-shell --version 2>/dev/null | grep -oE '[0-9]+' | head -1)
    case " 45 46 47 48 49 50 51 " in
        *" $SHELL_VER "*) ok "GNOME Shell $SHELL_VER" ;;
        *) warn "GNOME Shell $SHELL_VER 未在测试范围内（45–51），可能仍可用" ;;
    esac
else
    warn "没找到 gnome-shell，确认这是 GNOME 桌面吗"
fi

if [ "$(uname -m)" != "x86_64" ] && [ "$(uname -m)" != "aarch64" ]; then
    warn "架构 $(uname -m) 未测试过（协议与架构无关，大概率可用）"
fi

# 会话总线是否可达：在 tty / ssh 里跑时 gnome-extensions 会抛一堆 GLib 堆栈
SHELL_OK=0
if command -v gnome-extensions >/dev/null 2>&1; then
    if gnome-extensions list >/dev/null 2>&1; then
        SHELL_OK=1
    fi
fi

# ---------------------------------------------------------------- 解包
step "解出扩展包"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
base64 -d > "$TMP/ext.zip" <<'@@B64MARK@@'
@@B64@@
@@B64MARK@@

[ -s "$TMP/ext.zip" ] || die "内嵌数据解压失败（文件可能被截断）"
python3 -c "import zipfile,sys;sys.exit(0 if zipfile.is_zipfile('$TMP/ext.zip') else 1)" \
    || die "内嵌数据不是有效的 zip"

WAS_ENABLED=0
if [ "$SHELL_OK" -eq 1 ] && \
   gnome-extensions list --enabled 2>/dev/null | grep -qx "$UUID"; then
    WAS_ENABLED=1
    gnome-extensions disable "$UUID" >/dev/null 2>&1 || true
fi

rm -rf "$DEST"
mkdir -p "$DEST"
python3 - "$TMP/ext.zip" "$DEST" <<'PYEOF'
import os, sys, zipfile
zf, dest = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(zf) as z:
    z.extractall(dest)
    for info in z.infolist():
        mode = info.external_attr >> 16
        if mode & 0o111:
            p = os.path.join(dest, info.filename)
            if os.path.isfile(p):
                os.chmod(p, 0o755)
PYEOF

[ -f "$DEST/metadata.json" ] || die "解包后缺少 metadata.json"
[ -f "$DEST/impl.js" ] || die "解包后缺少 impl.js"
chmod +x "$DEST/mchose-daemon.py" 2>/dev/null || true
ok "已安装到 $DEST"

# 命令行工具
mkdir -p "$HOME/.local/bin"
install -m 755 "$DEST/mchose.py" "$BIN"
ok "命令行工具 $BIN"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) warn "$HOME/.local/bin 不在 PATH，需要在 ~/.bashrc 里加：
      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ---------------------------------------------------------------- udev
if [ "$DO_UDEV" -eq 1 ]; then
    step "配置设备访问权限"

    if [ "$(id -u)" -eq 0 ]; then
        printf '%s\n' "$RULE_TEXT" > "$RULE"
        udevadm control --reload-rules 2>/dev/null || true
        udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
        ok "已写入 $RULE"
    elif command -v sudo >/dev/null 2>&1; then
        say "需要 sudo 写 $RULE（只加一条 hidraw 权限规则）"
        if printf '%s\n' "$RULE_TEXT" | sudo tee "$RULE" >/dev/null; then
            sudo udevadm control --reload-rules 2>/dev/null || true
            sudo udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
            ok "已写入 $RULE"
            CUR_USER=$(id -un)
            if id -nG "$CUR_USER" | tr ' ' '\n' | grep -qx input; then
                ok "$CUR_USER 已在 input 组"
            elif getent group input >/dev/null 2>&1; then
                if sudo usermod -aG input "$CUR_USER"; then
                    NEED_RELOGIN=1
                    ok "已把 $CUR_USER 加入 input 组（重登后生效）"
                else
                    warn "加入 input 组失败，请手动执行：sudo usermod -aG input $CUR_USER"
                fi
            else
                warn "系统没有 input 组，只能靠 uaccess 规则；若读不到设备请检查 udev"
            fi
        else
            warn "写入规则失败，跳过。面板可能读不到设备。"
            warn "可稍后手动执行：echo '$RULE_TEXT' | sudo tee $RULE"
        fi
    else
        warn "没有 sudo，跳过 udev 规则。若读不到设备，请手动创建 $RULE："
        warn "  echo '$RULE_TEXT' | sudo tee $RULE"
    fi
else
    step "跳过 udev 配置（--no-udev）"
    warn "若面板显示「鼠标未连接」，补上这条规则后重新插拔鼠标："
    warn "  echo '$RULE_TEXT' | sudo tee $RULE"
fi

# ---------------------------------------------------------------- 启用
step "启用扩展"

# 把 uuid 补进启用列表：重登后 GNOME 会自动加载，用户不用再敲命令
add_to_enabled() {
    local cur new
    cur=$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null) || return 0
    case "$cur" in *"$UUID"*) return 0 ;; esac
    new=$(python3 - "$cur" "$UUID" <<'PYEND' 2>/dev/null
import ast, sys
raw = sys.argv[1].strip()
if raw.startswith('@as '):
    raw = raw[4:]
try:
    cur = ast.literal_eval(raw) if raw else []
except Exception:
    cur = []
if not isinstance(cur, list):
    cur = []
if sys.argv[2] not in cur:
    cur.append(sys.argv[2])
print("[" + ", ".join("'%s'" % u for u in cur) + "]")
PYEND
) || return 0
    [ -n "${new:-}" ] || return 0
    gsettings set org.gnome.shell enabled-extensions "$new" 2>/dev/null || true
    return 0
}

if [ "$SHELL_OK" -eq 0 ]; then
    add_to_enabled
    warn "连不上运行中的 GNOME 会话（可能是在 tty / SSH 里装的）"
    say "  已写入启用列表，注销重登后自动加载。"
elif gnome-extensions list 2>/dev/null | grep -qx "$UUID"; then
    # shell 已经认识这个 uuid：这是更新安装，可以立即重载
    if gnome-extensions enable "$UUID" 2>/dev/null; then
        ok "已启用，界面立即生效"
    else
        add_to_enabled
        warn "启用失败，已写入启用列表；注销重登后会加载"
    fi
else
    # 首次安装：shell 只在启动时扫描扩展目录，新目录当场看不到
    add_to_enabled
    warn "这是首次安装，GNOME 还没扫描到新扩展"
    say ""
    say "  ${C_B}注销重登${C_R}（或重启）后自动生效，面板右侧会出现鼠标图标。"
    if [ "${XDG_SESSION_TYPE:-}" = "x11" ]; then
        say "  X11 会话也可以按 Alt+F2，输入 r 回车重启 shell，不必注销。"
    fi
fi

# 全局开关关着的话，写进 enabled-extensions 也不会加载
DISABLE_ALL=$(gsettings get org.gnome.shell disable-user-extensions 2>/dev/null || echo false)
if [ "$DISABLE_ALL" = "true" ]; then
    warn "系统当前「禁用了所有用户扩展」，扩展不会加载"
    say "  开启后重登：gsettings set org.gnome.shell disable-user-extensions false"
fi

# ---------------------------------------------------------------- 收尾
step "安装完成"
say "  版本      $VER"
say "  扩展目录  $DEST"
say "  命令行    $BIN"
say ""
say "验证：插上鼠标，唤醒后运行"
say "  mchose info        # 应显示型号、固件、电量"
say "  mchose dpi         # 应列出六档 DPI"
say ""
if [ "$WAS_ENABLED" -eq 1 ]; then
    say "本次是更新安装，扩展已重新加载，无需注销。"
fi
if [ "$NEED_RELOGIN" -eq 1 ]; then
    say ""
    say "${C_B}重要：需要注销重登一次${C_R} —— input 组和新的 udev 规则都要重新登录才生效。"
fi
say "如果面板显示「鼠标未连接」：先动一下鼠标唤醒，再点面板里的「立即刷新」。"
say "要卸载：bash install-mchose.sh --uninstall"
"""


def sync_src() -> None:
    """把仓库里的源码收进 dist/.src。

    Python 侧在仓库根目录，扩展侧在 extension/ —— 仓库自身就是唯一真相，
    clone 下来即可重新打包，不依赖本机已安装的扩展。
    """
    SRC.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        if name in ("mchose.py", "mchose-daemon.py"):
            origin = ROOT / name
        else:
            origin = EXTENSION / name
        if not origin.exists():
            raise SystemExit(f"找不到源文件: {origin}")
        shutil.copy2(origin, SRC / name)


def sync_src() -> None:
    """把仓库里的源码收进 dist/.src。

    Python 侧在仓库根目录，扩展侧在 extension/ —— 仓库自身就是唯一真相，
    clone 下来即可重新打包，不依赖本机已安装的扩展。
    """
    SRC.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        if name in ("mchose.py", "mchose-daemon.py"):
            origin = ROOT / name
        else:
            origin = EXTENSION / name
        if not origin.exists():
            raise SystemExit(f"找不到源文件: {origin}")
        shutil.copy2(origin, SRC / name)


def build_zip() -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    zp = DIST / f"{UUID}.shell-extension.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            src = SRC / name
            if not src.exists():
                raise SystemExit(f"缺少源文件: {src}")
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if name in EXECUTABLE else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.date_time = (2026, 1, 1, 0, 0, 0)
            z.writestr(info, src.read_bytes())
    return zp


def main() -> None:
    sync_src()
    zp = build_zip()
    raw = zp.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    lines = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))

    out = (
        TEMPLATE
        .replace("@@UUID@@", UUID)
        .replace("@@VER@@", "1.0")
        .replace("@@RULE@@", UDEV_RULE)
        .replace("@@B64MARK@@", "MCHOSE_EXT_ZIP_B64")
        .replace("@@B64@@", lines)
    )

    sh = DIST / "install-mchose.sh"
    sh.write_text(out, encoding="utf-8")
    sh.chmod(0o755)

    print(f"zip    {zp}  {len(raw):,} 字节")
    print(f"脚本   {sh}  {len(out):,} 字节")


if __name__ == "__main__":
    main()
