#!/usr/bin/env bash
# 安装 MCHOSE GTK4 应用（libadwaita）
#
# 用法：
#   bash app/install-app.sh              安装 / 更新
#   bash app/install-app.sh --no-udev    不动系统文件
#   bash app/install-app.sh --uninstall  卸载
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"

# PyGObject 通常装在系统 Python 里，别被 pyenv/uv 之类的 python3 顶掉
PYTHON=/usr/bin/python3
[ -x "$PYTHON" ] || PYTHON=$(command -v python3)

PREFIX="${HOME}/.local"
LIB="${PREFIX}/lib/mchose-app"
BIN="${PREFIX}/bin/mchose-gtk"
DESKTOP="${PREFIX}/share/applications/io.github.wangpinglan.Mchose.desktop"
ICON="${PREFIX}/share/icons/hicolor/scalable/apps/io.github.wangpinglan.Mchose.svg"
RULE=/etc/udev/rules.d/70-mchose.rules

DO_UDEV=1
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --no-udev)   DO_UDEV=0 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 1 ;;
    esac
done

if [ "$UNINSTALL" -eq 1 ]; then
    echo "==> 卸载 MCHOSE GTK 应用"
    rm -rf "$LIB"
    rm -f "$BIN" "$DESKTOP" "$ICON"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "${PREFIX}/share/applications" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -f -t "${PREFIX}/share/icons/hicolor" 2>/dev/null || true
    echo "    已删除 $LIB、$BIN、桌面项和图标"
    echo "    udev 规则没有动（扩展还在用），要删：sudo rm -f $RULE"
    exit 0
fi

# ---------------------------------------------------------------- 依赖检查
echo "==> 检查依赖"
"$PYTHON" - <<'PY' || { echo "    缺少 GTK4 / libadwaita 的 Python 绑定，请先安装：sudo dnf install python3-gobject gtk4 libadwaita" >&2; exit 1; }
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
print(f"    GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()} · "
      f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}")
PY

# ---------------------------------------------------------------- 文件
echo "==> 安装到 ${LIB}"
mkdir -p "${LIB}/mchose_app" "${PREFIX}/bin" \
         "${PREFIX}/share/applications" \
         "${PREFIX}/share/icons/hicolor/scalable/apps"

install -m 644 "${HERE}/main.py" "${LIB}/main.py"
install -m 644 "${HERE}/mchose_app/__init__.py" "${LIB}/mchose_app/__init__.py"
install -m 644 "${HERE}/mchose_app/backend.py" "${LIB}/mchose_app/backend.py"
install -m 644 "${HERE}/mchose_app/widgets.py" "${LIB}/mchose_app/widgets.py"
install -m 644 "${HERE}/mchose_app/window.py" "${LIB}/mchose_app/window.py"
install -m 644 "${REPO}/mchose.py" "${LIB}/mchose.py"
install -m 644 "${REPO}/mchose_api.py" "${LIB}/mchose_api.py"

cat > "$BIN" <<EOF
#!/bin/sh
exec "${PYTHON}" "${LIB}/main.py" "\$@"
EOF
chmod 755 "$BIN"

install -m 644 "${HERE}/data/io.github.wangpinglan.Mchose.desktop" "$DESKTOP"
install -m 644 "${HERE}/data/io.github.wangpinglan.Mchose.svg" "$ICON"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "${PREFIX}/share/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f -t "${PREFIX}/share/icons/hicolor" 2>/dev/null || true
echo "    命令行：mchose-gtk    菜单里：MCHOSE 鼠标设置"

# ---------------------------------------------------------------- udev
if [ "$DO_UDEV" -eq 1 ]; then
    echo "==> 检查设备访问权限"
    if [ -f "$RULE" ]; then
        echo "    $RULE 已存在"
    elif [ -f "${REPO}/70-mchose.rules" ]; then
        echo "    需要 sudo 写 $RULE（只加一条 hidraw 权限规则）"
        sudo install -m 644 "${REPO}/70-mchose.rules" "$RULE"
        sudo udevadm control --reload-rules
        sudo udevadm trigger --subsystem-match=hidraw
        echo "    已写入"
    fi
else
    echo "==> 跳过 udev 配置（--no-udev）"
fi

echo
echo "完成。启动："
echo "  mchose-gtk        # 或在应用列表里搜 MCHOSE"
echo "如果提示「缺少设备权限」，重新插拔鼠标，或注销重登一次。"
