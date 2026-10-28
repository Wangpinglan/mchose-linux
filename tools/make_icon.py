#!/usr/bin/env python3
"""从 MCHOSE 字标生成应用图标（各尺寸 PNG）。

源图是 1000×1000、透明底、细线白字。直接用会在 48px 下变成糊掉的一片，
所以：裁掉空白 → 放大到画布 78% 宽 → 对 alpha 做几轮最大值滤波把线加粗
→ 贴在深色圆角渐变底板上 → 按 hicolor 需要的尺寸导出。

用法：
    python3 tools/make_icon.py [源图]

默认源图是仓库根目录的 MCHOSE.png，输出到 app/data/icons/。
需要 Pillow（python3-pillow）。
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "MCHOSE.png"
OUT_DIR = ROOT / "app" / "data" / "icons"

SIZES = (512, 256, 128, 64)
MASTER = 1024
SS = 2                     # 超采样倍数，缩小后边缘更干净
BG_TOP = (0x22, 0x23, 0x2A)
BG_BOTTOM = (0x0D, 0x0E, 0x12)
LOGO_WIDTH_RATIO = 0.78
BOLDEN_PASSES = 3          # 每轮把笔画向外扩 1px


def build_master(src_path: Path) -> Image.Image:
    src = Image.open(src_path).convert("RGBA")
    logo = src.crop(src.getchannel("A").getbbox())

    n = MASTER * SS
    canvas = Image.new("RGBA", (n, n), (0, 0, 0, 0))

    # 竖向渐变底板
    gradient = Image.new("RGBA", (n, n))
    draw = ImageDraw.Draw(gradient)
    for y in range(n):
        t = y / (n - 1)
        color = tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (n, y)], fill=(*color, 255))

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1],
                                           radius=int(n * 0.22), fill=255)
    canvas.paste(gradient, (0, 0), mask)

    # 极淡的描边，深色主题下也能分辨边界
    pad = int(n * 0.008)
    ImageDraw.Draw(canvas).rounded_rectangle(
        [pad, pad, n - 1 - pad, n - 1 - pad],
        radius=int(n * 0.21), outline=(255, 255, 255, 30), width=max(1, n // 512))

    # 字标：居中、加粗
    width = int(n * LOGO_WIDTH_RATIO)
    height = round(logo.height * width / logo.width)
    scaled = logo.resize((width, height), Image.LANCZOS)
    alpha = scaled.getchannel("A")
    for _ in range(BOLDEN_PASSES):
        alpha = alpha.filter(ImageFilter.MaxFilter(3))
    text = Image.new("RGBA", scaled.size, (255, 255, 255, 255))
    text.putalpha(alpha)
    canvas.alpha_composite(text, ((n - width) // 2, (n - height) // 2))

    return canvas.resize((MASTER, MASTER), Image.LANCZOS)


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.exists():
        print(f"找不到源图：{src}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = build_master(src)
    icon.save(OUT_DIR / "io.github.wangpinglan.Mchose-1024.png")
    for size in SIZES:
        icon.resize((size, size), Image.LANCZOS).save(
            OUT_DIR / f"io.github.wangpinglan.Mchose-{size}.png")
    print(f"已生成 {', '.join(str(s) for s in SIZES)} 到 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
