# -*- coding: utf-8 -*-
"""
严谨测量：Pillow 的几何图元是否有抗锯齿 + 超采样能否改善。

方法：画一个圆，取过圆心的水平扫描线，统计白色→黑色过渡带上的
"中间灰"像素个数。无抗锯齿时过渡是 1 像素的硬跳变；有抗锯齿时会出现
若干中间灰，且过渡宽度更接近真实的 1 像素覆盖比例。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw

W, H = 1920, 1080


def profile(mask_row):
    """返回过渡带上的灰度值序列"""
    return [int(v) for v in mask_row]


print("=" * 74)
print("测试 1：Pillow 几何图元本身有没有抗锯齿")
print("=" * 74)

for shape, fn in [
    ("椭圆 ellipse", lambda d, b: d.ellipse(b, fill=255)),
    ("矩形 rectangle", lambda d, b: d.rectangle(b, fill=255)),
    ("圆角矩形 rounded", lambda d, b: d.rounded_rectangle(b, radius=40, fill=255)),
    ("多边形 polygon（斜边）", lambda d, b: d.polygon([(b[0], b[3]), (b[2], b[1]), (b[2], b[3])], fill=255)),
]:
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    # 用奇数尺寸让边缘落在像素中间，最容易暴露锯齿
    fn(d, [800, 400, 1100, 700])
    a = np.asarray(img)
    # 取一条穿过斜边的扫描线
    row = a[650] if "polygon" in shape else a[550]
    nz = np.nonzero(row)[0]
    if len(nz) == 0:
        print(f"  {shape:<24} 无内容")
        continue
    x0, x1 = nz.min(), nz.max()
    # 找最左边的过渡：从左往右第一次变成非零
    trans = profile(row[max(0, x0 - 3):x0 + 4])
    # 统计整行中"中间灰"（1..254）的个数
    mid = int(((row > 0) & (row < 255)).sum())
    print(f"  {shape:<24} 左边缘过渡={trans}   中间灰像素={mid} 个")

print()
print("=" * 74)
print("测试 2：2x 超采样 + LANCZOS 降采样能否改善")
print("=" * 74)

for scale in (1, 2, 3):
    dw, dh = W * scale, H * scale
    img = Image.new("L", (dw, dh), 0)
    d = ImageDraw.Draw(img)
    d.ellipse([800 * scale, 400 * scale, 1100 * scale, 700 * scale], fill=255)
    if scale > 1:
        img = img.resize((W, H), Image.LANCZOS)
    a = np.asarray(img)
    row = a[550]
    nz = np.nonzero(row)[0]
    x0 = nz.min()
    trans = profile(row[max(0, x0 - 4):x0 + 6])
    mid = int(((row > 0) & (row < 255)).sum())
    print(f"  {scale}x 超采样:  左边缘过渡={trans}")
    print(f"             整行中间灰像素={mid} 个")

print()
print("=" * 74)
print("结论判据")
print("=" * 74)
print("  · 1x 若过渡为 [0,0,0,255,255] 形式（无中间灰）→ Pillow 图元无抗锯齿，需超采样")
print("  · 超采样倍率越高，中间灰越多、过渡越柔和 → 方案有效")
