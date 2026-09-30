# -*- coding: utf-8 -*-
"""
shots.py —— 可复用的「镜头库」。

把"一个镜头"做成一个函数：给定文案与时长，返回一个独立的 ae.Comp。
这样整期视频就是"从镜头库里挑镜头、排时间轴"，
而不是每个场景写一大坨渲染代码。

设计原则（针对"不要无聊"）：
  · 每个镜头**天生带运镜**（推/拉/移/呼吸），不写任何静止镜头
  · 景别是显式参数：特写 / 近景 / 中景 / 全景 / 空镜
  · 文字用**大字号 + 高对比**，信息量比旧版少一半、字号大一倍
  · 每个镜头都有自己的背景处理（暗角/压暗程度），避免"每屏一样"
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import ae

ROOT = Path(__file__).resolve().parent
F_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
F_REG = r"C:\Windows\Fonts\msyh.ttc"

CW, CH = 1920, 1080

# 统一色板
ACCENT = (56, 224, 255)
ACCENT2 = (139, 108, 255)
WARM = (255, 138, 92)
TEXT = (240, 246, 255)
MUTED = (146, 166, 200)
BG_DEEP = (5, 9, 20)


# ---------------------------------------------------------------- 底子
def deep_bg(w, h, t, seed=0):
    """深色底：纵向渐变 + 低频呼吸光斑（背景自身永远在动）"""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 2):
        k = y / h
        d.rectangle([0, y, w, y + 2],
                    fill=(int(5 + 6 * k), int(9 + 10 * k), int(20 + 22 * k)))
    a = np.asarray(img).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rng = np.random.default_rng(seed)
    for _ in range(2):
        rx, ry = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.8)
        r = rng.uniform(340, 460)
        col = np.array([rng.uniform(20, 60), rng.uniform(50, 100), rng.uniform(90, 150)])
        st, ph = rng.uniform(0.24, 0.38), rng.uniform(0, 6.3)
        cx = rx * w + math.sin(t * 0.19 + ph) * 30
        cy = ry * h + math.cos(t * 0.16 + ph) * 20
        g = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))) * st
        a += g[:, :, None] * col
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def photo_bg(path, comp_w, comp_h, zoom=1.0, crop=(0.5, 0.5)):
    """用 AI 底图做背景（可指定取景区域，实现"一张图多镜用"）"""
    p = Path(path) if not str(path).startswith(str(ROOT)) else Path(path)
    src = Image.open(p).convert("RGB")

    def fn(w, h, t):
        z = max(1.0, zoom)
        sw, sh = src.width / z, src.height / z
        ar = comp_w / comp_h
        if sw / sh > ar:
            sw = sh * ar
        else:
            sh = sw / ar
        x0 = (src.width - sw) * crop[0]
        y0 = (src.height - sh) * crop[1]
        c = src.crop((int(x0), int(y0), int(x0 + sw), int(y0 + sh)))
        return c.resize((w, h), Image.LANCZOS)
    return fn


# ---------------------------------------------------------------- 文字图层
def text_layer(name, text, size, color=TEXT, font=F_BOLD, z=0.0,
               tracking=2.0, glow=0.0, shadow=True):
    """
    文字图层（带可选外发光与投影，提升在复杂底图上的可读性）。

    ★ 宽度必须按**逐字累加**来算，不能用 getlength(整串)。
      getlength 会把字距调整（kerning）算进去，而绘制是逐字画的、不含 kerning，
      于是实际墨迹比预留宽度更宽，相邻词条会相互压上
      （实测 "AI" 与 "最危险的时候" 重叠：预留 237px，实际画 269px）。
    """
    f = ImageFont.truetype(font, int(size))
    sp = tracking
    ink_w = int(sum(f.getlength(c) + sp for c in text) - sp)   # 逐字累加，与绘制一致
    w = ink_w + 48
    h = int(size * 1.5)
    pad_x = (w - ink_w) / 2          # 让文字在图层里水平居中
    pad_y = int(size * 0.30)

    def paint(img, t, ss):
        f2 = ImageFont.truetype(font, int(size * ss))
        sps = sp * ss
        col = color
        if glow > 0:
            import scipy.ndimage as ndi
            mask = Image.new("L", (img.width, img.height), 0)
            md = ImageDraw.Draw(mask)
            x = pad_x * ss
            for c in text:
                md.text((x, pad_y * ss), c, font=f2, fill=255)
                x += f2.getlength(c) + sps
            m = ndi.gaussian_filter(np.asarray(mask).astype(np.float32),
                                    sigma=max(1.0, size * 0.085 * ss))
            m = np.clip(m / 255.0 * glow, 0, 1)
            a = np.asarray(img).astype(np.float32)
            for ci in range(3):
                a[:, :, ci] = np.clip(a[:, :, ci] + m * col[ci], 0, 255)
            a[:, :, 3] = np.clip(a[:, :, 3] + m * 255, 0, 255)
            img.paste(Image.fromarray(a.astype(np.uint8), "RGBA"), (0, 0))
        d = ImageDraw.Draw(img)
        if shadow:
            x = pad_x * ss
            for c in text:
                d.text((x + 2 * ss, pad_y * ss + 3 * ss), c, font=f2, fill=(0, 0, 0, 160))
                x += f2.getlength(c) + sps
        x = pad_x * ss
        for c in text:
            d.text((x, pad_y * ss), c, font=f2, fill=color)
            x += f2.getlength(c) + sps

    return ae.Layer(name, w, h, paint, z=z)


def rule_layer(name, width, color=ACCENT, thickness=6, z=0.0, grow_from_zero=True):
    """
    一条强调横线。
    grow_from_zero=True 时初始缩到 0（用于"扫过"动画），
    调用方需要自己把 scale_x 关键帧推到 100。
    """
    h = thickness + 4

    def paint(img, t, ss):
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, img.width - 1, int(thickness * ss)],
                            radius=int(thickness * ss / 2), fill=color)
    lay = ae.Layer(name, int(width), h, paint, z=z)
    if grow_from_zero:
        lay.scale_x.set(1)      # 1%（百分比口径）≈ 从零长出
    return lay


def bar_layer(name, label, value, col, w=680, h=76, z=0.0, selected=True):
    """
    一根概率条。

    ★ scale 是**百分比**（100 = 原始大小），不是比例。
      写成 0.95 会被当成 0.95%，560px 的条会画成 5px 细线，
      画面里"什么都没有"，极难定位（实测踩过）。
    ★ 文字区与条形区必须留够间距：标签最宽约 9 个中文字（≈310px），
      条形从 x=320 起才不会被文字压住。
    """
    def paint(img, t, ss):
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(F_BOLD, int(34 * ss))
        d.text((6 * ss, int(18 * ss)), label, font=f, fill=TEXT if selected else MUTED)
        bx = 320 * ss                      # 让开文字区
        bw = (w - 340) * ss
        d.rounded_rectangle([bx, int(24 * ss), bx + bw, int(52 * ss)],
                            radius=int(12 * ss), fill=(28, 36, 60) if selected else (22, 28, 46))
        d.rounded_rectangle([bx, int(24 * ss), bx + bw * value, int(52 * ss)],
                            radius=int(12 * ss), fill=col)
    lay = ae.Layer(name, w, h, paint, z=z)
    lay.scale_x.set(95)
    lay.scale_y.set(100)
    return lay


def card_layer(name, title, body, col, w=620, h=300, z=0.0):
    """信息卡（标题 + 说明），边框用主题色"""
    def paint(img, t, ss):
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=int(22 * ss),
                            fill=(16, 22, 40, 232), outline=(*col, 210), width=max(1, int(3 * ss)))
        f1 = ImageFont.truetype(F_BOLD, int(52 * ss))
        f2 = ImageFont.truetype(F_REG, int(32 * ss))
        d.text((40 * ss, int(38 * ss)), title, font=f1, fill=TEXT)
        lines = body if isinstance(body, (list, tuple)) else [body]
        for i, ln in enumerate(lines):
            d.text((40 * ss, int((130 + i * 46) * ss)), ln, font=f2, fill=MUTED)
    return ae.Layer(name, w, h, paint, z=z)


# ---------------------------------------------------------------- 镜头构造器
def _finish(comp, *, vignette=0.32, levels=(12, 210, 1.10), grain=0.0,
            blur=0.0, motion_blur=0.0):
    """统一的收尾调色（AE 里就是叠一串调整图层）"""
    if levels:
        comp.add(ae.AdjustmentLayer("lv", "levels", black=levels[0],
                                    white=levels[1], gamma=levels[2]))
    if blur > 0:
        comp.add(ae.AdjustmentLayer("bl", "blur", radius=blur))
    if vignette:
        comp.add(ae.AdjustmentLayer("vg", "vignette", strength=vignette))
    comp._motion_blur = motion_blur
    return comp


def shot_title(name, big, small, dur, *, bg=None, size=170, cam="push_in",
               accent=ACCENT, seed=1, big_y=0.50, small_y=0.70, scrim=0.0, **kw):
    """
    标题镜头：一个大字 + 一行小字。
    cam: push_in（缓推）/ pull_out（缓拉）/ rise（上移）

    big_y / small_y：文字在画面里的纵向位置（0~1）。
    底图中间若有实体内容（如脑扫描图、图表），文字必须让开 ——
    压在上面会同时毁掉画面和文字。
    scrim>0 时先压一层暗底，保证文字在花哨底图上仍然清晰。
    """
    comp = ae.Comp(name, CW, CH, bg=bg or (lambda w, h, t: deep_bg(w, h, t, seed)), fps=30)
    if scrim > 0:
        comp.add(ae.AdjustmentLayer("sc", "exposure", gain=1.0 - scrim))
    lay = text_layer("big", big, size, TEXT, tracking=size * 0.05, glow=0.30)
    lay.pos_x.set(CW / 2)
    if cam == "push_in":
        lay.pos_y.key([(0.0, CH * big_y + 26), (dur, CH * big_y - 8)], "ease_out")
        lay.scale_x.key([(0.0, 96), (dur, 104)], "ease_out")
        lay.scale_y.key([(0.0, 96), (dur, 104)], "ease_out")
    elif cam == "pull_out":
        lay.pos_y.key([(0.0, CH * big_y), (dur, CH * big_y)], "ease")
        lay.scale_x.key([(0.0, 112), (dur, 100)], "ease_out")
        lay.scale_y.key([(0.0, 112), (dur, 100)], "ease_out")
    else:
        lay.pos_y.key([(0.0, CH * (big_y + 0.04)), (dur, CH * (big_y - 0.04))], "ease_out")
    lay.opacity.key([(0.0, 0), (min(0.5, dur * 0.3), 100)], "expo_out")
    comp.add(lay)
    if small:
        s = text_layer("small", small, 42, accent, font=F_REG, tracking=3, glow=0.16)
        s.pos_x.set(CW / 2)
        s.pos_y.key([(dur * 0.22, CH * small_y + 26), (dur, CH * small_y)], "ease_out")
        s.opacity.key([(dur * 0.22, 0), (dur * 0.62, 100)], "ease_out")
        comp.add(s)

    # ★ 摄像机必须有真实的推拉，不能只让文字图层缩放。
    #   只缩放文字时底图完全不动，整帧变化几乎为 0（实测 f1 有 70 帧逐帧变化 0.00000）。
    #   让 **摄像机** 推拉，底图、文字、视差层会一起随景深关系变化，
    #   这才是"镜头在动"而不是"元素在动"。
    def _cam(c):
        if cam == "pull_out":
            c.dolly([(0.0, 108), (dur, 96)], "linear")
        elif cam == "push_in":
            c.dolly([(0.0, 96), (dur, 108)], "linear")
        else:
            c.dolly([(0.0, 98), (dur, 104)], "linear")
        return c

    comp.camera_at(_cam)
    comp.camera.constant_move(drift=0.5, push=0.7, seed=seed)
    return _finish(comp, **kw)


def shot_words(name, words, dur, *, bg=None, size=200, accent_at=(), seed=2,
               stagger=0.16, **kw):
    """
    逐词砸出：一行词按节奏依次出现（对应旁白的关键词）。
    accent_at: 需要高亮的词下标
    """
    comp = ae.Comp(name, CW, CH, bg=bg or (lambda w, h, t: deep_bg(w, h, t, seed)), fps=30)
    f = ImageFont.truetype(F_BOLD, int(size))
    # 与 text_layer 相同的逐字累加口径，保证排布与绘制一致
    widths = [int(sum(f.getlength(c) + size * 0.06 for c in wd) - size * 0.06) + 48
              for wd in words]
    total = sum(widths)
    x = (CW - total) / 2
    for i, wd in enumerate(words):
        col = ACCENT if i in accent_at else TEXT
        lay = text_layer(f"w{i}", wd, size, col, tracking=size * 0.06,
                         glow=0.34 if i in accent_at else 0.20)
        t_in = 0.25 + i * stagger
        lay.pos_x.key([(t_in, x + widths[i] / 2 + 30), (t_in + 0.55, x + widths[i] / 2)],
                      "expo_out")
        lay.pos_y.set(CH * 0.50)
        lay.scale_x.key([(t_in, 116), (t_in + 0.6, 100)], "back_out")
        lay.scale_y.key([(t_in, 116), (t_in + 0.6, 100)], "back_out")
        lay.opacity.key([(t_in, 0), (t_in + 0.28, 100)], "expo_out")
        comp.add(lay)
        x += widths[i]
    # 摄像机慢推，保证底图也在动（只让词条入场，画面后半段会接近静止）
    def _c(c):
        c.dolly([(0.0, 97), (dur, 106)], "linear")
        return c

    comp.camera_at(_c)
    comp.camera.constant_move(drift=0.45, push=0.8, seed=seed)
    return _finish(comp, **kw)


def shot_bars(name, prompt, bars, dur, *, bg=None, size=64, seed=3, **kw):
    """
    概率条镜头：给一句上文，列出候选词与概率，最高者被选中。
    bars: [(词, 概率, 颜色), ...]
    """
    comp = ae.Comp(name, CW, CH, bg=bg or (lambda w, h, t: deep_bg(w, h, t, seed)), fps=30)
    p = text_layer("prompt", prompt, size, MUTED, font=F_REG, tracking=2)
    p.pos_x.set(CW / 2)
    p.pos_y.set(CH * 0.24)
    p.opacity.key([(0.0, 0), (0.45, 100)], "expo_out")
    comp.add(p)
    n = len(bars)
    y0 = CH * 0.40
    for i, (label, val, col) in enumerate(bars):
        b = bar_layer(f"b{i}", label, val, col, z=0.0)
        b.pos_x.set(CW / 2)
        b.pos_y.key([(0.15 + i * 0.10, y0 + i * 74 + 26), (0.75 + i * 0.10, y0 + i * 74)],
                    "expo_out")
        b.scale_x.set(94)
        b.opacity.key([(0.15 + i * 0.10, 0), (0.55 + i * 0.10, 100)], "expo_out")
        comp.add(b)
    # 概率条画完之后若没有运镜，画面会完全定住（实测有静止帧）
    def _c(c):
        c.dolly([(0.0, 104), (dur, 95)], "linear")
        return c

    comp.camera_at(_c)
    comp.camera.constant_move(drift=0.4, push=0.7, seed=seed)
    return _finish(comp, **kw)


def shot_pairs(name, pairs, dur, *, bg=None, size=44, seed=4,
               label_y=0.10, card_y=0.74, **kw):
    """
    左右对照：两组内容，其中右边被高亮为"问题方"。

    版式：标签在上方、卡片在下方，**中间留白给底图** ——
    底图本身往往就是有信息量的画面（如两张对比图），
    把文字叠在它上面会同时毁掉画面和文字（实测标签压在脑扫描图上）。
    """
    comp = ae.Comp(name, CW, CH, bg=bg or (lambda w, h, t: deep_bg(w, h, t, seed)), fps=30)
    for i, (title, body, hot) in enumerate(pairs):
        x0 = 140 + i * 900
        col = WARM if hot else ACCENT
        if title:
            t1 = text_layer(f"t{i}", title, 40, col if hot else MUTED,
                            font=F_REG, tracking=2, glow=0.14 if hot else 0.0)
            t1.pos_x.set(x0 + 300)
            t1.pos_y.set(CH * label_y)
            t1.opacity.key([(0.0 + i * 0.18, 0), (0.5 + i * 0.18, 100)], "expo_out")
            comp.add(t1)
        if body:
            c = card_layer(f"c{i}", body, "", col, w=600, h=170, z=0.0)
            c.pos_x.set(x0 + 300)
            c.pos_y.key([(0.2 + i * 0.18, CH * card_y + 30), (0.9 + i * 0.18, CH * card_y)],
                        "back_out")
            c.scale_x.set(95)
            c.scale_y.set(95)
            c.opacity.key([(0.2 + i * 0.18, 0), (0.7 + i * 0.18, 100)], "expo_out")
            comp.add(c)
    def _c(c):
        c.dolly([(0.0, 98), (dur, 106)], "linear")
        return c

    comp.camera_at(_c)
    comp.camera.constant_move(drift=0.45, push=0.7, seed=seed)
    return _finish(comp, **kw)


def shot_conclusion(name, lines, dur, *, bg=None, size=104, seed=5, **kw):
    """结论镜头：多行结论，逐行推进（字号大、留白足）"""
    comp = ae.Comp(name, CW, CH, bg=bg or (lambda w, h, t: deep_bg(w, h, t, seed)), fps=30)
    for i, ln in enumerate(lines):
        col = ACCENT if i == len(lines) - 1 else TEXT
        s = size if i < len(lines) - 1 else int(size * 0.62)
        lay = text_layer(f"l{i}", ln, s, col, tracking=s * 0.05,
                         glow=0.30 if col == ACCENT else 0.18)
        lay.pos_x.set(CW / 2)
        y = CH * (0.38 + i * 0.16)
        lay.pos_y.key([(0.25 + i * 0.4, y + 34), (1.05 + i * 0.4, y)], "back_out")
        lay.opacity.key([(0.25 + i * 0.4, 0), (0.75 + i * 0.4, 100)], "expo_out")
        comp.add(lay)
    comp.camera_at(lambda c: c.constant_move(drift=0.5, push=0.9, seed=seed))
    comp.camera.dolly([(0.0, 108), (dur, 96)], "linear")
    return _finish(comp, **kw)


def shot_photo_push(name, path, caption, dur, *, zoom0=1.0, zoom1=1.25,
                    crop0=(0.5, 0.5), crop1=(0.5, 0.5), cap_size=46,
                    cap_y=0.80, scrim=0.42, seed=6, **kw):
    """
    图片推镜镜头：AI 底图从 zoom0 推到 zoom1，同时平移聚焦点。
    这是最"像摄影"的一类镜头 —— 画面本身就是运动。
    """
    comp = ae.Comp(name, CW, CH, bg=deep_bg, fps=30)
    z = ae.ImageLayer("plate", path, CW, CH, zoom=zoom0, z=1.2)
    z.zoom.key([(0.0, zoom0), (dur, zoom1)], "linear")
    z.crop_x.key([(0.0, crop0[0]), (dur, crop1[0])], "linear")
    z.crop_y.key([(0.0, crop0[1]), (dur, crop1[1])], "linear")
    comp.add(z)
    if scrim > 0:
        comp.add(ae.AdjustmentLayer("scrim", "exposure", gain=1.0 - scrim))
    if caption:
        c = text_layer("cap", caption, cap_size, TEXT, font=F_BOLD,
                       tracking=cap_size * 0.04, glow=0.30)
        c.pos_x.set(CW / 2)
        c.pos_y.key([(0.0, CH * cap_y + 20), (dur, CH * cap_y - 10)], "ease_out")
        c.opacity.key([(0.1, 0), (0.8, 100)], "expo_out")
        comp.add(c)
    # 图片层自己会在推（zoom0→zoom1），但摄像机也必须动 ——
    # 否则星点/散景等视差层完全静止，实测这类镜头画面几乎不动。
    def _c(c):
        c.dolly([(0.0, 98), (dur, 107)], "linear")
        return c

    comp.camera_at(_c)
    comp.camera.constant_move(drift=0.55, push=1.0, seed=seed)
    return _finish(comp, **kw)


def shot_emptys(name, dur, *, path=None, zoom=1.2, crop=(0.5, 0.5),
                seed=7, vignette=0.42, **kw):
    """空镜：只有画面与运镜，没有文字。用来做呼吸与转场缓冲。"""
    comp = ae.Comp(name, CW, CH, bg=deep_bg, fps=30)
    if path:
        z = ae.ImageLayer("plate", path, CW, CH, zoom=zoom, z=1.2)
        z.zoom.key([(0.0, zoom), (dur, zoom * 1.22)], "linear")
        z.crop_x.key([(0.0, crop[0]), (dur, min(1.0, crop[0] + 0.06))], "linear")
        z.crop_y.key([(0.0, crop[1]), (dur, min(1.0, crop[1] + 0.05))], "linear")
        comp.add(z)
        comp.add(ae.AdjustmentLayer("scrim", "exposure", gain=0.55))
    def _c(c):
        c.dolly([(0.0, 97), (dur, 109)], "linear")
        return c

    comp.camera_at(_c)
    comp.camera.constant_move(drift=0.6, push=1.1, seed=seed)
    return _finish(comp, vignette=vignette, **kw)
