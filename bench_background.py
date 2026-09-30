# -*- coding: utf-8 -*-
"""
背景优化实验。
现状问题：background() 每帧都做 mgrid + 3 次全画布 exp() + float32 加法，
在 4K 下是 1125ms/帧，且缓存需要 4.6GB。

优化方向：
  A) 复用坐标网格、用可分离的 1D 高斯代替 2D exp
  B) 背景在低分辨率生成后放大（背景本身是平滑低频内容，放大损失很小）
     —— 前景几何仍在设备分辨率绘制，抗锯齿效果不受影响
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import render


def background_current(t):
    """现状实现（未改动）"""
    return render.background(t)


def background_fast(t, low_scale=0.5):
    """
    优化实现：
      · 纵向渐变用一维插值（不再是每行一次向量乘）
      · 光斑用可分离的 1D 高斯外积，避免 2D exp
      · 网格用 1D 索引广播
      · 在 low_scale 分辨率计算后放大（默认半分辨率）
    """
    W0, H0 = render.W, render.H
    lw, lh = max(1, int(W0 * low_scale)), max(1, int(H0 * low_scale))

    k = np.linspace(0.0, 1.0, lh, dtype=np.float32)[:, None]
    top = np.array(render.C_BG_TOP, dtype=np.float32)[None, :]
    bot = np.array(render.C_BG_BOT, dtype=np.float32)[None, :]
    arr = top * (1 - k) + bot * k                      # (lh,3)
    arr = np.repeat(arr[:, None, :], lw, axis=1)       # (lh,lw,3)

    # 光斑：1D 高斯外积
    xs = np.arange(lw, dtype=np.float32)
    ys = np.arange(lh, dtype=np.float32)
    for (cx, cy, r, col, st) in [
        (lw * 0.50 + render.S(120) * low_scale * np.sin(t * 0.23),
         lh * 0.34 + render.S(60) * low_scale * np.cos(t * 0.19),
         render.S(780) * low_scale, (40, 90, 150), 0.52),
        (lw * 0.16 + render.S(80) * low_scale * np.cos(t * 0.31),
         lh * 0.88 + render.S(50) * low_scale * np.sin(t * 0.27),
         render.S(620) * low_scale, (70, 40, 130), 0.42),
        (lw * 0.88 + render.S(60) * low_scale * np.sin(t * 0.17 + 2),
         lh * 0.13 + render.S(40) * low_scale * np.cos(t * 0.21),
         render.S(560) * low_scale, (20, 110, 140), 0.32),
    ]:
        gx = np.exp(-((xs - cx) ** 2) / (2 * r * r)) * st
        gy = np.exp(-((ys - cy) ** 2) / (2 * r * r))
        arr += (gy[:, None] * gx[None, :])[:, :, None] * np.array(col, dtype=np.float32)

    # 网格
    step = render.S(96) * low_scale
    if step >= 2:
        gi = np.arange(lw, dtype=np.float32)
        gj = np.arange(lh, dtype=np.float32)
        onx = (gi % step) < max(1.0, render.S(1) * low_scale)
        ony = (gj % step) < max(1.0, render.S(1) * low_scale)
        arr += onx[None, :, None] * np.array([24, 37, 61], dtype=np.float32)
        arr += ony[:, None, None] * np.array([24, 37, 61], dtype=np.float32)

    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    return img.resize((W0, H0), Image.BILINEAR)


import math
import random


def add_stars(img, t, low_scale=0.5):
    """星点单独叠加（在输出分辨率上画，避免放大后变糊）"""
    a = np.asarray(img).astype(np.float32)
    rng = random.Random(20240607)
    for i in range(150):
        x = rng.randrange(render.W); y = rng.randrange(render.H)
        ph = rng.random() * math.tau
        b = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(t * (0.8 + rng.random()) + ph))
        s = max(1, int(round(render.S(1 + int(b > 0.8)))))
        a[max(0, y - s):y + s, max(0, x - s):x + s] += b * 55
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


print("=" * 76)
print("背景生成优化对比")
print("=" * 76)
for scale in (1.0, 2.0):
    render.set_scale(scale, (1920, 1080))
    print(f"\nSCALE={scale}  设备 {render.W}x{render.H}")

    t0 = time.time()
    for i in range(3):
        background_current(i * 0.3)
    t_cur = (time.time() - t0) / 3
    ref = background_current(1.0)

    for ls in (0.5, 0.35):
        t0 = time.time()
        for i in range(3):
            add_stars(background_fast(i * 0.3, ls), i * 0.3, ls)
        t_new = (time.time() - t0) / 3
        cand = add_stars(background_fast(1.0, ls), 1.0, ls)
        d = np.abs(np.asarray(ref).astype(np.int16) - np.asarray(cand).astype(np.int16))
        print(f"  优化(低分辨率 {ls}): {t_new*1000:6.0f}ms  加速 {t_cur/t_new:4.1f}x  "
              f"平均色差 {d.mean():5.2f}  最大 {int(d.max()):3d}")
    print(f"  现状:              {t_cur*1000:6.0f}ms")
