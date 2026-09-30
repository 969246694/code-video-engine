# -*- coding: utf-8 -*-
"""
对照测量：旧的 reason 场景（单版式放 11.5 秒）vs 新的四镜头剪辑。
用完全相同的指标，确保是可比的口径。
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import render
import ae
import scene_cut_reason as S

FPS = 30
DUR = 11.5
ROOT = Path(__file__).resolve().parent


def motion_stats(get_frame, n):
    prev = None
    d = []
    for k in range(n):
        a = get_frame(k / FPS)
        if prev is not None:
            d.append(float(np.abs(a - prev).mean()))
        prev = a
    d = np.array(d)
    k60 = int(len(d) * 0.6)
    return d.mean(), d.min(), d[k60:].mean(), d[:k60].mean()


print("=" * 76)
print("对照：同一段内容，旧实现（单版式）vs 新实现（四镜头剪辑）")
print("=" * 76)
print(f"{'':16}{'逐帧变化均值':>13}{'最小':>9}{'平稳期':>9}{'入场期':>9}  平稳/入场")
print("-" * 76)

# ---- 旧实现：render.py 的 reason 场景
EP = 2
tl = build.load_timeline(EP)
sc = [s for s in tl["scenes"] if s["id"] == "reason"][0]
render.set_scale(0.5, (1920, 1080), profile="h")
caches = build.build_bg_caches(tl, 3.0)
render.set_background_image(None)


def old_frame(t):
    img = render.background(0.0)
    img = render.RENDERERS[sc["visual"]](img, float(t), sc["dur"], sc, tl)
    return np.asarray(img.convert("L").resize((320, 180), Image.BILINEAR), dtype=np.float32)


m_old = motion_stats(old_frame, int(DUR * FPS))
print(f"{'旧（单版式）':<16}{m_old[0]:>13.3f}{m_old[1]:>9.3f}{m_old[2]:>9.3f}{m_old[3]:>9.3f}"
      f"  {m_old[2]/max(1e-6,m_old[3]):>8.1%}")

# ---- 新实现：四镜头
shots = S.build_shots()


def new_frame(t):
    comp, lt = S.render_cut(t, shots)
    img = ae.still(comp, 320, 180, lt, ss=0.5, motion_blur=0.4)
    return np.asarray(img.convert("L"), dtype=np.float32)


m_new = np.array(motion_stats(new_frame, int(DUR * FPS)))
print(f"{'新（四镜头）':<16}{m_new[0]:>13.3f}{m_new[1]:>9.3f}{m_new[2]:>9.3f}{m_new[3]:>9.3f}"
      f"  {m_new[2]/max(1e-6,m_new[3]):>8.1%}")

print("-" * 76)
print(f"{'提升':<16}{m_new[0]/max(1e-6,m_old[0]):>12.1f}x"
      f"{'':>9}{m_new[2]/max(1e-6,m_old[2]):>8.1f}x"
      f"{'':>9}{'-':>9}")

print()
print("=" * 76)
print("视线运动（cut）的显著性")
print("=" * 76)
cuts = []
for t0, t1, comp in shots[1:]:
    prev = new_frame(t0 - 1 / FPS)
    cur = new_frame(t0)
    cuts.append(float(np.abs(cur - prev).mean()))
for (t0, t1, comp), c in zip(shots[1:], cuts):
    print(f"  t={t0:5.2f}s  切到 {comp.name}  画面变化 {c:8.3f}")
print(f"\n  平均切点变化 {np.mean(cuts):.2f}，是镜头内平均逐帧变化的 "
      f"{np.mean(cuts)/max(1e-6,m_new[0]):.0f} 倍 —— 这才是\"切\"该有的力度")
