# -*- coding: utf-8 -*-
"""
布局碰撞检测。
把每个场景的关键元素声明为矩形，检查：
  1) 是否超出画布安全区
  2) 场景内元素之间是否重叠
  3) 是否与顶部进度条、右上角标识、底部字幕条冲突
运行后会打印所有冲突，便于在渲染前发现问题。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SAFE = (0, 12, 1920, 1080)
CAPTION_BOX = (0, 902, 1920, 990)       # 字幕条（居中，实际较窄，这里取保守全宽）
BRAND_BOX = (1400, 40, 1836, 96)
PROGRESS_BOX = (0, 0, 1920, 6)


def rects_for(scene_id, Y_BODY):
    y = Y_BODY
    R = {}
    if scene_id == "hook":
        R["核心光球"] = (840, 250, 1080, 495)
        R["标题"] = (300, 590, 1620, 712)
        R["下划线"] = (750, 816, 1170, 824)
        R["副标题"] = (600, 840, 1320, 888)
    elif scene_id == "predict":
        R["标题块"] = (132, 306, 1200, 544)
        R["句子卡片"] = (1000, 548, 1840, 648)
        R["概率标签"] = (988, 656, 1400, 692)
        R["概率条"] = (988, 692, 1870, 880)
    elif scene_id == "token":
        R["标题块"] = (132, 306, 1200, 544)
        R["Token 卡"] = (125, 560, 1795, 694)
        R["图例"] = (600, 726, 1320, 766)
        R["注释"] = (620, 800, 1300, 840)
    elif scene_id == "context":
        R["标题块"] = (132, 306, 1200, 544)
        R["关注度百分比"] = (130, 548, 1120, 592)
        R["词卡行"] = (130, 616, 1120, 734)
        R["注意力弧线"] = (130, 734, 1120, 894)
        R["决策面板"] = (1240, 548, 1870, 890)
    elif scene_id == "train":
        R["标题块"] = (132, 306, 1200, 544)
        R["阶段标签"] = (132, 556, 1060, 620)
        R["损失曲线"] = (1668, 502, 1818, 774)
        R["流程节点"] = (130, 656, 1590, 788)
        R["循环箭头"] = (130, 788, 1590, 812)
    elif scene_id == "outro":
        # 结论文字嵌在概率环内圈；环带是纯装饰且画在文字之下，允许包围盒相交
        R["概率环"] = (600, 250, 1320, 742)
        R["结论行1"] = (300, 470, 1620, 600)
        R["结论行2"] = (300, 622, 1620, 752)
        R["副标语"] = (450, 806, 1470, 866)
        R["强调线"] = (600, 874, 1320, 880)
    return R


def overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def area(r):
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def inter(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0, x1 - x0) * max(0, y1 - y0)


def main():
    tl = json.loads((ROOT / "build" / "timeline.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    import render
    Y_BODY = render.title_bottom({})
    print(f"标题块底部 Y_BODY = {Y_BODY}\n")
    problems = 0
    for sc in tl["scenes"]:
        R = rects_for(sc["id"], Y_BODY)
        msgs = []
        for name, r in R.items():
            if r[0] < SAFE[0] or r[1] < SAFE[1] or r[2] > SAFE[2] or r[3] > SAFE[3]:
                msgs.append(f"  [越界] {name} {r}")
            if overlap(r, CAPTION_BOX):
                msgs.append(f"  [压字幕] {name} {r} 与字幕条重叠 {inter(r, CAPTION_BOX)}px²")
            if overlap(r, BRAND_BOX):
                msgs.append(f"  [压标识] {name} {r}")
            if overlap(r, PROGRESS_BOX):
                msgs.append(f"  [压进度条] {name} {r}")
        names = list(R)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = R[names[i]], R[names[j]]
                ov = inter(a, b)
                if ov > 0:
                    small = min(area(a), area(b))
                    pct = ov / small * 100 if small else 0
                    msgs.append(f"  [重叠] {names[i]} × {names[j]}  交集 {ov}px² ({pct:.1f}%)")
        status = "OK" if not msgs else f"{len(msgs)} 个问题"
        print(f"[{sc['id']:<9}] {status}")
        for m in msgs:
            print(m)
        problems += len(msgs)
    print(f"\n合计 {problems} 个布局问题")


if __name__ == "__main__":
    main()
