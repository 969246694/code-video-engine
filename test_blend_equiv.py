# -*- coding: utf-8 -*-
"""
验证 blend 优化没有改变输出。

优化前：整幅 1920x1080 逐像素运算
优化后：只对图层非空包围盒运算
两者结果应当**逐像素完全一致**（除了包围盒外的区域本来就是恒等）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ae


def old_blend(bottom, top, alpha, mode):
    """优化前的实现（整幅运算）"""
    if mode == "normal":
        return bottom * (1 - alpha) + top * alpha
    if mode == "add":
        return np.clip(bottom + top * alpha, 0, 255)
    if mode == "screen":
        b = bottom / 255.0
        s = 1 - (1 - b) * (1 - top / 255.0)
        return np.clip(bottom * (1 - alpha) + s * 255.0 * alpha, 0, 255)
    if mode == "multiply":
        m = bottom * top / 255.0
        return np.clip(bottom * (1 - alpha) + m * alpha, 0, 255)
    if mode == "overlay":
        b = bottom / 255.0
        s = np.where(b < 0.5, 2 * b * (top / 255.0),
                     1 - 2 * (1 - b) * (1 - top / 255.0))
        return np.clip(bottom * (1 - alpha) + s * 255.0 * alpha, 0, 255)
    if mode == "soft_light":
        b = bottom / 255.0
        s = np.where(top / 255.0 < 0.5,
                     2 * b * (top / 255.0) + b * b * (1 - 2 * (top / 255.0)),
                     2 * b * (1 - top / 255.0) + np.sqrt(np.maximum(0, b)) * (2 * (top / 255.0) - 1))
        return np.clip(bottom * (1 - alpha) + s * 255.0 * alpha, 0, 255)
    return bottom * (1 - alpha) + top * alpha


rng = np.random.default_rng(0)
H, W = 240, 320
print("=" * 66)
print("blend 优化等价性验证（新实现 vs 旧实现，逐像素比较）")
print("=" * 66)
print(f"{'混合模式':<14}{'最大绝对差':>14}{'结论':>10}")
print("-" * 66)
ok = True
for mode in ae.BLEND_MODES:
    bottom = rng.uniform(0, 255, (H, W, 3)).astype(np.float32)
    top = rng.uniform(0, 255, (H, W, 3)).astype(np.float32)
    # 模拟一个只有局部内容的图层
    alpha = np.zeros((H, W, 1), dtype=np.float32)
    alpha[60:140, 90:200] = rng.uniform(0, 1, (80, 110, 1)).astype(np.float32)
    a1 = old_blend(bottom.copy(), top, alpha, mode)
    a2 = ae.blend(bottom.copy(), top, alpha, mode)
    d = float(np.abs(a1 - a2).max())
    good = d < 0.02
    ok = ok and good
    print(f"  {mode:<12}{d:>14.6f}{'✓ 一致' if good else '✗ 不一致':>10}")

print("-" * 66)
print(f"{'全部一致 ✓' if ok else '存在差异 ✗'}")
print()
print("说明：包围盒外 alpha 为 0，两种实现都是恒等变换，因此只需比较盒内。")
print("      上面是**整幅**比较结果，包含盒外区域。")
sys.exit(0 if ok else 1)
