# -*- coding: utf-8 -*-
"""版面机检（新管线）：读 driver 导出的"版面盒"，检查元素碰撞 / 安全边距 / 字幕带侵入。
   （老引擎有 check_layout.py，那个只管老管线；这个是 IR + Chromium 管线的对应件。）

用法：uv run --python 3.12 python check_boxes.py build/layout.json

判定规则（一条就够）：两盒在 x、y 上都重叠 >2px，且**互不包含** → 碰撞。
"包含"是合法的（bars.block 包住 bars.title）；"部分重叠"才是事故（注脚压住坐标轴）。"""
import json, sys
from pathlib import Path

W, H = 1920, 1080
SAFE_X, SAFE_Y = 56, 44
CAP_TOP, CAP_BOT = H - 176, H - 88

def contains(a, b, tol=2):
    return (a["x"] - tol <= b["x"] and a["y"] - tol <= b["y"]
            and a["x"] + a["w"] + tol >= b["x"] + b["w"] and a["y"] + a["h"] + tol >= b["y"] + b["h"])

def overlap(a, b, tol=2):
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return ox > tol and oy > tol

def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python check_boxes.py <layout.json>")
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    hits = 0
    for fr in data:
        boxes = fr.get("boxes", [])
        msgs = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if overlap(a, b) and not (contains(a, b) or contains(b, a)):
                    msgs.append(f"碰撞 {a['tag']} × {b['tag']}")
        for b in boxes:
            if b["x"] < SAFE_X or b["y"] < SAFE_Y or b["x"] + b["w"] > W - SAFE_X or b["y"] + b["h"] > H - SAFE_Y:
                msgs.append(f"越安全边距 {b['tag']} ({b['x']},{b['y']},{b['w']}x{b['h']})")
            if b["tag"] != "caption" and b["y"] + b["h"] > CAP_TOP - 8 and b["y"] < CAP_BOT:
                msgs.append(f"侵入字幕带 {b['tag']}")
        if msgs:
            hits += len(msgs)
            print(f"[FAIL] 帧 {fr['frame']}  t={fr['t']}s")
            for m in msgs:
                print("        !! " + m)
    print("=" * 66)
    print(f"版面机检：{len(data)} 帧，问题 {hits} 处 -> {'全部通过' if hits == 0 else '需修'}")
    return 0 if hits == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
