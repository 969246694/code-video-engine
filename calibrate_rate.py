# -*- coding: utf-8 -*-
"""
语速校准（按指定期数）：找出让成片总长最接近 60 秒的 rate。
同时报告哪些场景是被 min_dur 下限撑住的 —— 那部分不受语速影响。
"""
import asyncio
import subprocess
import sys
from pathlib import Path

import edge_tts
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import script as ep_mod  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
EP = ep_mod.parse_ep()
VIDEO, SCENES = ep_mod.load(EP)


def dur(p):
    r = subprocess.run([FFMPEG, "-i", str(p)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for l in r.stderr.splitlines():
        if "Duration:" in l:
            h, m, s = l.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0

async def measure(rate):
    tmp = ROOT / "preview" / "_cal.mp3"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    t = VIDEO["head"]
    floors, speech = [], 0.0
    for sc in SCENES:
        c = edge_tts.Communicate(sc["narration"], VIDEO["voice"], rate=rate)
        with open(tmp, "wb") as f:
            async for ch in c.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
        d = dur(tmp)
        speech += d
        scene = max(d + VIDEO["pad"], sc["min_dur"])
        if scene > d + VIDEO["pad"] + 0.005:
            floors.append(sc["id"])
        t += scene + VIDEO["gap"]
    return t - VIDEO["gap"] + VIDEO["tail"], speech, floors


async def main():
    print(f"第 {EP} 期《{VIDEO['title']}》  目标约 60s")
    best = None
    for rate in ["+16%", "+20%", "+24%", "+28%", "+32%", "+36%"]:
        total, speech, floors = await measure(rate)
        gap = abs(total - 60.0)
        tag = ""
        if best is None or gap < best[1]:
            best = (rate, gap, total)
            tag = "  <== 最接近 60s"
        print(f"  {rate:>5}  旁白合计 {speech:6.2f}s  成片 {total:6.2f}s  "
              f"min_dur撑高: {','.join(floors) if floors else '无'}{tag}")
    print(f"\n推荐: {best[0]}  ->  成片 {best[2]:.2f}s")
    return best


if __name__ == "__main__":
    asyncio.run(main())
