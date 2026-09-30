# -*- coding: utf-8 -*-
"""
一键构建：
  1) 读取 build/timeline.json（由 narrate.py 生成）
  2) 逐帧调用 render.render_frame，通过 stdin 流式喂给 ffmpeg 编码
  3) 在 Python 侧把 6 段旁白按时间轴拼成整条 WAV，再与视频封装
产物：output/AI科普-什么是大语言模型.mp4
"""
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import imageio_ffmpeg
from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import render  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 24000          # edge-tts 输出 24kHz 单声道
BUILD = ROOT / "build"
OUTPUT = ROOT / "output"


def load_timeline(ep=None):
    sys.path.insert(0, str(ROOT))
    import script as ep_mod
    ep = ep_mod.parse_ep() if ep is None else ep
    p = ep_mod.timeline_path(ep)
    if not p.exists():
        sys.exit(f"缺少 {p.name}，请先运行: python narrate.py --ep={ep}")
    tl = json.loads(p.read_text(encoding="utf-8"))
    tl["episode"] = ep
    # 转场与绘制窗口在载入时算好，挂到 tl 上供逐帧使用
    tl["_xfades"] = build_transitions(tl)
    tl["_windows"] = scene_windows(tl)
    return tl


# ------------------------------------------------------------------ 音轨
def decode_pcm(path: Path) -> np.ndarray:
    """解码为 24kHz 单声道 float32"""
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le",
         "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"解码失败 {path}: {r.stderr.decode('utf-8', 'replace')[:400]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def build_audio(tl) -> np.ndarray:
    total = tl["total"]
    n = int(round(total * SR)) + SR // 2
    buf = np.zeros(n, dtype=np.float32)
    for sc in tl["scenes"]:
        seg = decode_pcm(ROOT / sc["audio"])
        start = int(round(sc["start"] * SR))
        end = min(n, start + len(seg))
        buf[start:end] += seg[:end - start]
    # 轻量限幅，避免叠加削波
    peak = float(np.max(np.abs(buf))) or 1.0
    if peak > 0.98:
        buf *= 0.98 / peak
    return buf


def write_wav(path: Path, audio: np.ndarray):
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2").tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(pcm)))
        f.write(b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", len(pcm)))
        f.write(pcm)


# ------------------------------------------------------------------ 视频
def build_video(tl, wav_path: Path, dest: Path, dry_frames=None, ss=2.0,
                size=(1920, 1080), profile=None):
    """
    渲染并编码。
    ss = 设备像素比（超采样倍率）：2.0 表示按 2 倍分辨率绘制后降采样回目标尺寸。
    目的：给 Pillow 的几何图元补上抗锯齿 —— Pillow 对椭圆、圆角矩形、
    斜线都不做抗锯齿（实测边缘过渡为 [0,0,0,255] 的硬跳变，零个中间灰像素）。
    size / profile = 输出画幅与版式档案（横屏 / 竖屏共用同一套场景代码）。
    """
    cfg = tl["video"]
    out_w, out_h, FPS = int(size[0]), int(size[1]), cfg["fps"]
    total = tl["total"]
    n_frames = int(round(total * FPS))

    render.set_scale(ss, (out_w, out_h), profile=profile)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 背景缓存：2x 下单帧约 24MB，降低缓存密度以控制内存
    # ★ 按背景种类各建一份（AI 背景场景必须如此，否则取到的全是程序化渐变）
    cache_fps = 3.0 if ss <= 1.0 else 2.0
    n_cache = int(total * cache_fps) + 12
    t_bg = time.time()
    bg_cache = build_bg_caches(tl, cache_fps)
    kinds = len([k for k in bg_cache if k != "__procedural__"])
    print(f"      背景缓存 {n_cache} 帧/种 × {len(bg_cache)} 种"
          f"（其中 AI 背景 {kinds} 种）  {time.time()-t_bg:.0f}s   "
          f"设备尺寸 {render.W}x{render.H}")

    cmd = [
        FFMPEG, "-y", "-v", "error", "-stats",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", str(FPS), "-i", "-",
        "-i", str(wav_path),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.2",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart",
        str(dest),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)

    t0 = time.time()
    try:
        for k in range(n_frames):
            t = k / FPS
            img = render_active_frame(tl, t, bg_cache, cache_fps)
            if dry_frames is not None and k in dry_frames:
                img.save(ROOT / "preview" / f"frame_{k:05d}_t{t:.2f}.png")
            if ss != 1.0:
                img = img.resize((out_w, out_h), Image.LANCZOS)
            proc.stdin.write(img.tobytes())
            if k % (FPS * 5) == 0 or k == n_frames - 1:
                el = time.time() - t0
                fps = (k + 1) / el if el > 0 else 0
                eta = (n_frames - k - 1) / fps if fps > 0 else 0
                print(f"  帧 {k+1:5d}/{n_frames}  {fps:5.1f} fps  ETA {eta:5.0f}s", flush=True)
    finally:
        if proc.stdin:
            proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    if proc.returncode != 0:
        sys.exit(f"ffmpeg 失败 ({proc.returncode}):\n{err[-3000:]}")
    return n_frames


def get_bg(t, bg_cache, cache_fps):
    """按 cache_fps 缓存插值取背景"""
    fi = t * cache_fps
    i0 = min(int(fi), len(bg_cache) - 1)
    i1 = min(i0 + 1, len(bg_cache) - 1)
    if i0 == i1:
        return bg_cache[i0].copy()
    return Image.blend(bg_cache[i0], bg_cache[i1], fi - i0)


# ---------------------------------------------------------------- 转场
#
# 原先 5 次场景切换都是「整体淡出到黑 + 0.28s 间隔 + 淡入」，
# 实测每次约 0.37s 画面接近全黑（合计 1.83s），屏幕上会明显闪黑，
# 且全部是同一种处理 —— 这是廉价感的主要来源之一。
#
# 现在：相邻场景在 xfade 秒内直接交叠，不再有黑场；按语义给不同类型。
TRANSITION_PLAN = ["sweep", "wipe", "dip", "sweep", "wipe"]
XFADE = 0.42          # 转场时长（秒）


def build_transitions(tl):
    """计算转场：[前场景, 后场景, (start, end, kind)]"""
    scenes = tl["scenes"]
    out = []
    for i in range(len(scenes) - 1):
        a, b = scenes[i], scenes[i + 1]
        dur = min(XFADE, a["dur"] * 0.45, b["dur"] * 0.45)
        start = a["start"] + a["dur"] - dur / 2
        out.append([a, b, (start, start + dur, TRANSITION_PLAN[i % len(TRANSITION_PLAN)])])
    return out


def scene_windows(tl):
    """
    每个场景的绘制窗口。相邻窗口有 xfade 的重叠区，**不留黑场间隙**。
    首场景从 0 开始，末场景延到片尾。
    """
    scenes = tl["scenes"]
    wins = []
    for i, sc in enumerate(scenes):
        start = 0.0 if i == 0 else sc["start"] - XFADE / 2
        end = tl["total"] if i == len(scenes) - 1 else sc["start"] + sc["dur"] + XFADE / 2
        wins.append((start, end))
    return wins


def scene_envelope(tl, idx, t):
    """
    场景自身的淡入淡出权重。转场重叠区里两个场景都是 1（交给转场合成），
    只有全片开头淡入、结尾淡出 —— 这样中间不会出现黑场。
    """
    n = len(tl["scenes"])
    if idx == 0:
        fin = render.ease_out(render.seg(t, 0.0, 0.42))
    else:
        fin = 1.0
    if idx == n - 1:
        end = tl["total"]
        fout = 1.0 - render.smoothstep(render.seg(t, end - 0.55, end))
    else:
        fout = 1.0
    return fin * fout


def scene_bg_path(sc, tl):
    """场景的 AI 背景图路径（未配置则返回 None，用程序化渐变）"""
    rel = sc.get("bg")
    if not rel:
        return None
    p = ROOT / rel
    return p if p.exists() else None


def set_scene_bg(sc, tl):
    """切换当前场景的背景图（None 则恢复程序化渐变）"""
    render.set_background_image(scene_bg_path(sc, tl))


def draw_scene(img, sc, t, tl):
    """把场景内容画到 img 上（含顶部进度条与角标）"""
    local = t - sc["start"]
    img = render.RENDERERS[sc["visual"]](img, local, sc["dur"], sc, tl)
    draw = render.sd(render.ImageDraw.Draw(img))
    render.progress_bar(draw, local, tl["total"], sc["start"], sc["dur"])
    render.brand(draw, tl["video"]["series"])
    return img


def ease_transition(p, kind):
    """转场进度曲线：擦除类用过冲，更有力"""
    p = max(0.0, min(1.0, p))
    if kind in ("wipe", "sweep"):
        return render.ease_in_out_back(p, 1.1)
    return render.smoothstep(p)


_bg_set_for = {"key": None}


def ensure_bg(sc, tl):
    """确保 render 的底图与场景匹配（内部去重，避免每帧重复解码）"""
    key = sc.get("bg") or "__procedural__"
    if _bg_set_for["key"] != key:
        render.set_background_image(scene_bg_path(sc, tl))
        _bg_set_for["key"] = key


_wipe_cache = {}


def wipe_mask_pair(p, kind, w, h):
    """
    返回 (前场景掩码, 后场景掩码)，互补。
    掩码在 1/32 分辨率生成再放大 —— 只影响擦除边界的锐利度，肉眼不可辨，但快很多。
    """
    key = (kind, w, h)
    if key not in _wipe_cache:
        gw, gh = max(8, w // 32), max(8, h // 32)
        yy, xx = np.mgrid[0:gh, 0:gw]
        coord = (xx / gw) if kind == "wipe" else (xx / gw) * 0.74 + (yy / gh) * 0.26
        _wipe_cache[key] = (coord, gw, gh)
    coord, gw, gh = _wipe_cache[key]
    pm = -0.16 + p * 1.32          # 掩码区间略超出 [0,1]，保证首尾干净
    band = 0.09
    old = np.clip((pm + band - coord) / band, 0, 1)
    new = np.clip((coord - (pm - band)) / band, 0, 1)
    m_old = Image.fromarray((old * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR)
    m_new = Image.fromarray((new * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR)
    return m_old, m_new


def compose_transition(fr_a, fr_b, p, kind):
    """按类型合成两帧（设备分辨率）"""
    w, h = fr_a.size
    if kind == "dip":
        # 后场景轻微放大推入（"推镜"感）；交叠期无黑场
        k = 1.0 + 0.04 * (1.0 - p)
        big = fr_b.resize((max(1, int(w * k)), max(1, int(h * k))), Image.BILINEAR)
        ox, oy = (big.width - w) // 2, (big.height - h) // 2
        fr_b = big.crop((ox, oy, ox + w, oy + h))
        return Image.blend(fr_a, fr_b, p)

    m_old, m_new = wipe_mask_pair(p, kind, w, h)
    frame = Image.composite(fr_a, fr_b, m_old)
    if kind == "sweep":
        # 边界加一道光带，强化"扫过"
        cx = int((-0.16 + p * 1.32) * w)
        band = max(8, int(w * 0.045))
        x0, x1 = max(0, cx - band), min(w, cx + band)
        if x1 > x0:
            arr = np.asarray(frame).astype(np.float32)
            col = np.array(render.C_ACCENT, dtype=np.float32)
            fall = np.exp(-((np.arange(x0, x1) - cx) ** 2) / (2 * (band * 0.45) ** 2))
            arr[:, x0:x1] = np.clip(
                arr[:, x0:x1] + fall[None, :, None] * col[None, None, :] * 0.6, 0, 255)
            frame = Image.fromarray(arr.astype(np.uint8), "RGB")
    return frame


def draw_captions(img, tl, t):
    """字幕在转场合成之后绘制，避免被叠化稀释/重复"""
    draw = render.sd(render.ImageDraw.Draw(img))
    for sc in tl["scenes"]:
        caps = sc["captions"]
        if caps and caps[0]["start"] - 0.12 <= t <= caps[-1]["end"] + 0.35:
            render.draw_caption(img, draw, caps, t)
    return img


_bg_caches = {}


def build_bg_caches(tl, cache_fps):
    """
    按「背景种类」分别构建背景缓存 —— 这是必须的，不能只建一份。

    踩过的坑：原先只建一份缓存（进程启动时 _bg_image 还是 None），
    于是所有缓存帧都是程序化渐变；即使 ensure_bg 在渲染前正确设置了
    AI 背景，取到的仍是缓存里的旧渐变，成片里根本看不到 AI 图。
    现在每种背景各自一份缓存，渲染时按当前场景选择。

    index 归属规则：按场景**绘制窗口**的中点判断该时刻属于哪种背景，
    这样每个时刻都有确定归属，缓存内部连续。
    """
    keys = []
    bounds = []          # [(w0, w1, key)]
    for sc, (w0, w1) in zip(tl["scenes"], tl["_windows"]):
        k = sc.get("bg") or "__procedural__"
        keys.append(k)
        bounds.append((w0, w1, k))
    uniq = list(dict.fromkeys(keys))

    n = int(tl["total"] * cache_fps) + 12
    out = {k: [None] * n for k in uniq}

    for k in uniq:
        render.set_background_image(None if k == "__procedural__" else ROOT / k)
        for i in range(n):
            t = i / cache_fps
            key = _bg_key_at(bounds, t)
            if key == k:
                out[k][i] = render.background(t)
    # 补齐空槽（边界处可能没有归属），用同种背景的邻近帧填充
    for k in uniq:
        arr = out[k]
        last = None
        for i in range(n):
            if arr[i] is None:
                arr[i] = last if last is not None else render.background(i / cache_fps)
            else:
                last = arr[i]
    render.set_background_image(None)
    return out


def _bg_key_at(bounds, t):
    for w0, w1, k in bounds:
        if w0 <= t < w1:
            return k
    return bounds[-1][2] if t >= bounds[-1][1] else bounds[0][2]


def get_bg_for(sc, tl, t, bg_caches, cache_fps):
    """
    取当前场景对应的背景。

    除了从对应缓存取图，还要把 render 模块的 _bg_image 同步成当前场景的图 ——
    渲染函数内部会据此判断"底图是 AI 图还是程序化渐变"，
    从而决定是否画自己的图表（见 render_gap 的 draw_panel）。
    """
    k = sc.get("bg") or "__procedural__"
    if _bg_set_for["key"] != k:
        render.set_background_image(None if k == "__procedural__" else ROOT / k)
        _bg_set_for["key"] = k
    cache = bg_caches.get(k)
    if cache is None:
        return render.background(t)
    return get_bg(t, cache, cache_fps)


def render_active_frame(tl, t, bg_cache, cache_fps=3.0):
    """
    渲染 t 时刻的帧（设备分辨率）。
    落在转场窗口内时同时渲染前后两个场景并按类型合成；否则只渲染当前场景。

    bg_cache 参数兼容两种形式：
      · dict  —— 每种背景一份缓存（推荐，AI 背景必需）
      · list  —— 单一缓存（仅程序化背景的旧调用点）
    """
    caches = bg_cache

    def bg_of(sc):
        if isinstance(caches, dict):
            return get_bg_for(sc, tl, t, caches, cache_fps)
        return get_bg(t, caches, cache_fps)

    # ---- 转场窗口
    for a, b, win in tl["_xfades"]:
        if win[0] <= t < win[1]:
            p = ease_transition((t - win[0]) / (win[1] - win[0]), win[2])
            fa = draw_scene(bg_of(a), a, t, tl)
            fb = draw_scene(bg_of(b), b, t, tl)
            frame = compose_transition(fa, fb, p, win[2])
            return draw_captions(frame, tl, t)

    # ---- 普通帧
    cur, widx = tl["scenes"][0], 0
    for i, (sc, (w0, w1)) in enumerate(zip(tl["scenes"], tl["_windows"])):
        if w0 <= t < w1:
            cur, widx = sc, i
            break
    else:
        if t >= tl["_windows"][-1][1]:
            cur, widx = tl["scenes"][-1], len(tl["scenes"]) - 1
    img = draw_scene(bg_of(cur), cur, t, tl)
    env = scene_envelope(tl, widx, t)
    if env < 0.999:
        img = render.apply_fade(img, env)
    return draw_captions(img, tl, t)



from PIL import Image  # noqa: E402


def parse_args():
    """
    命令行参数：
      --preview     只出静帧，不编码视频
      --ss=N        超采样倍率（默认 2）
      --format=h|v  输出画幅：h=横屏 1920x1080（默认），v=竖屏 1080x1920
    """
    ss, fmt = 2.0, "h"
    for a in sys.argv[1:]:
        if a.startswith("--ss="):
            try:
                ss = float(a.split("=", 1)[1])
            except ValueError:
                sys.exit(f"--ss 需要数字，收到: {a}")
            if not (0.5 <= ss <= 4.0):
                sys.exit(f"--ss 需在 0.5~4.0 之间，收到 {ss}")
        elif a.startswith("--format="):
            fmt = a.split("=", 1)[1].strip().lower()
            if fmt not in FORMATS:
                sys.exit(f"--format 只支持 h 或 v，收到 {fmt}")
    return ss, fmt


# 输出画幅：竖屏版共用同一套场景代码与时间轴，只换版式档案与画布尺寸
FORMATS = {
    "h": dict(size=(1920, 1080), prof="h", suffix=""),
    "v": dict(size=(1080, 1920), prof="v", suffix="-竖屏"),
}


# ------------------------------------------------------------------ 主流程
def main():
    tl = load_timeline()
    ss, fmt = parse_args()
    fcfg = FORMATS[fmt]
    out_w, out_h = fcfg["size"]
    dest_suffix = fcfg["suffix"]

    # 竖屏版已可用（逐场景竖向构图 + 0 处元素越界 + 成片验收通过）。
    # 早期版本因适配层 bug 会产生坏片子，那时这里曾拦截；现在不再拦。
    if fmt == "v" and "--preview" not in sys.argv:
        print("竖屏 1080x1920：与横屏共用同一场景代码，逐场景竖向构图")
        print("  验收：python verify_output.py v")

    # 预览模式：只导出若干关键帧 PNG，不做完整渲染
    if "--preview" in sys.argv:
        pdir = ROOT / "preview" / ("v" if fmt == "v" else "ss1" if ss == 1.0 else "ss2")
        pdir.mkdir(parents=True, exist_ok=True)
        render.set_scale(ss, (out_w, out_h), profile=fcfg["prof"])
        print(f"版式档案 {render.LAYOUT['margin']=} ".replace("render.LAYOUT", ""), end="")
        print(f"margin={render.LAYOUT['margin']} y_body={render.LAYOUT['y_body']} "
              f"title_size={render.LAYOUT['title_size']}")
        cache_fps = 3.0 if ss <= 1.0 else 2.0
        bg_cache = [render.background(i / cache_fps)
                    for i in range(int(tl["total"] * cache_fps) + 12)]
        print(f"导出预览帧（时长 {tl['total']:.2f}s，{out_w}x{out_h}，超采样 {ss}x）...")
        for sc in tl["scenes"]:
            for frac in (0.32, 0.62, 0.90):
                t = sc["start"] + sc["dur"] * frac
                img = render_active_frame(tl, t, bg_cache, cache_fps)
                if ss != 1.0:
                    img = img.resize((out_w, out_h), Image.LANCZOS)
                p = pdir / f"{sc['id']}_{int(frac*100):02d}.png"
                img.save(p)
        print(f"  已导出 {len(tl['scenes'])*3} 张 -> {pdir}")
        return

    ep = tl.get("episode", 1)
    print(f"第 {ep} 期《{tl['video']['title']}》")
    print(f"时长 {tl['total']:.2f}s  场景 {len(tl['scenes'])}  分辨率 "
          f"{out_w}x{out_h}@{tl['video']['fps']}  超采样 {ss}x"
          f"{'' if ss == 1.0 else '（抗锯齿）'}  版式 {fmt}")

    print("\n[1/3] 组装音轨 ...")
    audio = build_audio(tl)
    wav = BUILD / f"narration_ep{ep}.wav"
    write_wav(wav, audio)
    print(f"      {wav.name}  {len(audio)/SR:.2f}s  "
          f"峰值 {np.max(np.abs(audio)):.3f}  RMS {np.sqrt((audio**2).mean()):.4f}")

    print("\n[2/3] 渲染视频 ...")
    dest = OUTPUT / f"AI科普-{tl['video']['title']}{dest_suffix}.mp4"
    n = build_video(tl, wav, dest, ss=ss, size=(out_w, out_h), profile=fcfg["prof"])
    print(f"      {n} 帧 -> {dest}")

    print("\n[3/3] 校验 ...")
    r = subprocess.run([FFMPEG, "-i", str(dest)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in r.stderr.splitlines():
        if "Duration" in line or "Stream #" in line:
            print("      " + line.strip())
    size = dest.stat().st_size / 1024 / 1024
    print(f"      文件大小 {size:.1f} MB")


if __name__ == "__main__":
    main()
