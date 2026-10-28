#!/usr/bin/env python3
"""把 mchose-linux 打成 RPM 和 DEB 包（noarch，纯文件 + 脚本）。

包内容（系统级安装，与 ~/.local 的用户级安装互不冲突，但扩展 UUID 相同，
两处都装的话 GNOME 会提示冲突，二选一）：

    /usr/bin/mchose                     CLI
    /usr/bin/mchose-gtk                 GTK 应用启动器
    /usr/lib/mchose-app/                GTK 应用 + 协议代码
    /usr/share/gnome-shell/extensions/mchose-mouse@local/   GNOME 扩展
    /usr/share/applications/io.github.wangpinglan.Mchose.desktop
    /usr/share/icons/hicolor/{64,128,256,512}x*/apps/io.github.wangpinglan.Mchose.png
    /etc/udev/rules.d/70-mchose.rules   %config(noreplace) / dpkg conffiles
    /usr/share/doc/mchose/              README / PROTOCOL / LICENSE

用法：
    python3 tools/build_packages.py          # 产物在 dist/mchose-*.rpm / *.deb
需要 rpmbuild（dnf install rpm-build）；deb 用 ar + tar 手工组装，不依赖 dpkg。
"""

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
STAGE = DIST / ".pkgroot"
RPM_TOP = DIST / "rpm"
DEB_DIR = DIST / "deb"

VERSION = (ROOT / "VERSION").read_text().strip()
PKG = "mchose"
MAINTAINER = "Wangpinglan <129617700+Wangpinglan@users.noreply.github.com>"
URL = "https://github.com/Wangpinglan/mchose-linux"
SUMMARY = "MCHOSE 鼠标控制（GNOME 扩展 + GTK 应用 + CLI）"

DESCRIPTION = """\
在 Linux 下控制 MCHOSE / 迈从 A5、AX5 Pro 系列鼠标：顶栏电量、DPI 六档、
回报率、静默高度、按键消抖、休眠时间、配置文件切换。
协议逆向自厂商 Windows 驱动，直接通过标准 HID Feature Report 通信，无需 Wine。
含 GNOME 扩展（系统级，需注销重登一次）和 GTK4 + libadwaita 桌面应用。
"""

ICON_SIZES = (512, 256, 128, 64)

# 扩展目录里要有全 8 个文件，其中 mchose*.py 在仓库根目录
EXT_FILES = (
    ("extension/metadata.json", "metadata.json"),
    ("extension/extension.js", "extension.js"),
    ("extension/impl.js", "impl.js"),
    ("extension/daemon.js", "daemon.js"),
    ("extension/stylesheet.css", "stylesheet.css"),
    ("mchose.py", "mchose.py"),
    ("mchose_api.py", "mchose_api.py"),
    ("mchose-daemon.py", "mchose-daemon.py"),
)

APP_FILES = (
    ("app/main.py", "main.py"),
    ("app/mchose_app/__init__.py", "mchose_app/__init__.py"),
    ("app/mchose_app/backend.py", "mchose_app/backend.py"),
    ("app/mchose_app/widgets.py", "mchose_app/widgets.py"),
    ("app/mchose_app/window.py", "mchose_app/window.py"),
    ("mchose.py", "mchose.py"),
    ("mchose_api.py", "mchose_api.py"),
)

REFRESH = """\
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules >/dev/null 2>&1 || :
    udevadm trigger --subsystem-match=hidraw >/dev/null 2>&1 || :
fi
command -v update-desktop-database >/dev/null 2>&1 && \\
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || :
command -v gtk-update-icon-cache >/dev/null 2>&1 && \\
    gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || :
exit 0
"""


# ---------------------------------------------------------------- 布景
def stage() -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    for d in ("usr/bin", "usr/lib/mchose-app",
              "usr/share/gnome-shell/extensions/mchose-mouse@local",
              "usr/share/applications", "usr/share/doc/mchose",
              "etc/udev/rules.d"):
        (STAGE / d).mkdir(parents=True)

    def install(rel_src: str, rel_dst: str, mode: int = 0o644) -> None:
        src = ROOT / rel_src
        dst = STAGE / rel_dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, mode)

    # CLI 与 GTK 应用
    install("app/main.py", "usr/lib/mchose-app/main.py")
    for src, dst in APP_FILES:
        install(src, f"usr/lib/mchose-app/{dst}")

    (STAGE / "usr/bin/mchose").write_text(
        "#!/usr/bin/sh\nexec /usr/bin/python3 /usr/lib/mchose-app/mchose.py \"$@\"\n")
    (STAGE / "usr/bin/mchose-gtk").write_text(
        "#!/usr/bin/sh\nexec /usr/bin/python3 /usr/lib/mchose-app/main.py \"$@\"\n")
    os.chmod(STAGE / "usr/bin/mchose", 0o755)
    os.chmod(STAGE / "usr/bin/mchose-gtk", 0o755)

    # GNOME 扩展（整目录系统级安装）
    for src, dst in EXT_FILES:
        install(src, f"usr/share/gnome-shell/extensions/mchose-mouse@local/{dst}")

    # 桌面项与图标
    install("app/data/io.github.wangpinglan.Mchose.desktop",
            "usr/share/applications/io.github.wangpinglan.Mchose.desktop")
    for size in ICON_SIZES:
        install(f"app/data/icons/io.github.wangpinglan.Mchose-{size}.png",
                f"usr/share/icons/hicolor/{size}x{size}/apps/"
                "io.github.wangpinglan.Mchose.png")

    # udev 规则与文档
    install("70-mchose.rules", "etc/udev/rules.d/70-mchose.rules")
    for name in ("README.md", "PROTOCOL.md", "LICENSE"):
        install(name, f"usr/share/doc/mchose/{name}")
    (STAGE / "usr/share/doc/mchose/copyright").write_text(
        f"Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        f"Upstream-Name: mchose-linux\nSource: {URL}\n\n"
        "Files: *\nCopyright: 2026 Wangpinglan\nLicense: MIT\n")
    return STAGE


# ---------------------------------------------------------------- RPM
def build_rpm(stage_root: Path) -> Path:
    for d in ("RPMS", "SPECS", "tmp"):
        (RPM_TOP / d).mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%a %b %d %Y")
    spec = f"""\
Name:           {PKG}
Version:        {VERSION}
Release:        1%{{?dist}}
Summary:        {SUMMARY}

License:        MIT
URL:            {URL}
BuildArch:      noarch

Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Recommends:     gnome-shell

%description
{DESCRIPTION}

%prep
# 纯文件包：不用 Source tarball（容器文件系统不允许 tar 恢复 mtime），
# 布景目录由 --define _stage 传入，%install 直接 cp（不保留时间戳）。

%install
mkdir -p "%{{buildroot}}"
cp -r "%{{_stage}}/." "%{{buildroot}}/"

%post
{REFRESH}
%postun
{REFRESH}
%files
%{{_bindir}}/mchose
%{{_bindir}}/mchose-gtk
%{{_prefix}}/lib/mchose-app
%{{_prefix}}/share/gnome-shell/extensions/mchose-mouse@local
%{{_datadir}}/applications/io.github.wangpinglan.Mchose.desktop
{_icons_spec()}
%config(noreplace) %{{_sysconfdir}}/udev/rules.d/70-mchose.rules
%{{_prefix}}/share/doc/mchose

%changelog
* {today} {MAINTAINER} - {VERSION}-1
- 初次打包：GNOME 扩展（系统级）、GTK4 应用、CLI、udev 规则
"""
    spec_path = RPM_TOP / "SPECS" / f"{PKG}.spec"
    spec_path.write_text(spec, encoding="utf-8")

    r = subprocess.run(
        ["rpmbuild", "-bb", "--define", f"_topdir {RPM_TOP}",
         "--define", f"_tmppath {RPM_TOP}/tmp",
         "--define", f"_stage {stage_root}",
         "--undefine", "__brp_mangle_shebangs", str(spec_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        print(r.stdout[-2500:])
        raise SystemExit(f"rpmbuild 失败 (rc={r.returncode})")
    rpms = sorted((RPM_TOP / "RPMS").rglob("*.rpm"))
    if not rpms:
        raise SystemExit("rpmbuild 没有产出 rpm")
    out = DIST / rpms[0].name
    shutil.copy2(rpms[0], out)
    return out


def _icons_spec() -> str:
    return "\n".join(
        "%{{_datadir}}/icons/hicolor/{s}x{s}/apps/io.github.wangpinglan.Mchose.png"
        .format(s=s) for s in ICON_SIZES)


# ---------------------------------------------------------------- DEB
def _tar_gz(src_root: Path, members: list[str], out: Path) -> None:
    with tarfile.open(out, "w:gz") as tf:
        for m in members:
            p = src_root / m
            info = tf.gettarinfo(str(p), arcname=m)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if p.is_dir():
                info.mode = 0o755
                tf.addfile(info)
            else:
                info.mode = (0o755 if os.access(p, os.X_OK) else 0o644)
                with open(p, "rb") as f:
                    tf.addfile(info, f)


def build_deb(stage_root: Path) -> Path:
    if DEB_DIR.exists():
        shutil.rmtree(DEB_DIR)
    (DEB_DIR / "control").mkdir(parents=True)

    # data.tar.gz：整个布景树
    members = ["."] + [f"./{p.relative_to(stage_root)}"
                       for p in sorted(stage_root.rglob("*"))]
    _tar_gz(stage_root, members, DEB_DIR / "data.tar.gz")

    size_kb = sum(f.stat().st_size for f in stage_root.rglob("*")
                  if f.is_file()) // 1024

    md5_lines = []
    for p in sorted(stage_root.rglob("*")):
        if p.is_file() and "doc/" not in str(p.relative_to(stage_root)):
            rel = "./" + str(p.relative_to(stage_root))
            md5_lines.append(f"{hashlib.md5(p.read_bytes()).hexdigest()}  {rel}")
    (DEB_DIR / "control" / "md5sums").write_text("\n".join(md5_lines) + "\n")
    (DEB_DIR / "control" / "conffiles").write_text(
        "/etc/udev/rules.d/70-mchose.rules\n")

    control = f"""\
Package: {PKG}
Version: {VERSION}-1
Architecture: all
Maintainer: {MAINTAINER}
Installed-Size: {size_kb}
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1
Recommends: gnome-shell
Section: utils
Priority: optional
Homepage: {URL}
Description: {SUMMARY}
""" + "\n".join(" " + line for line in DESCRIPTION.strip().splitlines()) + "\n"
    (DEB_DIR / "control" / "control").write_text(control, encoding="utf-8")

    postinst = "#!/bin/sh\nset -e\n" + REFRESH
    postrm = ("#!/bin/sh\nset -e\n"
              "command -v update-desktop-database >/dev/null 2>&1 && \\\n"
              "    update-desktop-database /usr/share/applications >/dev/null 2>&1 || :\n"
              "command -v gtk-update-icon-cache >/dev/null 2>&1 && \\\n"
              "    gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || :\n"
              "exit 0\n")
    for name, text in (("postinst", postinst), ("postrm", postrm)):
        p = DEB_DIR / "control" / name
        p.write_text(text)
        os.chmod(p, 0o755)

    # control.tar.gz：control + 脚本 + md5sums + conffiles
    cmembers = ["./control", "./md5sums", "./conffiles", "./postinst", "./postrm"]
    _tar_gz(DEB_DIR / "control", cmembers, DEB_DIR / "control.tar.gz")

    # ar 组装：debian-binary + control.tar.gz + data.tar.gz
    (DEB_DIR / "debian-binary").write_text("2.0\n")
    out = DIST / f"{PKG}_{VERSION}-1_all.deb"
    subprocess.run(["ar", "r", str(out), "debian-binary",
                    "control.tar.gz", "data.tar.gz"],
                   cwd=DEB_DIR, check=True, stdout=subprocess.PIPE)
    return out


# ---------------------------------------------------------------- 校验
def verify(rpm: Path, deb: Path, stage_root: Path) -> None:
    test = DIST / ".rpmtest"
    shutil.rmtree(test, ignore_errors=True)
    test.mkdir(parents=True)
    try:
        subprocess.run(["rpm", "-i", "--root", str(test), "--nodeps",
                        "--noscripts", str(rpm)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        # 有些容器文件系统不允许恢复 mtime，rpm -i 会失败；用 cpio 校验载荷
        r = subprocess.run(f"rpm2cpio {rpm} | cpio -id --no-absolute-filenames",
                           shell=True, cwd=test, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode()[-500:])
            raise SystemExit("rpm 载荷解包失败")
    got = sorted(str(p.relative_to(test)) for p in test.rglob("*")
                 if p.is_file() and "var/lib" not in str(p))
    want = sorted(str(p.relative_to(stage_root)) for p in stage_root.rglob("*")
                  if p.is_file())
    missing = [w for w in want if w not in got]
    if missing:
        raise SystemExit(f"rpm 缺文件: {missing}")
    print(f"rpm 校验通过：{len(want)} 个文件与布景一致")

    subprocess.run(["ar", "t", str(deb)], check=True, stdout=subprocess.PIPE)
    with tarfile.open(DEB_DIR / "data.tar.gz") as tf:
        names = set(tf.getnames())
    for rel in ("./usr/bin/mchose-gtk", "./etc/udev/rules.d/70-mchose.rules",
                "./usr/share/gnome-shell/extensions/mchose-mouse@local/impl.js"):
        if rel not in names:
            raise SystemExit(f"deb 缺 {rel}")
    print(f"deb 校验通过：data.tar {len(names)} 项")


def main() -> int:
    st = stage()
    rpm = build_rpm(st)
    deb = build_deb(st)
    verify(rpm, deb, st)
    shutil.rmtree(STAGE, ignore_errors=True)
    shutil.rmtree(DIST / ".rpmtest", ignore_errors=True)
    print(f"\n{rpm}  ({rpm.stat().st_size:,} B)")
    print(f"{deb}  ({deb.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
