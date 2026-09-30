# -*- coding: utf-8 -*-
"""
女声试听：用同一句旁白合成多个中文女声，供挑选。
同时实测每个音色的语速，用于把整片总时长控制在 60 秒附近。
"""
import asyncio
import subprocess
import sys
from pathlib import Path

import edge_tts
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "preview" / "voices"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# 试听句：包含中英混排、标点停顿、陈述语气，接近正片风格
LINE = "你有没有想过，那些能写文章、回答问题的 AI，是怎么学会说话的？今天我们用一分钟，把这个黑盒拆开。"

# 女声候选：音色风格差异要拉开，便于对比
VOICES = [
    ("xiaoxiao", "zh-CN-XiaoxiaoNeural", "晓晓", "通用播音女声，温暖自然，最百搭"),
    ("xiaoyi",   "zh-CN-XiaoyiNeural",   "晓伊", "年轻活泼，偏少女感，节奏轻快"),
    ("xiaobei",  "zh-CN-liaoning-XiaobeiNeural", "晓北（辽宁）", "东北口音，爽朗带喜感"),
    ("xiaoni",   "zh-CN-shaanxi-XiaoniNeural",   "晓妮（陕西）", "陕西口音，憨厚亲切"),
]


async def synth(text, voice, rate, dest):
    c = edge_tts.Communicate(text, voice, rate=rate)
    n = 0
    with open(dest, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
                n += len(ch["data"])
    return n


def dur(path):
    r = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"试听句长度 {len(LINE)} 字，语速 +8%\n")
    print(f"{'标识':<10}{'音色名':<12}{'时长':>8}  风格")
    parts = []
    for key, voice, name, desc in VOICES:
        dest = OUT / f"{key}.mp3"
        await synth(LINE, voice, "+8%", dest)
        d = dur(dest)
        print(f"{key:<10}{name:<12}{d:>7.2f}s  {desc}")
        parts.append(dest)

    # 合成一条连播文件，方便一次听完全部候选
    lst = OUT / "list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.as_posix()}'\n")
    joined = OUT / "全部女声试听.mp3"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(joined)], check=True)
    print(f"\n连播试听文件: {joined}")
    print(f"单个试听文件目录: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
