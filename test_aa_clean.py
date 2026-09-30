# -*- coding: utf-8 -*-
"""
干净的抗锯齿测量：用合成图形隔离 Pillow 图元的边缘质量。
全帧统计会被 H.264 压缩噪声和背景纹理污染，这里只测几何边缘本身。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw
import render

W, H = 1920, 1080


def render_geo(scale):
    """在指定倍率下画一组几何图元，再降采样回 1920x1080"""
    render.set_scale(scale, (W, H))
    dw, dh = render.W, render.H
    img = Image.new("RGB", (dw, dh), (10, 14, 28))
    d = ImageDraw.Draw(img)
    s = scale
    # 圆（椭圆边缘最容易暴露锯齿）
    d.ellipse([200 * s, 200 * s, 500 * s, 500 * s], fill=(90, 200, 255))
    # 圆角矩形
    d.rounded_rectangle([600 * s, 200 * s, 900 * s, 500 * s], radius=int(40 * s),
                        fill=(255, 138, 92))
    # 细斜线
    d.line([(1000 * s, 500 * s), (1700 * s, 200 * s)], fill=(238, 243, 255),
           width=int(3 * s))
    # 细圆角描边
    d.rounded_rectangle([200 * s, 620 * s, 900 * s, 900 * s], radius=int(30 * s),
                        outline=(56, 224, 255), width=int(2 * s))
    if scale != 1.0:
        img = img.resize((W, H), Image.LANCZOS)
    return img


def edge_stats(img, sy, sx):
    """统计某区域的过渡特征"""
    a = np.asarray(img.convert("L")).astype(np.int16)
    g = np.concatenate([np.abs(np.diff(a[sy, sx], axis=1)).ravel(),
                        np.abs(np.diff(a[sy, sx], axis=0)).ravel()])
    hard = float((g > 60).mean() * 100)
    soft = float(((g > 2) & (g <= 60)).mean() * 100)
    return hard, soft


def ramp_width(img, y, x0, x1):
    """扫描线的过渡带宽度（从背景到前景，中间灰像素越多越平滑）"""
    a = np.asarray(img.convert("L"))[y, x0:x1]
    return [int(v) for v in a]


print("=" * 78)
print("合成几何抗锯齿对比（隔离测试，无压缩噪声）")
print("=" * 78)
print(f"{'倍率':<8}{'圆边缘硬跳变%':>16}{'圆边缘过渡%':>15}{'圆角矩形硬跳变%':>18}{'细斜线硬跳变%':>16}")
print("-" * 78)
for scale in (1.0, 2.0, 3.0):
    img = render_geo(scale)
    h1, s1 = edge_stats(img, slice(200, 500), slice(200, 500))
    h2, s2 = edge_stats(img, slice(200, 500), slice(600, 900))
    h3, s3 = edge_stats(img, slice(190, 510), slice(1000, 1700))
    print(f"{scale:<8}{h1:>15.3f}%{s1:>14.2f}%{h2:>17.3f}%{h3:>15.3f}%")
    img.save(Path(__file__).resolve().parent / "preview" / f"_aa_{scale}x.png")

print()
print("圆的水平扫描线剖面（y=350，看边缘过渡）：")
for scale in (1.0, 2.0, 3.0):
    img = Image.open(Path(__file__).resolve().parent / "preview" / f"_aa_{scale}x.png")
    prof = ramp_width(img, 350, 190, 215)
    print(f"  {scale}x: {prof}")

render.set_scale(1.0, (W, H))
