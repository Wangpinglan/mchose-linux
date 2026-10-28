# mchose-linux — MCHOSE A5 / AX5 Pro 系列鼠标的 Linux 驱动（GNOME 扩展 + GTK 应用）

<p align="center"><img src="docs/icon.png" width="112" alt="MCHOSE"></p>

厂商驱动 `A5_AX5_Pro_ProMax_Drive_Ver2066.exe` 只有 Windows 版且早已停止更新。
本项目通过逆向该驱动，在 Linux 上原生复刻了它的核心配置功能。

**不需要 Wine，不需要内核模块，不需要 DKMS。**

## 快速安装

```bash
curl -fsSL https://github.com/wangpinglan/mchose-linux/releases/latest/download/install-mchose.sh | bash
```

这是三种安装方式里最省事的一种（GNOME 扩展），全部三种见「[安装](#安装)」：
① 一键脚本装扩展 · ② install/uninstall 脚本（开发）· ③ RPM/DEB 系统包。
需要先发一次 Release（见「[发布新版本](#发布新版本)」）这条命令才有效。
首次装完要注销重登一次。

两种用法：

| | 用途 | 位置 |
|---|---|---|
| **GNOME 扩展** | 顶栏常驻电量/DPI，点击展开完整控制面板 | `~/.local/share/gnome-shell/extensions/mchose-mouse@local/` |
| **命令行工具** | 脚本化、远程操作、排错 | `mchose.py` |

## 原理

逆向结论：这个驱动**没有用任何厂商私有协议**，走的是标准 HID Feature Report。
Windows 端通过 `HidSharp` 调 `SetFeature` / `GetFeature`，Linux 端对应
`/dev/hidraw*` 的 `HIDIOCSFEATURE` / `HIDIOCGFEATURE`，一一对应。

协议细节（报文结构、命令码表、字段偏移）见 [PROTOCOL.md](PROTOCOL.md)。

## GNOME 扩展

顶栏右侧显示 `🔋 76% · 1600`，点开是分组卡片式面板：

- 概览卡：型号 / 固件 / PID + 电量环
- DPI：六档每档都能直接输入数值（回车生效），点色点切当前档，另有对数滑块
- 回报率：型号基础列表 + 4K 接收器追加（A5 Pro 配 4K 接收器可到 4000）
- 高级：静默高度、直线修正、按键消抖、休眠时间、配置文件

```bash
bash dist/install-mchose.sh
```

安装/卸载见「[安装](#安装) · ①」。

## GTK 应用（libadwaita）

不想装扩展的话，也可以用桌面应用：GTK4 + libadwaita 1.x，窗口式界面，
不依赖 GNOME Shell 版本，KDE / Sway 下同样能跑（只要有 GTK4 和 libadwaita）。

<p align="center">
  <img src="docs/app-light.png" width="330" alt="浅色">
  <img src="docs/app-dark.png" width="330" alt="深色">
</p>

- 顶部概览：型号、固件、PID、有线/接收器、电量环
- DPI 六档：右侧直接改数字，点色点切当前档
- 回报率、静默高度、休眠时间、按键消抖、直线修正、配置文件 1–5
- 底部没有「保存」按钮：改完立刻写入设备，成功弹 toast

```bash
bash app/install-app.sh        # 装到 ~/.local，菜单里出现「MCHOSE 鼠标设置」
mchose-gtk                     # 或者直接命令行启动
bash app/install-app.sh --uninstall
```

依赖：`gtk4`、`libadwaita`、`python3-gobject`。Fedora 上：

```bash
sudo dnf install gtk4 libadwaita python3-gobject
```

它和扩展共用同一套协议代码（`mchose.py`）和状态组装（`mchose_api.py`），
udev 规则也是同一条 `70-mchose.rules`，装过扩展的机器直接就能用。
RPM/DEB 包（安装方式 ③）里已经自带这个应用；`app/install-app.sh` 装的
用户级副本，`uninstall.sh` 也会一并清掉。

## 安装

三种方式按需选一种：

| 方式 | 装什么 | 位置 | 适合 |
|------|--------|------|------|
| ① GNOME 扩展 | 顶栏扩展 + CLI + udev 规则 | 用户级（`~/.local`） | 日常使用，一条命令 |
| ② install / uninstall 脚本 | CLI + udev 规则 | 用户级 + `/etc` | 已克隆仓库，想从源码跑 |
| ③ RPM / DEB 包 | 扩展（系统级）+ GTK 应用 + CLI + udev 规则 | `/usr` + `/etc` | 想要桌面应用，交给包管理器 |

顶栏扩展和 GTK 应用功能一样，二选一或都装都行；三条路装的 udev 规则是同一条
`70-mchose.rules`，装过任意一种即可。

### ① GNOME 扩展（一键脚本）

一条命令即可装好，无需克隆仓库：

```bash
curl -fsSL https://github.com/wangpinglan/mchose-linux/releases/latest/download/install-mchose.sh | bash
```

或先下载再执行：

```bash
bash install-mchose.sh
```

- 脚本内嵌扩展 zip 与 udev 规则，不联网、不需要克隆本仓库
- **不要加 sudo 运行**——只有写 udev 规则那一步会自己请求提权；sudo 整个脚本
  会让扩展装进 root 的家目录，你的桌面会话看不到（脚本已做 `SUDO_USER` 保护，别依赖它）
- **首次安装必须注销重登**：GNOME 只在启动时扫描扩展目录；同一台机器上更新则即时生效

其他选项：

```bash
bash install-mchose.sh --no-udev    # 不动系统文件（没有 sudo 时）
```

只想要扩展本体（不要 CLI）的话，用标准扩展 zip：

```bash
gnome-extensions install --force mchose-mouse@local.shell-extension.zip
```

这种方式不含 udev 规则，需要自行安装：复制 `70-mchose.rules` 到
`/etc/udev/rules.d/`，或改用 ②③。

卸载：`bash uninstall.sh`，见 ②。

### ② install.sh / uninstall.sh（源码脚本安装）

适合已经克隆了仓库、或想从源码运行的人。`install.sh` 只装 CLI 与 udev 规则（不装扩展文件）：

```bash
./install.sh          # CLI → ~/.local/bin/mchose，udev 规则 → /etc
```

想从源码运行扩展：把仓库的 `extension/` 拷贝（或软链）到
`~/.local/share/gnome-shell/extensions/mchose-mouse@local`，改完代码重载扩展即可。

`uninstall.sh` 负责反向清理，也是从 ①② 换到 ③ 的第一步：

```bash
bash uninstall.sh --dry-run   # 只看会做什么
bash uninstall.sh             # 动 /etc 前会确认，先备份
```

它卸掉用户级的一切（扩展、GTK 应用、CLI、桌面项、图标，含 `app/install-app.sh`
装的东西），并把 `/etc/udev/rules.d` 里**不属于 mchose 包**的旧规则删掉——
无主的 `70-mchose.rules` 会让 ③ 的 rpm 事务报文件冲突，`99-mchose.rules`
则是早期失效写法。gsettings 的 enabled-extensions 故意不动：
之后装 ③ 并注销重登，扩展自动启用。

### ③ RPM / DEB 包（系统级）

发行版包一次装齐 GNOME 扩展（系统级）、GTK4 桌面应用、CLI 和 udev 规则：

```bash
sudo dnf install ./mchose-1.2.0-1.fc44.noarch.rpm   # Fedora / RHEL / openSUSE
sudo apt install ./mchose_1.2.0-1_all.deb           # Debian / Ubuntu
```

- 扩展装在 `/usr/share/gnome-shell/extensions/mchose-mouse@local`，
  注销重登一次后启用；之前装过用户级并启用过的话会自动启用，
  全新机器手动执行一次 `gnome-extensions enable mchose-mouse@local`
- **从 ①② 换过来先跑 `bash uninstall.sh`**：同一个扩展 UUID 两处都装会冲突
- 卸载：`sudo dnf remove mchose` / `sudo apt remove mchose`
- 从源码打包：`python3 tools/build_packages.py`（需要 `rpmbuild`；
  deb 用 `ar` + tar 组装，不依赖 dpkg）

## 打包与发布

### 重新打包

```bash
python3 tools/build_release.py      # 单文件安装器 + 标准扩展 zip
python3 tools/build_packages.py     # RPM + DEB（需要 rpmbuild）
```

`dist/.src` 是每次打包时从仓库自动收集的中间目录，不用手动维护。

### 发布新版本

```bash
python3 tools/build_release.py
```

然后在 GitHub 上新建 Release（tag 形如 `v1.2.0`），把这些文件作为附件上传：

| 文件 | 用途 |
|---|---|
| `dist/install-mchose.sh` | 一键安装脚本（方式 ①），README 顶部的 `curl` 命令依赖它 |
| `dist/mchose-mouse@local.shell-extension.zip` | 标准扩展包，可 `gnome-extensions install` |
| `dist/mchose-<ver>-1.fc*.noarch.rpm` | Fedora / RHEL 系统包（方式 ③） |
| `dist/mchose_<ver>-1_all.deb` | Debian / Ubuntu 系统包（方式 ③） |

**文件名不要改**，`releases/latest/download/<文件名>` 是按名字取附件的。
仓库如果改名，同步更新本 README 里的下载链接和 `extension/metadata.json` 的 `url`。

## 用法

```bash
mchose list                  # 扫描设备，标出哪个节点是配置接口
mchose info                  # 固件版本、PID、当前 profile、回报率、消抖、LOD

mchose dpi                   # 读 DPI 档位（含原始字节，用于校准布局）
mchose dpi --switch 1        # 切到第 2 档
mchose polling               # 读回报率
mchose polling 4000          # 设 4000Hz
mchose sleep 15              # 15 分钟无操作休眠（下发时换算成秒）
mchose debounce 4            # 按键消抖 4ms
mchose lod 2                 # 静默高度 2mm（单位就是毫米）
mchose anglesnap on          # 开直线修正
mchose profile 2             # 切到 2 号配置文件
mchose battery               # 电量
mchose raw 2 1 0x81 0        # 发原始帧调试
```

## 支持的型号

| 型号 | 有线 PID | 无线 PID |
|------|----------|----------|
| A5 Pro | F017 | F013 |
| A5 Pro Max | F019 | F013 |
| AX5 Pro | F026 | F024 |
| AX5 Pro Max | F022 | F024 |
| A5 4K 接收器 | — | F015 |

VID 统一是 `0x2023`。配置接口的判据：HID Report Descriptor 里
Usage Page `0xFFFF`、64 字节 Feature（`06ffff 0900 a101 ... 9540 b102`），
即 Feature Report 长度 65（含 report id）。

## 状态

| 功能 | 状态 |
|------|------|
| 设备发现 / 识别配置接口 | ✅ 已实现 |
| 固件版本、PID、profile 读写 | ✅ 已实现 |
| 回报率写入 + 档位裁剪 | ✅ 已实现（型号表基础列表 + 复刻 GetGoldenDonglePollingRate 追加） |
| 回报率读取 | ✅ 已实现（出厂未设置时回 `0x00`，界面提示「还没设过」） |
| 休眠时间读写 | ✅ 已实现（单位秒，默认 900 = 15 分） |
| 按键消抖读写 | ✅ 已实现 |
| 静默高度 (LOD) 读写 | ✅ 已实现 |
| 直线修正开关 | ✅ 已实现 |
| DPI 档位读取 | ✅ 已实现（大端 16 位） |
| DPI 档位写入 | ✅ 已实现 |
| DPI 档位颜色读取 | ✅ 已实现（组2 码0x81） |
| 电量 | ✅ 已实现（组0 码0x83，`[7]`状态 `[8]`百分比） |
| 灯效 | ⚠️ 帧结构已知（组2 码0x00），未封装到 CLI |
| 按键重映射 | ⚠️ 帧结构已知（组3），未封装到 CLI |
| 宏 | ⚠️ 帧结构已知，未封装 |
| 固件升级 | ❌ 不做（风险太高，用 Windows 一次性刷完即可） |

DPI 和电量已用真机（A5 Pro + 4K 接收器，PID F017 / 接收器 F015）验证通过。
`mchose dpiprobe` 会打印原始收发帧，排查协议不符时用。

## 加新功能

`mchose.py` 里 `Mchose` 类的方法都是一行帧构造：

```python
# 读：命令码 | 0x80，参数从 [7] 开始
r = self.xfer_read(group, code, params, length=len(params))
# 写
self.xfer_write(group, code, params, length=len(params))
```

返回值 `r` 是 65 字节应答，`r[1] == 0xA1` 表示设备确认。
参数区从 `r[7]` 开始，但有些命令的应答会偏移一位（`r[8]`），
具体看 PROTOCOL.md 里每个命令的说明。

## 已知坑

- **udev 规则必须以 `70-` 开头**。`systemd` 在 `73-seat-late.rules` 里才有
  `TAG=="uaccess" ... RUN{builtin}+="uaccess"`，把标签变成 ACL；放在 `99-` 里
  标签加得太晚，规则里的 `TAG+="uaccess"` 不会生效，表现就是面板报
  「没有权限打开 /dev/hidrawN」——有线、无线切换后节点重新枚举时尤其明显。
  （旧的 `99-mchose.rules` 安装脚本会自动删掉。）
- **设备休眠时 HID 通道会断**。读不到就动一下鼠标唤醒，再试。
- 一个鼠标会枚举出 3～4 个 hidraw 节点（键盘接口、鼠标接口、Consumer 接口、
  配置接口）。只有配置接口的 Feature Report 长度是 65，`mchose list` 会标出来。
- 如果 Feature Report 写不进去，可能有别的程序占了接口 —— 关掉
  其他鼠标软件（Solaar、libratbag 等）再试。
- `libinput` / `hid-microsoft` 一般不会抢这个厂商接口，但个别内核版本
  会把未知 HID 设备当鼠标接管。真遇到了加一条 `quirks`：

  ```
  # /etc/modprobe.d/mchose.conf
  options usbhid quirks=0x2023:0xF017:0x0004
  ```

## 仓库结构

```
mchose.py                 命令行工具 + 协议实现（零依赖，仅标准库）
mchose_api.py             状态读写封装（守护进程和 GTK 应用共用）
mchose-daemon.py          常驻守护进程，扩展通过 stdin/stdout 的 JSON 与它通信
install.sh                装 CLI 与 udev 规则（配合源码运行）
uninstall.sh              卸载用户级安装，恢复到可直接装 RPM/DEB 的状态
70-mchose.rules           udev 权限规则

app/                      GTK4 + libadwaita 桌面应用
  main.py                 入口，Adw.Application
  mchose_app/window.py    主窗口与交互
  mchose_app/widgets.py   电量圆环、档位圆点（Cairo 自绘）
  mchose_app/backend.py   设备句柄与后台线程
  data/                   桌面项与多尺寸图标（由根目录 MCHOSE.png 生成）
  install-app.sh          装到 ~/.local

extension/                GNOME 扩展源码
  metadata.json
  extension.js            外壳（十几行），用带时间戳的 URL 载入 impl.js
  impl.js                 界面与交互
  daemon.js               守护进程的 GJS 客户端
  stylesheet.css

tools/
  build_release.py        打包：生成单文件安装脚本 + 标准扩展 zip
  build_packages.py       打包：RPM + DEB（noarch，含扩展/应用/CLI/udev 规则）
  make_icon.py            从 MCHOSE.png 生成各尺寸图标（需要 Pillow）

docs/                     截图
dist/                     打包产物（不入库，用上面脚本生成）
PROTOCOL.md               协议逆向笔记：报文结构、命令码、字段偏移
```

仓库以 `extension/` 与 `mchose.py` 为源，`dist/` 下的产物全部由
`tools/build_release.py` 生成，改完代码重新跑一次即可。

逆向中间产物（`extract/` 厂商安装包解包、`src/` 反编译出的 C# 源码）体积大且含
厂商二进制，放在 `.gitignore` 里不入库。查协议时看
`src/pgckyKZq6gyHNM8uTF/m4E88s4JvMgGlnhjWi.cs`，类名是厂商混淆的随机串。

## 法律说明

仅用于在 Linux 下使用自己合法拥有的硬件。不含厂商代码，不含固件，
不绕过任何授权或许可证校验。
