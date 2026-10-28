#!/usr/bin/env bash
# 开发用：装 CLI 与 udev 规则（配合仓库源码运行；普通安装用根目录的
# install-gnome-extension.sh 或 app/install-gtk-app.sh）
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
# 必须是 70-：uaccess 标签要在 73-seat-late.rules 之前打上，否则 ACL 不生效
RULE_DST="/etc/udev/rules.d/70-mchose.rules"
RULE_OLD="/etc/udev/rules.d/99-mchose.rules"

echo "==> 安装脚本到 ${BIN_DIR}/mchose"
mkdir -p "$BIN_DIR"
install -m 755 "${REPO}/mchose.py" "${BIN_DIR}/mchose"

echo "==> 检查 PATH"
case ":$PATH:" in
  *":${BIN_DIR}:"*) echo "    ${BIN_DIR} 已在 PATH 中" ;;
  *) echo "    ⚠ ${BIN_DIR} 不在 PATH，请把下面这行加到 ~/.bashrc："
     echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo "==> 安装 udev 规则（免 sudo 访问设备）"
if [[ -f "$RULE_DST" ]] && cmp -s "${REPO}/70-mchose.rules" "$RULE_DST"; then
  echo "    ${RULE_DST} 已是最新"
else
  sudo install -m 644 "${REPO}/70-mchose.rules" "$RULE_DST"
  [[ -f "$RULE_OLD" ]] && sudo rm -f "$RULE_OLD"
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=hidraw
  echo "    已安装"
fi

echo "==> 把当前用户加入 input 组（uaccess 失效时的兜底）"
if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
  echo "    已在 input 组"
else
  sudo usermod -aG input "$USER"
  echo "    已加入 —— 需要重新登录（或 newgrp input）才生效"
fi

echo
echo "完成。插上鼠标后运行："
echo "  mchose list"
echo "  mchose info"
