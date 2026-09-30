# -*- coding: utf-8 -*-
"""
构建第 2 期 · 多镜头剪辑版。

与旧的 build.py 并列（旧管线仍保留，两期可以各用各的做法）：
  · 画面：由 compose_ep2.py 的镜头表驱动，逐帧渲染 ae 合成
  · 音轨：复用旧管线的旁白（按场景起点摆放，与旧版完全同一套时间轴）
  · 字幕：复用旧时间轴的 captions，烧在镜头之上

用法：
  python build_cut.py --ss=1.5          # 构建
  python build_cut.py --preview         # 只出静帧
"""
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import ae                      # noqa: E402
import compose_ep2 as EP       # noqa: E402
import script as ep_mod        # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FPS = 30
SR = 24000
OUT_W, OUT_H = 1920, 1080
LEAD = 0.45          # 片头留白（与旧版一致）
TAIL = 1.05          # 片尾定格
CAP_Y = 902          # 字幕条位置（旧版横屏布局）
CAP_H = 88
CAP_SIZE = 46


# ---------------------------------------------------------------- 音频
def decode_pcm(path: Path) -> np.ndarray:
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-f", "s16le",
                        "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"解码失败 {path}: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def build_audio(tl, total):
    n = int(round(total * SR)) + SR
    buf = np.zeros(n, dtype=np.float32)
    for sc in tl["scenes"]:
        seg = decode_pcm(ROOT / sc["audio"])
        st = int(round((sc["start"] + LEAD) * SR))
        en = min(n, st + len(seg))
        buf[st:en] += seg[:en - st]
    peak = float(np.max(np.abs(buf))) or 1.0
    if peak > 0.98:
        buf *= 0.98 / peak
    return buf


def write_wav(path: Path, audio: np.ndarray):
    data = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16))
        f.write(b"data" + struct.pack("<I", len(data)) + data)


# ---------------------------------------------------------------- 字幕
def caption_at(caps, t):
    """
    返回 t 时刻应显示的字幕文本（绝对时间）。

    ★ 必须用**严格区间**判断，不能加容差窗口。
      早先用 [start-0.10, end+0.35]，只要相邻字幕间隔 < 0.45s
      就会同时命中两条（实测有 371 帧处于这种状态）——
      虽然 caption_at 只返回第一条、画面不会真的显示两行，
      但这意味着"后面那条字幕晚放 0.35s"，与旁白严格对齐的前提被破坏。
      另外相邻字幕本身也有 0.05s 重叠，一起修掉。
    """
    # 区间取 [start, end)：两端都闭会在"前一条恰好结束在后一条起点"的那一帧
    # 同时命中两条（实测 1 帧）—— 半开区间即可消除。
    for c in caps:
        if c["start"] <= t < c["end"]:
            return c["text"]
    return None


def draw_caption(img, text, font_path):
    """在画面底部画字幕条（深底 + 白字），与旧版风格一致但重画以适配新引擎"""
    if not text:
        return img
    f = ImageFont.truetype(font_path, CAP_SIZE)
    tw = sum(f.getlength(c) + 1.0 for c in text) - 1.0
    pad = 46
    bw = tw + pad * 2
    x0 = (OUT_W - bw) / 2
    y0 = CAP_Y
    layer = Image.new("RGBA", (int(bw) + 4, CAP_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, layer.width - 1, layer.height - 1], radius=14,
                        fill=(8, 12, 24, 214), outline=(70, 90, 130, 190), width=2)
    a = np.asarray(img).astype(np.float32)
    base = img.convert("RGBA")
    base.alpha_composite(layer, (int(x0), int(y0)))
    img = base.convert("RGB")
    d2 = ImageDraw.Draw(img)
    xx = x0 + pad
    for c in text:
        d2.text((xx + 2, y0 + 20 + 2), c, font=f, fill=(0, 0, 0))
        d2.text((xx, y0 + 20), c, font=f, fill=(240, 246, 255))
        xx += f.getlength(c) + 1.0
    return img


# ---------------------------------------------------------------- 渲染
def render_frame(plan, caps, font_path, t, ss):
    """渲染绝对时间 t 的一帧"""
    for t0, t1, comp in plan:
        if t0 <= t < t1:
            local = t - t0
            img = comp.render(local, ss=ss, motion_blur=getattr(comp, "_motion_blur", 0.0))
            if (img.width, img.height) != (OUT_W, OUT_H):
                img = img.resize((OUT_W, OUT_H), Image.LANCZOS)
            img = draw_caption(img, caption_at(caps, t), font_path)
            return img
    # t 在片头/片尾
    if t < plan[0][0]:
        comp = plan[0][2]
        img = comp.render(0.0, ss=ss, motion_blur=0.0)
    else:
        comp = plan[-1][2]
        img = comp.render(plan[-1][1] - plan[-1][0], ss=ss, motion_blur=0.0)
    if (img.width, img.height) != (OUT_W, OUT_H):
        img = img.resize((OUT_W, OUT_H), Image.LANCZOS)
    return img


def main():
    ss = 1.5
    preview = False
    for a in sys.argv[1:]:
        if a.startswith("--ss="):
            ss = float(a.split("=", 1)[1])
        elif a == "--preview":
            preview = True

    tl = json.loads(ep_mod.timeline_path(2).read_text(encoding="utf-8"))
    plan = EP.build_shots()
    caps = [c for sc in tl["scenes"] for c in sc["captions"]]
    caps.sort(key=lambda c: c["start"])

    t_start = plan[0][0] - LEAD
    t_end = plan[-1][1] + TAIL
    total = t_end - t_start
    n_frames = int(round(total * FPS))

    from shots import F_BOLD
    print("=" * 76)
    print("第 2 期 · 多镜头剪辑版")
    print("=" * 76)
    print(f"镜头 {len(plan)} 个   全片 {total:.2f}s   {n_frames} 帧   "
          f"超采样 {ss}x   字幕 {len(caps)} 条")
    lens = [b - a for a, b, _ in plan]
    print(f"平均镜头长度 {sum(lens)/len(lens):.2f}s   "
          f"最短 {min(lens):.2f}s   最长 {max(lens):.2f}s")

    if preview:
        outdir = ROOT / "verify" / "cut2"
        outdir.mkdir(parents=True, exist_ok=True)
        for i, (t0, t1, comp) in enumerate(plan, 1):
            t = t0 + (t1 - t0) * 0.7
            img = render_frame(plan, caps, F_BOLD, t, ss)
            img.save(outdir / f"{i:02d}_{comp.name}.png")
        print(f"已导出 {len(plan)} 张 -> {outdir}")
        return

    print("\n[1/3] 组装音轨 ...")
    audio = build_audio(tl, total)
    wav = ROOT / "build" / "narration_ep2_cut.wav"
    write_wav(wav, audio)
    print(f"      {wav.name}  {len(audio)/SR:.2f}s  峰值 {np.max(np.abs(audio)):.3f}")

    dest = ROOT / "output" / "AI科普-第2期-多镜头剪辑版.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    print("\n[2/3] 渲染 ...")
    cmd = [FFMPEG, "-y", "-v", "error", "-stats",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OUT_W}x{OUT_H}",
           "-r", str(FPS), "-i", "-", "-i", str(wav),
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-profile:v", "high",
           # 音频统一成 48kHz 立体声（与第 1 期成片一致，避免不同期规格不一）
           "-af", "aresample=48000,aformat=channel_layouts=stereo",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart",
           "-shortest", str(dest)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    t0 = time.time()
    try:
        for k in range(n_frames):
            t = t_start + k / FPS
            img = render_frame(plan, caps, F_BOLD, t, ss)
            proc.stdin.write(img.tobytes())
            if k % (FPS * 5) == 0 or k == n_frames - 1:
                el = time.time() - t0
                fps = (k + 1) / el if el > 0 else 0
                eta = (n_frames - k - 1) / fps if fps > 0 else 0
                print(f"      帧 {k+1:5d}/{n_frames}  {fps:5.2f} fps  ETA {eta:5.0f}s",
                      flush=True)
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    if proc.returncode != 0:
        sys.exit(f"ffmpeg 失败:\n{err[-2000:]}")

    print("\n[3/3] 校验 ...")
    r = subprocess.run([FFMPEG, "-i", str(dest)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in r.stderr.splitlines():
        if "Duration" in line or "Stream #" in line:
            print("      " + line.strip())
    print(f"      文件大小 {dest.stat().st_size/1024/1024:.1f} MB")
    print(f"\n完成 -> {dest}")


if __name__ == "__main__":
    main()
