# -*- coding: utf-8 -*-
"""
静态元素压盖检查（按画幅 × 期数）。

方法：逐个时刻渲染，把所有"文本绘制调用"的包围盒收集起来，
两两检查矩形相交。文字压文字是最难在代码里发现的问题，
肉眼要看很多帧才能碰上一处。

只检查文本与卡片（rounded_glow_card）两类真实元素，
不检查背景装饰（光斑/散景会自然交叠，属设计）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw
import build
import render

ROOT = Path(__file__).resolve().parent
EP = int(sys.argv[1]) if len(sys.argv) > 1 else 2
tl = build.load_timeline(EP)

FRACS = (0.25, 0.5, 0.75, 0.95)


def collect(sc_id, visual, prof, size, frac):
    """收集一次渲染里所有文本与卡片的设备像素包围盒"""
    render.set_scale(1.0, size, profile=prof)
    bg = render.background(1.0)
    sc = [s for s in tl["scenes"] if s["id"] == sc_id][0]
    t = sc["dur"] * frac
    img = bg.copy()
    boxes = []

    orig_dt = render.draw_tracked
    orig_card = render.rounded_glow_card

    # 逐字绘制的实际占位宽度，必须用"渲染后测墨迹"来得到 ——
    # Pillow 的 textlength / textbbox 在逐字绘制场景下都会**低报**宽度
    # （实测 38 号中文逐字排版比 textbbox 宽约 1.9 倍），据此估算包围盒
    # 会把行内相邻词块大面积误判成压盖。这里用真实渲染结果作为基准。
    _scratch = Image.new("RGB", (size[0], size[1]), (0, 0, 0))
    _measuring = [False]

    def rendered_ink_w(text, f, spacing):
        """在临时图上按 draw_tracked 渲染，返回墨迹宽度（设备像素）与起点偏移"""
        _measuring[0] = True
        try:
            tmp = _scratch.copy()
            d = render.sd(ImageDraw.Draw(tmp))
            ox, oy = 40, 60
            render.draw_tracked(d, (ox, oy), text, f, (255, 255, 255), spacing)
            a = np.asarray(tmp.convert("L"))
        finally:
            _measuring[0] = False
        ys, xs = np.nonzero(a > 60)
        if not len(xs):
            return 0, 0
        return int(xs.max() - xs.min() + 1), int(xs.min() - render.SX(ox))

    def dt_spy(draw, xy, text, f, fill, spacing=0.0, anchor_center=False):
        if text.strip() and not _measuring[0]:
            w_dev, lead = rendered_ink_w(text, f, spacing)
            x = render.SX(xy[0]) + lead
            if anchor_center:
                x = render.SX(xy[0]) - w_dev / 2 + lead
            y = render.SY(xy[1])
            boxes.append(("text", text[:16], x, y, x + w_dev, y + f.size * 1.15))
        return orig_dt(draw, xy, text, f, fill, spacing, anchor_center)

    def card_spy(im, box, radius=18, **kw):
        b = render.scaled_box(box)
        boxes.append(("card", "", b[0], b[1], b[2], b[3]))
        return orig_card(im, box, radius=radius, **kw)

    render.draw_tracked = dt_spy
    render.rounded_glow_card = card_spy
    try:
        img = render.RENDERERS[visual](img, t, sc["dur"], sc, tl)
        # 字幕条是最后绘制、层级最高的元素，压住场景内容时会出现两行字糊在一起。
        # 精确算字宽很绕，直接登记它占用的**纵向区间 + 画面中线附近宽度**，
        # 与任何落入该纵带的场景文字判为冲突（保守，但能抓住真问题）。
        cap_h_dev = render.SY(render.LAYOUT["cap_h"]) - render.SY(0)
        record_caption_band(render.SY(render.LAYOUT["caption_y"]) - 6,
                            render.SY(render.LAYOUT["caption_y"]) + cap_h_dev + 6)
    finally:
        render.draw_tracked = orig_dt
        render.rounded_glow_card = orig_card
    return boxes


def overlap(a, b, pad=0):
    ax0, ay0, ax1, ay1 = a[2] + pad, a[3] + pad, a[4] - pad, a[5] - pad
    bx0, by0, bx1, by1 = b[2], b[3], b[4], b[5]
    ix = min(ax1, bx1) - max(ax0, bx0)
    iy = min(ay1, by1) - max(ay0, by0)
    return ix > 2 and iy > 2, ix * iy


# 字幕带：由 collect() 每次登记，供主循环判断"场景文字是否侵入字幕带"
CAPTION_BAND = {"y0": None, "y1": None}


def record_caption_band(y0, y1):
    CAPTION_BAND["y0"], CAPTION_BAND["y1"] = y0, y1


print("=" * 78)
print(f"第 {EP} 期：文本/卡片压盖检查")
print("=" * 78)
total_bad = 0
for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    print(f"\n档案 {prof} ({size[0]}x{size[1]})")
    for sc in tl["scenes"]:
        bad = []
        for frac in FRACS:
            boxes = collect(sc["id"], sc["visual"], prof, size, frac)
            by0, by1 = CAPTION_BAND["y0"], CAPTION_BAND["y1"]
            for i in range(len(boxes)):
                a = boxes[i]
                # 场景文字侵入字幕带 → 冲突（字幕是最高层级，压上去就糊）
                if a[0] == "text" and by0 is not None and a[5] > by0 + 4 and a[3] < by1:
                    vover = min(a[5], by1) - max(a[3], by0)
                    if vover > 4:
                        bad.append((frac, a[1], "字幕带", vover * (a[4] - a[2])))
                for j in range(i + 1, len(boxes)):
                    b = boxes[j]
                    # 卡片之间不算压盖
                    if a[0] == "card" and b[0] == "card":
                        continue
                    ov, area = overlap(a, b, pad=3)
                    if not ov:
                        continue
                    # 文字压文字才算问题；且交叠要「显著」才算：
                    # 同一行内相邻词块（如逐词生成的句子）包围盒会自然邻接，
                    # 字体宽度估算带来的几像素交叠属正常，不算压盖。
                    if a[0] == "text" and b[0] == "text":
                        smaller = min((a[4] - a[2]) * (a[5] - a[3]),
                                      (b[4] - b[2]) * (b[5] - b[3]))
                        if smaller > 0 and area / smaller >= 0.20:
                            bad.append((frac, a[1], b[1], area))
        if bad:
            total_bad += len(bad)
            print(f"  {sc['id']:<9} 文字压盖 {len(bad)} 处")
            seen = set()
            for frac, t1, t2, area in bad[:4]:
                key = (t1, t2)
                if key in seen:
                    continue
                seen.add(key)
                print(f"      t={frac:.2f}  「{t1}」 × 「{t2}」  交叠 {area}px²")
        else:
            print(f"  {sc['id']:<9} ✓ 无文字压盖")

render.set_scale(1.0, (1920, 1080), profile="h")
print()
print("=" * 78)
print(f"合计 {total_bad} 处文字压盖")
print("=" * 78)
sys.exit(1 if total_bad else 0)
