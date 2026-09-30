# -*- coding: utf-8 -*-
"""
分辨率无关性验证：
  · 各场景在 1x 与 2x 下的内容包围盒应几乎一致（证明换倍率不改变布局）
  · 2x 的锯齿硬边应明显低于 1x
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent


def bbox(p):
    a = np.asarray(Image.open(p).convert("L")).astype(np.int16)
    med = np.median(a, axis=1, keepdims=True)
    ys, xs = np.nonzero(np.abs(a - med) > 12)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None


def hard_edge(p):
    a = np.asarray(Image.open(p).convert("L")).astype(np.int16)
    g = np.concatenate([np.abs(np.diff(a, axis=1)).ravel(),
                        np.abs(np.diff(a, axis=0)).ravel()])
    return float((g > 60).mean() * 100)


scenes = ["hook", "predict", "token", "context", "train", "outro"]

print("=" * 82)
print("布局一致性：1x vs 2x 内容包围盒")
print("=" * 82)
all_ok = True
for sc in scenes:
    p1 = ROOT / "preview" / "ss1" / f"{sc}_62.png"
    p2 = ROOT / "preview" / "ss2" / f"{sc}_62.png"
    b1, b2 = bbox(p1), bbox(p2)
    if not (b1 and b2):
        print(f"  {sc:<9} 无法定位内容")
        continue
    d = [b2[i] - b1[i] for i in range(4)]
    ok = all(abs(x) <= 8 for x in d)
    all_ok &= ok
    print(f"  {sc:<9} 1x={b1}  2x={b2}  偏差={d}  {'✓' if ok else '✗ 不一致'}")
print(f"\n  结论: {'✓ 换倍率不改变布局（分辨率无关成立）' if all_ok else '✗ 存在布局漂移'}")

print()
print("=" * 82)
print("抗锯齿质量：锯齿硬边占比（越低越好）")
print("=" * 82)
print(f"  {'场景':<10}{'1x':>12}{'2x':>12}{'改善':>12}")
h1s, h2s = [], []
for sc in scenes:
    a = hard_edge(ROOT / "preview" / "ss1" / f"{sc}_62.png")
    b = hard_edge(ROOT / "preview" / "ss2" / f"{sc}_62.png")
    h1s.append(a); h2s.append(b)
    print(f"  {sc:<10}{a:>11.3f}%{b:>11.3f}%{(b-a)/a*100:>11.0f}%")
m1, m2 = np.mean(h1s), np.mean(h2s)
print(f"  {'平均':<10}{m1:>11.3f}%{m2:>11.3f}%{(m2-m1)/m1*100:>11.0f}%")
print(f"\n  结论: 锯齿硬边下降 {(m1-m2)/m1*100:.0f}%")
