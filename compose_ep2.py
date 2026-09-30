# -*- coding: utf-8 -*-
"""
第 2 期 · 多镜头剪辑版（AE 引擎）

把 62 秒拆成 19 个镜头（平均镜头长度 3.3s）。
对比旧版：6 个镜头 / 平均 10.3s —— 那才是"无聊"的直接来源。

节奏设计（不是平均分配）：
  · 开场 3 个镜头各 1.8~2.0s   → 快速建立"这里有话说"
  · 中段 4~5s 一个镜头         → 讲清楚一个点
  · 结尾 3 个镜头 2.2~4.2s     → 收束、留印象

每个镜头都有独立景别与运镜；空镜用来换气，避免"全程都是字"。
"""
from pathlib import Path

import ae
import shots as S

CW, CH = 1920, 1080
EP2 = "assets/image"

# AI 底图（沿用已生成的 6 张，通过取景与推镜做出不同镜头）
BG_HOOK = f"{EP2}/ep2_bg_hook_20260929185814.png"
BG_CONF = f"{EP2}/ep2_bg_conf_20260929185916.png"
BG_FLUENT = f"{EP2}/ep2_bg_fluent_20260929185916.png"
BG_GAP = f"{EP2}/ep2_bg_gap_20260929185916.png"
BG_REASON = f"{EP2}/ep2_bg_reason_20260929185916.png"
BG_OUTRO = f"{EP2}/ep2_bg_outro_20260929185916.png"


def abs_path(rel):
    return str(Path(__file__).resolve().parent / rel)


# 各场景的配音时长（来自 build/timeline_ep2.json，旁白与字幕必须严格对齐）
SCENE_SPANS = [
    ("hook",   0.45,  8.05, [1.8, 2.0, 4.2]),            # 3 镜
    ("conf",   8.33, 17.93, [4.4, 2.4, 2.8]),            # 3 镜
    ("fluent", 18.21, 28.59, [3.0, 3.4, 4.0]),           # 3 镜
    ("gap",    28.87, 38.27, [2.2, 3.4, 3.8]),           # 3 镜
    ("reason", 38.55, 50.09, [3.0, 2.4, 4.0, 2.1]),      # 4 镜
    ("outro",  50.37, 60.95, [2.6, 4.6, 3.4]),           # 3 镜
]


def distribute(weights, total, fps=30):
    """
    把 total（秒）按 weights 比例分配成若干段，保证总和精确等于 total，
    且每段都是**整数帧** —— 否则逐帧渲染会与旁白产生累积错位。

    用最大余数法：先按比例取整帧数，再把余下的帧补给小数部分最大的。
    """
    total_frames = int(round(total * fps))
    s = sum(weights)
    raw = [w / s * total_frames for w in weights]
    out = [int(r) for r in raw]
    rem = total_frames - sum(out)
    order = sorted(range(len(raw)), key=lambda i: -(raw[i] - int(raw[i])))
    for k in range(rem):
        out[order[k % len(order)]] += 1
    return [f / fps for f in out]



def build_shots():
    """
    返回 [(t0, t1, comp)]，t 为全片绝对时间。

    每个场景的镜头时长由 distribute() 按"设计权重"分配，
    并**精确对齐该场景的旁白起止** —— 画面切换不会与配音错位。
    """
    plan = []

    def scene(sid):
        """取出该场景的 (t0, t1, 镜头时长列表)"""
        for s in SCENE_SPANS:
            if s[0] == sid:
                t0, t1, w = s[1], s[2], s[3]
                return t0, t1, distribute(w, t1 - t0)
        raise KeyError(sid)

    def add_group(sid, builders):
        """
        builders: [fn(dur) -> Comp, ...]，个数须与 SCENE_SPANS 的权重数一致
        """
        for s in SCENE_SPANS:
            if s[0] == sid:
                t0, t1, w = s[1], s[2], s[3]
                break
        else:
            raise KeyError(sid)
        durs = distribute(w, t1 - t0)
        assert len(durs) == len(builders), \
            f"{sid}: 权重 {len(w)} 个但 builder {len(builders)} 个"
        clock = t0
        for dur, fn in zip(durs, builders):
            comp = fn(dur)
            plan.append([round(clock, 4), round(clock + dur, 4), comp])
            clock += dur

    # ================================================= 场景 1：开场（3 镜）
    add_group("hook", [
        lambda d: S.shot_words("h1", ["AI", "最危险的时候"], d,
                               accent_at=(1,), size=190, seed=11, vignette=0.34),
        lambda d: S.shot_words("h2", ["不是它说不知道"], d,
                               size=170, seed=12, vignette=0.34),
        lambda d: S.shot_photo_push("h3", abs_path(BG_HOOK), "而是它一本正经地编", d,
                                    zoom0=1.02, zoom1=1.22,
                                    crop0=(0.5, 0.42), crop1=(0.5, 0.52),
                                    cap_size=62, cap_y=0.78, scrim=0.34, seed=13,
                                    vignette=0.30),
    ])

    # ================================================= 场景 2：关键差别（3 镜）
    add_group("conf", [
        lambda d: S.shot_pairs("c1", [("它给出的答案", "光速约 30 万公里/秒", False),
                                      ("同一次对话里它编的", "该书出版于 1987 年", True)], d,
                               bg=S.photo_bg(abs_path(BG_CONF), CW, CH, 1.06, (0.5, 0.5)),
                               seed=21, vignette=0.34),
        lambda d: S.shot_bars("c2", "它内部的置信度", [
            ("编造的那个答案", 0.91, S.WARM),
            ("答对的那个答案", 0.94, S.ACCENT)], d,
            size=46, seed=22, vignette=0.36),
        # 底图是两张脑扫描图（画面中部有实体内容）→ 文字下移 + 压暗
        lambda d: S.shot_title("c3", "它不是在骗你", "它是真的觉得自己在说真话", d,
                               bg=S.photo_bg(abs_path(BG_CONF), CW, CH, 1.35, (0.5, 0.30)),
                               size=112, big_y=0.72, small_y=0.87, scrim=0.34,
                               seed=23, vignette=0.38),
    ])

    # ================================================= 场景 3：语言太顺（3 镜）
    add_group("fluent", [
        lambda d: S.shot_title("f1", "因为语言太顺了", "流利，不等于正确", d,
                               bg=S.photo_bg(abs_path(BG_FLUENT), CW, CH, 1.10, (0.5, 0.5)),
                               size=136, cam="pull_out", seed=31, vignette=0.34),
        lambda d: S.shot_words("f2", ["语法正确", "语气自然", "逻辑通顺"], d,
                               accent_at=(0, 1, 2), size=128, stagger=0.34, seed=32,
                               vignette=0.36),
        lambda d: S.shot_title("f3", "但事实是编的", "流利只是语言能力", d,
                               bg=S.photo_bg(abs_path(BG_FLUENT), CW, CH, 1.42, (0.62, 0.62)),
                               size=142, seed=33, vignette=0.42),
    ])

    # ================================================= 场景 4：训练目标（3 镜）
    add_group("gap", [
        lambda d: S.shot_emptys("g1", d, path=abs_path(BG_GAP), zoom=1.18,
                                crop=(0.42, 0.55), seed=41, vignette=0.44),
        # 底图是分叉曲线，文字避开曲线所在的中上部
        lambda d: S.shot_title("g2", "它只被训练过一件事", "预测下一个词", d,
                               bg=S.photo_bg(abs_path(BG_GAP), CW, CH, 1.30, (0.55, 0.42)),
                               size=112, big_y=0.60, small_y=0.76, scrim=0.26,
                               seed=42, vignette=0.38),
        lambda d: S.shot_photo_push("g3", abs_path(BG_GAP), "它从来没被训练过判断真假", d,
                                    zoom0=1.15, zoom1=1.40,
                                    crop0=(0.62, 0.55), crop1=(0.74, 0.64),
                                    cap_size=54, cap_y=0.80, scrim=0.38, seed=43,
                                    vignette=0.36),
    ])

    # ================================================= 场景 5：没有退路（4 镜）
    add_group("reason", [
        lambda d: S.shot_words("r1", ["每一个位置", "都必须给出一个词"], d,
                               accent_at=(1,), size=132, seed=51, vignette=0.36),
        lambda d: S.shot_title("r2", "没有「不知道」这个选项", "", d,
                               bg=S.photo_bg(abs_path(BG_REASON), CW, CH, 1.24, (0.5, 0.42)),
                               size=112, cam="rise", seed=52, vignette=0.38),
        lambda d: S.shot_bars("r3", "没有依据时，它会挑最顺的那个", [
            ("「1987 年那篇论文」", 0.86, S.ACCENT),
            ("「有研究表明」", 0.31, (90, 130, 180)),
            ("「我不确定」", 0.02, S.WARM)], d,
            size=44, seed=53, vignette=0.38),
        lambda d: S.shot_conclusion("r4", ["于是它把话接了下去"], d,
                                    size=112, seed=54, vignette=0.40),
    ])

    # ================================================= 场景 6：结论（3 镜）
    add_group("outro", [
        lambda d: S.shot_photo_push("o1", abs_path(BG_OUTRO), "它不是在撒谎", d,
                                    zoom0=1.05, zoom1=1.24,
                                    crop0=(0.5, 0.42), crop1=(0.5, 0.52),
                                    cap_size=92, cap_y=0.74, scrim=0.36, seed=61,
                                    vignette=0.32),
        lambda d: S.shot_conclusion("o2", ["它只是太擅长", "把话说顺"], d,
                                    size=118, seed=62, vignette=0.36),
        lambda d: S.shot_title("o3", "关键结论，自己再查一遍", "别把流利当正确", d,
                               bg=S.photo_bg(abs_path(BG_OUTRO), CW, CH, 1.30, (0.5, 0.30)),
                               size=104, seed=63, vignette=0.42),
    ])

    # ================================================= 收尾：消除场景间缝隙
    #
    # ★ 必须做这一步。SCENE_SPANS 给出的是**旁白**起止，而场景之间有 0.28s 的
    #   静音间隔（VIDEO["gap"]）。早期版本只让镜头覆盖旁白区间，
    #   于是那 0.28s 不属于任何镜头 —— render_frame 找不到归属时会落到
    #   "尾部兜底"分支，错误地渲染**最后一个镜头**。
    #   实测有 107 帧（约 3.6 秒）画面上出现的是结尾镜头，
    #   却穿插在全片各处，属严重内容错误。
    #
    #   修法：每个镜头的结束时间顺延到下一个镜头的起点，最后一个镜头补齐到片尾。
    #   副作用是各镜头稍长一点（总长不变），但保证了时间轴上"任何时刻都有确定归属"。
    for i in range(len(plan) - 1):
        plan[i][1] = plan[i + 1][0]
    plan[-1][1] = max(plan[-1][1], 60.95)

    return plan


if __name__ == "__main__":
    P = build_shots()
    total = P[-1][1]
    print("=" * 78)
    print("第 2 期 · 多镜头剪辑版 · 镜头表")
    print("=" * 78)
    print(f"{'#':>3} {'镜头':<8}{'起':>7}{'止':>7}{'时长':>7}  景别")
    print("-" * 78)
    kinds = {
        "h1": "特写", "h2": "中景", "h3": "全景(推镜)",
        "c1": "对照全景", "c2": "近景(数据)", "c3": "中景",
        "f1": "中景(拉)", "f2": "特写组", "f3": "近景",
        "g1": "空镜", "g2": "中景", "g3": "全景(推镜)",
        "r1": "特写", "r2": "中景", "r3": "近景(数据)", "r4": "中景",
        "o1": "全景(推镜)", "o2": "中景", "o3": "中景",
    }
    lens = []
    for i, (t0, t1, c) in enumerate(P, 1):
        short = c.name
        lens.append(t1 - t0)
        print(f"{i:>3} {short:<8}{t0:>7.1f}{t1:>7.1f}{t1-t0:>7.1f}  {kinds.get(short,'—')}")
    n = len(P)
    print("-" * 78)
    print(f"镜头数 {n}   总时长 {total:.1f}s   平均镜头长度 {total/n:.2f}s")
    print(f"最短 {min(lens):.1f}s   最长 {max(lens):.1f}s")
    print()
    print("对照旧版：6 个镜头 / 平均 10.3s")
    print(f"提升：镜头数 {n/6:.1f}×   平均镜头长度缩短 {10.3/(total/n):.1f}×")
