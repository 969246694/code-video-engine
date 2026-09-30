# -*- coding: utf-8 -*-
"""
双画幅冒烟测试：横屏 1920x1080 与竖屏 1080x1920，全部场景多时刻渲染。
用法: python test_formats.py [期数]
同时检查竖屏下内容是否越界（左右裁切）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import build
import render

ROOT = Path(__file__).resolve().parent
EP = int(sys.argv[1]) if len(sys.argv) > 1 else 1
tl = build.load_timeline(EP)

print("=" * 76)
print(f"双画幅冒烟测试（第 {EP} 期《{tl['video']['title']}》）")
print("=" * 76)

for fmt, size, prof in (("h", (1920, 1080), "h"), ("v", (1080, 1920), "v")):
    render.set_scale(1.0, size, profile=prof)
    bg = render.background(1.0)
    n = 0
    t0 = time.time()
    for sc in tl["scenes"]:
        for frac in (0.1, 0.4, 0.7, 0.95):
            t = sc["dur"] * frac
            img = bg.copy()
            try:
                img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
                d = render.sd(render.ImageDraw.Draw(img))
                render.draw_caption(img, d, sc["captions"], sc["start"] + t)
                render.progress_bar(d, t, tl["total"], sc["start"], sc["dur"])
                render.brand(d, tl["video"]["series"])
                render.apply_fade(img, render.scene_alpha(t, sc["dur"]))
            except Exception:
                import traceback
                print(f"  FAIL {fmt} {sc['id']} frac={frac}")
                traceback.print_exc()
                sys.exit(1)
            n += 1
    el = time.time() - t0
    print(f"  {fmt}: {n} 帧通过  {el/n*1000:.0f}ms/帧  画布 {render.W}x{render.H}  "
          f"可用宽度 AV={render.AV():.0f}  Y()拉伸={render.Y(878)-render.Y(544):.0f}px")
    # 出一张代表性静帧（用中间那个场景，避免硬编码某期的场景 id）
    sc = tl["scenes"][len(tl["scenes"]) // 2]
    t = sc["dur"] * 0.62
    img = bg.copy()
    img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
    d = render.sd(render.ImageDraw.Draw(img))
    render.draw_caption(img, d, sc["captions"], sc["start"] + t)
    render.progress_bar(d, t, tl["total"], sc["start"], sc["dur"])
    render.brand(d, tl["video"]["series"])
    img.save(ROOT / "preview" / f"_smoke_ep{EP}_{fmt}.png")

# 元素越界审计：拦截 rounded_glow_card，检查包围盒是否出画布。
# 之前这里用「边缘列差分」来判断，但那会被顶部进度条（横贯整幅、线宽很细）
# 干扰，产生 74+ 的假跳变 —— 已换成直接审计元素几何，可靠得多。
print()
print("元素越界审计（卡片包围盒是否出画布）:")
for fmt, size, prof in (("h", (1920, 1080), "h"), ("v", (1080, 1920), "v")):
    render.set_scale(1.0, size, profile=prof)
    W, H = render.W, render.H
    bad = []
    for sc in tl["scenes"]:
        t = sc["dur"] * 0.62
        img = render.background(1.0)
        orig = render.rounded_glow_card

        def wrapped(im, box, radius=18, _sc=sc, **kw):
            b = render.scaled_box(box)
            if b[0] < -4 or b[2] > W + 4 or b[1] < -4 or b[3] > H + 4:
                bad.append((_sc["id"], tuple(round(v) for v in b)))
            return orig(im, box, radius=radius, **kw)

        render.rounded_glow_card = wrapped
        try:
            img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
        finally:
            render.rounded_glow_card = orig
    flag = "✓ 无越界" if not bad else f"✗ {len(bad)} 处越界"
    print(f"  {fmt} ({W}x{H}): {flag}")
    for sid, b in bad[:3]:
        print(f"      {sid} box={b}")

render.set_scale(1.0, (1920, 1080), profile="h")
