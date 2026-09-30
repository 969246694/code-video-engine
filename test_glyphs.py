# -*- coding: utf-8 -*-
"""
字形可用性检查（回归测试）。

发现的问题：`msyhbd.ttc`（微软雅黑 Bold）**缺少带圈数字 ①②③** 等字形，
渲染时会显示成豆腐块 □。而场景里的"① 预训练"等标签用的正是粗体 ——
这个问题从第 1 期就存在，两期成片都带着它。

这里把所有会出现在画面上的特殊字符，在三种字重下逐个验证，
并作为回归测试固化，避免以后引入新的缺字形字符。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import render

# 场景里实际用到的、非 ASCII/CJK 的符号
SPECIAL = ["\u2460", "\u2461", "\u2462",            # ①②③
           "\u2713", "\u2717",                       # ✓ ✗
           "\u2192", "\u2193", "\u2191", "\u2190",   # 箭头
           "\u2014", "\u00b7", "\u00d7", "\u221a",   # — · × √
           "\u300c", "\u300d",                       # 「」
           "\uff08", "\uff09", "\uff0c", "\u3002",   # （），。
           "\uff1a", "\uff1f", "\uff01",             # ：？！
           "\u2026",                                 # …
           ]

WEIGHTS = {"bold": render.F_BOLD, "regular": render.F_REG, "light": render.F_LIGHT}


def glyph_ok(path, ch, size=60):
    """用墨迹像素数判断：豆腐块/空白都视为缺失"""
    f = ImageFont.truetype(path, size)
    img = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(img).text((8, 8), ch, font=f, fill=255)
    ink = int((np.asarray(img) > 100).sum())
    # 正常字形：墨迹适中；豆腐块：轮廓框（偏多）；缺失：接近 0
    return 20 < ink < 1100, ink


print("=" * 74)
print("字形可用性检查")
print("=" * 74)
print(f"{'字符':<6}{'码点':<10}", end="")
for w in WEIGHTS:
    print(f"{w:<12}", end="")
print()
problems = []
for c in SPECIAL:
    print(f"{c:<6}U+{ord(c):04X}    ", end="")
    for w, path in WEIGHTS.items():
        ok, ink = glyph_ok(path, c)
        print(f"{('OK' if ok else f'缺({ink})'):<12}", end="")
        if not ok:
            problems.append((c, w, ink))
    print()

print()
if problems:
    print("发现问题：")
    for c, w, ink in problems:
        print(f"  {c!r} (U+{ord(c):04X}) 在 {w} 字重下缺失（墨迹 {ink}）")
    print()
    print("  说明：这些字符若用在对应字重上会渲染成豆腐块 □。")
    print("  处理方式：避免在粗体里用此字符，或改用 regular 字重。")
else:
    print("全部字符在三种字重下均可用 ✓")

print()
print("=" * 74)
print("当前场景源码里是否用到了缺字形的组合")
print("=" * 74)
src = (Path(__file__).resolve().parent / "render.py").read_text(encoding="utf-8")
bad_chars = {c for c, w, _ in problems if w == "bold"}
found = []
for c in bad_chars:
    if c in src:
        found.append(c)
if found:
    print(f"  render.py 中出现了粗体缺字形的字符: {found}")
    print("  需要替换为非带圈写法（如「1 预训练」）或改用 regular 字重。")
else:
    print("  未使用 ✓")

sys.exit(1 if (problems and found) else 0)
