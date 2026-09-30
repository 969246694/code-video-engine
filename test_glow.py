# -*- coding: utf-8 -*-
"""验证 make_glow 的径向衰减：检查盒子边界是否残留硬边"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw
import render

W, H = render.W, render.H
# 场景 1：纯黑背景 + 亮圆 → 辉光必须平滑衰减到 0
img = Image.new("RGB", (W, H), (0, 0, 0))
d = ImageDraw.Draw(img)
cx, cy, r = W // 2, H // 2, 60
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(120, 220, 255))
before = np.asarray(img).astype(np.int16)
render.make_glow(img, (cx - 245, cy - 245, cx + 245, cy + 245), render.C_ACCENT, 46, 0.95)
diff = (np.asarray(img).astype(np.int16) - before).max(axis=2)

print("[纯黑+亮圆]")
print("  中心 diff=%d  边缘(盒边界外1px) diff=%d" % (diff[cy, cx], diff[cy, cx + 246]))
print("  盒边界内侧 diff:", [int(diff[cy, cx + k]) for k in (238, 240, 242, 244, 245)])
print("  径向 profile(每30px):", [int(diff[cy, cx + k]) for k in range(0, 250, 30)])
edge = diff[cy, cx + 244:cx + 252]
print("  跨边界跳变 =", int(abs(np.diff(edge.astype(int))).max()), "(应接近 0)")
bg = render.background(4.7)
before2 = np.asarray(bg).astype(np.int16)
render.make_glow(bg, (715, 127, 1205, 617), render.C_ACCENT, 46, 0.95)
diff2 = (np.asarray(bg).astype(np.int16) - before2).max(axis=2)
print("\n[真实背景 t=4.7]")
print("  窗口内 mean=%.2f max=%d" % (diff2[127:617, 715:1205].mean(), diff2[127:617, 715:1205].max()))
print("  对角线切片:", [int(diff2[127 + i * 60, 715 + i * 60]) for i in range(8)])
print("  窗口外改动像素 =", int((diff2 > 2).sum() - (diff2[127:617, 715:1205] > 2).sum()))
