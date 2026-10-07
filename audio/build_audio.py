# -*- coding: utf-8 -*-
"""音频层：旁白 TTS → 时间轴 → 母带（响度/峰值）→ 输出"定时版 IR"。

用法：
    uv run --python 3.12 --with edge-tts --with numpy python audio/build_audio.py films/x.json

产出：
    build/audio/<shot>.mp3        每个镜头的旁白
    build/<film>_timed.json       定时版 IR（镜头起止由旁白决定，字幕由句子边界决定）
    build/audio_timeline.json     时间轴（镜头/旁白区间/字幕条）
    build/master.wav              母带（-16 LUFS，真峰值 ≤ -1.5 dBTP）
"""
import asyncio, json, subprocess, sys
from pathlib import Path
import numpy as np
import edge_tts

ROOT = Path(__file__).resolve().parent.parent
SR = 48000
FF = sys.argv[2] if len(sys.argv) > 2 else "ffmpeg"

def decode(path: Path) -> np.ndarray:
    r = subprocess.run([FF, "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"解码失败 {path}: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)

async def tts(text: str, voice: str, rate: str, out_mp3: Path, tries: int = 4):
    """返回句子边界（绝对秒）——这是字幕与画面和声音对齐的依据。
    微软端点会瞬时失败（NoAudioReceived），必须重试；重试仍失败则报错，绝不静默产出无声片。"""
    last = None
    for i in range(tries):
        try:
            c = edge_tts.Communicate(text, voice, rate=rate)
            bounds, wrote = [], 0
            with open(out_mp3, "wb") as f:
                async for ch in c.stream():
                    if ch["type"] == "audio":
                        f.write(ch["data"]); wrote += len(ch["data"])
                    elif ch["type"] == "SentenceBoundary":
                        bounds.append({"text": ch["text"], "start": ch["offset"] / 1e7, "dur": ch["duration"] / 1e7})
            if wrote == 0:
                raise RuntimeError("空音频")
            return bounds
        except Exception as e:                       # noqa: BLE001
            last = e
            print(f"  [tts] 第 {i+1} 次失败：{type(e).__name__} {str(e)[:60]}  重试…")
            await asyncio.sleep(1.2 * (i + 1))
    raise RuntimeError(f"TTS 连续 {tries} 次失败：{last}")

def split_cues(text: str, span_start: float, span_dur: float, max_chars: int):
    """把一句话按标点/长度切成字幕条，按字数分配时间（中文语速在句内近似均匀）"""
    parts, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "，。！？；、：," or len(cur) >= max_chars:
            if cur.strip():
                parts.append(cur.strip())
            cur = ""
    if cur.strip():
        parts.append(cur.strip())
    parts = [p.rstrip("，。、；：,;:") or p for p in parts]      # 字幕不显示尾标点
    total = sum(len(p) for p in parts) or 1
    cues, t = [], span_start
    for p in parts:
        d = span_dur * len(p) / total
        cues.append({"start": round(t, 3), "end": round(t + d, 3), "text": p})
        t += d
    return cues

def fix_overlaps(cues, gap: float = 0.02):
    """句子边界之间可能有几毫秒重叠 → 强制单调、留 20ms 缝（半开区间）"""
    cues = sorted(cues, key=lambda c: c["start"])
    for i in range(len(cues) - 1):
        lim = cues[i + 1]["start"] - gap
        if cues[i]["end"] > lim:
            cues[i]["end"] = round(max(cues[i]["start"] + 0.25, lim), 3)
    return cues

def ambient(dur: float, seed: int = 7) -> np.ndarray:
    """程序化环境垫底：几层失谐正弦 + 慢 LFO，做"空间感"，不做旋律"""
    n = int(dur * SR); t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float32)
    for f, a in [(55, 0.55), (82.5, 0.30), (110, 0.20), (164.8, 0.11), (221, 0.07)]:
        lfo = 0.55 + 0.45 * np.sin(2 * np.pi * (0.021 + f / 1400) * t + f)
        out += (a * np.sin(2 * np.pi * f * t + 1.7 * np.sin(2 * np.pi * 0.05 * t)) * lfo).astype(np.float32)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n).astype(np.float32)
    # 一阶低通（气息感）
    a = 0.0015
    for i in range(1, n):
        noise[i] = noise[i - 1] + a * (noise[i] - noise[i - 1])
    out += noise * 2.2
    out /= max(1e-6, float(np.abs(out).max()))
    return out

def loudnorm_two_pass(src: Path, dst: Path, I=-16.0, TP=-1.5, LRA=11.0):
    p1 = subprocess.run([FF, "-v", "info", "-i", str(src), "-af",
                         f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = p1.stderr
    js = txt[txt.rfind("{"):txt.rfind("}") + 1]
    m = json.loads(js)
    af = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    p2 = subprocess.run([FF, "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", str(SR), "-ac", "2", str(dst)],
                        capture_output=True)
    if p2.returncode != 0:
        raise RuntimeError(p2.stderr.decode("utf-8", "replace")[:300])
    return m

async def main():
    film_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "films" / "attention.json"
    film = json.loads(film_path.read_text(encoding="utf-8"))
    adir = ROOT / "build" / "audio"; adir.mkdir(parents=True, exist_ok=True)
    style = film.get("style", {})
    aud = film.get("audio", {})
    voice = aud.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = aud.get("rate", "+6%")
    gate = float(aud.get("cueMaxChars", 13))
    lead = float(aud.get("lead", 0.55))
    tail = float(aud.get("tail", 0.42))
    gap = float(aud.get("gap", 0.30))
    bgm_level = float(aud.get("bgm", 0.055))
    target_I = float(aud.get("lufs", -16.0))

    t = lead
    shots, narr_spans, cues_all = [], [], []
    for sh in film["shots"]:
        n = sh.get("narration")
        seg = np.zeros(0, dtype=np.float32)
        bounds = []
        if n and n.get("text"):
            mp3 = adir / f"{sh['id']}.mp3"
            bjson = adir / f"{sh['id']}.bounds.json"
            force = "--force" in sys.argv
            if mp3.exists() and bjson.exists() and mp3.stat().st_size > 1024 and not force:
                bounds = json.loads(bjson.read_text(encoding="utf-8"))
                print(f"  [tts] 复用 {mp3.name}（{len(bounds)} 句）")
            else:
                bounds = await tts(n["text"], n.get("voice", voice), n.get("rate", rate), mp3)
                bjson.write_text(json.dumps(bounds, ensure_ascii=False), encoding="utf-8")
            seg = decode(mp3)
        dur = len(seg) / SR
        start = t
        end = start + dur + (tail if dur > 0 else 0.9)
        # 字幕：有句子边界就用边界，否则按整段均分
        if bounds:
            for b in bounds:
                cues_all += split_cues(b["text"], start + b["start"], b["dur"], int(gate))
        elif n and n.get("text"):
            cues_all += split_cues(n["text"], start + 0.15, max(0.4, dur - 0.3), int(gate))
        if dur > 0:
            narr_spans.append({"shot": sh["id"], "start": round(start, 3), "end": round(start + dur, 3)})
        s2 = dict(sh); s2["start"] = round(start, 3); s2["end"] = round(end, 3)
        shots.append(s2)
        t = end + gap
    # ★ 时间轴必须连续：缝隙（gap）归前一镜头，切点精确落在下一段旁白的起点。
    #   否则缝隙帧会落到"没有归属"的分支，实测会错误地渲染最后一个镜头（旧项目踩过同一个坑）。
    for i in range(len(shots) - 1):
        shots[i]["end"] = shots[i + 1]["start"]
    total = shots[-1]["end"] + 0.35

    cues_all = fix_overlaps(cues_all)
    # 时间轴
    timeline = {"film": film.get("id", film_path.stem), "fps": film["fps"], "size": film["size"],
                "duration": round(total, 3), "voice": voice, "target_lufs": target_I,
                "shots": [{"id": s["id"], "start": s["start"], "end": s["end"], "template": s["template"]} for s in shots],
                "narration": narr_spans, "cues": cues_all}

    # 母带：旁白 + 环境垫底（旁白时自动压低）
    N = int(round(total * SR))
    mix = np.zeros(N, dtype=np.float32)
    for sh in shots:
        mp3 = adir / f"{sh['id']}.mp3"
        if not mp3.exists():
            continue
        seg = decode(mp3)
        st = int(round(sh["start"] * SR))
        en = min(N, st + len(seg))
        mix[st:en] += seg[:en - st]
    if bgm_level > 0:
        pad = ambient(total)
        pad = pad[:N] if len(pad) >= N else np.pad(pad, (0, N - len(pad)))
        env = np.zeros(N, dtype=np.float32)
        for span in narr_spans:
            a, b = int(span["start"] * SR), int(span["end"] * SR)
            env[a:b] = 1.0
        k = int(0.25 * SR)
        ker = np.ones(k, dtype=np.float32) / k
        env = np.convolve(env, ker, mode="same")
        mix = mix + pad * bgm_level * (1.0 - 0.7 * env)
    peak = float(np.abs(mix).max()) or 1.0
    if peak > 0.98:
        mix *= 0.98 / peak
    raw = ROOT / "build" / "mix_raw.wav"
    import struct
    stereo = np.repeat(np.clip(mix, -1, 1), 2)          # 单声道 → 真立体声（同相）
    data = (stereo * 32767).astype("<i2").tobytes()
    with open(raw, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 2, SR, SR * 4, 4, 16))
        f.write(b"data" + struct.pack("<I", len(data)) + data)
    master = ROOT / "build" / "master.wav"
    m = loudnorm_two_pass(raw, master, I=target_I)

    # 定时版 IR
    film_t = dict(film)
    film_t["shots"] = shots
    film_t["duration"] = round(total, 3)
    film_t["captions"] = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in cues_all]
    film_t["audioFile"] = "build/master.wav"
    tp = ROOT / "build" / f"{film_path.stem}_timed.json"
    tp.write_text(json.dumps(film_t, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "build" / "audio_timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"旁白 {len(narr_spans)} 段 / 字幕 {len(cues_all)} 条 / 全片 {total:.2f}s")
    for s in timeline["shots"]:
        print(f"  {s['id']:<4} {s['start']:6.2f} → {s['end']:6.2f}  ({s['end']-s['start']:4.2f}s)  {s['template']}")
    print(f"母带 → {master.name}   响度实测 in={m['input_i']} LUFS  目标 {target_I}")
    print(f"定时版 IR → {tp.name}")

if __name__ == "__main__":
    asyncio.run(main())
