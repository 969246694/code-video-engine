# -*- coding: utf-8 -*-
"""对比 1x 与 2x 超采样的抗锯齿质量"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent


def midgray(p):
    """过渡像素占比：梯度落在中间区间的比例，越高说明边缘越柔和"""
    a = np.asarray(Image.open(p).convert("L")).astype(np.int16)
    g = np.concatenate([np.abs(np.diff(a, axis=1)).ravel(),
                        np.abs(np.diff(a, axis=0)).ravel()])
    return float(((g > 2) & (g < 60)).mean() * 100)


def hard_edge(p):
    """硬跳变占比：梯度 > 60 的比例，越低越好"""
    a = np.asarray(Image.open(p).convert("L")).astype(np.int16)
    g = np.concatenate([np.abs(np.diff(a, axis=1)).ravel(),
                        np.abs(np.diff(a, axis=0)).ravel()])
    return float((g > 60).mean() * 100)


print("=" * 74)
print("1x 直出 vs 2x 超采样  抗锯齿对比")
print("=" * 74)
print(f"{'场景':<10}{'过渡像素%(1x)':>16}{'过渡像素%(2x)':>16}{'硬跳变%(1x)':>15}{'硬跳变%(2x)':>15}")
print("-" * 74)
scenes = ["hook", "predict", "token", "context", "train", "outro"]
mg1s, mg2s, he1s, he2s = [], [], [], []
for sc in scenes:
    p1 = ROOT / "preview" / "ss1" / f"{sc}_62.png"
    p2 = ROOT / "preview" / "ss2" / f"{sc}_62.png"
    if not (p1.exists() and p2.exists()):
        continue
    a, b = midgray(p1), midgray(p2)
    c, d = hard_edge(p1), hard_edge(p2)
    mg1s.append(a); mg2s.append(b); he1s.append(c); he2s.append(d)
    print(f"{sc:<10}{a:>15.2f}%{b:>15.2f}%{c:>14.3f}%{d:>14.3f}%")
print("-" * 74)
print(f"{'平均':<10}{np.mean(mg1s):>15.2f}%{np.mean(mg2s):>15.2f}%"
      f"{np.mean(he1s):>14.3f}%{np.mean(he2s):>14.3f}%")
print()
print(f"过渡像素（抗锯齿过渡）提升: {(np.mean(mg2s)-np.mean(mg1s))/np.mean(mg1s)*100:+.1f}%")
print(f"硬跳变（锯齿硬边）下降:   {(np.mean(he2s)-np.mean(he1s))/np.mean(he1s)*100:+.1f}%")
