# -*- coding: utf-8 -*-
"""渲染器冒烟测试 + 性能测量（不编码视频）"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
import render  # noqa: E402

tl = json.loads(Path("build/timeline.json").read_text(encoding="utf-8"))
bg = render.background(1.0)

# 1) 全场景多时刻渲染，捕获异常
t0 = time.time()
count = 0
for sc in tl["scenes"]:
    for frac in (0.05, 0.25, 0.45, 0.65, 0.85, 0.99):
        t = sc["dur"] * frac
        img = bg.copy()
        try:
            img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
            d = render.ImageDraw.Draw(img)
            render.draw_caption(img, d, sc["captions"], sc["start"] + t)
            render.progress_bar(d, t, tl["total"], sc["start"], sc["dur"])
            render.brand(d, tl["video"]["series"])
            render.apply_fade(img, render.scene_alpha(t, sc["dur"]))
        except Exception as e:
            import traceback
            print(f"FAIL scene={sc['id']} frac={frac} t={t:.2f}: {type(e).__name__}: {e}")
            traceback.print_exc()
            sys.exit(1)
        count += 1
el = time.time() - t0
print(f"OK  {count} 帧全部渲染通过")
print(f"    {el/count*1000:.0f} ms/帧  ->  60s@30fps 预计 {el/count*1800/60:.1f} 分钟")

# 2) 单帧背景/绘制耗时拆解
t1 = time.time()
for i in range(10):
    render.background(i * 0.1)
print(f"    background: {(time.time()-t1)/10*1000:.0f} ms/帧")

sc = tl["scenes"][4]
t1 = time.time()
for i in range(10):
    img = bg.copy()
    img = render.RENDERERS["train"](img, 8.0, sc["dur"], sc, tl)
print(f"    train 场景绘制: {(time.time()-t1)/10*1000:.0f} ms/帧")
