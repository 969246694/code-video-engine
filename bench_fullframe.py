# -*- coding: utf-8 -*-
"""
端到端帧成本基准：包含完整帧流程（背景 + 场景 + 字幕 + 进度条 + 转场 + 降采样）。
用于判断 2x 超采样是否能接受。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
import render
import build

ROOT = Path(__file__).resolve().parent
tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))

# 预生成背景缓存（与 build 一致：3fps）
def make_cache(scale):
    render.set_scale(scale, (1920, 1080))
    n = int(tl["total"] * 3) + 12
    t0 = time.time()
    cache = [render.background(i / 3.0) for i in range(n)]
    return cache, time.time() - t0


print("=" * 78)
print("端到端帧成本（每个场景取 3 个时刻，共 18 帧）")
print("=" * 78)
print(f"{'倍率':<6}{'设备尺寸':<14}{'背景缓存':<12}{'单帧均值':<12}{'单帧峰值':<12}{'全片预估':<12}")
print("-" * 78)

results = {}
for scale in (1.0, 2.0):
    cache, t_cache = make_cache(scale)
    times = []
    for sc in tl["scenes"]:
        for frac in (0.2, 0.55, 0.9):
            t_abs = sc["start"] + sc["dur"] * frac
            t0 = time.time()
            img = build.render_active_frame(tl, t_abs, cache)
            if scale != 1.0:
                img = img.resize((1920, 1080), Image.LANCZOS)
            times.append(time.time() - t0)
    n_frames = int(tl["total"] * tl["video"]["fps"])
    total = t_cache + (sum(times) / len(times)) * n_frames
    results[scale] = (times, t_cache, total)
    print(f"{scale:<6}{f'{render.W}x{render.H}':<14}{t_cache:>9.0f}s  "
          f"{sum(times)/len(times)*1000:>8.0f}ms  {max(times)*1000:>8.0f}ms  "
          f"{total/60:>8.1f}min")

print()
print("逐场景单帧耗时（ms）：")
print(f"{'场景':<12}{'1x':>10}{'2x':>10}{'倍率':>8}")
for i, sc in enumerate(tl["scenes"]):
    a = sum(results[1.0][0][i*3:(i+1)*3]) / 3 * 1000
    b = sum(results[2.0][0][i*3:(i+1)*3]) / 3 * 1000
    print(f"{sc['id']:<12}{a:>10.0f}{b:>10.0f}{b/a:>8.1f}x")

render.set_scale(1.0, (1920, 1080))
