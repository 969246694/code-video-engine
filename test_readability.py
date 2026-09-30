# -*- coding: utf-8 -*-
"""
文字可读性检查：测量"文字墨迹"与"其正下方背景"的对比度。

AI 背景有细节纹理，文字叠上去后局部对比度可能不够。
这个检查逐帧找出文字区域，比较文字像素与周围背景像素的亮度差。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import ImageDraw
import build
import render

ROOT = Path(__file__).resolve().parent
EP = int(sys.argv[1]) if len(sys.argv) > 1 else 2
tl = build.load_timeline(EP)

print("=" * 76)
print(f"第 {EP} 期：文字可读性（文字与背景的亮度差）")
print("=" * 76)

for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    render.set_scale(1.0, size, profile=prof)
    print(f"\n档案 {prof}")
    for sc in tl["scenes"]:
        build.ensure_bg(sc, tl)
        bg = render.background(0.0)
        t = sc["dur"] * 0.75
        img = bg.copy()
        img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
        a = np.asarray(img.convert("L")).astype(np.float32)
        b = np.asarray(bg.convert("L")).astype(np.float32)
        diff = a - b
        # 文字像素 = 与背景差异明显的地方
        ink = np.abs(diff) > 30
        if ink.sum() < 50:
            print(f"  {sc['id']:<9} 无有效文字像素")
            continue
        # 文字区域的背景亮度（局部背景有多亮/多花）
        bg_under = b[ink]
        ink_lum = a[ink]
        contrast = np.abs(ink_lum - bg_under).mean()
        # 背景自身的局部标准差（纹理越花，文字越难读）
        bg_std = float(bg_under.std())
        verdict = "✓" if contrast > 55 else ("△ 偏低" if contrast > 38 else "✗ 不足")
        print(f"  {sc['id']:<9} 文字对比 {contrast:5.1f}  背景纹理 {bg_std:5.1f}  {verdict}")

render.set_scale(1.0, (1920, 1080), profile="h")
print()
print("说明：文字对比 = 文字像素与其正下方背景的平均亮度差（越大越清晰）")
print("      背景纹理 = 文字下方背景亮度的标准差（越大说明底图越花，越干扰阅读）")
