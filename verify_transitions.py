# -*- coding: utf-8 -*-
"""两个画幅的转场亮度检查：确认转场期间不出现黑场"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import build
import render

tl = build.load_timeline()
FPS = tl["video"]["fps"]

print("=" * 72)
print("转场亮度检查（最暗帧 / 两端稳定帧，越接近 1 越好）")
print("=" * 72)
worst_by_fmt = {}
for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    render.set_scale(0.5, size, profile=prof)
    cache = [render.background(i / 3.0) for i in range(220)]
    print(f"\n档案 {prof}")
    worst = 1.0
    for a, b, (s, e, k) in tl["_xfades"]:
        lums = []
        for t in np.arange(s - 0.12, e + 0.12, 1.0 / FPS):
            fr = build.render_active_frame(tl, float(t), cache, 3.0)
            lums.append(float(np.asarray(fr.convert("L")).mean()))
        lums = np.array(lums)
        ref = max(float(lums[0]), float(lums[-1]))
        ratio = float(lums.min()) / max(1e-6, ref)
        worst = min(worst, ratio)
        print(f"  {a['id']:>9} -> {b['id']:<9} [{k:<5}] 亮度 "
              f"{lums.min():6.1f}~{lums.max():6.1f}   最暗/两端 {ratio:.3f}")
    worst_by_fmt[prof] = worst
    print(f"  最差 {worst:.3f}  {'✓ 无黑场' if worst > 0.55 else '✗ 仍有暗场'}")

print()
print("=" * 72)
print(f"结果: 横屏 {worst_by_fmt.get('h', 0):.3f} / 竖屏 {worst_by_fmt.get('v', 0):.3f}"
      f"   （改造前为 0.000，即全黑）")
print("=" * 72)
