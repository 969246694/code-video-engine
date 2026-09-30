# -*- coding: utf-8 -*-
"""性能基准：1x 直出 vs 2x 超采样，拆解背景与场景绘制耗时"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import render

tl = json.loads((Path(__file__).resolve().parent / "build" / "timeline.json").read_text(encoding="utf-8"))
SCENES = {s["id"]: s for s in tl["scenes"]}


def bench(scale, scene_id="context", n=3):
    render.set_scale(scale, (1920, 1080))
    s = SCENES[scene_id]

    t0 = time.time()
    for i in range(n):
        render.background(i * 0.3)
    tb = (time.time() - t0) / n

    t0 = time.time()
    for i in range(n):
        img = render.background(1.0)
        img = render.RENDERERS[s["visual"]](img, 6.4, s["dur"], s, tl)
        render.apply_fade(img, render.scene_alpha(6.4, s["dur"]))
    ts = (time.time() - t0) / n

    # 降采样耗时
    big = render.background(1.0)
    t0 = time.time()
    for i in range(n):
        big.resize((1920, 1080), Image.LANCZOS)
    tr = (time.time() - t0) / n

    mem = render.W * render.H * 3 / 1024 / 1024
    print(f"  SCALE={scale}  设备尺寸 {render.W}x{render.H}  "
          f"背景 {tb*1000:6.0f}ms  场景 {ts*1000:6.0f}ms  降采样 {tr*1000:5.0f}ms  "
          f"单帧显存 {mem:.1f}MB")
    return tb, ts, tr


print("性能基准（context 场景，6.4s 时刻）")
for scale in (1.0, 2.0):
    bench(scale)

print()
n_frames = int(tl["total"] * tl["video"]["fps"])
print(f"全片 {n_frames} 帧预估：")
for scale in (1.0, 2.0):
    render.set_scale(scale, (1920, 1080))
    # 背景按 3fps 缓存（超采样下缓存内存受限）
    probes = int(tl["total"] * 3) + 12
    tb, ts, tr = bench(scale, n=2)
    t_bg_total = tb * probes
    t_scene_total = ts * n_frames
    t_resize = tr * n_frames if scale != 1 else 0
    mem_cache = probes * render.W * render.H * 3 / 1024 / 1024
    total = t_bg_total + t_scene_total + t_resize
    print(f"  SCALE={scale}: 背景预渲染 {t_bg_total:5.0f}s + 场景 {t_scene_total:5.0f}s"
          f" + 降采样 {t_resize:4.0f}s = {total/60:5.1f} 分钟   （背景缓存 {mem_cache:.0f}MB）")

render.set_scale(1.0, (1920, 1080))
