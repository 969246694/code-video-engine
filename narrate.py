# -*- coding: utf-8 -*-
"""
配音 + 字幕时间轴生成
- 用 edge-tts 把每个场景的旁白合成成 MP3
- 新版服务端把 WordBoundary 降级为 SentenceBoundary（句级 offset/duration）
  因此这里把句级锚点按「字数」比例细分到每个字，再合并成字幕行，
  精度可控制在单字量级（约 ±0.1s），足以做到字幕严丝合缝。
- 用 ffprobe / ffmpeg 测真实音频时长，驱动视频时间轴
"""
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import edge_tts
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "build" / "audio"
OUT = ROOT / "build"

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = Path(FFMPEG).parent / "ffprobe.exe"
if not FFPROBE.exists():
    FFPROBE = None

PUNCT = "。！？，、；：…—「」『』（）《》〈〉“”‘’"
# 停顿权重：句末标点停得久，逗号次之，其他标点几乎不停
PAUSE_W = {"。": 1.6, "！": 1.6, "？": 1.6, "…": 1.6, "，": 0.9, "、": 0.7,
           "；": 0.9, "：": 0.6, "—": 0.5}


def audio_duration(path: Path) -> float:
    if FFPROBE and FFPROBE.exists():
        r = subprocess.run(
            [str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        )
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    r = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"无法获取音频时长: {path}")


def split_sentences(text: str):
    """按句末标点切句，标点归属前一句。返回 [(句子文本, 在原文中的起始下标)]"""
    out, start = [], 0
    for i, ch in enumerate(text):
        if ch in "。！？…":
            out.append((text[start:i + 1], start))
            start = i + 1
    if start < len(text):
        out.append((text[start:], start))
    return out


def align(text: str, sentences, total: float):
    """
    把句级锚点细分到字级：返回 [(char, abs_start, abs_end), ...]，覆盖原文所有字符。
    句内按「字数 + 标点停顿权重」分配时长。
    """
    out = []
    sents = split_sentences(text)
    n = min(len(sents), len(sentences))
    for si in range(n):
        stext, _ = sents[si]
        off = sentences[si]["offset"]
        dur = sentences[si]["duration"]
        weights = []
        for ch in stext:
            if ch in PUNCT:
                weights.append(PAUSE_W.get(ch, 0.3) * 0.55)
            elif ch.strip() == "":
                weights.append(0.25)
            else:
                weights.append(1.0)
        wsum = sum(weights) or 1.0
        t = off
        for ch, w in zip(stext, weights):
            step = dur * w / wsum
            out.append((ch, t, t + step))
            t += step
    return out


def make_captions(text: str, sentences, total: float, max_chars: int = 14, min_chars: int = 6):
    """
    用字级时间轴把原文切成短字幕行（保留标点，可读性更好）。
    断行策略：遇句末标点必断；其他标点且已够长则断；否则到 max_chars 硬断。
    行的时间 = 首字起点 → 末字终点。
    """
    chars = align(text, sentences, total)
    if not chars:
        return []

    lines, buf = [], []
    for ch, a, b in chars:
        buf.append((ch, a, b))
        n = len(buf)
        hard = n >= max_chars
        # 句末标点必断；逗号/顿号类需已够长才断
        soft = (ch in "。！？…" and n >= 3) or (ch in "，、；：" and n >= min_chars)
        if hard or soft:
            lines.append(buf)
            buf = []
    if buf:
        lines.append(buf)

    caps = []
    for ln in lines:
        t = "".join(c for c, _, _ in ln).strip()
        if not t:
            continue
        caps.append({"text": t, "start": round(ln[0][1], 3), "end": round(ln[-1][2], 3)})

    # 字幕之间留一点缝：字级细分是按字符时长推算的，相邻两条常有几十毫秒重叠。
    # 若不加处理，"同一时刻"会命中两条字幕 —— 渲染时只画第一条，
    # 但后一条会被推迟到前一条结束才出现，与旁白错位。
    # 这里把重叠裁掉（后一条的起点不得早于前一条的终点）。
    for i in range(1, len(caps)):
        if caps[i]["start"] < caps[i - 1]["end"]:
            mid = (caps[i]["start"] + caps[i - 1]["end"]) / 2
            caps[i - 1]["end"] = round(mid, 3)
            caps[i]["start"] = round(mid, 3)
    caps = [c for c in caps if c["end"] - c["start"] > 0.12]
    return caps


async def synth(text: str, voice: str, rate: str, dest: Path):
    comm = edge_tts.Communicate(text, voice, rate=rate)
    sentences = []
    with open(dest, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                sentences.append({
                    "offset": chunk["offset"] / 1e7,
                    "duration": chunk["duration"] / 1e7,
                    "text": chunk["text"],
                })
    return sentences


async def build_narration(scenes, cfg, audio_dir):
    audio_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for i, sc in enumerate(scenes):
        dest = audio_dir / f"{i:02d}_{sc['id']}.mp3"
        sentences = await synth(sc["narration"], cfg["voice"], cfg["rate"], dest)
        dur = audio_duration(dest)
        caps = make_captions(sc["narration"], sentences, dur)
        result.append({
            "id": sc["id"], "audio": str(dest.relative_to(ROOT)),
            "narration": sc["narration"], "speech_dur": round(dur, 3),
            "sentences": [{k: (round(v, 3) if isinstance(v, float) else v)
                           for k, v in s.items()} for s in sentences],
            "captions": caps,
        })
        print(f"  [{i+1}/{len(scenes)}] {sc['id']:<9} 旁白 {dur:6.2f}s  "
              f"字幕 {len(caps):2d} 行  句锚点 {len(sentences)}")
    return result


def main():
    sys.path.insert(0, str(ROOT))
    import script as ep_mod
    ep = ep_mod.parse_ep()
    VIDEO, SCENES = ep_mod.load(ep)
    audio_dir = ep_mod.audio_dir(ep)
    tl_path = ep_mod.timeline_path(ep)

    print(f"第 {ep} 期《{VIDEO['title']}》")
    print(f"合成旁白（音色 {VIDEO['voice']}  语速 {VIDEO['rate']}）...")
    narr = asyncio.run(build_narration(SCENES, VIDEO, audio_dir))
    payload = {"episode": ep, "video": VIDEO, "scenes": []}

    t = VIDEO["head"]
    for sc, n in zip(SCENES, narr):
        dur = max(n["speech_dur"] + sc.get("pad", VIDEO["pad"]), sc["min_dur"])
        payload["scenes"].append({
            **{k: v for k, v in sc.items()},
            "start": round(t, 3), "dur": round(dur, 3),
            "speech_dur": n["speech_dur"], "audio": n["audio"],
            "sentences": n["sentences"],
            "captions": [{"text": c["text"],
                          "start": round(t + c["start"], 3),
                          "end": round(t + c["end"], 3)} for c in n["captions"]],
        })
        t += dur + VIDEO["gap"]

    total = t - VIDEO["gap"] + VIDEO["tail"]
    payload["total"] = round(total, 3)
    tl_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n视频总时长 {total:.2f}s")
    print(f"{'场景':<10}{'开始':>8}{'时长':>8}{'旁白':>8}  字幕示例")
    for s in payload["scenes"]:
        ex = s["captions"][0]["text"] if s["captions"] else "-"
        print(f"{s['id']:<10}{s['start']:>8.2f}{s['dur']:>8.2f}{s['speech_dur']:>8.2f}  {ex}")
    n_lines = sum(len(s["captions"]) for s in payload["scenes"])
    print(f"\n字幕共 {n_lines} 行 -> {tl_path}")


if __name__ == "__main__":
    main()
