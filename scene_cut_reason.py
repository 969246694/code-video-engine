# -*- coding: utf-8 -*-
"""
用 ae.py 把《AI 为什么会胡说八道》的 reason 段（11.5 秒）重做成真正的多镜头剪辑。

这是"PPT → 剪辑"的样板：
  旧版：一张决策表，放 11.5 秒不动（实测平稳期变化 0.000）
  新版：4 个镜头，每个镜头有景别、有运镜、有出入点，硬切衔接

镜头表（11.5s）：
  A 0.0–3.2   特写：一个字占满画面，摄影机极慢推近        （景别：特写）
  B 3.2–6.6   拉远露出整条决策链，前景虚焦元素掠过          （景别：全景）
  C 6.6–9.3   硬切到结果：一句通顺的编造                    （景别：近景）
  D 9.3–11.5  回到全局，结论浮现                            （景别：全景）
"""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ae

ROOT = Path(__file__).resolve().parent
FB = r"C:\Windows\Fonts\msyhbd.ttc"
FR = r"C:\Windows\Fonts\msyh.ttc"
C_ACCENT = (56, 224, 255)
C_WARM = (255, 138, 92)
C_TEXT = (238, 244, 255)
C_MUTED = (150, 168, 200)

CW, CH = 1920, 1080


# ---------------------------------------------------------------- 背景
def bg_plate(w, h, t):
    """
    深空底：纵向渐变 + 缓慢呼吸的光斑（背景自身也在动）。

    第一版把光斑半径开到 560~780、强度 0.35~0.55，结果整幅画面被抬到
    均值 75.7、蓝/红比 3.64 —— 通篇发蓝发雾，是"廉价感"的直接来源。
    现在收紧半径、降低强度，让底色保持真正的深色，亮部只集中在局部。
    """
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 2):
        k = y / h
        d.rectangle([0, y, w, y + 2],
                    fill=(int(5 + 6 * k), int(9 + 10 * k), int(20 + 22 * k)))
    a = np.asarray(img).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for (rx, ry, r, col, st, ph) in ((0.22, 0.30, 430, (30, 90, 150), 0.42, 0.0),
                                     (0.82, 0.74, 360, (70, 40, 130), 0.34, 2.1),
                                     (0.55, 0.14, 320, (20, 110, 140), 0.26, 4.2)):
        cx = rx * w + math.sin(t * 0.22 + ph) * 26
        cy = ry * h + math.cos(t * 0.18 + ph) * 18
        g = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))) * st
        a += g[:, :, None] * np.array(col, dtype=np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- 图层工厂
def make_text(text, size, color, font=FB, tracking=1.0, align="left"):
    """生成一个"画文字"的函数，用于 Layer"""
    def paint(img, t, ss):
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(font, max(1, int(size * ss)))
        sp = tracking * ss
        total = sum(f.getlength(c) + sp for c in text) - sp
        x = (img.width - total) / 2 if align == "center" else 0.0
        for c in text:
            d.text((x, 0), c, font=f, fill=color)
            x += f.getlength(c) + sp
    w = int(sum(ImageFont.truetype(font, size).getlength(c) + tracking for c in text)) + 8
    h = int(size * 1.5)
    return w, h, paint


def text_layer(name, text, size, color, font=FB, z=0.0, tracking=1.0, align="left"):
    w, h, paint = make_text(text, size, color, font, tracking, align)
    return ae.Layer(name, w, h, paint, z=z)


def chip_layer(name, label, value, col, z=0.0):
    """一个"候选词"卡片图层（含选中态）"""
    w, h = 380, 78
    def paint(img, t, ss):
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=int(14 * ss),
                            fill=(24, 32, 56), outline=(*col, 230), width=max(1, int(2 * ss)))
        f1 = ImageFont.truetype(FB, int(34 * ss))
        d.text((20 * ss, 16 * ss), label, font=f1, fill=C_TEXT)
        f2 = ImageFont.truetype(FR, int(30 * ss))
        d.text((250 * ss, 30 * ss), value, font=f2, fill=col)
    return ae.Layer(name, w, h, paint, z=z)


def dust_layer(name, n=14, seed=3, z=0.0):
    """
    前景虚焦光斑：增强纵深（会被景深进一步虚化）。

    注意：光斑必须是**高度局部**的。第一版用 exp(-r²/2σ²)*26 且 σ 取 18~46，
    结果整幅画面被抬高约 26 级亮度、通篇发蓝发雾，画面立刻显得廉价。
    现在收紧 σ、降低强度，只保留几颗明确的散景点。
    """
    rng = np.random.default_rng(seed)
    pts = [(rng.random(), rng.random(), rng.uniform(7, 15), rng.uniform(0, 6.3)) for _ in range(n)]
    w, h = CW, CH

    def paint(img, t, ss):
        a = np.asarray(img).astype(np.float32)
        yy, xx = np.mgrid[0:img.height, 0:img.width].astype(np.float32)
        for (rx, ry, r, ph) in pts:
            cx = rx * img.width + math.sin(t * 0.35 + ph) * 40
            cy = ry * img.height + math.cos(t * 0.28 + ph) * 26
            rr = r * ss
            g = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * rr * rr)))
            a[:, :, 2] = np.clip(a[:, :, 2] + g * 60, 0, 255)
            a[:, :, 1] = np.clip(a[:, :, 1] + g * 40, 0, 255)
            a[:, :, 3] = np.clip(a[:, :, 3] + g * 150, 0, 255)
        out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")
        img.paste(out, (0, 0))

    return ae.Layer(name, w, h, paint, z=z)


# ---------------------------------------------------------------- 搭四个"镜头"
def build_shots():
    """返回 [(t0, t1, comp)] —— 每个 comp 就是一个镜头"""
    shots = []

    # ===== 镜头 A：特写 —— 一个字占满画面，极慢推近
    A = ae.Comp("shotA", CW, CH, bg=bg_plate, fps=30)
    big = text_layer("必须", "必须", 420, C_TEXT, z=0.0, tracking=6)
    big.pos_x.set(CW / 2)
    big.pos_y.key([(0.0, CH / 2 + 40), (3.2, CH / 2 - 10)], "ease_out")
    big.opacity.key([(0.0, 0), (0.55, 100)], "expo_out")
    big.scale_x.set(140)
    big.scale_y.set(140)
    sub = text_layer("sub", "每一个位置，都必须给出一个词", 44, C_ACCENT, font=FR, z=0.0)
    sub.pos_x.set(CW / 2)
    sub.pos_y.key([(0.6, CH * 0.78 + 24), (3.2, CH * 0.78)], "ease_out")
    sub.opacity.key([(0.6, 0), (1.5, 100)], "ease_out")
    fn1 = text_layer("fn1", "没有「我不说」这个选项", 40, C_WARM, font=FR, z=0.0)
    fn1.pos_x.set(CW / 2)
    fn1.pos_y.set(CH * 0.88)
    fn1.opacity.key([(1.5, 0), (2.4, 100)], "ease_out")
    A.add(big, sub, fn1)
    A.camera_at(lambda c: c.dolly([(0.0, 100), (3.2, 116)], "ease_out"))
    A.camera.constant_move(drift=0.42, push=0.6, seed=7)
    A.add(ae.AdjustmentLayer("lvA", "levels", black=12, white=210, gamma=1.12),
      ae.AdjustmentLayer("vigA", "vignette", strength=0.34))
    shots.append((0.0, 3.2, A))

    # ===== 镜头 B：拉远露出整条决策链；前景虚焦掠过（景深）
    B = ae.Comp("shotB", CW, CH, bg=bg_plate, fps=30)
    cols = [("它知道吗", [("知道", ""), ("不确定", ""), ("不知道", "")], C_ACCENT),
            ("但必须选一个", [("大概率", ""), ("可能", ""), ("也许", "")], C_ACCENT),
            ("继续接下去", [("在 1987 年", ""), ("有研究表明", ""), ("业内普遍认为", "")], C_ACCENT),
            ("结果", [("一句通顺的编造", ""), ("", ""), ("", "")], C_WARM)]
    for ci, (title, opts, col) in enumerate(cols):
        x0 = 96 + ci * 440
        th = text_layer(f"th{ci}", title, 32, C_MUTED, font=FR, z=0.01)
        th.pos_x.set(x0)
        th.pos_y.set(300)
        th.opacity.key([(0.0, 0), (0.35 + ci * 0.09, 100)], "expo_out")
        B.add(th)
        for oi, (label, _) in enumerate(opts):
            if not label:
                continue
            ch = chip_layer(f"c{ci}{oi}", label, "", col if oi == 0 else (70, 84, 118),
                            z=0.02 + oi * 0.005)
            ch.pos_x.set(x0 + 190)
            ch.pos_y.set(352 + oi * 86)
            ch.scale_x.set(96)
            ch.scale_y.set(96)
            ch.opacity.key([(0.15 + ci * 0.08 + oi * 0.06, 0),
                            (0.55 + ci * 0.08 + oi * 0.06, 100)], "back_out")
            B.add(ch)          # ← 第一版漏了这一行：芯片被创建却没加进合成，画面里根本没有
    note = text_layer("note", "没有依据时，它不会停 —— 它会挑最顺的那个词，把话接下去",
                      44, C_ACCENT, z=0.0, tracking=2)
    note.pos_x.set(CW / 2)
    note.pos_y.key([(0.8, CH * 0.72 + 30), (1.9, CH * 0.72)], "back_out")
    note.opacity.key([(0.8, 0), (1.7, 100)], "ease_out")
    B.add(note)
    B.add(dust_layer("dustB", n=12, seed=11, z=-0.9))       # 近景 → 最虚
    B.camera_at(lambda c: c.dolly([(0.0, 128), (3.4, 100)], "ease_in_out")
                if False else c.dolly([(0.0, 128), (3.4, 102)], "ease_out"))
    B.camera.constant_move(drift=0.42, push=0.6, seed=5)
    B.camera.focus.set(0.0)
    B.camera.dof.key([(0.0, 5.2), (1.2, 3.0), (3.4, 1.4)], "ease_out")   # 景深回收
    B.add(ae.AdjustmentLayer("lvB", "levels", black=12, white=210, gamma=1.12),
      ae.AdjustmentLayer("vigB", "vignette", strength=0.30))
    shots.append((3.2, 6.6, B))

    # ===== 镜头 C：硬切到结果 —— 近景
    C = ae.Comp("shotC", CW, CH, bg=bg_plate, fps=30)
    r1 = text_layer("r1", "于是它给出了答案：", 48, C_MUTED, font=FR, z=0.0)
    r1.pos_x.set(CW / 2)
    r1.pos_y.key([(0.0, CH * 0.36), (2.7, CH * 0.36 - 16)], "ease_out")
    r1.opacity.key([(0.0, 0), (0.5, 100)], "expo_out")
    r2 = text_layer("r2", "「1987 年那篇经典论文」", 78, C_TEXT, z=0.0)
    r2.pos_x.set(CW / 2)
    r2.pos_y.key([(0.12, CH * 0.50 + 30), (2.7, CH * 0.50)], "back_out")
    r2.scale_x.key([(0.12, 94), (0.9, 100)], "back_out")
    r2.scale_y.key([(0.12, 94), (0.9, 100)], "back_out")
    r2.opacity.key([(0.12, 0), (0.75, 100)], "ease_out")
    r3 = text_layer("r3", "语法正确 · 语气自然 · 但事实是编的", 40, C_WARM, font=FR, z=0.0)
    r3.pos_x.set(CW / 2)
    r3.pos_y.set(CH * 0.68)
    r3.opacity.key([(1.1, 0), (2.0, 100)], "ease_out")
    C.add(r1, r2, r3)
    C.camera_at(lambda c: c.dolly([(0.0, 100), (2.7, 108)], "ease_out"))
    C.camera.constant_move(drift=0.42, push=0.6, seed=9)
    C.camera.dof.set(0.0)
    C.add(ae.AdjustmentLayer("lvC", "levels", black=12, white=210, gamma=1.12),
      ae.AdjustmentLayer("vigC", "vignette", strength=0.36))
    shots.append((6.6, 9.3, C))

    # ===== 镜头 D：回到全局，结论浮现
    D = ae.Comp("shotD", CW, CH, bg=bg_plate, fps=30)
    ring = ae.Layer("ring", 700, 700, _ring_paint, z=0.4)
    ring.pos_x.set(CW / 2)
    ring.pos_y.set(CH * 0.46)
    ring.opacity.key([(0.0, 0), (0.9, 100)], "ease_out")
    ring.spin([(0.0, 0), (2.2, 24)], "linear")
    concl = text_layer("concl", "它不是在撒谎", 88, C_TEXT, z=0.0)
    concl.pos_x.set(CW / 2)
    concl.pos_y.key([(0.35, CH * 0.52 + 26), (2.2, CH * 0.52)], "back_out")
    concl.opacity.key([(0.35, 0), (1.1, 100)], "ease_out")
    concl2 = text_layer("concl2", "它只是太擅长把话说顺", 52, C_ACCENT, font=FR, z=0.0)
    concl2.pos_x.set(CW / 2)
    concl2.pos_y.set(CH * 0.66)
    concl2.opacity.key([(1.1, 0), (2.2, 100)], "ease_out")
    D.add(ring, concl, concl2)
    D.camera_at(lambda c: c.dolly([(0.0, 104), (2.2, 96)], "ease_out"))
    D.camera.constant_move(drift=0.42, push=0.6, seed=13)
    D.camera.dof.set(0.0)
    D.add(ae.AdjustmentLayer("lvD", "levels", black=12, white=210, gamma=1.12),
      ae.AdjustmentLayer("vigD", "vignette", strength=0.32))
    shots.append((9.3, 11.5, D))

    return shots


def _ring_paint(img, t, ss):
    d = ImageDraw.Draw(img)
    w, h = img.width, img.height
    for i in range(3):
        ph = (t * 0.32 + i / 3) % 1.0
        r = (150 + ph * 200) * ss
        al = int(90 * (1 - ph))
        if al > 3:
            d.ellipse([w / 2 - r, h / 2 - r * 0.66, w / 2 + r, h / 2 + r * 0.66],
                      outline=(*C_ACCENT, al), width=max(1, int(2 * ss)))


# ---------------------------------------------------------------- 渲染
def render_cut(t, shots):
    """给定全局时间，找到所属镜头并渲染（镜头之间的切换 = 硬切）"""
    for t0, t1, comp in shots:
        if t0 <= t < t1:
            return comp, t - t0
    return shots[-1][2], t - shots[-1][0]


if __name__ == "__main__":
    shots = build_shots()
    out = ROOT / "verify" / "ae"
    out.mkdir(parents=True, exist_ok=True)
    SS = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0

    print("=" * 74)
    print("ae.py 样板：reason 段 4 个镜头（特写 → 全景 → 近景 → 全景）")
    print("=" * 74)
    print(f"{'镜头':<8}{'时间':>14}{'时长':>7}  构图")
    for i, (t0, t1, c) in enumerate(shots):
        print(f"  {c.name:<6}{t0:>7.1f}–{t1:<6.1f}{t1-t0:>6.1f}s  "
              f"{['特写','全景','近景','全景'][i]}")

    # 每个镜头取一帧
    for i, (t0, t1, comp) in enumerate(shots):
        t = (t1 - t0) * 0.72
        img = ae.still(comp, CW, CH, t, ss=SS, motion_blur=0.5,
                       save=out / f"shot{i}_{comp.name}.png")
        print(f"  已渲染 {comp.name}  t={t:.1f}s")

    # 整段逐帧运动量（关键指标）
    print()
    print("连续运动量（平稳期与入场期的比值，越接近 1 说明画面一直在动）")
    FPS = 30
    prev = None
    mv = []
    for k in range(int(11.5 * FPS)):
        t = k / FPS
        comp, lt = render_cut(t, shots)
        img = ae.still(comp, 320, 180, lt, ss=0.5, motion_blur=0.4)
        a = np.asarray(img.convert("L"), dtype=np.float32)
        if prev is not None:
            mv.append(float(np.abs(a - prev).mean()))
        prev = a
    mv = np.array(mv)
    print(f"  逐帧变化：均值 {mv.mean():.3f}  最小 {mv.min():.3f}  "
          f"最大 {mv.max():.3f}")
    print(f"  平稳期(>60%)均值 {mv[int(len(mv)*0.6):].mean():.3f}")
    print(f"  判定：{'✓ 全程在动' if mv.min() > 0.05 else '✗ 存在静止帧'}")
