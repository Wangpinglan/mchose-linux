#!/usr/bin/env bash
# 安装 MCHOSE 鼠标配置工具
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
RULE_DST="/etc/udev/rules.d/99-mchose.rules"

echo "==> 安装脚本到 ${BIN_DIR}/mchose"
mkdir -p "$BIN_DIR"
install -m 755 "${HERE}/mchose.py" "${BIN_DIR}/mchose"

echo "==> 检查 PATH"
case ":$PATH:" in
  *":${BIN_DIR}:"*) echo "    ${BIN_DIR} 已在 PATH 中" ;;
  *) echo "    ⚠ ${BIN_DIR} 不在 PATH，请把下面这行加到 ~/.bashrc："
     echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo "==> 安装 udev 规则（免 sudo 访问设备）"
if [[ -f "$RULE_DST" ]]; then
  echo "    ${RULE_DST} 已存在，跳过"
else
  sudo install -m 644 "${HERE}/99-mchose.rules" "$RULE_DST"
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=hidraw
  echo "    已安装"
fi

echo "==> 把当前用户加入 input 组"
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
