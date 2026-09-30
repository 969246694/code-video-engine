# -*- coding: utf-8 -*-
"""渲染多镜头版第 2 期的静帧（每个镜头一张），并测速"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ae
import compose_ep2 as EP

ROOT = Path(__file__).resolve().parent
SS = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
MB = 0.5 if SS <= 1.0 else 0.5

plan = EP.build_shots()
out = ROOT / "verify" / "cut2"
out.mkdir(parents=True, exist_ok=True)

print("=" * 80)
print(f"多镜头版 · 渲染静帧（超采样 {SS}x，运动模糊 0.5）")
print("=" * 80)
times = []
for i, (t0, t1, comp) in enumerate(plan, 1):
    local = (t1 - t0) * 0.72
    t0c = time.time()
    img = ae.still(comp, EP.CW, EP.CH, local, ss=SS, motion_blur=0.5)
    dt = time.time() - t0c
    times.append(dt)
    img.save(out / f"{i:02d}_{comp.name}.png")
    print(f"  {i:>2} {comp.name:<6} {t1-t0:>4.1f}s  渲染 {dt:>5.2f}s")

print()
tot_video = sum(t1 - t0 for t0, t1, _ in plan)
mean = float(np.mean(times))
print(f"  单帧均值 {mean:.2f}s  峰值 {max(times):.2f}s")
print(f"  全片 {int(tot_video*30)+30} 帧（含片头片尾）→ 预计 "
      f"{mean*(int(tot_video*30)+30)/60:.1f} 分钟")
