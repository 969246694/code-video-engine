# -*- coding: utf-8 -*-
"""音频判据：旁白/字幕/母带三层对齐检查。
   用法：uv run --python 3.12 --with numpy python verify_audio.py <ffmpeg> [mp4]"""
import json, subprocess, sys, struct
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
FF = sys.argv[1] if len(sys.argv) > 1 else "ffmpeg"
MP4 = Path(sys.argv[2]) if len(sys.argv) > 2 else None
TL = json.loads((ROOT / "build" / "audio_timeline.json").read_text(encoding="utf-8"))
SR = 48000

def decode(p: Path):
    r = subprocess.run([FF, "-v", "error", "-i", str(p), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)

def ebur128(p: Path):
    r = subprocess.run([FF, "-v", "info", "-i", str(p), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = r.stderr
    def grab(key):
        vals = [l for l in txt.splitlines() if key in l]
        return vals[-1].split()[-2] if vals else None
    I = grab("I:")
    LRA = grab("LRA:")
    peak = None
    for l in txt.splitlines():
        if "Peak:" in l:
            peak = l.split()[-2]
    return I, LRA, peak

master = ROOT / "build" / "master.wav"
x = decode(master)
dur = len(x) / SR
rows = []
def row(name, val, ok, note=""):
    rows.append((name, val, ok, note))
    print(f"  {name:<26} {val:>18}  {'PASS' if ok else 'FAIL'}  {note}")

I, LRA, peak = ebur128(master)
if I:
    iv = float(I); row("整片响度", f"{iv:.2f} LUFS", abs(iv - TL["target_lufs"]) <= 1.0, f"目标 {TL['target_lufs']}")
if peak:
    pv = float(peak); row("真峰值", f"{pv:.2f} dBFS", pv <= -1.0, "≤ -1.0")
row("母带时长", f"{dur:.2f}s", abs(dur - TL["duration"]) <= 0.35, f"时间轴 {TL['duration']:.2f}s")

# 旁白区间：不该有长静音（自然停顿允许，死气与吞字不允许）
# 口径说明：整段覆盖率会把句间自然停顿算成"缺陷"，所以改看 开头静音 / 结尾静音 / 内部最长停顿
win = int(0.05 * SR)
env = np.sqrt(np.convolve(x.astype(np.float64) ** 2, np.ones(win) / win, mode="same"))
speech_thr = max(0.004, float(np.percentile(env, 90)) * 0.18)
lead_lag, inner = [], []
for sp in TL["narration"]:
    a, b = int(sp["start"] * SR), int(sp["end"] * SR)
    seg = env[a:b] > speech_thr
    idx = np.where(seg)[0]
    if len(idx) == 0:
        lead_lag.append(9.9); inner.append(9.9); continue
    lead_lag.append(idx[0] / SR); lead_lag.append((len(seg) - idx[-1] - 1) / SR)
    run = best = 0
    for v in seg:
        run = 0 if v else run + 1
        best = max(best, run)
    inner.append(best / SR)
row("旁白首尾静音", f"{max(lead_lag):.2f}s (最差)", max(lead_lag) <= 0.60, "≤0.60s")
row("旁白内部最长停顿", f"{max(inner):.2f}s", max(inner) <= 0.90, "≤0.90s（自然停顿允许）")

# 字幕与旁白区间对齐
cues = TL["cues"]
viol_span = 0; viol_ovl = 0; viol_len = 0
for i, c in enumerate(cues):
    inside = any(sp["start"] - 0.25 <= c["start"] and c["end"] <= sp["end"] + 0.25 for sp in TL["narration"])
    if not inside: viol_span += 1
    if c["end"] - c["start"] < 0.55 or len(c["text"]) < 3: viol_len += 1
    if i + 1 < len(cues) and c["end"] > cues[i + 1]["start"] + 0.001: viol_ovl += 1
row("字幕落在旁白区间内", f"{len(cues)-viol_span}/{len(cues)}", viol_span == 0)
row("字幕时长/长度合法", f"{len(cues)-viol_len}/{len(cues)}", viol_len == 0, "≥0.55s 且 ≥3 字")
row("字幕不重叠", f"{viol_ovl} 处", viol_ovl == 0)

# 画面对齐：镜头必须包住旁白
bad_shot = 0
for sp in TL["narration"]:
    sh = next((s for s in TL["shots"] if s["id"] == sp["shot"]), None)
    if not sh or not (sh["start"] <= sp["start"] + 0.01 and sp["end"] <= sh["end"] + 0.01): bad_shot += 1
row("镜头包住旁白", f"{len(TL['narration'])-bad_shot}/{len(TL['narration'])}", bad_shot == 0)

# 与成片对照
if MP4 and MP4.exists():
    r = subprocess.run([FF, "-i", str(MP4)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    import re
    d = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    has_a = "Audio:" in r.stderr
    if d:
        vd = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
        row("成片时长", f"{vd:.2f}s", abs(vd - dur) <= 0.35, f"母带 {dur:.2f}s")
    row("成片含音轨", str(has_a), has_a)

print("=" * 74)
bad = [r for r in rows if not r[2]]
print(f"音频判据：{len(rows)-len(bad)}/{len(rows)} 通过" + ("" if not bad else f"  未过：{[b[0] for b in bad]}"))
