# -*- coding: utf-8 -*-
"""
验证方案可行性：ImageDraw.Draw(transform=...) 能否做坐标缩放。

如果可行，超采样 / 高分辨率 / 竖屏适配都可以在"设备像素"层面解决：
布局仍写 1920x1080 逻辑坐标，绘制时自动乘 scale —— 不需要改任何场景代码。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image, ImageDraw
import PIL

print("Pillow 版本:", PIL.__version__)

S = 2.0
OUT_W, OUT_H = 1920, 1080          # 最终输出尺寸
DEV_W, DEV_H = int(OUT_W * S), int(OUT_H * S)   # 设备（超采样）尺寸

# ---- 方案 A：transform 参数
img = Image.new("RGB", (DEV_W, DEV_H), (10, 14, 28))
try:
    d = ImageDraw.Draw(img, transform=(S, 0, 0, S, 0, 0))
    ok_transform = True
except Exception as e:
    ok_transform = False
    print("transform 参数不支持:", type(e).__name__, e)

if ok_transform:
    # 用逻辑坐标绘制一个 100x100 的方块在 (50,60)
    d.rectangle([50, 60, 150, 160], fill=(56, 224, 255))
    px = img.load()
    # 逻辑 (50,60) 应落在设备 (100,120)
    corner_hit = px[102, 122]
    outside_hit = px[96, 116]
    print(f"  transform 生效: 设备(102,122)={corner_hit}  逻辑外(96,116)={outside_hit}")
    print(f"  期望值 (56,224,255) -> {'通过' if corner_hit == (56, 224, 255) else '不通过'}")

# ---- 方案 B：绘制后再缩放（作为对照）
img2 = Image.new("RGB", (DEV_W, DEV_H), (10, 14, 28))
d2 = ImageDraw.Draw(img2)
d2.rectangle([100, 120, 300, 320], fill=(56, 224, 255))
down = img2.resize((OUT_W, OUT_H), Image.LANCZOS)
print(f"\n  手动缩放对照: 输出尺寸 {down.size}")

# ---- 关键测试：抗锯齿效果差异
def edge_roughness(im):
    import numpy as np
    a = np.asarray(im.convert("L")).astype(np.float32)
    g = np.concatenate([np.abs(np.diff(a, axis=1)).ravel(), np.abs(np.diff(a, axis=0)).ravel()])
    return (g > 40).mean() * 100

# 1x 直接画圆（无抗锯齿）
i1 = Image.new("RGB", (OUT_W, OUT_H), (0, 0, 0))
ImageDraw.Draw(i1).ellipse([900, 480, 1020, 600], fill=(255, 255, 255))

# 2x 超采样后降采样
i2 = Image.new("RGB", (DEV_W, DEV_H), (0, 0, 0))
try:
    ImageDraw.Draw(i2, transform=(S, 0, 0, S, 0, 0)).ellipse([900, 480, 1020, 600], fill=(255, 255, 255))
except Exception:
    ImageDraw.Draw(i2).ellipse([1800, 960, 2040, 1200], fill=(255, 255, 255))
i2s = i2.resize((OUT_W, OUT_H), Image.LANCZOS)

print(f"\n  圆形边缘粗糙度：1x 直接绘制 = {edge_roughness(i1):.3f}%   2x 超采样 = {edge_roughness(i2s):.3f}%")
i1.save("preview/_tf_1x.png")
i2s.save("preview/_tf_2x.png")
print("  已保存对照图 preview/_tf_1x.png / _tf_2x.png")
