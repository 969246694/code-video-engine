# -*- coding: utf-8 -*-
"""
横屏一致性检查：版式档案重构后，横屏渲染应与之前完全一致。
同时打印 Y() 映射（横屏应为恒等变换）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
import numpy as np
import render
import build

ROOT = Path(__file__).resolve().parent
tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))

print("横屏（1920x1080）版式档案检查")
render.set_scale(2.0, (1920, 1080))
print(f"  设备尺寸 {render.W}x{render.H}")
print(f"  LAYOUT: margin={render.LAYOUT['margin']} title_y={render.LAYOUT['title_y']} "
      f"title_size={render.LAYOUT['title_size']} y_body={render.LAYOUT['y_body']} "
      f"caption_y={render.LAYOUT['caption_y']}")
print("  Y() 恒等性检查（横屏应原样返回）:")
ok = True
for v in (544, 620, 700, 780, 878):
    yv = render.Y(v)
    same = abs(yv - v) < 1e-6
    ok &= same
    print(f"    Y({v}) = {yv:.2f}  {'✓' if same else '✗'}")
print(f"  {'✓ Y() 在横屏为恒等映射' if ok else '✗ Y() 有偏移，会破坏横屏布局'}")

cache = [render.background(i / 2.0) for i in range(4)]
sc = [s for s in tl["scenes"] if s["id"] == "context"][0]
t = sc["start"] + sc["dur"] * 0.62
img = build.render_active_frame(tl, t, cache, 2.0)
img.resize((1920, 1080), Image.LANCZOS).save(ROOT / "preview" / "_h_check.png")
print(f"\n  已渲染 preview/_h_check.png")

# 与上一版成片同帧比较几何位置（判断是否回归）
ref_path = ROOT / "preview" / "_ref_1x.png"
if ref_path.exists():
    r = np.asarray(Image.open(ref_path).convert("L")).astype(np.float32)
    n = np.asarray(Image.open(ROOT / "preview" / "_h_check.png").convert("L")).astype(np.float32)

    def centroid(a, sy, sx, thr):
        blk = a[sy, sx]
        ys, xs = np.nonzero(blk > thr)
        if len(xs) == 0:
            return None
        return (xs.min(), ys.min(), xs.max(), ys.max(), xs.mean(), ys.mean())

    print("\n  与上一版成片的特征定位比对：")
    for name, sy, sx, thr in [("字幕文字", slice(915, 985), slice(600, 1330), 200),
                              ("标题文字", slice(320, 400), slice(130, 1100), 200),
                              ("右侧决策条", slice(600, 690), slice(1300, 1780), 150)]:
        cr, cn = centroid(r, sy, sx, thr), centroid(n, sy, sx, thr)
        if not cr or not cn:
            print(f"    {name}: 未找到")
            continue
        dx, dy = cn[4] - cr[4], cn[5] - cr[5]
        dw = (cn[2] - cn[0]) - (cr[2] - cr[0])
        dh = (cn[3] - cn[1]) - (cr[3] - cr[1])
        good = abs(dx) < 2 and abs(dy) < 2 and abs(dw) <= 2 and abs(dh) <= 2
        print(f"    {name:<12} dx={dx:+.2f} dy={dy:+.2f} 尺寸差 {dw:+d}x{dh:+d}  "
              f"{'✓' if good else '✗ 位置变了'}")
