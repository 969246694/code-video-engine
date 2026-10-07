# -*- coding: utf-8 -*-
"""判据：对成片做量化验收（口径移植自旧项目 verify_cut.py）。
   用法：python verify.py [ffmpeg路径] [影片json] [成片mp4]

   内存策略：**流式逐帧**解码，只保留每帧的少量统计量。
   （曾经把整片解成 float32 —— 539 帧要 12.5 GiB，直接 OOM；长片必须流式。）"""
import json, subprocess, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
FF = sys.argv[1] if len(sys.argv) > 1 else "ffmpeg"
FILM = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "film.json"
mp4 = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "output" / "sample.mp4"
film = json.loads(FILM.read_text(encoding="utf-8"))
W, H = film["size"]; fps = film["fps"]
caps = film["captions"]
cap_top = H - 176; cap_h = 88
fsz = W * H * 3

def read_exact(fp, n):
    out = b""
    while len(out) < n:
        chunk = fp.read(n - len(out))
        if not chunk:
            return out
        out += chunk
    return out

proc = subprocess.Popen([FF, "-v", "error", "-i", str(mp4), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                        stdout=subprocess.PIPE)
m, cover, quiet, has_cap = [], [], [], []
prev = None
i = 0
while True:
    buf = read_exact(proc.stdout, fsz)
    if len(buf) < fsz:
        break
    g = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3).astype(np.float32).mean(axis=2)
    if prev is not None:
        m.append(float(np.abs(g - prev).mean()))
    prev = g
    t = i / fps
    wanted = any(c["start"] <= t < c["end"] for c in caps)
    has_cap.append(wanted)
    if wanted:
        band = g[cap_top + 20:cap_top + cap_h - 20]
        cover.append(float((band > 120).mean()) > 0.01)
    quiet.append(float((g[cap_top - 44:cap_top - 6] > 110).mean()))
    i += 1
proc.stdout.close(); proc.wait()
n = i
m = np.array(m, dtype=np.float32)

# 镜头按时间轴单调铺满（searchsorted）：缝隙/片头/片尾都归最近的前一镜头，
# 与渲染器 shotAt() 的行为一致；否则"缝隙"会被误算成切点（实测把 2 个切点算成 4 个）。
idx = np.arange(n) / fps
starts = np.array([s["start"] for s in film["shots"]], dtype=np.float64)
shot_of = np.clip(np.searchsorted(starts, idx, side="right") - 1, 0, len(film["shots"]) - 1)
same = shot_of[:-1] == shot_of[1:]
intra = m[same]; cuts = m[~same]
static = int((intra < 0.05).sum())
lens = [s["end"] - s["start"] for s in film["shots"]]

rows = []
def row(name, value, lo=None, hi=None, unit="", note=""):
    ok = "-"
    if lo is not None or hi is not None:
        ok = "PASS" if ((lo is None or value >= lo) and (hi is None or value <= hi)) else "FAIL"
    rows.append((name, ok))
    print(f"  {name:<26} {value:>10.3f}{unit:<4} 判据 {str(lo)+'~'+str(hi):<12} {ok}  {note}")

print("=" * 84); print(f"成片判据（量化）  {FILM.name} → {mp4.name}"); print("=" * 84)
print(f"  帧数 {n}  时长 {n/fps:.2f}s  {W}x{H}@{fps}  镜头 {len(film['shots'])}")
print(f"  平均镜头长 {np.mean(lens):.2f}s  最短 {min(lens):.2f}s  最长 {max(lens):.2f}s")
print("-" * 84)
row("镜头内平均运动", float(intra.mean()), 0.05, None, "", "旧片基准 0.497")
row("镜头内最小运动", float(intra.min()), 0.0001, None, "", "不得有完全静止帧")
row("完全静止帧数", float(static), None, 0, " 帧")
row("切点强度", float(cuts.mean()), None, None, "", f"{len(cuts)} 个切点")
row("切点/镜头内运动", float(cuts.mean() / max(1e-6, intra.mean())), 3.0, None, "x", "旧片基准 27x")
if cover:
    row("字幕覆盖", float(np.mean(cover)) * 100, 95, None, "%", f"{len(cover)} 帧应有字幕")
row("字幕带上方安静带", float(np.max(quiet)) * 100, None, 2.0, "%", "防内容与字幕带相撞")
overlap = 0
cs = sorted(caps, key=lambda c: c["start"])
for a, b in zip(cs, cs[1:]):
    if a["end"] > b["start"]:
        overlap += 1
row("同时只能显示一条字幕", float(overlap), None, 0, " 处")
print("=" * 84)
bad = [r for r in rows if r[1] == "FAIL"]
print(f"结论：{len(rows)-len(bad)}/{len(rows)} 通过" + ("" if not bad else f"  未过：{[b[0] for b in bad]}"))
