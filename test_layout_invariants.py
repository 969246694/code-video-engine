# -*- coding: utf-8 -*-
"""
版式层不变量测试。

这些是「必须永远成立」的性质，改版式代码后立刻跑，比事后看渲染快得多：
  1. Y() 在两个档案下都必须是恒等映射（竖屏坐标是手工绝对坐标，不能被二次拉伸）
  2. AV() 横屏返回参照宽度、竖屏返回竖屏设计宽度
  3. 字幕条必须落在画布内且不与底部冲突
  4. 标题自动缩号后必须不超出可用宽度
  5. 超采样倍率不改变几何（1x 与 2x 的内容位置一致）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import json
import numpy as np
from PIL import Image, ImageDraw
import render

fails = []


def check(cond, msg):
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        fails.append(msg)


print("=" * 76)
print("[1] Y() 恒等性（两个档案都必须成立）")
print("=" * 76)
for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    render.set_scale(1.0, size, profile=prof)
    errs = [abs(render.Y(v) - v) for v in (544, 620, 700, 780, 878)]
    check(max(errs) < 1e-6,
          f"档案 {prof}: Y() 最大偏差 {max(errs):.2e}（竖屏若不为恒等，元素会被二次位移）")

print()
print("=" * 76)
print("[2] AV() 可用宽度")
print("=" * 76)
render.set_scale(1.0, (1920, 1080), profile="h")
check(render.AV() == 1920, f"横屏 AV()={render.AV():.0f}（应为参照宽度 1920）")
render.set_scale(1.0, (1080, 1920), profile="v")
check(render.AV() == 1080, f"竖屏 AV()={render.AV():.0f}（应为竖屏设计宽度 1080）")

print()
print("=" * 76)
print("[3] 字幕条位置与画布边界")
print("=" * 76)
for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    render.set_scale(1.0, size, profile=prof)
    L = render.LAYOUT
    top = L["caption_y"]
    bottom = top + L["cap_h"]
    h_design = 1080 if prof == "h" else 1920
    check(bottom <= h_design - 20,
          f"档案 {prof}: 字幕条 y {top}~{bottom} 在画布高 {h_design} 内（底部留白 "
          f"{h_design - bottom}px）")
    check(L["cap_h"] >= L["cap_size"] * 1.6,
          f"档案 {prof}: 字幕条高度 {L['cap_h']} 容纳字号 {L['cap_size']}")

print()
print("=" * 76)
print("[4] 标题自动缩号（各场景真实标题都必须不溢出）")
print("=" * 76)
tl = json.loads((Path(__file__).resolve().parent / "build" / "timeline.json")
                .read_text(encoding="utf-8"))
for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    render.set_scale(1.0, size, profile=prof)
    avail = render.AV()
    margin = render.LAYOUT["margin"]
    limit = avail - margin * 2
    worst = 0
    worst_sc = ""
    for sc in tl["scenes"]:
        lines = sc["title"].split("\n")
        fs = render._fit_title_size(lines)
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        for ln in lines:
            w = render.tw(probe, ln, render.font(fs, "bold"), 4.5)
            if w > worst:
                worst, worst_sc = w, f"{sc['id']}「{ln}」"
    check(worst <= limit,
          f"档案 {prof}: 最宽标题 {worst:.0f}px ≤ 限宽 {limit:.0f}px（{worst_sc}）")

print()
print("=" * 76)
print("[5] 超采样不改变几何（1x 与 2x 内容位置一致）")
print("=" * 76)


def content_bbox(scale, prof, size):
    """
    内容包围盒：与**同一倍率的纯背景**相减，只保留场景绘制的内容。

    这样做的原因：直接对整幅图做阈值检测会把背景本身（暗角、散景、光斑）
    也算成"内容"，而这些是低频渐变的，1x 与 2x 的阈值越界位置不同，
    会产生几十像素的假偏差。用背景差分就干净了。
    """
    render.set_scale(scale, size, profile=prof)
    bg = render.background(2.0)
    sc = [s for s in tl["scenes"] if s["id"] == "context"][0]
    t = sc["dur"] * 0.62
    img = bg.copy()
    img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
    if scale != 1.0:
        img = img.resize(size, Image.LANCZOS)
        bg = bg.resize(size, Image.LANCZOS)
    a = np.asarray(img.convert("L")).astype(np.int16)
    b = np.asarray(bg.convert("L")).astype(np.int16)
    delta = np.abs(a - b)
    # 阈值取 16：抗锯齿会把边缘能量摊开，高阈值下 2x 的弱边缘会落到阈值以下
    # （实测阈值 24 时假报 22px 偏差，阈值 ≤18 时两者完全一致）。
    # 用低阈值可以测出真实的几何位置，而不受抗锯齿分布影响。
    ys, xs = np.nonzero(delta > 16)
    if not len(xs):
        return (0, 0, 0, 0)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    b1 = content_bbox(1.0, prof, size)
    b2 = content_bbox(2.0, prof, size)
    d = [abs(b2[i] - b1[i]) for i in range(4)]
    # 容差 6px：文字边缘在两种倍率下的抗锯齿分布不同，包围盒边缘会有几个像素的
    # 差（实测 v 档右沿 5px）。这是渲染细节差异，不是布局位移 —— 布局问题会是
    # 几十像素量级（之前 x 用错轴时是 22~479px）。
    check(max(d) <= 6,
          f"档案 {prof}: 1x 包围盒 {b1} vs 2x {b2}（最大偏差 {max(d)}px）")

render.set_scale(1.0, (1920, 1080), profile="h")
print()
print("=" * 76)
print(f"结果: {'全部通过 ✓' if not fails else f'{len(fails)} 项失败 ✗'}")
print("=" * 76)
sys.exit(0 if not fails else 1)
