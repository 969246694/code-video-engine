# -*- coding: utf-8 -*-
"""出一张竖屏代表性静帧供目视检查"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render

ROOT = Path(__file__).resolve().parent
tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))

render.set_scale(1.0, (1080, 1920), profile="v")
bg = render.background(1.0)
for sid, frac in (("hook", 0.62), ("context", 0.62), ("predict", 0.75), ("train", 0.72)):
    sc = [s for s in tl["scenes"] if s["id"] == sid][0]
    t = sc["dur"] * frac
    img = bg.copy()
    img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
    d = render.sd(render.ImageDraw.Draw(img))
    render.draw_caption(img, d, sc["captions"], sc["start"] + t)
    render.progress_bar(d, t, tl["total"], sc["start"], sc["dur"])
    render.brand(d, tl["video"]["series"])
    img.save(ROOT / "preview" / f"_v_{sid}.png")
    print(f"preview/_v_{sid}.png")
render.set_scale(1.0, (1920, 1080), profile="h")
