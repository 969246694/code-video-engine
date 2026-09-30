# -*- coding: utf-8 -*-
"""
超采样（supersampling）实验：
DSH 当前用 Pillow 的 AGG 渲染器，几何图形（椭圆/圆角矩形/贝塞尔细线）没有抗锯齿，
这是画面显得"糙"的一个结构性原因。
这里对比 1x / 1.5x / 2x 超采样后的边缘质量与渲染耗时。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import render

tl = json.loads(Path("build/timeline.json").read_text(encoding="utf-8"))
bg = render.background(6.0)

# 选一个几何元素最密集的场景：注意力机制（细贝塞尔弧线 + 圆角卡片）
sc = [s for s in tl["scenes"] if s["id"] == "context"][0]
t_local = sc["dur"] * 0.62


def render_at(scale):
    """以 scale 倍分辨率渲染，再降采样回原始尺寸"""
    ow, oh = 1920, 1080
    render.W, render.H = int(ow * scale), int(oh * scale)
    render.reset_caches()
    try:
        w, h = render.W, render.H
        img = render.background(6.0)
        img = render.RENDERERS[sc["visual"]](img, t_local, sc["dur"], sc, tl)
        d = render.ImageDraw.Draw(img)
        render.draw_caption(img, d, sc["captions"], sc["start"] + t_local)
        render.progress_bar(d, t_local, tl["total"], sc["start"], sc["dur"])
        render.brand(d, tl["video"]["series"])
        if scale != 1:
            img = img.resize((ow, oh), Image.LANCZOS)
        return img
    finally:
        render.W, render.H = ow, oh
        render.reset_caches()


def edge_score(img):
    """
    量化"锯齿"：统计相邻像素梯度的尖峰比例。
    锯齿越明显，梯度直方图的尾部（单像素级跳变）越重。
    """
    a = np.asarray(img.convert("L")).astype(np.float32)
    gx = np.abs(np.diff(a, axis=1))
    gy = np.abs(np.diff(a, axis=0))
    g = np.concatenate([gx.ravel(), gy.ravel()])
    # 单像素硬跳变：梯度 > 40 的像素占比
    hard = (g > 40).mean()
    # 中间调过渡（30~90）越多越平滑
    mid = ((g > 8) & (g <= 40)).mean()
    return hard, mid


print(f"场景: {sc['id']}  局部时刻 t={t_local:.2f}s")
print(f"{'倍率':<8}{'耗时':>10}{'硬跳变占比':>14}{'过渡占比':>12}")
ref = None
for scale in (1.0, 1.5, 2.0):
    t0 = time.time()
    img = render_at(scale)
    el = time.time() - t0
    hard, mid = edge_score(img)
    img.save(Path("preview") / f"_ss_{scale}x.png")
    if ref is None:
        ref = np.asarray(img).astype(np.int16)
    else:
        d = np.abs(np.asarray(img).astype(np.int16) - ref).mean()
        print(f"{scale:<8}{el*1000:>9.0f}ms{hard*100:>13.3f}%{mid*100:>11.2f}%"
              f"   (与1x平均差异 {d:.2f})")
        continue
    print(f"{scale:<8}{el*1000:>9.0f}ms{hard*100:>13.3f}%{mid*100:>11.2f}%")

# 推算全片耗时
n = int(tl["total"] * tl["video"]["fps"])
print(f"\n全片 {n} 帧预计编码耗时：")
for scale, ms in ((1.0, None), (1.5, None), (2.0, None)):
    pass
