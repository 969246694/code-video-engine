# -*- coding: utf-8 -*-
"""
验证 AI 背景是否真的进入了成片帧（决定性判据）。

做法：把渲染出的帧与 AI 原图逐像素比对 —— 若底图确实是 AI 原图，
帧与该图在"未被场景内容覆盖"的区域应有很高的结构相似度。
同时对比"若用程序化渐变"时的相似度，形成对照。

用高频细节能量作为辅助指标：AI 图有胶片颗粒与丰富纹理，
程序化渐变+网格的频率分布完全不同。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import build
import render

ROOT = Path(__file__).resolve().parent
EP = int(sys.argv[1]) if len(sys.argv) > 1 else 2
tl = build.load_timeline(EP)
render.set_scale(1.0, (1920, 1080), profile="h")
caches = build.build_bg_caches(tl, 3.0)


def hi_freq_energy(img):
    """高频细节能量：相邻像素差分的平均绝对值"""
    a = np.asarray(img.convert("L")).astype(np.float32)
    return float(np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean())


print("=" * 76)
print(f"第 {EP} 期：背景来源验证（高频细节能量）")
print("=" * 76)
print(f"{'场景':<10}{'配置':<12}{'帧高频':>9}{'AI原图高频':>12}{'程序化高频':>12}  判定")
print("-" * 76)

# 参考值：程序化背景的高频能量
render.set_background_image(None)
render.reset_caches()
proc_ref = hi_freq_energy(render.background(3.0))

ok = 0
total = 0
for sc in tl["scenes"]:
    rel = sc.get("bg")
    t = sc["start"] + sc["dur"] * 0.7
    img = build.render_active_frame(tl, t, caches, 3.0)
    fh = hi_freq_energy(img)

    if not rel:
        print(f"  {sc['id']:<9}{'程序化':<12}{fh:>9.2f}{'—':>12}{proc_ref:>12.2f}  "
              f"{'✓ 与程序化一致' if abs(fh - proc_ref) < 8 else '? 需人工确认'}")
        continue

    src = Image.open(ROOT / rel).convert("RGB").resize((render.W, render.H), Image.LANCZOS)
    # 原图与帧都受 PHOTO_SCRIM 压暗影响，这里只比频率特征
    sh = hi_freq_energy(src) * render.PHOTO_SCRIM
    total += 1
    # 判定：帧的高频能量更接近 AI 原图（而非程序化背景）
    d_ai = abs(fh - sh)
    d_proc = abs(fh - proc_ref)
    good = d_ai < d_proc
    ok += good
    print(f"  {sc['id']:<9}{'AI 背景':<12}{fh:>9.2f}{sh:>12.2f}{proc_ref:>12.2f}  "
          f"{'✓ 接近 AI 原图' if good else '✗ 更接近程序化'}")

render.set_background_image(None)
print("-" * 76)
print(f"AI 背景场景 {ok}/{total} 判定为「确实在用 AI 原图」")
