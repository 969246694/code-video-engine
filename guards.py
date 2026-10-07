# -*- coding: utf-8 -*-
"""静帧守卫：模型自检用（不需要人看图也能发现排版事故）。
   用法：uv run --python 3.12 --with numpy --with pillow python guards.py build/stills_dir
   检查：安全边距 / 字幕带安静 / 曝光与削顶 / 字幕板对比度"""
import sys, glob
from pathlib import Path
import numpy as np
from PIL import Image

W, H = 1920, 1080
CAP_TOP = H - 176
SAFE_X, SAFE_Y = 56, 44
INK = 105          # 文字/亮元素的判定阈值

def check(path):
    g = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    out = []
    # 1) 安全边距：亮元素不得贴边
    mask = g > INK
    ys, xs = np.where(mask)
    if len(xs):
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        viol = (x0 < SAFE_X) or (x1 > W - SAFE_X) or (y0 < SAFE_Y) or (y1 > H - SAFE_Y)
        out.append(("安全边距", f"bbox=({x0},{y0})-({x1},{y1})", not viol))
    else:
        out.append(("安全边距", "画面无亮元素", False))
    # 2) 字幕带上方安静带
    band = g[CAP_TOP - 44:CAP_TOP - 6]
    q = float((band > 110).mean())
    out.append(("字幕带安静", f"{q*100:.2f}%", q < 0.02))
    # 3) 曝光
    # 削顶判定：暗底片里"大面积纯白字"是设计意图（要脆要亮），不是过曝。
    # 只有整幅过曝（>8%）或画面整体发灰（mean 越界）才算事故。
    mean = float(g.mean()); clip = float((g >= 250).mean())
    out.append(("曝光/削顶", f"mean={mean:.1f} clip={clip*100:.2f}%", 12 <= mean <= 110 and clip < 0.08))
    # 4) 字幕板对比度（板内文字 vs 板底）
    plate = g[CAP_TOP + 16:CAP_TOP + 72, W // 2 - 420:W // 2 + 420]
    c = float(np.percentile(plate, 97) - np.percentile(plate, 20))
    out.append(("字幕板对比度", f"{c:.0f}", c >= 90))
    return out

files = sorted(glob.glob(str(Path(sys.argv[1]) / "*.png"))) if len(sys.argv) > 1 else []
if not files:
    sys.exit("用法: python guards.py <静帧目录>")
bad = 0
for f in files:
    rows = check(f)
    fails = [r for r in rows if not r[2]]
    bad += len(fails)
    tag = "OK  " if not fails else "FAIL"
    print(f"[{tag}] {Path(f).name}")
    for name, val, ok in rows:
        if not ok:
            print(f"        !! {name}: {val}")
print("=" * 60)
print(f"静帧 {len(files)} 张，问题 {bad} 处 -> {'全部通过' if bad == 0 else '需修'}")
