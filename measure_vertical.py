# -*- coding: utf-8 -*-
"""
量化竖屏的真实缺口：逐场景测出「有多少内容落在可见宽度之外」。

竖屏可用宽度 = 1080px；参照坐标系是 1920 宽，映射比例 0.5625。
所以参照坐标 x > 1920 的内容必然被裁掉，x ∈ (1080, 1920) 的部分也会被裁。
这里统计每个场景在竖屏下被裁掉的元素面积占比。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import render

ROOT = Path(__file__).resolve().parent
tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))


def render_scene(prof, size, scene_id, frac=0.62):
    render.set_scale(1.0, size, profile=prof)
    bg = render.background(1.0)
    sc = [s for s in tl["scenes"] if s["id"] == scene_id][0]
    t = sc["dur"] * frac
    img = bg.copy()
    img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
    d = render.sd(render.ImageDraw.Draw(img))
    render.draw_caption(img, d, sc["captions"], sc["start"] + t)
    render.progress_bar(d, t, tl["total"], sc["start"], sc["dur"])
    render.brand(d, tl["video"]["series"])
    return img


def content_mask(img, bg):
    """前景内容掩码：与纯背景差异明显的像素"""
    a = np.asarray(img.convert("L")).astype(np.int16)
    b = np.asarray(bg.convert("L")).astype(np.int16)
    return np.abs(a - b) > 18


print("=" * 78)
print("竖屏缺口量化（判断每个场景在 1080x1920 下有多少内容被裁掉）")
print("=" * 78)
print(f"{'场景':<10}{'内容像素':>10}{'可见区占比':>12}{'结论':<28}")
print("-" * 78)

render.set_scale(1.0, (1080, 1920), profile="v")
bg_v = render.background(1.0)
total_visible = 0
for sc in tl["scenes"]:
    img = render_scene("v", (1080, 1920), sc["id"])
    m = content_mask(img, bg_v)
    n = int(m.sum())
    # 可见区域：整幅画布都是可见的，所以占比恒为 100%。
    # 真正的判据是「内容是否顶到左右边缘」——顶到说明被裁。
    cols = m.sum(axis=0)
    left_dense = cols[:6].sum() / max(1, cols.sum())
    right_dense = cols[-6:].sum() / max(1, cols.sum())
    edge = max(left_dense, right_dense)
    if edge > 0.02:
        verdict = f"✗ 内容顶到边缘被裁 ({edge*100:.1f}%)"
    else:
        verdict = "✓ 有安全边距"
    print(f"{sc['id']:<10}{n:>10}{'':>12}{verdict:<28}")
    total_visible += 1

print()
print("=" * 78)
print("横屏对照（应全部有安全边距）")
print("=" * 78)
render.set_scale(1.0, (1920, 1080), profile="h")
bg_h = render.background(1.0)
for sc in tl["scenes"]:
    img = render_scene("h", (1920, 1080), sc["id"])
    m = content_mask(img, bg_h)
    cols = m.sum(axis=0)
    edge = max(cols[:8].sum(), cols[-8:].sum()) / max(1, cols.sum())
    verdict = "✓ 有安全边距" if edge <= 0.02 else f"✗ 顶到边缘 ({edge*100:.1f}%)"
    print(f"  {sc['id']:<10}{verdict}")

render.set_scale(1.0, (1920, 1080), profile="h")
