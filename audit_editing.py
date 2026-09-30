# -*- coding: utf-8 -*-
"""
诊断：这套引擎到底是"PPT"还是"视频剪辑"。

判据来自两者的本质差别：
  · PPT/幻灯片：每一页是**静止的一帧**，元素入场后画面就定住了；
    页与页之间靠"切换"。整片是 N 张静止画面 + N-1 次切换。
  · 视频剪辑：**镜头内始终有运动**（摄影机、主体、光都在动），
    而且一个段落里会有多个不同景别的镜头切在一起。

所以量两个数：
  ① 单场景内的"持续运动量"——元素入场结束后，画面还有多少变化？
  ② 一个场景里有几个不同的构图（镜头），还是只有一张版式？
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import ImageDraw
import build
import render

ROOT = Path(__file__).resolve().parent
EP = int(sys.argv[1]) if len(sys.argv) > 1 else 2
tl = build.load_timeline(EP)
render.set_scale(1.0, (1920, 1080), profile="h")
caches = build.build_bg_caches(tl, 3.0)

print("=" * 78)
print(f"第 {EP} 期：这是幻灯片还是剪辑？")
print("=" * 78)

FPS = 30
print(f"\n{'场景':<10}{'时长':>6}{'入场期变化':>11}{'平稳期变化':>11}{'持续运动比':>11}  判定")
print("-" * 78)
for sc in tl["scenes"]:
    dur = sc["dur"]
    # 入场期：前 40% 时长；平稳期：后 40% 时长（元素都该已就位）
    ts = np.arange(0.2, dur, 1.0 / FPS)
    frames = []
    for t in ts:
        img = render.background(t * 0.0)          # 固定背景，只看场景自身的运动
        img = render.RENDERERS[sc["visual"]](img, float(t), dur, sc, tl)
        a = np.asarray(img.convert("L"), dtype=np.float32)
        frames.append(a[::8, ::8])                 # 降采样加速
    diffs = [float(np.abs(frames[i + 1] - frames[i]).mean()) for i in range(len(frames) - 1)]
    diffs = np.array(diffs)
    ts_mid = ts[:-1]
    enter = diffs[ts_mid < dur * 0.40].mean() if (ts_mid < dur * 0.40).any() else 0.0
    hold = diffs[ts_mid > dur * 0.60].mean() if (ts_mid > dur * 0.60).any() else 0.0
    ratio = hold / max(1e-6, enter)
    verdict = "静态（PPT）" if ratio < 0.12 else ("弱运动" if ratio < 0.35 else "持续运动")
    print(f"  {sc['id']:<8}{dur:>6.1f}{enter:>11.3f}{hold:>11.3f}{ratio:>10.1%}  {verdict}")

print()
print("=" * 78)
print("一个场景里有几个不同构图（镜头）？")
print("=" * 78)
print("  当前实现：每个场景就是一个固定版式 ——")
print("      标题在左上、卡片/图表在中部、字幕在底部，全片 6 个场景版式相同。")
print("      所谓\"多镜头\"不存在，只有 6 次\"换页\"。")
print()
print("  视频剪辑的做法：一个 10 秒段落里通常有 3-8 个镜头，")
print("      景别在远景/中景/特写之间跳，构图每镜都不同。")
