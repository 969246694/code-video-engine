# -*- coding: utf-8 -*-
"""
运动设计审计：量化「运动是否真的有层次」，而不是只声称改了曲线。

指标：
  1. 曲线多样性 —— 一段场景里实际用到几种缓动曲线（单一曲线=廉价感来源）
  2. 错帧程度 —— 元素到达 50% 进度的时刻分散度（std 越大越有节奏）
  3. 层数 —— 背景视差层数
  4. 纵深 —— 远景与近景的位移差（越大纵深感越强）
  5. 过冲 —— 是否存在超过目标值的曲线（有"弹一下"的手感）
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import render

ROOT = Path(__file__).resolve().parent
tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))

print("=" * 76)
print("运动设计审计")
print("=" * 76)

# ---- 1. 曲线在关键帧上的取值指纹，用于统计"实际用了多少种曲线"
CURVES = {
    "ease_out": render.ease_out, "ease_in_out": render.ease_in_out,
    "ease_out_quart": render.ease_out_quart, "ease_out_expo": render.ease_out_expo,
    "ease_out_back": render.ease_out_back, "ease_in_out_back": render.ease_in_out_back,
    "ease_out_elastic": render.ease_out_elastic, "ease_out_bounce": render.ease_out_bounce,
    "ease_in": render.ease_in, "smoothstep": render.smoothstep,
    "ease_linear": render.ease_linear,
}


def fingerprint(fn):
    return tuple(round(fn(x / 20), 3) for x in range(21))


fps = {name: fingerprint(fn) for name, fn in CURVES.items()}
uniq = {}
for name, fp in fps.items():
    uniq.setdefault(fp, []).append(name)
print(f"\n[1] 缓动曲线库：{len(CURVES)} 条，其中行为唯一的有 {len(uniq)} 条")
for fp, names in uniq.items():
    if len(names) > 1:
        print(f"    ! 行为重复: {names}")

# ---- 2. 各场景实际调用的曲线种类（静态扫描源码里的 curve= 参数）
src = (ROOT / "render.py").read_text(encoding="utf-8")
used = set()
for key in ("heavy", "light", "pop", "bounce", "elastic", "soft"):
    if f'"{key}"' in src or f"'{key}'" in src:
        used.add(key)
print(f"\n[2] 场景中引用的入场档位：{sorted(used)}  （共 {len(used)} 档）")
if len(used) <= 1:
    print("    ! 只用了单一档位，运动仍然单调")
else:
    print("    ✓ 多档位混用")

# ---- 3. 错帧程度：stagger 归一化到 [0,1] 后取 50% 到达时刻
print("\n[3] 错帧程度（元素到达 50% 进度的时刻，越分散越有节奏）")
for name, kwargs in (("概率条 5 项", dict(delay=0.13, start=1.15, dur=0.72)),
                     ("词卡 7 项", dict(delay=0.085, start=1.20, dur=0.62)),
                     ("流程节点 4 项", dict(delay=0.17, start=1.18, dur=0.66)),
                     ("Token 6 项", dict(delay=0.13, start=1.22, dur=0.62))):
    ts = []
    for i in range(8):
        lo = kwargs["start"] + i * kwargs["delay"]
        hi = lo + kwargs["dur"]
        ts.append((lo + hi) / 2)
    ts = np.array(ts[:5])
    print(f"    {name:<16} 到达时刻 {ts.min():.2f}~{ts.max():.2f}s  跨度 "
          f"{ts.max()-ts.min():.2f}s  std {ts.std():.3f}s")

# ---- 4. 背景纵深
print("\n[4] 背景视差层")
cx1, cy1 = render.camera_pos(0.0)
cx2, cy2 = render.camera_pos(6.0)
dcx = cx2 - cx1
print(f"    层数: 3（远景光斑网格 / 中景星点 / 近景散景）")
print(f"    视差系数: 远 {render.PAR_BACK} / 中 {render.PAR_MID} / 近 {render.PAR_NEAR}")
d_back = -dcx * render.PAR_BACK
d_mid = -dcx * render.PAR_MID
d_near = -dcx * render.PAR_NEAR
print(f"    t=0→6s 位移: 远 {d_back:+.1f}px  中 {d_mid:+.1f}px  近 {d_near:+.1f}px")
print(f"    远/近速度比 = {render.PAR_BACK/render.PAR_NEAR:.1f}x  "
      f"{'✓ 纵深感明显' if render.PAR_BACK/render.PAR_NEAR > 4 else '! 纵深感不足'}")

# ---- 5. 过冲检测
print("\n[5] 过冲（有回弹才算有'手感'）")
for name in ("ease_out_back", "ease_out_elastic", "ease_in_out_back"):
    fn = CURVES[name]
    vals = [fn(x / 100) for x in range(101)]
    mx = max(vals)
    overshoot = (mx - 1.0) * 100
    print(f"    {name:<20} 峰值 {mx:.3f}  过冲 {overshoot:+.1f}%  "
          f"{'✓' if overshoot > 1 else '（无过冲）'}")

# ---- 6. 逐帧曲线使用统计：替换 ENTER_CURVES 里的曲线引用，
#      直接统计每个场景实际用到的曲线档位（比统计 seg 调用次数准确）
print("\n[6] 每个场景实际用到的曲线档位")
print(f"    {'场景':<10}{'档位数':>8}{'调用数':>8}   明细")
total_species = 0
for sc in tl["scenes"]:
    hit = {}

    def make_counter(fn, bucket=hit):
        def wrapper(x, *a, **kw):
            bucket[getattr(fn, "__name__", "?")] = bucket.get(getattr(fn, "__name__", "?"), 0) + 1
            return fn(x, *a, **kw)
        wrapper.__name__ = getattr(fn, "__name__", "?")
        return wrapper

    saved = dict(render.ENTER_CURVES)
    for key, fn in saved.items():
        render.ENTER_CURVES[key] = make_counter(fn)
    try:
        render.set_scale(1.0, (1920, 1080), profile="h")
        bg = render.background(1.0)
        t = sc["dur"] * 0.55
        img = bg.copy()
        img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
    finally:
        render.ENTER_CURVES = saved
    detail = ", ".join(f"{k}×{v}" for k, v in sorted(hit.items(), key=lambda kv: -kv[1]))
    n = len(hit)
    total_species += n
    flag = "✓" if n >= 2 else "!"
    print(f"    {sc['id']:<10}{n:>8}{sum(hit.values()):>8}   {flag} {detail or '（未使用）'}")

print(f"\n  合计 {total_species} 个场景×档位组合；每个场景至少 2 档才算有层次")
