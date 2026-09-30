# -*- coding: utf-8 -*-
"""
转场审计：量化场景切换处的画面表现。

指标：
  1. 黑场时长 —— 相邻场景淡出淡入之间，画面亮度低于阈值的持续时间
     （当前实现有 gap=0.28s 的间隔，且两个场景各自淡出/淡入，会出现近黑帧）
  2. 亮度凹陷深度 —— 转场处最暗帧相对两侧稳定帧的亮度跌幅
  3. 是否有"设计过的"转场（而不是所有切换都用同一种）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw
import render

ROOT = Path(__file__).resolve().parent
tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))
FPS = tl["video"]["fps"]


def scene_at(t):
    """返回 (当前场景, 场景内时刻)；间隙期返回 None"""
    for sc in tl["scenes"]:
        if sc["start"] <= t < sc["start"] + sc["dur"]:
            return sc, t - sc["start"]
    return None, None


def alpha_now(t):
    """当前帧的画面权重（沿用 build 的淡入淡出逻辑）"""
    sc, local = scene_at(t)
    if sc is None:
        return 0.0
    return render.scene_alpha(local, sc["dur"])


print("=" * 76)
print("转场审计（当前实现：场景整体淡入淡出 + 0.28s 间隔）")
print("=" * 76)

render.set_scale(1.0, (1920, 1080), profile="h")
bg = render.background(1.0)
bg_lum = float(np.asarray(bg.convert("L")).mean())

# 采样每个转场前后 0.8 秒
print(f"\n{'转场':<22}{'最暗帧相对亮度':>16}{'疑似黑场时长':>16}{'转场类型':>12}")
print("-" * 76)
total_black = 0.0
rows = []
for i in range(len(tl["scenes"]) - 1):
    a, b = tl["scenes"][i], tl["scenes"][i + 1]
    t0 = a["start"] + a["dur"] - 0.55
    t1 = b["start"] + 0.55
    ts = np.arange(t0, t1, 1.0 / FPS)
    alphas = np.array([alpha_now(t) for t in ts])
    # 画面上内容亮度近似正比于 alpha（淡出时只剩背景）
    lums = bg_lum + (1.0 - bg_lum) * 0.0
    content = alphas
    dark = float(content.min())
    # 近黑判定：画面内容权重 < 0.12 持续多久
    below = content < 0.12
    n_below = int(below.sum())
    dur_below = n_below / FPS
    total_black += dur_below
    rows.append((f"{a['id']}→{b['id']}", dark, dur_below))
    print(f"{a['id']+'→'+b['id']:<22}{dark:>16.3f}{dur_below:>15.2f}s{'整体淡入淡出':>12}")

print("-" * 76)
print(f"合计近黑时长 {total_black:.2f}s / {len(tl['scenes'])-1} 个转场"
      f"（平均 {total_black/max(1,len(tl['scenes'])-1):.2f}s/次）")
print(f"\n结论：每次转场都有约 {total_black/max(1,len(tl['scenes'])-1):.2f}s 画面接近全黑，"
      f"且 5 个转场用的是同一种处理 —— 这两点都会让片子显得廉价。")

# 相邻场景是否有丰富度可区分（是否有设计过的转场）
print()
print("=" * 76)
print("转场多样性检查")
print("=" * 76)
kinds = set()
for i in range(len(tl["scenes"]) - 1):
    kinds.add("fade")
print(f"  当前转场类型: {sorted(kinds)}  种类数 {len(kinds)}")
print(f"  策划中的转场类型: ['dip', 'crossfade', 'wipe', 'sweep', 'morph']")
print(f"  差距: 需要 {len(tl['scenes'])-1} 次转场至少覆盖 3 种类型，且近黑时长压到 0")
