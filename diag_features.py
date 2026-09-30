# -*- coding: utf-8 -*-
"""
精确比对：定位两张图里同一特征的几何位置，判断是否有真实位移。
用"亮像素包围盒"和"质心"来量化字幕条与标题的位置差。
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
ref = np.asarray(Image.open(ROOT / "preview" / "_ref_1x.png").convert("L")).astype(np.float32)
new = np.asarray(Image.open(ROOT / "preview" / "_new_1x.png").convert("L")).astype(np.float32)


def centroid(a, sy, sx, thr):
    """在指定区域内找亮度超过阈值的像素，返回包围盒与质心"""
    blk = a[sy, sx]
    ys, xs = np.nonzero(blk > thr)
    if len(xs) == 0:
        return None
    return (int(xs.min() + sx.start), int(ys.min() + sy.start),
            int(xs.max() + sx.start), int(ys.max() + sy.start),
            float(xs.mean() + sx.start), float(ys.mean() + sy.start), len(xs))


checks = [
    ("字幕文字", slice(915, 985), slice(600, 1330), 200),
    ("标题文字", slice(320, 400), slice(130, 1100), 200),
    ("顶部进度条", slice(0, 8), slice(0, 1500), 120),
    ("右侧决策条(喝)", slice(600, 690), slice(1300, 1780), 150),
]

print(f"{'特征':<16}{'图':<6}{'包围盒':<34}{'质心':<22}{'像素数'}")
print("-" * 100)
for name, sy, sx, thr in checks:
    r = centroid(ref, sy, sx, thr)
    n = centroid(new, sy, sx, thr)
    for label, c in (("ref", r), ("new", n)):
        if c is None:
            print(f"{name:<16}{label:<6}{'未找到':<34}")
            continue
        bbox = f"x[{c[0]},{c[2]}] y[{c[1]},{c[3]}]"
        cen = f"({c[4]:.2f},{c[5]:.2f})"
        print(f"{name:<16}{label:<6}{bbox:<34}{cen:<22}{c[6]}")
    if r and n:
        dx, dy = n[4] - r[4], n[5] - r[5]
        dw = (n[2] - n[0]) - (r[2] - r[0])
        dh = (n[3] - n[1]) - (r[3] - r[1])
        print(f"{'':16}{'差值':<6}质心偏移 dx={dx:+.2f} dy={dy:+.2f}   尺寸差 dw={dw:+d} dh={dh:+d}")
    print()

# 直方图差异：判断是否只是压缩噪声
print("灰度直方图对比（整体）：")
for label, a in (("ref", ref), ("new", new)):
    h, _ = np.histogram(a, bins=[0, 20, 40, 60, 80, 120, 180, 256])
    tot = h.sum()
    print(f"  {label}: " + "  ".join(f"{v/tot*100:5.2f}%" for v in h))
