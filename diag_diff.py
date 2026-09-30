# -*- coding: utf-8 -*-
"""
诊断：差异是"整体偏移"还是"真实内容不同"。
方法：把新帧在 ±3 像素范围内平移，看是否能显著降低差异。
如果某个偏移量让差异骤降，说明是 1~2 像素的定位差；否则是内容差异。
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
ref = np.asarray(Image.open(ROOT / "preview" / "_ref_1x.png").convert("L")).astype(np.float32)
new = np.asarray(Image.open(ROOT / "preview" / "_new_1x.png").convert("L")).astype(np.float32)

print("平移搜索（左：dx，上：dy）")
print(f"{'dx':>4}{'dy':>4}{'平均绝对差':>14}{'相对最优':>10}")
best = None
results = []
for dy in range(-3, 4):
    for dx in range(-3, 4):
        # 用裁剪重叠区域比较，避免边界填充干扰
        m = 8
        r = ref[m:-m, m:-m]
        n = new[m + dy:new.shape[0] - m + dy, m + dx:new.shape[1] - m + dx]
        d = np.abs(r - n).mean()
        results.append((d, dx, dy))
results.sort()
base = results[0][0]
for d, dx, dy in results[:7]:
    print(f"{dx:>4}{dy:>4}{d:>14.4f}{d/base:>10.3f}")

print(f"\n最差组合: dx={results[-1][1]} dy={results[-1][2]} 差={results[-1][0]:.4f}")

# 静态区域检查：只看标题文字区（不该有运动）
print("\n分区域检查（不平移）：")
regions = {
    "标题区(静态)": (slice(300, 420), slice(120, 1200)),
    "词卡行(静态)": (slice(600, 740), slice(120, 1140)),
    "注意力弧线(有动画)": (slice(740, 900), slice(120, 1140)),
    "字幕条(静态)": (slice(900, 1000), slice(500, 1420)),
    "纯背景(无内容)": (slice(200, 280), slice(1500, 1900)),
}
for name, (sy, sx) in regions.items():
    d = np.abs(ref[sy, sx] - new[sy, sx])
    print(f"  {name:<22} 平均差={d.mean():7.3f}  最大差={d.max():5.0f}  >8的像素={int((d>8).sum()):7d}")
