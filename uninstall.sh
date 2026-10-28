#!/usr/bin/env bash
# mchose 用户级安装卸载脚本。
#
# 清掉 install-cli.sh / install-gtk-app.sh / install-gnome-extension.sh /
# 手动安装留下的所有用户级文件，
# 并把 /etc 里不属于 mchose 包的旧 udev 规则一并清走，
# 卸完就是干净状态，可以直接安装系统包：
#
#   sudo dnf install ./mchose-*.rpm      # Fedora / RHEL / openSUSE
#   sudo apt install ./mchose_*.deb      # Debian / Ubuntu
#
# 用法：
#   bash uninstall.sh                交互式（动 /etc 前会确认）
#   bash uninstall.sh --yes          不询问，直接删
#   bash uninstall.sh --keep-udev    完全不动 /etc
#   bash uninstall.sh --dry-run      只打印将要做的事
set -euo pipefail

DRYRUN=0; YES=0; KEEP_UDEV=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRYRUN=1 ;;
        --yes)       YES=1 ;;
        --keep-udev) KEEP_UDEV=1 ;;
        -h|--help)   sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 1 ;;
    esac
done

run()      { if [ "$DRYRUN" = 1 ]; then echo "    [dry] $*"; else "$@"; fi; }
run_root() { if [ "$(id -u)" -eq 0 ]; then run "$@"; else run sudo "$@"; fi }

echo "==> 停止 mchose 守护进程"
# 只匹配守护进程脚本名，不会碰到别的进程
run pkill -f 'mchose-daemon\.py' 2>/dev/null || true

# ---------------------------------------------------------------- 用户级文件
remove() {
    if [ -e "$1" ]; then
        run rm -rf "$1"
    else
        echo "    跳过（不存在）：$1"
    fi
}

echo "==> 删除用户级文件"
remove "$HOME/.local/share/gnome-shell/extensions/mchose-mouse@local"
remove "$HOME/.local/lib/mchose-app"
remove "$HOME/.local/bin/mchose-gtk"
remove "$HOME/.local/bin/mchose"
remove "$HOME/.local/share/applications/io.github.wangpinglan.Mchose.desktop"
for size in 512 256 128 64; do
    remove "$HOME/.local/share/icons/hicolor/${size}x${size}/apps/io.github.wangpinglan.Mchose.png"
done
remove "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.wangpinglan.Mchose.svg"

# ---------------------------------------------------------------- 缓存刷新
if [ "$DRYRUN" != 1 ]; then
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# ---------------------------------------------------------------- /etc 的旧规则
# rpm 安装时如果 /etc/udev/rules.d/70-mchose.rules 已被一个无主文件占着，
# 事务会直接报文件冲突失败——所以这里把「不属于 mchose 包」的规则都清掉。
echo "==> 清理 /etc/udev/rules.d 里的旧规则"
BACKUP="$HOME/.local/share/mchose-uninstall-backup-$(date +%Y%m%d-%H%M%S)"
DID_UDEV=0
for rule in /etc/udev/rules.d/70-mchose.rules /etc/udev/rules.d/99-mchose.rules; do
    [ -f "$rule" ] || continue
    if command -v rpm >/dev/null 2>&1 && rpm -qf "$rule" >/dev/null 2>&1; then
        echo "    保留（rpm 包所有）：$rule"
        continue
    fi
    if command -v dpkg >/dev/null 2>&1 && dpkg -S "$rule" >/dev/null 2>&1; then
        echo "    保留（deb 包所有）：$rule"
        continue
    fi
    if [ "$KEEP_UDEV" = 1 ]; then
        echo "    跳过（--keep-udev）：$rule"
        continue
    fi
    if [ "$YES" != 1 ] && [ "$DRYRUN" != 1 ]; then
        if [ -t 0 ]; then
            printf "    删除 %s（先备份）？[Y/n] " "$rule"
            read -r answer
            case "$answer" in n*|N*) echo "    跳过：$rule"; continue ;; esac
        else
            echo "    非交互环境，跳过 $rule（要删请加 --yes）"
            continue
        fi
    fi
    run mkdir -p "$BACKUP"
    run cp "$rule" "$BACKUP/$(basename "$rule").bak"
    run_root rm -f "$rule"
    DID_UDEV=1
done

if [ "$DID_UDEV" = 1 ]; then
    run_root udevadm control --reload-rules 2>/dev/null || true
    run_root udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
fi

# ---------------------------------------------------------------- 校验
echo "==> 校验"
if [ -e "$HOME/.local/share/gnome-shell/extensions/mchose-mouse@local" ]; then
    echo "    ⚠ 扩展目录还在，请手动检查"
else
    echo "    扩展目录已清除"
fi
if command -v mchose >/dev/null 2>&1; then
    echo "    ⚠ PATH 里仍有 mchose：$(command -v mchose)"
else
    echo "    mchose 命令已不在 PATH"
fi

echo
echo "卸载完成。现在可以直接安装系统包："
echo "    sudo dnf install ./mchose-*.rpm      # Fedora / RHEL / openSUSE"
echo "    sudo apt install ./mchose_*.deb      # Debian / Ubuntu"
echo "装完注销重登一次，扩展会自动启用（enabled-extensions 里的登记没有动）。"
[ "$DID_UDEV" = 1 ] && echo "被删的旧规则备份在：$BACKUP"
exit 0
