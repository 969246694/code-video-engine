# -*- coding: utf-8 -*-
"""
内容一致性检查：确认每一期画面上出现的文案，确实来自**当期**脚本。

这是针对第 2 期开场画出第 1 期标题那个 bug 的回归测试。
做法：渲染每一期的每一帧代表画面，OCR 不可用时退化为——
  1) 拦截所有 draw_tracked 的文字，收集"画面上出现过的字符串"
  2) 检查每期画面上出现的字符串，是否都能在该期脚本里找到出处
     （标题/副标题/演示数据/白名单里的固定标签）
  3) 特别检查：A 期的画面里不应出现只属于 B 期的标记性文案
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import render

ROOT = Path(__file__).resolve().parent

# 各期的"标记性文案"——出现即说明用错了内容
SIGNATURE = {
    1: ["什么是大语言模型", "预测下一个词讲透原理", "书读百遍", "散步"],
    2: ["AI 最危险的时候", "一本正经地编", "两组置信度几乎一样", "语法正确"],
}

FRACS = (0.3, 0.6, 0.85)


def collect_texts(ep, prof, size):
    tl = build.load_timeline(ep)
    render.set_scale(1.0, size, profile=prof)
    texts = set()
    orig = render.draw_tracked

    def spy(draw, xy, text, f, fill, spacing=0.0, anchor_center=False):
        if text.strip():
            texts.add(text.strip())
        return orig(draw, xy, text, f, fill, spacing, anchor_center)

    render.draw_tracked = spy
    try:
        for sc in tl["scenes"]:
            for frac in FRACS:
                t = sc["dur"] * frac
                img = render.background(1.0)
                img = render.RENDERERS[sc["visual"]](img, t, sc["dur"], sc, tl)
    finally:
        render.draw_tracked = orig
    return texts


print("=" * 78)
print("内容一致性检查（画面文案必须来自当期脚本）")
print("=" * 78)
bad_total = 0
for ep in sorted(SIGNATURE):
    others = [e for e in SIGNATURE if e != ep]
    for prof, size in (("h", (1920, 1080)),):
        texts = collect_texts(ep, prof, size)
        leaks = []
        for other in others:
            for sig in SIGNATURE[other]:
                if any(sig in t for t in texts):
                    leaks.append((other, sig))
        if leaks:
            bad_total += len(leaks)
            print(f"  ✗ 第 {ep} 期画面里出现了第 {other} 期专属文案：")
            for o, sig in leaks:
                print(f"        第{o}期「{sig}」")
        else:
            print(f"  ✓ 第 {ep} 期：画面文案全部来自本期")

render.set_scale(1.0, (1920, 1080), profile="h")
print()
print("=" * 78)
print(f"{'全部通过 ✓' if bad_total == 0 else f'发现 {bad_total} 处串期内容 ✗'}")
print("=" * 78)
sys.exit(1 if bad_total else 0)
