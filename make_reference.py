# -*- coding: utf-8 -*-
"""
从已交付的成片里抽出参考帧，用于验证"坐标相对化"改造是否改变了 1x 输出。
成片是用改造前的代码渲染的，所以它是权威的 before 基准。
抽帧后与改造后的渲染结果逐像素比对。
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import imageio_ffmpeg
from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import render  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
VIDEO = ROOT / "output" / "AI科普-什么是大语言模型.mp4"
tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))

# 取 context 场景 62% 处（几何元素最密集：细贝塞尔弧线 + 圆角卡片 + 圆角字幕条）
SCENE_ID, FRAC = "context", 0.62
sc = [s for s in tl["scenes"] if s["id"] == SCENE_ID][0]
t_abs = sc["start"] + sc["dur"] * FRAC
frame_no = int(round(t_abs * 30))

print(f"参考帧: 成片 t={t_abs:.3f}s  第 {frame_no} 帧")

# 用 ffmpeg 精确抽取该帧（无损 PNG）
ref_path = ROOT / "preview" / "_ref_from_video.png"
subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(VIDEO),
                "-vf", f"select=eq(n\\,{frame_no})", "-frames:v", "1", str(ref_path)],
               check=True)
ref = Image.open(ref_path).convert("RGB")
ref.save(ROOT / "preview" / "_ref_1x.png")
print(f"  已保存参考帧: {ref.size}")

# 用改造后的代码渲染同一帧（S=1）
render.set_scale(1.0, (1920, 1080))
t_local = sc["dur"] * FRAC
img = render.background(t_abs)
img = render.RENDERERS[sc["visual"]](img, t_local, sc["dur"], sc, tl)
d = render.sd(render.ImageDraw.Draw(img))
render.draw_caption(img, d, sc["captions"], t_abs)
render.progress_bar(d, t_local, tl["total"], sc["start"], sc["dur"])
render.brand(d, tl["video"]["series"])
render.apply_fade(img, render.scene_alpha(t_local, sc["dur"]))
img.save(ROOT / "preview" / "_new_1x.png")

a = np.asarray(ref).astype(np.int16)
b = np.asarray(img).astype(np.int16)
d_ = np.abs(a - b)
n_diff = int((d_.max(axis=2) > 3).sum())
tot = ref.size[0] * ref.size[1]
print(f"\n比对结果:")
print(f"  显著差异像素(>3): {n_diff} / {tot}  ({n_diff/tot*100:.4f}%)")
print(f"  最大通道差: {int(d_.max())}   平均差: {d_.mean():.4f}")
if n_diff / tot < 0.001:
    print("  ✓ 与改造前成片一致（差异来自 H.264 有损压缩，非代码改动）")
else:
    ys, xs = np.nonzero(d_.max(axis=2) > 8)
    if len(xs):
        print(f"  ✗ 存在结构性差异: x[{xs.min()},{xs.max()}] y[{ys.min()},{ys.max()}]")

# 生成差异可视化
diff_img = Image.fromarray((np.clip(d_.max(axis=2) * 6, 0, 255)).astype(np.uint8), "L")
diff_img.save(ROOT / "preview" / "_ref_diff.png")
print(f"  差异图: preview/_ref_diff.png")
