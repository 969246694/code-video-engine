# -*- coding: utf-8 -*-
"""
ae.py —— 一个轻量的"专属 AE"内核。

设计目标：把"每屏一张静止版式"换成"一切皆可关键帧"的合成模型。

与 AE 的对应关系：
    Comp        ↔ 合成（可嵌套，一个 comp 可作为另一个 comp 的图层）
    Layer       ↔ 图层（含变换、不透明度、混合模式、z 深度）
    Kf          ↔ 关键帧（含贝塞尔缓动、过冲、回弹、定格）
    Camera      ↔ 摄像机（位置/缩放/旋转/对焦距离 → 驱动景深与视差）
    Adjustment  ↔ 调整图层（模糊、调色、暗角，作用于其下所有图层）
    MotionBlur  ↔ 运动模糊（单帧内对时间子采样后叠加）
    time remap  ↔ 时间重映射（变速、定格、倒放）

关键区别（这正是"PPT vs 剪辑"的分水岭）：
    旧渲染器里位置/缩放/透明度都是**常量**，元素入场后画面就冻结（实测平稳期变化 0.000）。
    这里每个属性都是一条**时间曲线**，画面天然全程在动。

坐标约定：合成内部一律用 **comp 坐标**（宽 cw × 高 ch），
本模块负责映射到输出设备像素，场景代码不需要关心超采样/画幅。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import scipy.ndimage as ndi

# ---------------------------------------------------------------- 缓动
#
# AE 的缓动本质是"影响速度曲线"：进出速度、是否过冲。
# 这里用少量命名曲线覆盖绝大多数用法，外加自定义贝塞尔。

EASINGS = {}


def _ease(name):
    def deco(fn):
        EASINGS[name] = fn
        return fn
    return deco


@_ease("linear")
def _lin(t):
    return t


@_ease("ease")
def _ease_io(t):
    return t * t * (3 - 2 * t)


@_ease("ease_in")
def _ease_in(t):
    return t * t * t


@_ease("ease_out")
def _ease_out(t):
    return 1 - (1 - t) ** 3


@_ease("ease_in_out")
def _ease_in_out(t):
    """先加速后减速（AE 默认的 Easy Ease），镜头推拉最常用"""
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


@_ease("smooth")
def _smooth(t):
    """平滑起停，比 ease_in_out 更轻"""
    return t * t * t * (t * (t * 6 - 15) + 10)


@_ease("expo_out")
def _expo_out(t):
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


@_ease("expo_in")
def _expo_in(t):
    return 0.0 if t <= 0 else 2 ** (10 * (t - 1))


@_ease("back_out")
def _back_out(t):
    """收尾过冲：AE 里常用的"弹到位"手感"""
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


@_ease("elastic_out")
def _elastic_out(t):
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    c4 = 2 * math.pi / 3
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * c4) + 1


@_ease("bounce_out")
def _bounce_out(t):
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def bezier_ease(p1x, p1y, p2x, p2y):
    """
    自定义三次贝塞尔缓动（与 AE 的速度曲线编辑器等价）。
    用牛顿迭代求 x→t 的反函数，再取 y。
    """
    def bez(t, a, b):
        return 3 * (1 - t) ** 2 * t * a + 3 * (1 - t) * t * t * b + t ** 3

    def fn(x):
        x = min(1.0, max(0.0, x))
        lo, hi = 0.0, 1.0
        for _ in range(24):
            mid = (lo + hi) / 2
            if bez(mid, p1x, p2x) < x:
                lo = mid
            else:
                hi = mid
        return bez((lo + hi) / 2, p1y, p2y)

    return fn


# ---------------------------------------------------------------- 关键帧
@dataclass
class Kf:
    """一个关键帧：时间 + 值 + 到下一帧所用的缓动"""
    t: float
    v: float
    ease: object = "ease"          # 名字 或 函数 或 "hold"


class Prop:
    """
    可关键帧化的属性。没有关键帧时就是常量；有关键帧时按时间求值。

    三种用法可以**叠加**（与 AE 的"表达式叠在关键帧之上"一致）：
      set(v)                  常量
      key([(t,v),...])        关键帧序列
      expr(fn)                表达式：fn(t, prop) -> float，**加在**基础值之上

    ★ expr 必须是"附加量"而不是"替换量"。
      踩过的坑：Camera.constant_move() 调用 zoom.expr(...) 之后，
      之前用 dolly() 设的推镜关键帧被整个丢掉 ——
      那个镜头于是完全静止（实测逐帧变化 0.00000）。
      现在 at() 返回 base + expr，两者共存。
    """

    __slots__ = ("_const", "_kfs", "_expr")

    def __init__(self, v=0.0):
        self._const = float(v)
        self._kfs: list[Kf] = []
        self._expr = None

    # -------- 构造
    def set(self, v):
        self._const = float(v)
        self._kfs = []
        self._expr = None
        return self

    def key(self, points, ease="ease"):
        """points: [(t, v), ...]；ease 可给单个名字或与点等长的列表"""
        self._kfs = []
        for i, (t, v) in enumerate(points):
            e = ease[i] if isinstance(ease, (list, tuple)) else ease
            self._kfs.append(Kf(float(t), float(v), e))
        self._kfs.sort(key=lambda k: k.t)
        return self

    def expr(self, fn):
        """
        表达式：fn(t, prop) -> float，**叠加**在关键帧/常量之上。
        可多次调用（后一次覆盖前一次，但都不会丢掉关键帧）。
        """
        self._expr = fn
        return self

    def wiggle(self, amp, freq, seed=0):
        """AE 的 wiggle()：在基础值上叠加平滑噪声"""
        rng = np.random.default_rng(seed)
        n = 512
        raw = rng.standard_normal(n)
        smooth = ndi.gaussian_filter1d(raw, sigma=max(1.0, 0.5 / max(1e-6, freq) * 30))
        base = self.raw

        def fn(t, p):
            f = (t * freq) % 1.0
            i0 = int(t * freq) % n
            i1 = (i0 + 1) % n
            val = smooth[i0] * (1 - f) + smooth[i1] * f
            return val * amp
        return self.expr(fn)

    # -------- 求值
    @property
    def raw(self):
        return self._const if not self._kfs else self._kfs[0].v

    def at(self, t):
        if not self._kfs:
            base = self._const
        else:
            ks = self._kfs
            if t <= ks[0].t:
                base = ks[0].v
            elif t >= ks[-1].t:
                base = ks[-1].v
            else:
                for i in range(len(ks) - 1):
                    a, b = ks[i], ks[i + 1]
                    if a.t <= t <= b.t:
                        span = max(1e-9, b.t - a.t)
                        u = (t - a.t) / span
                        e = a.ease
                        if e == "hold":
                            base = a.v
                        else:
                            f = EASINGS[e] if isinstance(e, str) else e
                            base = a.v + (b.v - a.v) * f(u)
                        break
                else:
                    base = ks[-1].v
        if self._expr is not None:
            base = base + self._expr(t, self)
        return base

    def __call__(self, t):
        return self.at(t)


# ---------------------------------------------------------------- 混合模式
_vig_cache = {}

BLEND_MODES = ("normal", "add", "screen", "multiply", "overlay", "soft_light")


def blend(bottom: np.ndarray, top: np.ndarray, alpha: np.ndarray, mode: str):
    """
    把 top 以 alpha 合成到底层 bottom 上（float32，0..255）。
    只对**图层的非空包围盒**做运算 —— 这是关键优化：
    整幅 1920×1080 是 207 万像素，而一个文字图层只占其中很小一块。
    实测整幅运算时 blend 占总渲染时间的 40%（1.74s / 2 帧）。
    """
    if alpha.size == 0:
        return bottom
    h, w = alpha.shape[:2]
    y0 = int(max(0, np.floor(_first_ink_row(alpha))))
    y1 = int(min(bottom.shape[0], np.ceil(_last_ink_row(alpha))))
    x0 = int(max(0, np.floor(_first_ink_col(alpha))))
    x1 = int(min(bottom.shape[1], np.ceil(_last_ink_col(alpha))))
    if y1 <= y0 or x1 <= x0:
        return bottom

    sub_b = bottom[y0:y1, x0:x1]
    sub_t = top[y0:y1, x0:x1]
    sub_a = alpha[y0:y1, x0:x1]

    if mode == "normal":
        out = sub_b * (1 - sub_a) + sub_t * sub_a
    elif mode == "add":
        out = np.clip(sub_b + sub_t * sub_a, 0, 255)
    elif mode == "screen":
        b = sub_b / 255.0
        s = 1 - (1 - b) * (1 - sub_t / 255.0)
        out = np.clip(sub_b * (1 - sub_a) + s * 255.0 * sub_a, 0, 255)
    elif mode == "multiply":
        m = sub_b * sub_t / 255.0
        out = np.clip(sub_b * (1 - sub_a) + m * sub_a, 0, 255)
    elif mode == "overlay":
        b = sub_b / 255.0
        s = np.where(b < 0.5, 2 * b * (sub_t / 255.0),
                     1 - 2 * (1 - b) * (1 - sub_t / 255.0))
        out = np.clip(sub_b * (1 - sub_a) + s * 255.0 * sub_a, 0, 255)
    elif mode == "soft_light":
        b = sub_b / 255.0
        s = np.where(sub_t / 255.0 < 0.5,
                     2 * b * (sub_t / 255.0) + b * b * (1 - 2 * (sub_t / 255.0)),
                     2 * b * (1 - sub_t / 255.0)
                     + np.sqrt(np.maximum(0, b)) * (2 * (sub_t / 255.0) - 1))
        out = np.clip(sub_b * (1 - sub_a) + s * 255.0 * sub_a, 0, 255)
    else:
        out = sub_b * (1 - sub_a) + sub_t * sub_a
    bottom[y0:y1, x0:x1] = out
    return bottom


def _ink_bounds(alpha):
    """alpha 中非零像素的包围盒（用列/行投影找，比 nonzero 快）"""
    rows = alpha.reshape(alpha.shape[0], -1).max(axis=1)
    cols = alpha.reshape(-1, alpha.shape[1]).max(axis=0)
    nz_r = np.flatnonzero(rows > 0.004)
    nz_c = np.flatnonzero(cols > 0.004)
    if nz_r.size == 0 or nz_c.size == 0:
        return None
    return int(nz_r[0]), int(nz_r[-1]) + 1, int(nz_c[0]), int(nz_c[-1]) + 1


def _first_ink_row(alpha):
    b = _ink_bounds(alpha)
    return 0 if b is None else b[0]


def _last_ink_row(alpha):
    b = _ink_bounds(alpha)
    return 0 if b is None else b[1]


def _first_ink_col(alpha):
    b = _ink_bounds(alpha)
    return 0 if b is None else b[2]


def _last_ink_col(alpha):
    b = _ink_bounds(alpha)
    return 0 if b is None else b[3]


# ---------------------------------------------------------------- 图层
# ---------------------------------------------------------------- 图层
def _check_t(v):
    return float(v)


def _check_scale(v, name=""):
    """
    缩放口径守卫：scale 是百分比（100 = 原始大小）。
    传进来 0 < v <= 4 时几乎一定是把比例当成了百分比
    （实测有人写成 0.95 想让图形缩到 95%，结果缩到 0.95% 完全看不见），
    这里自动换算并提示。
    """
    v = float(v)
    if 0 < v <= 4:
        print(f"  [ae] 警告：图层 {name!r} 的 scale={v} 疑似是**比例**，"
              f"已按百分比换算为 {v*100:.0f}（scale 口径：100=原始大小）")
        return v * 100
    if v <= 0:
        return 0.01
    return v


class Layer:
    """
    图层。变换与外观属性**全部可关键帧化** —— 这是与旧渲染器的根本差别。

    坐标：图层内的绘制使用图层自身的局部坐标（宽 w × 高 h），
    放置到合成里时由 position / scale / rotation / anchor 决定。

    ★ scale 是**百分比**（100 = 原始大小），与 AE 一致，不是比例！
      踩过的坑：写成 0.95 会被引擎当成 0.95%，560px 的图形画成 5px 细线，
      而且画面里"什么都没有"，很难一眼看出是缩放口径问题。
      下面 _check_scale 专门拦这种误用。
    """

    __slots__ = ("name", "w", "h", "z", "pos_x", "pos_y", "scale_x", "scale_y",
                 "rot", "opacity", "blend", "draw_fn", "visible", "blur",
                 "anchor_x", "anchor_y", "_img_cache")

    def __init__(self, name, w, h, draw_fn, z=0.0):
        self.name = name
        self.w, self.h = int(w), int(h)
        self.z = float(z)                    # 深度：影响视差与景深虚化
        self.draw_fn = draw_fn               # fn(img, t) -> None，在局部坐标画
        # 变换（全部是可关键帧属性）
        self.pos_x = Prop(w / 2)
        self.pos_y = Prop(h / 2)
        self.scale_x = Prop(100.0)           # 百分比，与 AE 一致
        self.scale_y = Prop(100.0)
        self.rot = Prop(0.0)                 # 度
        self.opacity = Prop(100.0)
        self.blur = Prop(0.0)                # 额外模糊（像素，合成坐标）
        self.anchor_x = Prop(w / 2)
        self.anchor_y = Prop(h / 2)
        self.blend = "normal"
        self.visible = True
        self._img_cache = {}

    # ---------------- scale 口径守卫
    def set_scale(self, sx, sy=None):
        """
        设置缩放（百分比）。传比例（如 0.95）会被识别并自动换算成百分比，
        同时给出明确提示 —— 避免"图形缩到看不见"这类难以定位的问题。
        """
        sy = sx if sy is None else sy
        sx, sy = _check_scale(sx, self.name), _check_scale(sy, self.name)
        self.scale_x.set(sx)
        self.scale_y.set(sy)
        return self

    def scale_key(self, points, ease="ease"):
        """按百分比设关键帧（同样做比例→百分比的自动换算）"""
        pts = [(_check_t(t), _check_scale(v, self.name)) for t, v in points]
        self.scale_x.key(pts, ease)
        self.scale_y.key(pts, ease)
        return self

    # 便捷设置
    def at(self, x=None, y=None):
        if x is not None:
            self.pos_x.set(x)
        if y is not None:
            self.pos_y.set(y)
        return self

    def move(self, points, ease="ease"):
        self.pos_x.key([(t, v[0]) for t, v in points], ease)
        self.pos_y.key([(t, v[1]) for t, v in points], ease)
        return self

    def zoom(self, points, ease="ease"):
        self.scale_key(points, ease)
        return self

    def fade(self, points, ease="ease"):
        self.opacity.key(points, ease)
        return self

    def spin(self, points, ease="ease"):
        self.rot.key(points, ease)
        return self

    def render_local(self, t, ss=1.0):
        """把图层内容画到自己的画布上（缓存：内容不随时间变化时只画一次）"""
        key = (round(t, 3), ss)
        if key in self._img_cache:
            return self._img_cache[key]
        W, H = int(self.w * ss), int(self.h * ss)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.draw_fn(img, t, ss)
        if len(self._img_cache) > 8:
            self._img_cache.clear()
        self._img_cache[key] = img
        return img


class TextLayer(Layer):
    """文字图层：字体/字号/字距/颜色都可关键帧化"""

    def __init__(self, name, text, font_path, size, color=(255, 255, 255),
                 tracking=0.0, align="left"):
        self.text = text
        self.font_path = font_path
        self.size = Prop(size)
        self.tracking = Prop(tracking)
        self.color = color
        self.align = align
        self._font_cache = {}
        self._bbox = self._measure()
        super().__init__(name, self._bbox[0], self._bbox[1], self._paint)

    def _font(self, px):
        key = max(1, int(round(px)))
        if key not in self._font_cache:
            self._font_cache[key] = ImageFont.truetype(self.font_path, key)
        return self._font_cache[key]

    def _measure(self):
        f = self._font(self.size.raw)
        if not self.text:
            return (1, 1)
        w = sum(f.getlength(c) + self.tracking.raw for c in self.text)
        w = max(1, int(w - self.tracking.raw))
        h = max(1, int(f.size * 1.25))
        return (w + 4, h + 4)

    def _paint(self, img, t, ss):
        d = ImageDraw.Draw(img)
        f = self._font(self.size.at(t) * ss)
        sp = self.tracking.at(t) * ss
        col = self.color
        if self.align == "center":
            total = sum(f.getlength(c) + sp for c in self.text) - sp
            x = (img.width - total) / 2
        else:
            x = 0.0
        for c in self.text:
            d.text((x, 0), c, font=f, fill=col)
            x += f.getlength(c) + sp


class ImageLayer(Layer):
    """
    图片图层：用于 AI 生成的底图。

    关键用途：**同一张底图可以在不同镜头里取不同区域/不同缩放** ——
    这是"一张图拆成多个镜头"的手段（对应 AE 里对素材设置不同的
    Scale/Position 关键帧），比每镜都生成新图省得多，也保证画风统一。

    zoom 是相对合成长边的比例；聚焦点用 crop_x / crop_y（0~1 归一化）。
    """

    def __init__(self, name, path, comp_w, comp_h, zoom=1.0,
                 crop_x=0.5, crop_y=0.5, z=1.0):
        self.path = str(path)
        self.comp_w, self.comp_h = comp_w, comp_h
        self.zoom = Prop(zoom)
        self.crop_x = Prop(crop_x)
        self.crop_y = Prop(crop_y)
        self._src = Image.open(self.path).convert("RGB")
        super().__init__(name, comp_w, comp_h, self._paint, z=z)

    def _paint(self, img, t, ss):
        """按 zoom 与聚焦点裁剪原图，铺满图层画布"""
        W, H = img.width, img.height
        z = max(1.0, self.zoom.at(t))
        cx = self.crop_x.at(t)
        cy = self.crop_y.at(t)
        sw = min(self._src.width, self._src.width / z)
        sh = min(self._src.height, self._src.height / z)
        # 保持合成宽高比
        ar = self.comp_w / self.comp_h
        if sw / sh > ar:
            sw = sh * ar
        else:
            sh = sw / ar
        x0 = (self._src.width - sw) * min(1.0, max(0.0, cx))
        y0 = (self._src.height - sh) * min(1.0, max(0.0, cy))
        crop = self._src.crop((int(x0), int(y0), int(x0 + sw), int(y0 + sh)))
        img.paste(crop.resize((W, H), Image.LANCZOS), (0, 0))


class AdjustmentLayer(Layer):
    """
    调整图层：作用于其下所有已合成内容（模糊 / 调色 / 暗角）。
    这是 AE 里做统一质感的关键手段。
    """

    def __init__(self, name, op, **params):
        super().__init__(name, 1, 1, lambda img, t, ss: None, z=-999)
        self.op = op
        self.params = params
        self.visible = True

    def apply(self, arr: np.ndarray, t: float) -> np.ndarray:
        p = {k: (v.at(t) if isinstance(v, Prop) else v) for k, v in self.params.items()}
        if self.op == "blur":
            r = p.get("radius", 0.0)
            if r <= 0.01:
                return arr
            out = np.empty_like(arr)
            for c in range(3):
                out[:, :, c] = ndi.gaussian_filter(arr[:, :, c], sigma=r)
            return out
        if self.op == "vignette":
            h, w = arr.shape[:2]
            st = round(p.get("strength", 0.3), 4)
            key = (h, w, st)
            v = _vig_cache.get(key)
            if v is None:
                yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
                nx = (xx - w / 2) / (w / 2)
                ny = (yy - h / 2) / (h / 2)
                v = 1.0 - st * np.clip(nx ** 2 * 0.8 + ny ** 2, 0, 1.6) ** 0.9
                v = np.maximum(v, 1.0 - st * 1.2)
                v = v[:, :, None]
                if len(_vig_cache) > 6:
                    _vig_cache.clear()
                _vig_cache[key] = v
            return arr * v
        if self.op == "exposure":
            return np.clip(arr * p.get("gain", 1.0) + p.get("lift", 0.0), 0, 255)
        if self.op == "levels":
            # 黑白场校正：AE 的 Levels 等价物，用来把底子压成真正的深色
            lo = p.get("black", 0.0)
            hi = p.get("white", 255.0)
            g = p.get("gamma", 1.0)
            x = np.clip((arr - lo) / max(1e-6, hi - lo), 0, 1)
            return np.clip((x ** (1.0 / max(1e-6, g))) * 255.0, 0, 255)
        if self.op == "saturation":
            lum = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
            s = p.get("amount", 1.0)
            return np.clip(lum[:, :, None] + (arr - lum[:, :, None]) * s, 0, 255)
        if self.op == "tint":
            col = np.array(p.get("color", (255, 255, 255)), dtype=np.float32)
            amt = p.get("amount", 0.15)
            return np.clip(arr * (1 - amt) + (arr * col / 255.0) * amt, 0, 255)
        return arr


# ---------------------------------------------------------------- 摄像机
class Camera:
    """
    摄像机：驱动视差与景深。

    每个图层有 z 深度；摄像机的 zoom 与 position 让不同深度产生不同位移
    （z 越小离镜头越近，位移越大），并且与焦平面的距离决定虚化半径 ——
    这就是景深。旧渲染器只有"四层视差"，这里升级为真摄像机。
    """

    __slots__ = ("x", "y", "zoom", "rot", "focus", "dof", "near", "far")

    def __init__(self, w, h):
        self.x = Prop(0.0)
        self.y = Prop(0.0)
        self.zoom = Prop(100.0)
        self.rot = Prop(0.0)
        self.focus = Prop(0.0)       # 焦平面深度
        self.dof = Prop(0.0)         # 景深强度（0=全清晰）
        self.near = 1.0
        self.far = 1.0

    def dolly(self, points, ease="ease"):
        """推拉镜头：zoom 变化"""
        self.zoom.key(points, ease)
        return self

    def pan(self, points, ease="ease"):
        self.x.key([(t, v[0]) for t, v in points], ease)
        self.y.key([(t, v[1]) for t, v in points], ease)
        return self

    def drift(self, amp=10.0, speed=0.3, seed=1):
        """手持微晃：让画面"活着"，即使没有其他运动"""
        rng = np.random.default_rng(seed)
        ax, ay = rng.uniform(0.6, 1.4), rng.uniform(0.6, 1.4)
        px, py = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
        self.x.expr(lambda t, p: math.sin(t * speed * 2.1 + px) * amp * ax)
        self.y.expr(lambda t, p: math.sin(t * speed * 1.7 + py) * amp * ay)
        return self

    def breathe(self, amp=1.2, speed=0.5):
        """呼吸感：极其缓慢的缩放脉动，肉眼几乎察觉不到但画面不再死板"""
        self.zoom.expr(lambda t, p: math.sin(t * speed * 6.283) * amp)
        return self

    def constant_move(self, drift=0.35, push=0.5, rot=0.03, seed=2):
        """
        恒定微动 —— 消灭"镜头末尾静止"。

        这是"PPT 感"最隐蔽的来源：每个镜头都写成
        「动起来 → 缓停 → 切到下一镜」，于是镜头后段完全死掉
        （实测 shotA 末尾逐帧变化 0.004 / 41.9 的切点，相差一万倍）。

        专业做法是让镜头**要么在运动中切走，要么始终保持恒定微动**。
        这里叠加三个互不锁相的低频运动（横移、缓推、极轻微旋转），
        它们在整段时长内都不会同时回到零，因此画面始终有变化。
        """
        rng = np.random.default_rng(seed)
        px, py = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
        pz, pr = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
        # 频率互质，避免周期性"同时停住"
        fx, fy = 0.103, 0.071
        fz, fr = 0.037, 0.019
        self.x.expr(lambda t, p: math.sin(t * fx * 6.283 + px) * drift * 10)
        self.y.expr(lambda t, p: math.sin(t * fy * 6.283 + py) * drift * 6)
        self.zoom.expr(lambda t, p: math.sin(t * fz * 6.283 + pz) * push * 0.6)
        self.rot.expr(lambda t, p: math.sin(t * fr * 6.283 + pr) * rot)
        return self


# ---------------------------------------------------------------- 合成
class Comp:
    """
    合成。可以嵌套：一个 Comp 能作为另一个 Comp 的图层（对应 AE 的 precomp）。

    渲染流程（与 AE 一致）：
      1) 解析摄像机 → 每个图层得到位移/缩放/虚化
      2) 按 z 从远到近排序，逐个渲染并合成
      3) 调整图层作用于其下已合成的内容
      4) 运动模糊 = 对时间做子采样后叠加
    """

    def __init__(self, name, w, h, bg=None, fps=30):
        self.name = name
        self.w, self.h = int(w), int(h)
        self.layers: list[Layer] = []
        self.adjustments: list[AdjustmentLayer] = []
        self.bg = bg                    # fn(w, h, t) -> RGB Image，或 None
        self.fps = fps
        self.camera: Camera | None = None

    def add(self, *layers):
        for l in layers:
            if isinstance(l, AdjustmentLayer):
                self.adjustments.append(l)
            else:
                self.layers.append(l)
        return self

    def camera_at(self, *a, **kw):
        self.camera = Camera(self.w, self.h) if self.camera is None else self.camera
        for fn in a:
            fn(self.camera)
        return self.camera

    # ---------------- 单帧渲染（不含运动模糊）
    def _render_once(self, t, ss=1.0):
        W, H = int(self.w * ss), int(self.h * ss)
        if self.bg is not None:
            base = self.bg(self.w, self.h, t).convert("RGB")
            if (base.width, base.height) != (W, H):
                base = base.resize((W, H), Image.LANCZOS)
            arr = np.asarray(base).astype(np.float32)
        else:
            arr = np.zeros((H, W, 3), dtype=np.float32)

        cam = self.camera
        cx, cy = self.w / 2, self.h / 2
        cam_x = cam.x.at(t) if cam else 0.0
        cam_y = cam.y.at(t) if cam else 0.0
        cam_z = (cam.zoom.at(t) if cam else 100.0) / 100.0
        cam_rot = cam.rot.at(t) if cam else 0.0
        focus = cam.focus.at(t) if cam else 0.0
        dof = cam.dof.at(t) if cam else 0.0

        # 按 z 从远到近（z 越大越远，先画）
        ordered = sorted([l for l in self.layers if l.visible and l.opacity.at(t) > 0.1],
                         key=lambda l: -l.z)

        for lay in ordered:
            # ---- 视差：深度决定位移比例（z 越大越远 → 位移越小）
            par = 1.0 / (1.0 + max(0.0, lay.z) * 0.55)
            tx = -cam_x * par
            ty = -cam_y * par

            px = lay.pos_x.at(t) + tx
            py = lay.pos_y.at(t) + ty
            sx = lay.scale_x.at(t) / 100.0 * cam_z
            sy = lay.scale_y.at(t) / 100.0 * cam_z
            rot = lay.rot.at(t) + cam_rot
            op = min(1.0, max(0.0, lay.opacity.at(t) / 100.0))
            if op <= 0.001:
                continue

            # ---- 渲染图层内容
            limg = lay.render_local(t, ss)
            # 变换：以 anchor 为基准缩放旋转
            ax = lay.anchor_x.at(t) * ss
            ay = lay.anchor_y.at(t) * ss
            nw, nh = max(1, int(limg.width * sx)), max(1, int(limg.height * sy))
            limg2 = limg.resize((nw, nh), Image.LANCZOS)
            ax2, ay2 = ax * sx, ay * sy
            if abs(rot) > 0.01:
                limg2 = limg2.rotate(-rot, resample=Image.BICUBIC,
                                     center=(ax2, ay2), expand=False)

            # ---- 景深虚化：离焦平面越远越虚
            blur_r = lay.blur.at(t) * ss
            if dof > 0.01:
                blur_r += dof * abs(lay.z - focus) * ss
            if blur_r > 0.15:
                a = np.asarray(limg2).astype(np.float32)
                for c in range(3):
                    a[:, :, c] = ndi.gaussian_filter(a[:, :, c], sigma=blur_r)
                limg2 = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")

            # ---- 放置
            ox = int(round(px * ss - ax2))
            oy = int(round(py * ss - ay2))
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            canvas.paste(limg2, (ox, oy), limg2)
            ta = np.asarray(canvas).astype(np.float32)
            alpha = (ta[:, :, 3:4] / 255.0) * op
            arr = blend(arr, ta[:, :, :3], alpha, lay.blend)

        # ---- 调整图层（作用于其下所有内容）
        for adj in self.adjustments:
            if adj.visible:
                arr = adj.apply(arr, t)
        return arr

    # ---------------- 带运动模糊的渲染
    def render(self, t, ss=1.0, motion_blur=0.0):
        """
        motion_blur: 快门角度等效（0=关闭，1≈1/2 帧的快门）。
        做法：在一帧的时间跨度内取多个子时刻分别渲染再平均 ——
        与真实摄影机同一原理，也是 AE 运动模糊的实现方式。
        """
        if motion_blur <= 0.001:
            arr = self._render_once(t, ss)
        else:
            n = 3
            span = motion_blur / self.fps
            acc = np.zeros((int(self.h * ss), int(self.w * ss), 3), dtype=np.float32)
            for i in range(n):
                sub = t + (i / (n - 1) - 0.5) * span
                acc += self._render_once(sub, ss)
            arr = acc / n
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def still(comp: Comp, out_w, out_h, t, ss=2.0, motion_blur=0.0, save=None):
    """渲染一张静帧，输出到目标尺寸（超采样后降采样，即抗锯齿）"""
    img = comp.render(t, ss=ss, motion_blur=motion_blur)
    if (img.width, img.height) != (out_w, out_h):
        img = img.resize((out_w, out_h), Image.LANCZOS)
    if save:
        img.save(save)
    return img


def sequence(comp: Comp, out_w, out_h, t0, t1, ss=2.0, motion_blur=0.0):
    """逐帧生成（供编码器消费）"""
    n = max(1, int(round((t1 - t0) * comp.fps)))
    for i in range(n):
        yield still(comp, out_w, out_h, t0 + i / comp.fps, ss=ss, motion_blur=motion_blur)


def make_text_layer(name, text, font_path, size, color, tracking=0.0, align="left"):
    return TextLayer(name, text, font_path, size, color, tracking, align)

