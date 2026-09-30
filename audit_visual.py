# -*- coding: utf-8 -*-
"""
视觉审计：量化"画面有多少是靠文字撑的"。

判据（不需要 OCR）：
  文字区域的特征是「局部亮（白字）+ 高频边缘密集」。
  用 8x8 块统计：块内同时满足「有接近纯白的像素」且「边缘密度高」→ 判为文字块。

输出：
  · 文字覆盖率 —— 文字块占画面的比例（越低越好，说明画面靠图形/影像说话）
  · 图形元素数 —— 非文字的、有明确结构的连通区域数
  · 画面元素类型分布
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image
import scipy.ndimage as ndi

ROOT = Path(__file__).resolve().parent


def text_ratio(img):
    """估算文字覆盖率"""
    a = np.asarray(img.convert("L")).astype(np.float32)
    h, w = a.shape
    bh, bw = 8, 8
    gh, gw = h // bh, w // bw
    core = a[:gh * bh, :gw * bw]
    blocks = core.reshape(gh, bh, gw, bw).transpose(0, 2, 1, 3)
    bright = (blocks > 205).mean(axis=(2, 3))          # 接近纯白的比例
    # 边缘密度：块内水平梯度（每块取 bw-1 个差分）
    gx = np.abs(np.diff(core, axis=1))
    gh2, gw2 = gx.shape[0] // bh, gx.shape[1] // bw
    gxb = gx[:gh2 * bh, :gw2 * bw].reshape(gh2, bh, gw2, bw).transpose(0, 2, 1, 3)
    gxb = gxb.mean(axis=(2, 3))
    m = min(bright.shape[0], gxb.shape[0]), min(bright.shape[1], gxb.shape[1])
    bright = bright[:m[0], :m[1]]
    gxb = gxb[:m[0], :m[1]]
    text_block = (bright > 0.055) & (gxb > 22)
    return float(text_block.mean()), text_block


def structure_count(img):
    """非文字区域里有多少个有结构的连通块（近似'图形元素数'）"""
    a = np.asarray(img.convert("L")).astype(np.float32)
    # 与低频背景的差 → 有内容的区域
    bg = ndi.gaussian_filter(a, sigma=28)
    diff = np.abs(a - bg)
    mask = diff > 12
    mask = ndi.binary_opening(mask, iterations=1)
    lab, n = ndi.label(mask)
    sizes = ndi.sum(mask, lab, range(1, n + 1))
    return int((sizes > 900).sum())


def audit(path, name):
    img = Image.open(path).convert("RGB")
    tr, tb = text_ratio(img)
    sc = structure_count(img)
    # 文字占比与图形元素数
    return name, tr, sc


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="verify/cut2")
    ap.add_argument("--pattern", default="[0-9][0-9]_*.png")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    d = ROOT / a.dir
    files = sorted(d.glob(a.pattern), key=lambda p: p.name)[:a.limit]
    print("=" * 74)
    print(f"视觉审计：{d.name}  共 {len(files)} 张")
    print("=" * 74)
    print(f"{'镜头':<16}{'文字覆盖率':>12}{'图形元素数':>12}  判定")
    print("-" * 74)
    trs, scs = [], []
    for p in files:
        n, tr, sc = audit(p, p.stem)
        trs.append(tr)
        scs.append(sc)
        v = "图像驱动" if tr < 0.06 else ("图文并重" if tr < 0.14 else "文字主导")
        print(f"  {n:<14}{tr:>11.1%}{sc:>12}  {v}")
    print("-" * 74)
    print(f"  平均文字覆盖率 {np.mean(trs):.1%}   平均图形元素数 {np.mean(scs):.1f}")
    print()
    print("参考：成熟科普/纪录片画面通常文字覆盖率 3-8%（文字只是标注），")
    print("      文字覆盖率 >15% 的画面观感接近'PPT 文字页'。")
