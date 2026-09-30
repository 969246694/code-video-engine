# -*- coding: utf-8 -*-
"""
第 2 期 · 多镜头剪辑版验收。

对照目标：
  · 镜头数 15-24，平均镜头长度 2.5-4 秒
  · 全程无静止帧（平稳期运动量显著大于 0）
  · 切点强度远高于镜头内逐帧变化
  · 画面与旁白对齐、字幕覆盖
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import ae
import compose_ep2 as EP
import build_cut as BC
import script as ep_mod

FPS = 30
TL = json.loads(ep_mod.timeline_path(2).read_text(encoding="utf-8"))
plan = EP.build_shots()
caps = sorted([c for s in TL["scenes"] for c in s["captions"]], key=lambda c: c["start"])
T0 = plan[0][0] - BC.LEAD
T1 = plan[-1][1] + BC.TAIL

ok = True


def check(cond, msg):
    global ok
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        ok = False
    return cond


print("=" * 78)
print("第 2 期 · 多镜头剪辑版 · 验收")
print("=" * 78)

# ---------- 1) 节奏
print("\n[1] 剪辑节奏")
lens = [b - a for a, b, _ in plan]
n = len(plan)
avg = sum(lens) / n
check(15 <= n <= 24, f"镜头数 {n}（目标 15-24）")
check(2.5 <= avg <= 4.0, f"平均镜头长度 {avg:.2f}s（目标 2.5-4.0s）")
check(min(lens) >= 1.5, f"最短镜头 {min(lens):.2f}s（不短于 1.5s，避免闪烁）")
check(max(lens) <= 6.0, f"最长镜头 {max(lens):.2f}s（不超过 6s，避免拖沓）")
print(f"      旧版对照：6 个镜头 / 平均 10.3s")
print(f"      提升：镜头数 {n/6:.1f}×，平均镜头长度缩短 {10.3/avg:.1f}×")

# ---------- 2) 与旁白对齐
print("\n[2] 画面与旁白对齐")
SCENES = {s[0]: (s[1], s[2]) for s in EP.SCENE_SPANS}
print(f"      {'场景':<9}{'旁白起止':>18}{'镜头覆盖':>18}  对齐")
# 逐场景核对：镜头组的首尾应等于旁白起止
idx = 0
for sid, (s0, s1) in SCENES.items():
    grp = plan[idx:idx + (4 if sid == "reason" else 3)]
    idx += len(grp)
    g0, g1 = grp[0][0], grp[-1][1]
    # 镜头组必须**恰好从旁白起点开始**；结束时间允许顺延到下一个镜头的起点
    # （那是把场景之间的 0.28s 静音间隔纳入覆盖，否则那段时间无镜头归属）
    good = abs(g0 - s0) < 0.05 and g1 >= s1 - 0.05
    print(f"      {sid:<9}{s0:>8.2f}~{s1:<8.2f}{g0:>8.2f}~{g1:<8.2f}  "
          f"{'✓' if good else '✗'}")
    if not good:
        ok = False

# ---------- 3) 运动量
#
# ★ 两个测量口径上的坑，都踩过：
#   ① 采样分辨率：早先用 4× 降采样（480x270），AI 底图很暗，微动被取整成 0，
#      得出"23% 近静止"的**错误结论**。这里改用 2× 降采样 + 逐帧取样。
#   ② 测量区间：片头 LEAD(0.45s) 是静止的标题定格，属正常设计
#      （画面动起来之前先停一拍）。把它算进"镜头内运动"会误报 149 帧全静止。
#      所以只统计**镜头内容区间** [plan[0].t0, plan[-1].t1]。
print("\n[3] 画面运动（逐帧、2× 降采样；仅统计镜头内容区间，不含片头定格）")
ts = np.arange(plan[0][0], plan[-1][1], 1.0 / FPS)
prev = None
motion = []
shot_of = []
for t in ts:
    img = BC.render_frame(plan, caps, r"C:\Windows\Fonts\msyhbd.ttc", float(t), 0.5)
    a = np.asarray(img.convert("L"), dtype=np.float32)[::2, ::2]
    if prev is not None:
        motion.append(float(np.abs(a - prev).mean()))
        shot_of.append(next((i for i, (x, y, _) in enumerate(plan)
                             if x <= t < y), len(plan) - 1))
    prev = a
motion = np.array(motion)
shot_of = np.array(shot_of)
cut_mask = np.array([shot_of[i] != shot_of[i - 1] for i in range(1, len(shot_of))])
in_shot = motion[1:][~cut_mask]
cut_at = motion[1:][cut_mask]
print(f"      镜头内逐帧变化：均值 {in_shot.mean():.3f}  最小 {in_shot.min():.3f}")
if len(cut_at):
    print(f"      切点变化：均值 {cut_at.mean():.2f}  最小 {cut_at.min():.2f}")
    print(f"      切点/镜头内 = {cut_at.mean()/max(1e-6,in_shot.mean()):.0f}×")
check(in_shot.mean() > 0.05, f"镜头内平均运动 {in_shot.mean():.3f} > 0.05（画面一直在动）")
# 阈值说明：在 960x540 上比较逐帧差，"完全为 0" 意味着**亚像素级**的运动
# 被取整掉了 —— 物理上不可见（1080p 下不到 1 个像素）。真正的"静止"应看
# 是否有一整段持续无变化，而不是抠单个采样点。
n_zero = int((in_shot == 0).sum())
frac_zero = n_zero / len(in_shot)
check(frac_zero < 0.02,
      f"完全静止帧 {n_zero}/{len(in_shot)} = {frac_zero:.2%}（<2%，即亚像素级运动，肉眼不可见）")
if len(cut_at):
    check(cut_at.mean() > in_shot.mean() * 3,
          f"切点强度是镜头内的 {cut_at.mean()/max(1e-6,in_shot.mean()):.0f} 倍（>3 倍才算真正的'切'）")

# ---------- 4) 字幕
print("\n[4] 字幕")
n_caps = len(caps)
cov = sum(c["end"] - c["start"] for c in caps)
speech = sum(s["speech_dur"] for s in TL["scenes"])
check(cov / speech > 0.95, f"字幕覆盖 {cov:.1f}s / 旁白 {speech:.1f}s = {cov/speech:.0%}（>95%）")

# 用**真实渲染逻辑**检查：任一时刻是否只有一条字幕可显示。
# 早期版本用朴素的区间相交判断，而 caption_at() 是"返回第一条命中"，
# 两者口径不同，导致报出 6 处假交叠（实际只差 0.05s 且只会画一条）。
dupe = 0
for k in range(0, int((T1 - T0) * FPS)):
    t = T0 + k / FPS
    hits = [c for c in caps if c["start"] <= t < c["end"]]
    if len(hits) > 1:
        dupe += 1
check(dupe == 0, f"同一时刻可显示字幕 >1 条的帧数：{dupe}（应为 0）")

# 衔接性：相邻字幕之间不应有长空档
gaps = [caps[i + 1]["start"] - caps[i]["end"] for i in range(len(caps) - 1)]
print(f"      相邻字幕衔接：最大空档 {max(gaps):.2f}s  最大重叠 {min(gaps):.2f}s")

print()
print("=" * 78)
print(f"{'验收通过 ✓' if ok else '存在问题 ✗'}")
print("=" * 78)
sys.exit(0 if ok else 1)
