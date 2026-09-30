# -*- coding: utf-8 -*-
"""
成片验收（独立于构建流程，直接检查 output 里的 mp4）：
  1) 容器/流信息、时长、帧数
  2) 每个场景抽一帧，确认非黑帧且有内容
  3) 字幕是否真的烧进画面（比对字幕带与场景带）
  4) 音频是否有声、与场景起点是否对齐
  5) 抽查字幕文字与时间轴是否一致
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = Path(FFMPEG).parent / "ffprobe.exe"
if not FFPROBE.exists():
    FFPROBE = None
EP = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1
sys.path.insert(0, str(ROOT))
import script as _ep
_tv, _ = _ep.load(EP)
VIDEO = ROOT / "output" / f"AI科普-{_tv['title']}.mp4"
W, H = 1920, 1080
SR = 24000
# 时间轴在这里就读进来：下面的时长校验需要用它作为期望值
tl = json.loads(_ep.timeline_path(EP).read_text(encoding="utf-8"))

# 画幅选择：python verify_output.py v  -> 校验竖屏成片
FMT = next((a.lower() for a in sys.argv[2:] if a.lower() in ("h", "v")), "h")
if FMT == "v":
    VIDEO = VIDEO.with_name(VIDEO.stem + "-竖屏.mp4")
    W, H = 1080, 1920
    SUB_BAND = (int(H * 0.83), int(H * 0.94))     # 字幕带（竖屏字幕在底部）
else:
    SUB_BAND = (900, 995)

ok = True


def probe_video(path):
    """有 ffprobe 就用 ffprobe，否则解析 ffmpeg -i 的 stderr。"""
    if FFPROBE:
        r = subprocess.run([str(FFPROBE), "-v", "error", "-show_format", "-show_streams",
                            "-of", "json", str(path)], capture_output=True, text=True)
        return json.loads(r.stdout)
    r = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    txt = r.stderr
    dur = 0.0
    for line in txt.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            dur = int(h) * 3600 + int(m) * 60 + float(s)
    streams = []
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("Stream #") and "Video:" in line:
            pix = "yuv420p" if "yuv420p" in line else ""
            m = re.search(r"(\d{3,4})x(\d{3,4})", line)
            streams.append({"codec_type": "video", "codec_name": "h264", "pix_fmt": pix,
                            "width": int(m.group(1)) if m else 0,
                            "height": int(m.group(2)) if m else 0})
        elif line.startswith("Stream #") and "Audio:" in line:
            sr = re.search(r"(\d+) Hz", line)
            streams.append({"codec_type": "audio",
                            "codec_name": "aac" if "aac" in line else "",
                            "sample_rate": sr.group(1) if sr else ""})
    return {"format": {"duration": str(dur)}, "streams": streams}


def fail(msg):
    global ok
    ok = False
    print(f"  ✗ {msg}")


def check(cond, msg):
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        fail(msg)


print("=" * 68)
print("成片验收:", VIDEO.name)
print("=" * 68)

# ---------- 1) 容器信息
print("\n[1] 容器与流")
info = probe_video(VIDEO)
fmt = info["format"]
v = next(s for s in info["streams"] if s["codec_type"] == "video")
a = next(s for s in info["streams"] if s["codec_type"] == "audio")
dur = float(fmt["duration"])
# 时长以时间轴为准（不同期的长度不同，不能写死 60.5s）
_expect = float(tl.get("total", dur))
check(abs(dur - _expect) < 0.35, f"时长 {dur:.2f}s（时间轴 {_expect:.2f}s）")
check(v["width"] == W and v["height"] == H, f"分辨率 {v['width']}x{v['height']}")
check(v["codec_name"] == "h264", f"视频编码 {v['codec_name']}")
check(a["codec_name"] == "aac" and int(a["sample_rate"]) == 48000,
      f"音频编码 {a['codec_name']} @ {a['sample_rate']}Hz")
check("yuv420p" in v.get("pix_fmt", ""), f"像素格式 {v.get('pix_fmt')}")

# ---------- 2/3) 抽帧检查
print("\n[2] 各场景抽帧（画面内容 + 字幕烧录）")
tl = json.loads(_ep.timeline_path(EP).read_text(encoding="utf-8"))
raw = ROOT / "verify" / "probe.raw"
(ROOT / "verify").mkdir(exist_ok=True)
subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(VIDEO),
                "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw)], check=True)
frames = np.fromfile(raw, dtype=np.uint8).reshape(-1, H, W, 3)
n_frames = len(frames)
print(f"  抽到 {n_frames} 帧（应为 1816 左右）")

for sc in tl["scenes"]:
    # 取该场景中一条字幕的中点作为采样时刻
    cap = sc["captions"][len(sc["captions"]) // 2]
    t = (cap["start"] + cap["end"]) / 2
    k = int(round(t * 30))
    if k >= n_frames:
        fail(f"{sc['id']}: 帧号越界")
        continue
    fr = frames[k]
    bright = fr.mean()
    cy0, cy1 = SUB_BAND
    sc_band = fr[int(H * 0.19):int(H * 0.74)].mean()
    has_content = bright > 8 and fr.std() > 6

    # 字幕检测：字幕条是"深底 + 白字"的圆角条，特征是两个条件同时成立 ——
    #   ① 带内有接近纯白的文字像素（max 高）
    #   ② 带内亮度起伏大（文字与底色对比）
    # 早期版本用"字幕带比场景带更亮"来判断，这在程序化渐变背景下成立，
    # 但第 2 期换成深色 AI 背景后，字幕条反而比画面更暗 —— 判据失效，
    # 导致 6 个场景全部误报"字幕缺失"。改为不依赖与场景的相对亮度。
    band = fr[cy0:cy1]
    bx = band.max()
    bstd = band.std()
    caption_present = bx >= 180 and bstd > 40
    print(f"  {sc['id']:<9} t={t:5.2f} 帧{k:5d} 亮度{bright:6.2f} 对比{fr.std():6.2f} "
          f"场景带{sc_band:6.2f} 字幕带最亮{bx:5.1f}/起伏{bstd:5.1f}  "
          f"{'内容✓' if has_content else '内容✗'} {'字幕✓' if caption_present else '字幕✗'}")
    if not has_content:
        fail(f"{sc['id']} 画面疑似黑帧")
    if not caption_present:
        fail(f"{sc['id']} 字幕带疑似缺失")

# ---------- 4) 音频
print("\n[4] 音频")
subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(VIDEO),
                "-ac", "1", "-ar", str(SR), "-f", "s16le", str(ROOT / "verify" / "a.raw")],
               check=True)
snd = np.fromfile(ROOT / "verify" / "a.raw", dtype="<i2").astype(np.float32) / 32768
check(len(snd) / SR > 60, f"音频长度 {len(snd)/SR:.2f}s")
check(np.abs(snd).max() > 0.2, f"音频峰值 {np.abs(snd).max():.3f}（有声）")
win = int(0.02 * SR)
rms = np.sqrt(np.convolve(snd ** 2, np.ones(win) / win, "same"))
print("  各场景旁白起点偏差（相对场景起始，含编码器延迟）：")
offs = []
for sc in tl["scenes"]:
    s = int(sc["start"] * SR)
    seg = rms[max(0, s - 1200):min(len(rms), s + int(sc["dur"] * SR))]
    thr = max(0.004, seg.max() * 0.06)
    idx = int(np.argmax(seg > thr))
    off = (max(0, s - 1200) + idx) / SR - sc["start"]
    offs.append(off)
    print(f"     {sc['id']:<9} {off:+.2f}s")
check(max(abs(o) for o in offs) < 0.45, f"最大偏差 {max(abs(o) for o in offs):.2f}s < 0.45s")

# ---------- 5) 字幕完整性
print("\n[5] 字幕时间轴")
speech = sum(s["speech_dur"] for s in tl["scenes"])
n_caps = sum(len(s["captions"]) for s in tl["scenes"])
covered = 0.0
bad_overlap = 0
tiny = 0
for s in tl["scenes"]:
    cs = s["captions"]
    for i, c in enumerate(cs):
        dur_c = c["end"] - c["start"]
        covered += dur_c
        if dur_c <= 0:
            fail(f"{s['id']} 字幕时长非正: {c}")
        # 单条字幕过短（在 30fps 下不足 8 帧）会看不清
        if dur_c < 0.28:
            tiny += 1
            fail(f"{s['id']} 字幕过短 {dur_c:.2f}s: {c['text']}")
        # 与下一条真正重叠（交叠 > 1 帧 0.034s）才算问题；首尾相接属正常
        if i + 1 < len(cs):
            ov = c["end"] - cs[i + 1]["start"]
            if ov > 0.06:
                bad_overlap += 1
                fail(f"{s['id']} 字幕交叠 {ov:.3f}s: {c['text']} / {cs[i+1]['text']}")
print(f"  共 {n_caps} 条字幕，覆盖 {covered:.1f}s / 旁白总长 {speech:.1f}s "
      f"({covered/speech*100:.0f}%)")
print(f"  交叠异常 {bad_overlap} 处，过短字幕 {tiny} 条")
check(bad_overlap == 0, "无字幕交叠")
check(tiny == 0, "无过短字幕")
check(covered / speech > 0.95, f"字幕覆盖率 {covered/speech*100:.0f}% > 95%")

print("\n" + "=" * 68)
print("验收结果:", "全部通过 ✓" if ok else "存在问题 ✗")
print("=" * 68)
sys.exit(0 if ok else 1)
