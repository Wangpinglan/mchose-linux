#!/usr/bin/env bash
# MCHOSE 鼠标控制面板 —— Fedora / GNOME 一键安装
#
# 用法：
#   bash install-gnome-extension.sh            安装或更新
#   bash install-gnome-extension.sh --no-udev  只装扩展，不动系统（没有 sudo 时用）
#   bash install-gnome-extension.sh --uninstall 卸载（配 --no-udev 则保留 udev 规则）
#
# 内含扩展本体（zip，base64 内嵌）与 hidraw udev 规则，不需要联网。

set -euo pipefail

UUID="mchose-mouse@local"
PY_MIN="3.6"
VER="1.2.0"

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
# 用户常常直接 `sudo ./install-gnome-extension.sh`，此时 HOME=/root，扩展会装进 root 家目录，
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
            if [ -f "$RULE" ] || [ -f "$RULE_OLD" ]; then
                rm -f "$RULE" "$RULE_OLD"
                udevadm control --reload-rules 2>/dev/null || true
                ok "已删除 udev 规则"
            fi
        else
            printf '%s\n' "$RULE_TEXT" > "$RULE"
            rm -f "$RULE_OLD"
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
RULE=/etc/udev/rules.d/70-mchose.rules
# 老版本装的名字：uaccess 标签加得太晚（73-seat-late 之后），ACL 不生效，升级时删掉
RULE_OLD=/etc/udev/rules.d/99-mchose.rules
# 单引号包裹：内容里有双引号，绝不能放进双引号字符串里，否则引号会被 shell 吃掉
RULE_TEXT='SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2023", MODE="0660", GROUP="input", TAG+="uaccess"'

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
    if [ "$DO_UDEV" -eq 1 ] && { [ -f "$RULE" ] || [ -f "$RULE_OLD" ]; }; then
        if [ "$(id -u)" -eq 0 ]; then
            rm -f "$RULE" "$RULE_OLD"
            if command -v udevadm >/dev/null 2>&1; then
                udevadm control --reload-rules 2>/dev/null || true
            fi
            ok "已删除 udev 规则"
        elif command -v sudo >/dev/null 2>&1; then
            sudo rm -f "$RULE" "$RULE_OLD"
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
base64 -d > "$TMP/ext.zip" <<'MCHOSE_EXT_ZIP_B64'
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
MCHOSE_EXT_ZIP_B64

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
        rm -f "$RULE_OLD"
        udevadm control --reload-rules 2>/dev/null || true
        udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
        ok "已写入 $RULE"
    elif command -v sudo >/dev/null 2>&1; then
        say "需要 sudo 写 $RULE（只加一条 hidraw 权限规则）"
        if printf '%s\n' "$RULE_TEXT" | sudo tee "$RULE" >/dev/null; then
            sudo rm -f "$RULE_OLD"
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
    say "${C_B}重要：需要注销重登一次${C_R} —— input 组成员身份要重新登录才生效（uaccess ACL 通常插拔后立刻可用）。"
fi
say "如果面板显示「鼠标未连接」：先动一下鼠标唤醒，再点面板里的「立即刷新」。"
say "要卸载：bash install-gnome-extension.sh --uninstall"
