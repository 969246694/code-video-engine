# -*- coding: utf-8 -*-
"""用 AI 背景渲染第 2 期静帧，检查合成效果"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import ImageDraw
import build
import render

ROOT = Path(__file__).resolve().parent
EP = int(sys.argv[1]) if len(sys.argv) > 1 else 2
tl = build.load_timeline(EP)
out = ROOT / "verify" / f"e{EP}"
out.mkdir(parents=True, exist_ok=True)

for prof, size in (("h", (1920, 1080)), ("v", (1080, 1920))):
    render.set_scale(1.0, size, profile=prof)
    print(f"\n档案 {prof}")
    for sc in tl["scenes"]:
        build.ensure_bg(sc, tl)
        bg = render.background(0.0)
        t = sc["dur"] * 0.75
        img = bg.copy()
        img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
        d = render.sd(ImageDraw.Draw(img))
        render.draw_caption(img, d, sc["captions"], sc["start"] + t)
        render.progress_bar(d, t, tl["total"], sc["start"], sc["dur"])
        render.brand(d, tl["video"]["series"])
        fn = out / f"ai_{prof}_{sc['id']}.png"
        img.save(fn)
        bgname = (sc.get("bg") or "程序化渐变").split("/")[-1][:28]
        print(f"  {sc['id']:<9} {bgname}")
print("\n已导出")
