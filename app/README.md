# MCHOSE 桌面应用（GTK4 + libadwaita）

窗口式的 MCHOSE 鼠标设置工具，功能与 [GNOME 扩展](../README.md#-gnome-扩展一键脚本)相同，
但不依赖 GNOME Shell 版本——KDE / Sway 等其他桌面也能跑。

<p align="center">
  <img src="../docs/app-light.png" width="330" alt="浅色">
  <img src="../docs/app-dark.png" width="330" alt="深色">
</p>

- 概览：型号、固件、PID、有线/接收器、电量环
- DPI 六档直接改数值，点色点切当前档
- 回报率、静默高度、休眠时间、按键消抖、直线修正、配置文件 1–5
- 改完立即写入设备，没有「保存」按钮；成功弹 toast

## 安装

```bash
bash install-gtk-app.sh
```

装到 `~/.local`：应用本体（菜单里出现「MCHOSE 鼠标设置」）、`mchose` 与
`mchose-gtk` 两条命令、桌面项和图标。udev 规则（`/etc/udev/rules.d/70-mchose.rules`）
缺失时会请求一次 sudo 写入。

选项：

```bash
bash install-gtk-app.sh --no-udev    # 不动系统文件（没有 sudo 时）
```

依赖：`gtk4`、`libadwaita`、`python3-gobject`。Fedora 上：

```bash
sudo dnf install gtk4 libadwaita python3-gobject
```

## 卸载

```bash
bash install-gtk-app.sh --uninstall
```

清除应用、两条命令、桌面项和图标；udev 规则保留（GNOME 扩展可能还在用），
确定要删：`sudo rm -f /etc/udev/rules.d/70-mchose.rules`。

要把扩展、应用等用户级安装一次清干净，用仓库根目录的
[uninstall.sh](../uninstall.sh)。

## 常见问题

- **提示缺少设备权限**：确认 `/etc/udev/rules.d/70-mchose.rules` 存在，
  然后重新插拔鼠标（或注销重登）让 ACL 生效
- **读不到设备**：鼠标休眠时 HID 通道会断，动一下鼠标唤醒再试

命令行用法（`mchose info`、`mchose dpi …`）见主
[README](../README.md#用法)。
