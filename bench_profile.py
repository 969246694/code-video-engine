# -*- coding: utf-8 -*-
"""
渲染成本剖析：回答"慢在哪、该优化什么、换语言值不值"。

用法：
    uv run --python 3.12 --with pillow --with numpy --with scipy --with imageio-ffmpeg python bench_profile.py
    python bench_profile.py --frames 20        # 多采几帧

输出三段：
  1. 每帧耗时（ss=1.5 / ss=1.0）——超采样值多少
  2. 合成 vs 字幕的耗时拆解——字幕是不是瓶颈
  3. self-time 归属：项目 Python / 第三方 Python / C 内建，以及 top 函数

判读口径：
  · 若 C 内建占比高 → 瓶颈在像素运算本身，换语言收益有限，该上 GPU/并行
  · 若项目 Python 占比高且集中在少数函数 → 先向量化/缓存那几个函数
"""
import argparse
import cProfile
import io
import json
import os
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image

import build_cut as BC
import compose_ep2 as EP
from shots import F_BOLD


def load_caps():
    try:
        import script as ep_mod
        tl = json.loads(ep_mod.timeline_path(2).read_text(encoding="utf-8"))
        caps = [c for sc in tl["scenes"] for c in sc["captions"]]
        caps.sort(key=lambda c: c["start"])
        return caps
    except Exception as e:  # 时间轴缺失不阻塞剖析
        print(f"[warn] 时间轴不可用（{e}），字幕剖析跳过")
        return []


def sample_times(plan, per_shot=2):
    ts = []
    for t0, t1, _ in plan:
        for k in range(per_shot):
            ts.append(t0 + (t1 - t0) * (0.45 + 0.4 * k))
    return ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=0, help="采样帧数上限（0=全部镜头）")
    args = ap.parse_args()

    caps = load_caps()
    plan = EP.build_shots()
    times = sample_times(plan)
    if args.frames:
        times = times[:args.frames]
    print(f"镜头 {len(plan)} 个   采样 {len(times)} 帧   "
          f"合成尺寸 {sorted({(c.w, c.h) for _, _, c in plan})}   CPU {os.cpu_count()} 核")

    def render_all(ss):
        for t in times:
            BC.render_frame(plan, caps, F_BOLD, t, ss)

    render_all(1.5)  # 预热缓存
    base = {}
    for ss in (1.5, 1.0):
        t0 = time.time()
        render_all(ss)
        dt = time.time() - t0
        base[ss] = dt / len(times)
        print(f"[1] 整帧 ss={ss}: {base[ss]*1000:7.1f} ms/帧   {1/base[ss]:5.2f} fps")
    print(f"    超采样 1.5 相对 1.0 的代价: {base[1.5]/base[1.0]:.2f}x")

    t0 = time.time()
    for t in times:
        for a, b, comp in plan:
            if a <= t < b:
                comp.render(t - a, ss=1.5, motion_blur=getattr(comp, "_motion_blur", 0.0))
                break
    ms = (time.time() - t0) / len(times) * 1000
    print(f"[2] 仅合成: {ms:7.1f} ms/帧   占比 {ms/base[1.5]/10:.1f}%")
    if caps:
        t0 = time.time()
        for t in times:
            BC.draw_caption(Image.new("RGB", (BC.OUT_W, BC.OUT_H)), BC.caption_at(caps, t), F_BOLD)
        ms = (time.time() - t0) / len(times) * 1000
        print(f"    仅字幕: {ms:7.1f} ms/帧   占比 {ms/base[1.5]/10:.1f}%")

    pr = cProfile.Profile()
    pr.enable(); render_all(1.5); pr.disable()
    st = pstats.Stats(pr)
    tot = sum(v[2] for v in st.stats.values())
    root = str(ROOT).lower()
    proj = sum(v[2] for k, v in st.stats.items() if str(k[0]).lower().startswith(root))
    lib = sum(v[2] for k, v in st.stats.items() if "site-packages" in str(k[0]).lower())
    cbu = sum(v[2] for k, v in st.stats.items() if str(k[0]).startswith("~") or str(k[0]) == "<built-in>")
    print(f"[3] self-time 归属: 项目Python {proj/tot*100:.1f}%   第三方Python {lib/tot*100:.1f}%   "
          f"C内建 {cbu/tot*100:.1f}%")
    print("    注：numpy ufunc 的时间会被记在调用它的项目函数里，所以“C 实际占比”高于上面一行。")
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("tottime").print_stats(14)
    print(buf.getvalue())


if __name__ == "__main__":
    main()
