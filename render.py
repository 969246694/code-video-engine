# -*- coding: utf-8 -*-
"""
《什么是大语言模型》—— 程序化动画渲染引擎

每个场景是一个纯函数 render_xxx(img, local_t, dur, sc, ctx)，
画面完全由时间 t 决定（无随机状态），因此：
  - 可以任意抽帧出预览图
  - 可以流式逐帧编码，内存占用恒定
"""
import math
from pathlib import Path
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080

# ---------------------------------------------------------------- 配色
C_BG_TOP    = (9, 13, 26)
C_BG_BOT    = (16, 12, 34)
C_ACCENT    = (56, 224, 255)     # 主强调色 青
C_ACCENT2   = (139, 108, 255)    # 次强调色 紫
C_WARM      = (255, 138, 92)     # 暖色（对比/否定项）
C_TEXT      = (238, 243, 255)
C_MUTED     = (140, 155, 190)
C_CARD      = (22, 29, 52)

FONT_DIR = r"C:\Windows\Fonts"
F_BOLD   = f"{FONT_DIR}\\msyhbd.ttc"
F_REG    = f"{FONT_DIR}\\msyh.ttc"
F_LIGHT  = f"{FONT_DIR}\\msyhl.ttc"

_TRACE_CAPTION = False   # 调试开关：打印字幕绘制细节

_font_cache = {}


def font(size, weight="bold"):
    """
    按**参照字号**加载字体，设备字号 = size × 设备像素比。

    ★ 字体只按 SCALE 缩放，不乘 fit 的轴向比例。
    这是必须的：换宽高比时如果字体按 fy 放大，字宽就会同时被 fy 拉宽，
    而 tw() 又要按 fx 还原成参照单位，两者相差 fy/fx 倍，量出的宽度
    与渲染结果对不上 —— 表现为"算得出放得下、实际却被裁"（实测标题被
    误缩到 48px，观感明显偏小）。字号由各版式档案独立给定。
    """
    dev = max(1, int(round(size * SCALE)))
    key = (dev, weight)
    if key not in _font_cache:
        path = {"bold": F_BOLD, "regular": F_REG, "light": F_LIGHT}[weight]
        _font_cache[key] = ImageFont.truetype(path, dev)
    return _font_cache[key]


# ---------------------------------------------------------------- 分辨率无关层
#
# 布局代码永远写「参照画布」上的坐标，由这一层换算到设备像素。
# 这样超采样抗锯齿、竖屏（换宽高比）、4K 都不需要改动场景代码。
#
# 两件独立的事，不要混为一谈：
#   SCALE —— 设备像素比，即超采样倍率（1x 直出 / 2x 抗锯齿），**等比例**
#   FIT   —— 参照画布 → 目标画布的映射，用于**换宽高比**（横屏 16:9 → 竖屏 9:16）
#
# 为什么不用 AST 自动改写坐标常量：那会把时间常数（t * 0.23）、比例系数、
# 颜色分量一起改掉，而且改错是静默的（动画时序错乱但不报错）。在绘图层
# 做变换可以精确区分「定位参数」与「颜色/文本/角度/宽度」。

SCALE = 1.0                       # 设备像素比；1.0 = 直出，2.0 = 2 倍超采样
LOGICAL_W, LOGICAL_H = 1920, 1080  # 目标（输出）逻辑尺寸
REF_W, REF_H = 1920, 1080          # 参照画布：布局代码里的坐标都是这个尺寸下的
FIT_MODE = "stretch"               # "stretch" | "cover"
_fit = (1.0, 1.0, 0.0, 0.0)        # (fx, fy, ox, oy)


def _compute_fit():
    """参照画布 → 目标逻辑画布的仿射映射"""
    global _fit
    if FIT_MODE == "cover":
        k = max(LOGICAL_W / REF_W, LOGICAL_H / REF_H)
        fx = fy = k
    elif FIT_MODE == "contain":
        k = min(LOGICAL_W / REF_W, LOGICAL_H / REF_H)
        fx = fy = k
    else:                              # stretch：两轴独立拉伸
        fx = LOGICAL_W / REF_W
        fy = LOGICAL_H / REF_H
    ox = (LOGICAL_W - REF_W * fx) / 2
    oy = (LOGICAL_H - REF_H * fy) / 2
    _fit = (fx, fy, ox, oy)


def S(v):
    """
    标量换算：按**纵向**比例（字号、圆角、线宽等按高度缩放最稳）。

    注意：这**不能**用来换算 x 坐标！竖屏下纵向比例是 1.778、横向是 0.5625，
    对 x 用 S() 会把宽度放大 3.2 倍（这正是竖屏第一版图形整个溢出的根因）。
    x 方向的量请用 SX()，或直接把包围盒交给 _scale_arg / ScaledDraw 处理。
    """
    return v * _fit[1] * SCALE


def SX(v):
    """横向像素量（x 坐标、宽度）"""
    return v * _fit[0] * SCALE


def SY(v):
    """纵向像素量（y 坐标、高度）——与 S() 等价，保留以表明意图"""
    return v * _fit[1] * SCALE


def fit_ref_scale():
    """
    参照画布到目标画布的统一缩放系数（取两轴较小者），
    用于在 stretch 模式下让圆角/字号不被非等比拉扁。
    """
    return min(_fit[0], _fit[1])


def set_scale(scale, logical_wh=(1920, 1080), ref_wh=None, fit="stretch", profile=None):
    """
    切换渲染尺寸。
      scale      设备像素比（超采样倍率）
      logical_wh 目标逻辑尺寸，如竖屏 (1080, 1920)
      ref_wh     参照画布（布局代码使用的坐标系），默认 1920x1080
      fit        参照→目标的映射方式：stretch / cover / contain
      profile    版式档案名（"h" 横屏 / "v" 竖屏）；None 时按宽高比自动选择
    """
    global SCALE, W, H, LOGICAL_W, LOGICAL_H, REF_W, REF_H, FIT_MODE, LAYOUT
    SCALE = float(scale)
    LOGICAL_W, LOGICAL_H = logical_wh
    if ref_wh is not None:
        REF_W, REF_H = ref_wh
    FIT_MODE = fit
    _compute_fit()
    W = int(round(logical_wh[0] * SCALE))
    H = int(round(logical_wh[1] * SCALE))
    _apply_profile(profile)
    reset_caches()
    return W, H


# ---------------------------------------------------------------- 版式档案
#
# 换宽高比时，除了坐标的仿射变换，字号、边距、标题块高度这些版式参数也要跟着变
# —— 手机上需要更大的字、更窄的正文宽度。用档案（profile）表达，而不是散落的 if。
#
#   margin      左右安全边距
#   title_y     标题首行顶部
#   title_size  主标题字号
#   title_lh    标题行高
#   y_body      正文起点（= 标题块底部）
#   caption_y   字幕条顶部
#   cap_size    字幕字号
#   cap_pad     字幕条左右内边距
#   cap_h       字幕条高度
#   brand_y     右上角标识的 y

PROFILES = {
    # 横屏 16:9（参照坐标系本身）
    "h": dict(margin=132, title_y=306, title_size=84, title_lh=112,
              y_body=544, caption_y=902, cap_size=46, cap_pad=76, cap_h=88,
              brand_y=60, kicker_y=214, align="left", is_vertical=False,
              body_stretch=True),
    # 竖屏 9:16：按 1080x1920 直接设计，坐标即目标像素。
    # body_stretch=False —— 竖屏各场景的 y 是手工排好的绝对坐标，不能被 Y() 二次拉伸。
    "v": dict(margin=56, title_y=300, title_size=96, title_lh=122,
              y_body=544, caption_y=1640, cap_size=58, cap_pad=88, cap_h=112,
              brand_y=76, kicker_y=206, align="center", is_vertical=True,
              body_stretch=False),
}

# 竖屏下可用宽度就是竖屏设计宽度本身（坐标即目标像素，无中间映射）
VERT_REF_USABLE_W = 1080
LAYOUT = dict(PROFILES["h"])

# 参照坐标系里的基线：横屏档案本身
REF_PROFILE = PROFILES["h"]


def _apply_profile(name=None):
    """按档案名（或按宽高比推断）切换版式参数"""
    global LAYOUT
    if name is None:
        name = "v" if LOGICAL_H > LOGICAL_W else "h"
    LAYOUT = dict(PROFILES[name])


def Y(v):
    """
    正文区 y 坐标映射：参照坐标系 → 当前坐标系。

    只有**横屏**需要它（把参照正文区映射到实际正文区），且横屏下是恒等。
    竖屏各场景的 y 已经是手工排好的绝对坐标，必须原样返回 —— 一旦拉伸就会
    二次位移、元素互相压盖。这一点由 profile 的 body_stretch 显式控制，
    而不是靠"caption_y 恰好相等"这种隐式巧合（那种写法一改字幕位置就会出错）。
    """
    if not LAYOUT.get("body_stretch", False):
        return v
    ref_top = REF_PROFILE["y_body"]
    ref_bottom = REF_PROFILE["caption_y"] - 26
    top = LAYOUT["y_body"]
    bottom = LAYOUT["caption_y"] - 26
    k = (bottom - top) / (ref_bottom - ref_top)
    return top + (v - ref_top) * k


def AV():
    """
    当前版式的可用宽度（参照坐标系单位）。
    横屏 = 参照宽度本身；竖屏 = 目标宽度换算回参照单位。
    场景里凡是「以画布宽度居中」的布局都必须用它，直接用 W 会按设备宽度
    居中，导致竖屏下内容被两侧裁掉。
    """
    return VERT_REF_USABLE_W if LAYOUT["is_vertical"] else REF_W


def VW():
    """竖屏下的横向压缩比；横屏为 1。用于把明显过宽的元素整体收窄。"""
    return AV() / REF_W if LAYOUT["is_vertical"] else 1.0


# 各绘图方法：需换算的「定位参数」下标、需换算的关键字（宽高/线宽/半径）、
# 以及返回长度或包围盒、需要还原为参照单位的查询方法。
_SCALE_ARGS = {
    "line": (0,), "ellipse": (0,), "rectangle": (0,), "rounded_rectangle": (0,),
    "polygon": (0,), "point": (0,), "arc": (0,), "chord": (0,), "pieslice": (0,),
    "text": (0,), "textbbox": (0,), "textlength": (0,), "bitmap": (0,),
}

# 关键字里属于「像素量」的参数：按纵向比例换算（换宽高比时线宽/半径按高度最稳）
_SCALE_KW = {
    "rounded_rectangle": ("radius", "width"),
    "rectangle": ("width",),
    "ellipse": ("width",),
    "arc": ("width",),
    "chord": ("width",),
    "pieslice": ("width",),
    "line": ("width",),
    "polygon": ("width",),
    "text": ("stroke_width", "spacing"),
    "multiline_text": ("spacing",),
}


def _scale_axis(v, axis):
    """按指定轴换算单个数值；axis=0 横向、axis=1 纵向"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v * _fit[axis] * SCALE
    if isinstance(v, (list, tuple)):
        out = []
        for i, e in enumerate(v):
            if isinstance(e, (int, float)) and not isinstance(e, bool):
                out.append(e * _fit[0 if i % 2 == 0 else 1] * SCALE)
            else:
                out.append(_scale_axis_any(e))
        return type(v)(out)
    return v


def _scale_axis_any(v):
    """递归处理嵌套结构：序列按奇偶下标分轴，裸标量按纵向"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v * _fit[1] * SCALE
    if isinstance(v, (list, tuple)):
        return _scale_axis(v, 1)
    return v


def scaled_box(box):
    """
    把一个 (x0, y0, x1, y1) 包围盒换算到设备像素。
    必须按轴向分别换算 —— 竖屏下 fx=0.5625、fy=1.778，混用会让图形严重变形。
    """
    x0, y0, x1, y1 = box
    return (x0 * _fit[0] * SCALE, y0 * _fit[1] * SCALE,
            x1 * _fit[0] * SCALE, y1 * _fit[1] * SCALE)


def _scale_arg(v):
    """
    递归换算坐标结构（点 / 列表 / 元组）。
    序列按奇偶下标区分 x / y，分别走 fx / fy —— 这样换宽高比时点与包围盒都能正确映射。
    非数值原样返回。
    """
    return _scale_axis_any(v)


class ScaledDraw:
    """
    包装 ImageDraw，把参照画布坐标自动换算到设备像素。

    关键设计：为了不破坏 `textlength()` 在布局计算中的可用性，
    **查询类**方法（textlength / textbbox / getbbox）返回的是**参照单位**的值
    （换算回去）。这样 `tw()` 量出的宽度与布局坐标在同一空间，
    不会出现「字体设备尺寸 + 坐标参照尺寸」的错位。
    """

    __slots__ = ("_d",)

    # 返回长度或包围盒、需要还原为参照单位的方法
    _QUERY = {"textlength", "textbbox", "getbbox", "multiline_textbbox"}

    def __init__(self, draw):
        self._d = draw

    def __getattr__(self, name):
        target = getattr(self._d, name)
        idxs = _SCALE_ARGS.get(name)
        kws = _SCALE_KW.get(name)
        is_query = name in self._QUERY

        if idxs is None and kws is None and not is_query:
            return target

        def wrapper(*args, **kwargs):
            args = list(args)
            for i in (idxs or ()):
                if i < len(args):
                    args[i] = _scale_arg(args[i])
            for kw in (kws or ()):                 # 线宽 / 圆角半径等像素量
                if kw in kwargs and kwargs[kw] is not None:
                    kwargs[kw] = _scale_px(kwargs[kw])
            out = target(*args, **kwargs)
            if is_query:
                # textlength 是长度量：只除以 SCALE（字体按 SCALE 缩放）
                if isinstance(out, (int, float)):
                    return out / SCALE
                if isinstance(out, (list, tuple)):
                    return type(out)(_inv(e) for e in out)
            return out

        return wrapper


def _scale_px(v):
    """
    把像素量（线宽 / 圆角半径）换算到设备像素。
    必须返回**整数**：Pillow 的 line / rectangle 等接口对 width 只接受 int，
    传 float 会抛 "'float' object cannot be interpreted as an integer"。
    """
    return max(1, int(round(v * _fit[1] * SCALE)))


def _inv(v):
    """
    设备像素 → 参照单位（仅除以 SCALE）。
    字体与几何都只按 SCALE 缩放，因此还原时也只除以 SCALE；
    包围盒同理（x 用 fx、y 用 fy，但长度量统一除以 SCALE）。
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v / SCALE
    if isinstance(v, (list, tuple)):
        out = []
        for i, e in enumerate(v):
            if isinstance(e, (int, float)) and not isinstance(e, bool):
                # 包围盒 (x0,y0,x1,y1)：按轴向各自的画布比例还原
                k = _fit[0] if i % 2 == 0 else _fit[1]
                out.append(e / (k * SCALE))
            else:
                out.append(_inv(e))
        return type(v)(out)
    return v


def _scale_arg_inv(v):
    """兼容旧名（等比例场景）"""
    return _inv(v)


def sd(draw):
    """把 ImageDraw（或 ScaledDraw 实例）包装成 ScaledDraw"""
    return draw if isinstance(draw, ScaledDraw) else ScaledDraw(draw)


def sp(v):
    """缩放一个点/矩形/点列（用于 img.paste / crop 等非 ImageDraw 接口）"""
    return _scale_arg(v)


# ---------------------------------------------------------------- 缓动
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_linear(t):
    return clamp(t)


def ease_out(t):
    """三次缓出：起步快、收尾缓（当前默认动效）"""
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in(t):
    t = clamp(t)
    return t ** 3


def ease_in_out(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


def ease_out_quart(t):
    """更长的收尾，适合大字块入场"""
    t = clamp(t)
    return 1 - (1 - t) ** 4


def ease_out_expo(t):
    """极快起步 + 长收尾，适合数字滚动、条形增长"""
    t = clamp(t)
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def ease_out_back(t, s=1.7):
    """过冲：越过目标再回弹，用于卡片弹入"""
    t = clamp(t)
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def ease_in_out_back(t, s=1.7):
    """两端过冲，用于强调式转场"""
    t = clamp(t)
    u = t * 2
    if u < 1:
        return 0.5 * (u * u * ((s + 1) * u - s))
    u -= 2
    return 0.5 * (u * u * ((s + 1) * u + s) + 2)


def ease_out_elastic(t, amp=1.0, period=0.32):
    """弹性回弹：用于光球、强调元素"""
    t = clamp(t)
    if t in (0.0, 1.0):
        return t
    return amp * 2 ** (-10 * t) * math.sin((t - period / 4) * math.tau / period) + 1


def ease_out_bounce(t):
    """落体回弹：用于元素"落下"的动效"""
    t = clamp(t)
    n, d = 7.5625, 2.75
    if t < 1 / d:
        return n * t * t
    if t < 2 / d:
        t -= 1.5 / d
        return n * t * t + 0.75
    if t < 2.5 / d:
        t -= 2.25 / d
        return n * t * t + 0.9375
    t -= 2.625 / d
    return n * t * t + 0.984375


def smoothstep(t):
    """平滑 0→1，无过冲，用于淡入淡出遮罩"""
    t = clamp(t)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- 运动设计
#
# 单一 ease_out 会让所有元素看起来"同时被推上来"，这是程序化动画最典型的廉价感来源。
# 专业 MG 的做法是：元素分批入场（错帧）、各用不同曲线表达不同"重量"、
# 关键元素带过冲或回弹。下面这套 API 把这些做法固定下来，场景里直接调用。

# 入场曲线档位：不同"重量"的元素用不同曲线
ENTER_CURVES = {
    "heavy": ease_out_quart,      # 大块元素：起步快、收尾长
    "light": ease_out_expo,       # 快速出现的小元素
    "pop": ease_out_back,         # 需要"弹一下"的卡片、标签
    "bounce": ease_out_bounce,    # 落体回弹
    "elastic": ease_out_elastic,  # 强调元素
    "soft": ease_in_out,          # 平滑过渡
}


def stagger(t, i, delay=0.10, start=0.0, dur=0.55, curve="heavy"):
    """
    错帧入场：第 i 个元素的进度（0→1）。
    比全部用同一个 seg(t, a, b) 更有节奏；i 越大入场越晚，形成"依次到位"。
    """
    fn = ENTER_CURVES.get(curve, ease_out)
    return fn(seg(t, start + i * delay, start + i * delay + dur))


def enter_xy(t, i, x, y, delay=0.10, start=0.0, dur=0.55, curve="heavy",
             direction="up", dist=28):
    """
    入场位移：返回 (x, y, 进度)。
    位移方向可指定，位移量随进度收敛到 0（结束后停在目标位置）。
    """
    p = stagger(t, i, delay, start, dur, curve)
    o = (1 - clamp(p)) * dist
    if direction == "up":
        return x, y + o, p
    if direction == "down":
        return x, y - o, p
    if direction == "left":
        return x + o, y, p
    return x - o, y, p


def wipe(t, i, delay=0.10, start=0.0, dur=0.5):
    """条形/下划线类元素的展开进度（带一点过冲，末端更有力）"""
    return ease_out_back(seg(t, start + i * delay, start + i * delay + dur), 1.2)


def count_up(t, start, end_t, target, curve="light"):
    """数字滚动：用于百分比、统计值"""
    return ENTER_CURVES.get(curve, ease_out)(seg(t, start, end_t)) * target


def pulse(t, period=2.4, phase=0.0, amp=0.06):
    """持续轻微呼吸，让静止元素不"死"（幅度很小，避免晃眼）"""
    return 1.0 + amp * math.sin((t / period + phase) * math.tau)


def arcless(t):
    """把 ease_out 的等价写法保留给旧代码，便于逐步迁移"""
    return ease_out(t)


def seg(t, a, b):
    """把 t 映射到 [a,b] 区间内的 0→1 进度"""
    if b <= a:
        return 1.0 if t >= b else 0.0
    return clamp((t - a) / (b - a))


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    t = clamp(t)
    return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))


# ---------------------------------------------------------------- 文字工具
def tw(draw, text, f, spacing=0.0):
    """
    带字距的文本宽度，返回**参照单位**（与布局坐标同一空间）。

    字体只按 SCALE 缩放，所以设备宽度除以 SCALE 即得参照宽度。
    不能除以 fx：fx 是画布映射比例，与字体无关，混用会造成 fy/fx 倍的量测偏差。
    """
    if not text:
        return 0
    if spacing == 0:
        return draw.textlength(text, font=f)
    return sum(draw.textlength(c, font=f) + spacing for c in text) - spacing


def draw_tracked(draw, xy, text, f, fill, spacing=0.0, anchor_center=False):
    """
    逐字绘制以支持字距。

    坐标按 x→fx、y→fy 换算到设备像素；字符步进 = 设备字宽 + spacing×SCALE。
    spacing 只乘 SCALE，不乘 fx —— 字距是排版量，与画布映射无关。

    ★ 这里**不能**用"均摊总宽"的方式来推进：实测逐字推进（字宽+字距）与
    真实整句墨迹宽度是一致的（横屏 1195px vs 推进 1227px）。曾经因为测量工具
    出错而误以为"逐字损失了字间距"，改成按平均宽度推进后反而把块宽放大了 40%。
    改动此处前请先跑 test_text_overlap.py 与 diag_fluent 的墨迹宽度对照。
    """
    x = SX(xy[0])
    y = SY(xy[1])
    sp_dev = spacing * SCALE
    d = draw._d if isinstance(draw, ScaledDraw) else draw
    if anchor_center:
        w_dev = tw(draw, text, f, spacing) * SCALE
        x -= w_dev / 2
    for c in text:
        d.text((x, y), c, font=f, fill=fill)
        x += d.textlength(c, font=f) + sp_dev
    return x


def wrap_cjk(draw, text, f, max_w, spacing=0.0):
    """中文按宽度折行"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""
            continue
        if tw(draw, cur + ch, f, spacing) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def make_glow(img, box, color, radius=26, strength=1.0):
    """
    局部辉光（radial bloom）。

    实现要点：
      1) 源信号 = 该区域「高亮部超出暗部底座的部分」，先缩到 1/4 做两遍模糊再放大，
         比全尺寸高斯快一个量级；
      2) 系数 = 0.72 * 径向衰减 * 模糊源。径向衰减保证在包围盒内平滑收敛到 0，
         因此不会在盒子边缘留下硬边（这是纯模糊方案的典型伪影）。
    坐标类参数按 SCALE 换算，strength 为强度比例不换算。

    radius 的换算要除以模糊降采样系数 4：辉光是在 1/4 分辨率图上做模糊的，
    所以设备像素半径要写作 radius*SCALE/4。若只写 S(radius)（=radius*SCALE），
    2x 下辉光的"相对扩散范围"会缩小一半 —— 表现为同一元素在不同倍率下
    辉光大小不一致（实测横屏 2x 的包围盒左沿比 1x 内缩 22px 就是这个原因）。
    """
    x0, y0, x1, y1 = (int(round(S(v))) for v in box)
    radius = S(radius) / 4.0
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    rw, rh = x1 - x0, y1 - y0
    if rw < 8 or rh < 8:
        return

    region = img.crop((x0, y0, x1, y1))
    arr = np.asarray(region).astype(np.float32)
    lum = arr.max(axis=2) / 255.0

    # 源：扣掉暗部底座，只有真正的亮部才渗光
    latent = np.clip(lum - 0.34, 0, 1) / 0.66
    sw, sh = max(1, rw // 4), max(1, rh // 4)
    small = Image.fromarray((latent * 255).astype(np.uint8), "L").resize((sw, sh), Image.BILINEAR)
    for _ in range(2):
        small = small.filter(ImageFilter.GaussianBlur(max(1.0, radius / 4.0)))
    blurred = np.asarray(small.resize((rw, rh), Image.BILINEAR)).astype(np.float32) / 255.0

    # 径向衰减：中心 1 → 边缘 0
    ax = np.linspace(-1.0, 1.0, rw, dtype=np.float32)[None, :]
    ay = np.linspace(-1.0, 1.0, rh, dtype=np.float32)[:, None]
    dist = np.sqrt(ax * ax + ay * ay)
    falloff = np.clip(1.0 - dist, 0.0, 1.0) ** 1.6

    factor = 0.72 * falloff * blurred * strength
    tint = np.array(mix(color, (255, 255, 255), 0.35), dtype=np.float32)[None, None, :]
    arr = np.clip(arr + factor[:, :, None] * tint, 0, 255).astype(np.uint8)
    img.paste(Image.fromarray(arr, "RGB"), (x0, y0))


# ---------------------------------------------------------------- 背景
_bg_cache = {}


def reset_caches():
    """
    清空尺寸相关缓存。改变 W/H（竖屏、4K、超采样）或版式后必须调用，
    否则缓存的背景层仍是旧尺寸，会触发广播错误或画出错位背景。
    """
    _bg_cache.clear()
    _font_cache.clear()
    _depth_fx.clear()


_BG_LOW = 0.5          # 背景低频部分按此比例生成后放大（背景是平滑内容，放大几乎无损）
CAM_SNAP = 3.0         # 远景缓存量化步长（参照像素）——量化越粗越快，越细越平滑
_bg_stars = None       # 星点静态属性缓存：(x, y, phase, speed)

# ---- 景深 / 视差参数
# 把背景拆成不同视差系数的层，用统一的「摄影机位置」驱动，
# 层间速度差产生纵深。系数以「摄影机速度的倍数」表示：
#   1.0 = 与摄影机同步（无限远），0 = 完全不动（贴脸）
PAR_BACK = 0.55        # 光斑 + 网格（远景）
PAR_MID = 0.30         # 星点（中景）
PAR_NEAR = 0.08        # 前景散景（近景，几乎不动）
PAR_FRONT = 1.35       # 前景元素视差 > 1：比远景更快，制造"贴着镜头"的纵深
CAM_AMP = 42.0         # 摄影机漂移幅度（参照像素）
CAM_SPEED = 0.11       # 摄影机漂移速度
VIG_STRENGTH = 0.12    # 暗角强度（很浅：只做视觉框，不压暗画面）
VIG_FLOOR = 0.90       # 暗角下限，保证四角不会过暗
_pre_vig = None        # 大小不变的暗角系数缓存
_depth_fx = {}         # 视差层缩放缓存（尺寸相关，由 reset_caches 清空）


def camera_pos(t):
    """
    统一的摄影机位置（参照像素）。所有视差层都由它驱动，
    因此它们运动方向一致、只有快慢不同 —— 这是纵深感成立的关键；
    各层用各自的频率乱动只会显得杂乱。
    """
    return (CAM_AMP * math.sin(t * CAM_SPEED),
            CAM_AMP * 0.42 * math.cos(t * CAM_SPEED * 0.77))


def _stars():
    """星点属性只需生成一次；坐标按参照比例存，绘制时再换算"""
    global _bg_stars
    if _bg_stars is None:
        rng = random.Random(20240607)
        _bg_stars = [(rng.random(), rng.random(), rng.random() * math.tau,
                      0.8 + rng.random()) for _ in range(150)]
    return _bg_stars


def _specks():
    """前景散景点：比星点更大更虚，视差最小，用来拉开近景层次"""
    global _bg_specks
    if _bg_specks is None:
        rng = random.Random(31337)
        _bg_specks = [(rng.random(), rng.random(), 0.5 + rng.random(),
                       rng.random() * math.tau) for _ in range(16)]
    return _bg_specks


_bg_specks = None
_bg_front = None


def _foreground():
    """
    极近景虚焦光斑（视差系数 > 1）。
    这是「前景层」—— 与场景内容同处一层但移动更快，
    让画面在背景纵深之外再有一层贴近镜头的空间感。
    """
    global _bg_front
    if _bg_front is None:
        rng = random.Random(90210)
        _bg_front = [(rng.random(), rng.random(), 1.4 + rng.random() * 1.6,
                      rng.random() * math.tau) for _ in range(6)]
    return _bg_front


_bg_cache = {}
_bg_image = None            # 当前场景的 AI 生成背景（设备分辨率），None 表示用程序化渐变
_bg_image_src = None        # 原图缓存，避免重复读盘
PAR_PHOTO = 0.22
PHOTO_SCRIM = 0.62        # AI 背景压暗系数（越低越暗，文字越清晰）            # AI 照片背景的视差系数（介于远景与中景之间）


def set_background_image(path):
    """
    设置当前场景的静态背景图（AI 生成）。传 None 恢复程序化渐变背景。

    设计取舍：AI 图**只替换"底色"这一层**，原有的星点、散景、前景光斑
    仍然照常绘制并参与视差 —— 运动设计（四层视差、统一摄影机）是这个引擎
    真正的价值，不能被一张静态图取代。照片层给一个较小的视差系数，
    让它像"远景"一样缓慢位移。
    """
    global _bg_image, _bg_image_src
    if path is None:
        _bg_image = None
        _bg_image_src = None
        return
    p = Path(path)
    if not p.exists():
        _bg_image = None
        _bg_image_src = None
        return
    if _bg_image_src is None or getattr(_bg_image_src, "_src_path", None) != str(p):
        im = Image.open(p).convert("RGB")
        _bg_image_src = im
        _bg_image_src._src_path = str(p)
    # 缩放到设备分辨率；超采样时按 2x 放大再降采样，保持与整体一致的锐度
    _bg_image = _bg_image_src.resize((W, H), Image.LANCZOS)


def _photo_layer(cam_x, cam_y):
    """把 AI 背景按视差位移贴到底层，并压暗以保证上层文字可读"""
    px = int(round(-cam_x * PAR_PHOTO * SCALE))
    py = int(round(-cam_y * PAR_PHOTO * SCALE))
    # 位移后露出的边缘用图像自身边缘拉伸补齐（避免出现黑边）
    big = Image.new("RGB", (W + 2 * abs(px) + 4, H + 2 * abs(py) + 4))
    ox = abs(px) + 2 - px if px < 0 else 2
    oy = abs(py) + 2 - py if py < 0 else 2
    big.paste(_bg_image, (ox, oy))
    crop = big.crop((abs(px) + 2, abs(py) + 2, abs(px) + 2 + W, abs(py) + 2 + H))
    return crop


def scrim(img, box, alpha=0.62, radius=18):
    """
    在指定区域压一层柔性暗底（"磨砂玻璃"效果）。

    用途：AI 背景图细节丰富，当场景里要放自己的图表/面板时，
    背景的图案会和前景内容抢注意力（实测 gap 场景里 AI 画的曲线
    与我画的曲线叠在一起，分不清哪个是重点）。压一层暗底后
    前景成为明确焦点，同时背景仍透出来，保留质感。
    """
    x0, y0, x1, y1 = scaled_box(box)
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    if x1 <= x0 or y1 <= y0:
        return img
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (6, 12, 26, 0))
    d = ImageDraw.Draw(layer)
    r = max(0, int(radius * SCALE))
    d.rounded_rectangle([0, 0, x1 - x0 - 1, y1 - y0 - 1], radius=r,
                        fill=(6, 12, 26, int(255 * alpha)))
    base = img.convert("RGBA")
    base.alpha_composite(layer, (x0, y0))
    return base.convert("RGB")


def background(t, tint=None):
    """
    深色渐变背景 + 光斑网格（远景） + 星点（中景） + 散景（近景）。

    三层用统一摄影机驱动、视差系数递减，形成纵深。

    性能设计（超采样到 4K 后每帧调用，必须够快）：
      · 远景色调变化极慢 → 按 CAM_SNAP 秒量化后缓存，期间复用同一张图，
        仅做平移合成（平移比重新计算快一个量级）
      · 光斑用可分离 1D 高斯外积，避免全画布 2D exp
      · 星点在输出分辨率直接画，避免放大后变糊
    """
    # 摄影机量化到 CAM_SNAP 秒，量化步长内复用缓存
    cam_x, cam_y = camera_pos(t)
    cam_key = (round(cam_x / CAM_SNAP), round(cam_y / CAM_SNAP))
    if _bg_cache.get("cam") != cam_key:
        _bg_cache["cam"] = cam_key
        _bg_cache["back"] = _render_back_layer(cam_x, cam_y)
    back = _bg_cache["back"]
    # 量化带来的偏移用剩余位移补偿，保证运动连续
    cam_dx, cam_dy = camera_pos(t)
    qx, qy = cam_key[0] * CAM_SNAP, cam_key[1] * CAM_SNAP
    dx_back = int(round(-(cam_dx - qx) * PAR_BACK * SCALE))
    dy_back = int(round(-(cam_dy - qy) * PAR_BACK * SCALE))

    img = Image.new("RGB", (W, H), C_BG_TOP)
    if _bg_image is not None:
        # AI 背景：贴照片层 + 压暗（保证叠在上面的文字/图表仍然清晰）
        img.paste(_photo_layer(cam_dx, cam_dy), (0, 0))
        a0 = np.asarray(img).astype(np.float32)
        a0 *= PHOTO_SCRIM
        img = Image.fromarray(np.clip(a0, 0, 255).astype(np.uint8), "RGB")
    else:
        img.paste(back, (dx_back, dy_back))

    a = np.asarray(img).astype(np.float32)

    # ---- 中景：星点（视差小于远景 → 移动更慢，产生纵深）
    sx = -cam_dx * PAR_MID * SCALE
    sy = -cam_dy * PAR_MID * SCALE
    size_big = max(1, int(round(S(2))))
    size_small = max(1, int(round(S(1))))
    for (rx, ry, ph, spd) in _stars():
        x = int(rx * W + sx)
        y = int(ry * H + sy)
        if not (0 <= x < W and 0 <= y < H):
            continue
        b = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(t * spd + ph))
        s = size_big if b > 0.8 else size_small
        a[max(0, y - s):y + s, max(0, x - s):x + s] += b * 55

    # ---- 近景：散景光斑（视差最小、体积最大、最虚）
    for (rx, ry, rad, ph) in _specks():
        x = rx * W - cam_dx * PAR_NEAR * SCALE
        y = ry * H - cam_dy * PAR_NEAR * SCALE
        r = S(rad * 9)
        glow_int = 12 + 10 * (0.5 + 0.5 * math.sin(t * 0.5 + ph))
        x0, y0 = max(0, int(x - r * 2)), max(0, int(y - r * 2))
        x1, y1 = min(W, int(x + r * 2)), min(H, int(y + r * 2))
        if x1 <= x0 or y1 <= y0:
            continue
        gx = np.arange(x0, x1, dtype=np.float32) - x
        gy = np.arange(y0, y1, dtype=np.float32) - y
        d2 = (gy ** 2)[:, None] + (gx ** 2)[None, :]
        a[y0:y1, x0:x1] += (np.exp(-d2 / (2 * r * r)) * glow_int)[:, :, None]

    # ---- 极近景：大颗虚焦光斑，视差系数 > 1（比远景移动更快）→ 贴近镜头的纵深
    # 位置刻意避开底部字幕带，避免干扰字幕可读性
    for (rx, ry, rad, ph) in _foreground():
        x = rx * W - cam_dx * PAR_FRONT * SCALE
        y = (ry * 0.62) * H - cam_dy * PAR_FRONT * SCALE
        r = S(rad * 26)
        glow_int = 9 + 7 * (0.5 + 0.5 * math.sin(t * 0.37 + ph))
        x0, y0 = max(0, int(x - r * 1.6)), max(0, int(y - r * 1.6))
        x1, y1 = min(W, int(x + r * 1.6)), min(H, int(y + r * 1.6))
        if x1 <= x0 or y1 <= y0:
            continue
        gx = np.arange(x0, x1, dtype=np.float32) - x
        gy = np.arange(y0, y1, dtype=np.float32) - y
        d2 = (gy ** 2)[:, None] + (gx ** 2)[None, :]
        a[y0:y1, x0:x1] += (np.exp(-d2 / (2 * r * r)) * glow_int)[:, :, None]

    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")
    if tint:
        img = Image.blend(img, Image.new("RGB", (W, H), tint), 0.0)
    return img


def _render_back_layer(cam_x, cam_y):
    """远景层：渐变 + 光斑 + 网格，按给定摄影机位置渲染一张完整画布"""
    lw, lh = max(1, int(W * _BG_LOW)), max(1, int(H * _BG_LOW))

    # 纵向渐变（1D 插值，不再逐行向量乘）
    k = np.linspace(0.0, 1.0, lh, dtype=np.float32)[:, None]
    arr = (np.array(C_BG_TOP, dtype=np.float32)[None, :] * (1 - k)
           + np.array(C_BG_BOT, dtype=np.float32)[None, :] * k)
    arr = np.repeat(arr[:, None, :], lw, axis=1)          # (lh, lw, 3)

    # 光斑：可分离 1D 高斯。基准位置 + 摄影机位移（视差系数 PAR_BACK）
    xs = np.arange(lw, dtype=np.float32)
    ys = np.arange(lh, dtype=np.float32)
    fx, fy = _fit[0], _fit[1]
    ox = (cam_x * PAR_BACK) * _BG_LOW * SCALE / max(1e-6, fx)
    oy = (cam_y * PAR_BACK) * _BG_LOW * SCALE / max(1e-6, fy)
    r1, r2, r3 = S(780) * _BG_LOW / fx, S(620) * _BG_LOW / fx, S(560) * _BG_LOW / fx
    for (bx, by, r, col, st) in [
        (lw * 0.50, lh * 0.34, r1, (40, 90, 150), 0.52),
        (lw * 0.16, lh * 0.88, r2, (70, 40, 130), 0.42),
        (lw * 0.88, lh * 0.13, r3, (20, 110, 140), 0.32),
    ]:
        cx, cy = bx + ox, by + oy
        r_eff_y = max(1.0, r)
        gx = np.exp(-((xs - cx) ** 2) / (2 * r * r)) * st
        gy = np.exp(-((ys - cy) ** 2) / (2 * r_eff_y * r_eff_y))
        arr += (gy[:, None] * gx[None, :])[:, :, None] * np.array(col, dtype=np.float32)

    # 细网格（随摄影机平移，用取模偏移实现无缝）
    gstep = S(96) * _BG_LOW / fx
    if gstep >= 2:
        gthick = max(1.0, S(1) * _BG_LOW)
        shift = int(round(ox)) % max(1, int(round(gstep)))
        onx = ((np.arange(lw) + shift) % gstep) < gthick
        ony = ((np.arange(lh) + int(round(oy))) % gstep) < gthick
        arr += onx[None, :, None] * np.array([24, 37, 61], dtype=np.float32)
        arr += ony[:, None, None] * np.array([24, 37, 61], dtype=np.float32)

    # 暗角：烘进远景层，让中近景元素更跳出来。
    # 强度刻意压得很轻 —— 实测 0.42 会让四角降到中心的 30%、整体亮度掉 20%，
    # 画面会明显发闷，失去原有明快感。这里用「下限保护」的方式做浅暗角。
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    nx = (xx - W / 2) / (W / 2)
    ny = (yy - H / 2) / (H / 2)
    vig = 1.0 - VIG_STRENGTH * np.clip(nx ** 2 * 0.78 + ny ** 2, 0, 1.7) ** 0.9
    vig = np.maximum(vig, VIG_FLOOR)

    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    img = img.resize((W, H), Image.BILINEAR)
    if not _LAYER_NO_VIG:
        a = np.asarray(img).astype(np.float32) * vig[:, :, None]
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")
    return img


_LAYER_NO_VIG = False


def vignette(img, strength=0.55):
    a = np.asarray(img).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx - W / 2) / (W / 2)
    ny = (yy - H / 2) / (H / 2)
    v = 1 - strength * np.clip((nx ** 2 * 0.75 + ny ** 2), 0, 1.6) ** 0.9
    a *= v[:, :, None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- 场景转场
def scene_alpha(t, dur):
    """场景淡入淡出进度 0~1"""
    fin = ease_out(seg(t, 0.0, 0.42))
    fout = 1 - ease_in_out(seg(t, dur - 0.34, dur))
    return fin * fout


def apply_fade(img, a):
    if a >= 0.999:
        return img
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.blend(black, img, a)


# ---------------------------------------------------------------- 通用 UI
def draw_kicker(draw, sc, t, tint):
    """左上角小标签"""
    draw = sd(draw)                      # 坐标按设备像素缩放
    a = ease_out(seg(t, 0.18, 0.65))
    if a <= 0:
        return
    x, y = 132, 214
    f = font(30, "bold")
    text = sc.get("kicker", "")
    w_dev = tw(draw, text, f, 4.0)       # 逻辑宽度
    draw.rounded_rectangle([x - 22, y - 14, x + w_dev + 22, y + 44], radius=10,
                           fill=(*C_CARD,), outline=(*[int(v * 0.5) for v in tint],))
    draw_tracked(draw, (x, y), text, f, tint, 4.0)


# 版式参数统一由 PROFILES / LAYOUT 提供（见上方版式档案）


def title_bottom(sc):
    """正文起点 y（当前坐标系）"""
    return LAYOUT["y_body"]


def draw_kicker(draw, sc, t, tint):
    """左上角小标签（位置与字号跟随版式档案；竖屏与标题左对齐）"""
    draw = sd(draw)
    a = ease_out(seg(t, 0.18, 0.65))
    if a <= 0:
        return
    m = LAYOUT["margin"]
    x, y = m, LAYOUT["kicker_y"]
    fs = LAYOUT["title_size"] * 0.36
    f = font(fs, "bold")
    text = sc.get("kicker", "")
    w = tw(draw, text, f, 4.0)
    pad_x, pad_y = fs * 0.73, fs * 0.47
    draw.rounded_rectangle([x - pad_x, y - pad_y, x + w + pad_x, y + fs * 1.47],
                           radius=fs * 0.33,
                           fill=(*C_CARD,), outline=(*[int(v * 0.5) for v in tint],))
    draw_tracked(draw, (x, y), text, f, tint, 4.0)


def _fit_title_size(lines, weight="bold"):
    """
    标题自动缩号：保证最宽的一行不超出可用宽度。

    必须用**与实际渲染完全相同的测量路径**（ScaledDraw + font() + tw()）。
    早期版本用未缩放的 ImageFont 直接 textlength，测出的宽度与渲染时
    （ScaledDraw 会把结果还原为参照单位）不是同一个量，导致
    "算出来能放下、实际却被裁掉"。这里让两者共用同一路径。
    """
    limit = AV() - LAYOUT["margin"] * 2
    probe = sd(ImageDraw.Draw(Image.new("RGB", (16, 16))))
    longest = max(lines, key=len) if lines else ""
    fs = LAYOUT["title_size"]
    while fs > 40:
        f = font(fs, weight)
        if tw(probe, longest, f, 4.5) <= limit:
            break
        fs *= 0.94
    return fs


def draw_title(draw, sc, t, tint):
    """
    主标题 + 强调下划线 + 副标题。
    运动设计：标题用「重」曲线（起步快、收尾长），下划线扫过带过冲，
    副标题最后淡入 —— 三者错帧，避免整块同时被推上来。
    """
    draw = sd(draw)
    m = LAYOUT["margin"]
    center = LAYOUT["align"] == "center"
    lines = sc["title"].split("\n")
    fs = _fit_title_size(lines)
    lh = LAYOUT["title_lh"] * (fs / LAYOUT["title_size"])
    y0 = LAYOUT["title_y"]
    f1 = font(fs, "bold")
    avail = (VERT_REF_USABLE_W if LAYOUT["is_vertical"] else REF_W)

    for i, ln in enumerate(lines):
        a = stagger(t, i, delay=0.14, start=0.28, dur=0.68, curve="heavy")
        if a <= 0:
            continue
        dy = (1 - a) * fs * 0.42
        w = tw(draw, ln, f1, 4.5)
        x = (avail - w) / 2 if center else m
        draw_tracked(draw, (x, y0 + i * lh + dy), ln, f1, mix(C_BG_TOP, C_TEXT, a), 4.5)

    y_last = y0 + (len(lines) - 1) * lh

    # 强调下划线：wipe 带过冲，扫过时末端更有力
    a2 = wipe(t, 0, delay=0, start=0.72, dur=0.52)
    if a2 > 0:
        w_last = tw(draw, lines[-1], f1, 4.5)
        x0 = (avail - w_last) / 2 if center else m
        y = y_last + fs * 1.26
        draw.rounded_rectangle([x0, y, x0 + w_last * clamp(a2), y + max(4, fs * 0.08)],
                               radius=fs * 0.05, fill=tint)

    # 副标题：最后入场，用平滑曲线（不再抢戏）
    sub = sc.get("subtitle", "")
    a3 = stagger(t, 0, delay=0, start=0.96, dur=0.56, curve="soft")
    if sub and a3 > 0:
        f2 = font(fs * 0.43, "regular")
        w = tw(draw, sub, f2, 3.0)
        x = (avail - w) / 2 if center else m
        draw_tracked(draw, (x, y_last + fs * 1.66), sub, f2,
                     mix(C_BG_TOP, C_MUTED, clamp(a3)), 3.0)


def draw_caption(img, draw, caps, t_abs):
    """
    底部字幕条 —— 每行独立淡入淡出。
    注意：caps 里的 start/end 是**全片绝对时间**（narrate.py 已把场景偏移加进去），
    因此这里必须传绝对时间 t_abs，而不是场景内相对时间。
    位置与字号来自版式档案，不写死画布尺寸（否则竖屏会画到画布外）。
    """
    draw = sd(draw)
    if _TRACE_CAPTION:
        print(f"[TRACE] draw_caption caps={len(caps)} t_abs={t_abs:.3f} "
              f"first={caps[0] if caps else None}")
    fs = LAYOUT["cap_size"]
    f = font(fs, "bold")
    for c in caps:
        st = c["start"]
        en = c["end"]
        # 与音频对齐：略有提前量，结束略延后
        tt = t_abs
        if tt < st - 0.09 or tt > en + 0.30:
            continue
        a = ease_out(seg(tt, st - 0.09, st + 0.13)) * (1 - ease_in_out(seg(tt, en + 0.08, en + 0.30)))
        if a <= 0.01:
            continue
        text = c["text"]
        w = tw(draw, text, f, 2.0)              # 参照单位宽度
        bw_l = w + LAYOUT["cap_pad"]
        bh_l = float(LAYOUT["cap_h"])
        bx_l = (REF_W - bw_l) / 2
        by_l = float(LAYOUT["caption_y"])
        dy = (1 - a) * 16
        pad = 4.0
        # 字幕底衬：只建字幕条大小的图层（比整幅画布快得多）
        lw = max(2, int(round(SX(bw_l + pad * 2))))
        lh = max(2, int(round(SY(bh_l + pad * 2 + 8))))
        layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        pad_dx, pad_dy = SX(pad), SY(pad)
        ld.rounded_rectangle([pad_dx, pad_dy + SY(dy), SX(pad + bw_l), SY(pad + bh_l + dy)],
                             radius=int(round(SX(20))),
                             fill=(6, 10, 22, int(200 * a)))
        ld.rounded_rectangle([pad_dx, pad_dy + SY(dy), SX(pad + bw_l), SY(pad + bh_l + dy)],
                             radius=int(round(SX(20))),
                             outline=(*C_ACCENT, int(80 * a)),
                             width=max(1, int(round(SX(2)))))
        px, py = int(round(SX(bx_l - pad))), int(round(SY(by_l - pad)))
        crop_box = (px, py, px + lw, py + lh)
        img.paste(Image.alpha_composite(img.crop(crop_box).convert("RGBA"),
                                        layer).convert("RGB"), (px, py))
        # 文本中心与底衬中心对齐
        draw_tracked(draw, (bx_l + LAYOUT["cap_pad"] / 2, by_l + fs * 0.34 + dy),
                     text, f, mix(C_BG_TOP, C_TEXT, a), 2.0)


def progress_bar(draw, t, total, sc_start, sc_dur):
    """顶部全局进度条（宽度用参照坐标系，由 ScaledDraw 映射到目标画布）"""
    draw = sd(draw)
    p = clamp((sc_start + t) / total)
    draw.rectangle([0, 0, REF_W * p, 4], fill=(*C_ACCENT,))
    draw.rectangle([0, 5, REF_W, 6], fill=(30, 38, 62))


def brand(draw, series):
    draw = sd(draw)
    f = font(fs_brand := LAYOUT["title_size"] * 0.31, "regular")
    w = tw(draw, series, f, 2.5)
    m = LAYOUT["margin"]
    draw_tracked(draw, (REF_W - m - w, LAYOUT["brand_y"]), series, f, (78, 92, 124), 2.5)


# ---------------------------------------------------------------- 图形工具
def _paste_rgba(img, layer):
    """把 RGBA 图层合成回 RGB 图像（原地）"""
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))


def rounded_glow_card(img, box, radius=18, fill=C_CARD, border=None, glow=None, glow_r=22):
    """
    圆角卡片（参照坐标输入，内部换算到设备像素）。
    包围盒必须按轴向分别换算 —— 用 S() 会把 x 也按纵向比例放大，
    竖屏下宽度会变成 3.2 倍（这正是竖屏第一版图形溢出的根因）。
    注意：调用后 img 像素已更新，之前的 ImageDraw 句柄失效，需重新获取。
    """
    box = list(scaled_box(box))
    radius = int(round(S(radius)))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(box, radius=radius, fill=(*fill, 255))
    if border:
        ld.rounded_rectangle(box, radius=radius, outline=(*border, 255),
                             width=max(1, int(round(S(2)))))
    _paste_rgba(img, layer)
    if glow:
        gr = S(glow_r)
        grx = SX(glow_r)
        make_glow(img, (box[0] - grx, box[1] - gr, box[2] + grx, box[3] + gr),
                  glow, gr)


def lerp_color(c1, c2, t):
    return mix(c1, c2, t)


# ================================================================ 场景 1
def render_hook(img, t, dur, sc, ctx):
    """
    开场（封面式构图）。

    竖屏单独一套参数：横屏的核心在 y=372、标题在 y=590（居中对齐参照宽度），
    直接放进 1080 宽的竖屏画布会被两侧裁掉，而且纵向留白过多。
    """
    draw = sd(ImageDraw.Draw(img))
    vert = LAYOUT["is_vertical"]
    avail = AV()
    cx = avail * 0.5
    cy = 700 if vert else 372
    ring_base = 210 if vert else 100
    ring_span = 460 if vert else 360
    part_r0 = 420 if vert else 260
    part_span = 900 if vert else 560
    title_y = 1180 if vert else 590
    rule_y = 1420 if vert else 816
    # 副标题与字幕条之间必须留出空隙：字幕条在 y=902（字号 46，高约 70），
    # 原来放在 856 会紧贴甚至压住字幕条，两行字几乎叠在一起（观感很差）。
    sub_y = 1420 if vert else 806
    title_size = 106 if vert else 114
    # 运动设计：光核用弹性展开（"点亮"的手感），标题比光核晚一拍、用重曲线收尾
    a_core = stagger(t, 0, delay=0, start=0.12, dur=1.35, curve="elastic")

    # 扩散圆环（收在标题之上）
    for i in range(3):
        ph = (t * 0.42 + i / 3) % 1.0
        r = ring_base + ph * ring_span
        al = int(70 * (1 - ph) * clamp(a_core))
        if al > 3:
            draw.ellipse([cx - r, cy - r * 0.66, cx + r, cy + r * 0.66],
                         outline=(*C_ACCENT, al), width=2)

    # 粒子向核心汇聚
    rng = random.Random(99)
    for i in range(230):
        ang = rng.random() * math.tau
        rad0 = part_r0 + rng.random() * part_span
        speed = 0.28 + rng.random() * 0.45
        prog = ((t * speed + rng.random()) % 1.0) ** 1.6
        r = rad0 * (1 - prog)
        x = cx + math.cos(ang) * r
        y = cy + math.sin(ang) * r * 0.58
        s = 1 + 2 * (1 - prog)
        al = int(200 * prog * a_core + 40)
        col = mix(C_ACCENT2, C_ACCENT, prog)
        if 0 < x < avail and 0 < y < REF_H:
            draw.ellipse([x - s, y - s, x + s, y + s], fill=(*col, al))

    # 核心球（辉光被限制在标题上方）
    if a_core > 0:
        pulse = 1 + 0.10 * math.sin(t * 3.4)
        r = (48 + 26 * ease_out_back(a_core)) * pulse * (1.35 if vert else 1.0)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*mix(C_ACCENT2, C_ACCENT, 0.5),))
        gr = 150 if vert else 116
        make_glow(img, (cx - gr, cy - gr, cx + gr, cy + gr + 2), C_ACCENT, 30, 0.95)

    # 主标题（比重曲线更"重"：起步快、长收尾，落位后不再晃动）
    # ★ 文案必须取自场景数据，不能写死 —— 写死会让复用它的一期画出别人的标题。
    f = font(title_size, "bold")
    a = stagger(t, 0, delay=0, start=0.86, dur=0.95, curve="heavy")
    if a > 0:
        text = sc.get("cover_title") or sc["title"].replace("\n", " ")
        d = (1 - a) * 34
        w = tw(draw, text, f, 8.0)
        draw_tracked(draw, ((avail - w) / 2, title_y + d), text, f,
                     mix(C_BG_TOP, C_TEXT, clamp(a)), 8.0)

    # 强调底线：wipe 扫过带过冲
    a3 = wipe(t, 0, delay=0, start=1.42, dur=0.62)
    if a3 > 0:
        bw = (560 if vert else 420) * clamp(a3)
        draw.rounded_rectangle([(avail - bw) / 2, rule_y, (avail + bw) / 2, rule_y + 6],
                               radius=3, fill=C_ACCENT)

    # 副标题：最后入场，平滑曲线
    a2 = stagger(t, 0, delay=0, start=1.70, dur=0.72, curve="soft")
    if a2 > 0:
        f2 = font(46 if vert else 44, "regular")
        text = sc.get("cover_sub") or sc.get("subtitle", "")
        w = tw(draw, text, f2, 4.0)
        draw_tracked(draw, ((avail - w) / 2, sub_y), text, f2,
                     mix(C_BG_TOP, mix(C_ACCENT, C_MUTED, 0.30), clamp(a2)), 4.0)
    return img


# ================================================================ 场景 2
def DEMO_SENTENCE(sc):
    """predict 场景演示用的半句话，可由期数脚本用 demo_sentence 覆盖"""
    return sc.get("demo_sentence") or "今天天气不错，我们去公园…"


def render_predict(img, t, dur, sc, ctx):
    """核心机制：预测下一个词 → 概率条"""
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT)
    draw_title(draw, sc, t, C_ACCENT)

    # 半句话卡片（竖屏下收窄右移、整体下移，避开左上角的标题块与副标题）
    if LAYOUT["is_vertical"]:
        box = [280, 760, AV() - 30, 860]
        txt_fs = 48
    else:
        box = [1000, 548, 1840, 648]
        txt_fs = 46
    a = ease_out(seg(t, 1.30, 2.00))
    if a > 0:
        text = DEMO_SENTENCE(sc)
        bw_card = box[2] - box[0]
        dy = (1 - a) * 24
        rounded_glow_card(img, [box[0], box[1] + dy, box[2], box[3] + dy], 16,
                          fill=(26, 34, 60), border=mix(C_BG_TOP, C_ACCENT, a),
                          glow=C_ACCENT, glow_r=16)
        d2 = sd(ImageDraw.Draw(img))
        f = font(txt_fs, "bold")
        wtxt = tw(d2, text, f, 2.0)
        tx = box[0] + (bw_card - wtxt) / 2
        draw_tracked(d2, (tx, box[1] + 34 + dy), text, f, mix(C_BG_TOP, C_TEXT, a), 2.0)
        if (t * 2) % 1 < 0.6 and a > 0.9:
            d2.rectangle([tx + wtxt + 8, box[1] + 32 + dy, tx + wtxt + 12,
                          box[1] + 96 + dy], fill=C_ACCENT)

    # 概率条（竖屏下左移并收窄）
    items = sc.get("candidates") or [
            ("散步", 0.62, C_ACCENT), ("跑步", 0.17, (90, 160, 220)),
            ("回家", 0.11, (120, 120, 190)), ("吃饭", 0.06, C_WARM),
            ("唱歌", 0.04, (110, 90, 140))]
    vert = LAYOUT["is_vertical"]
    if vert:
        # 竖屏：卡片下移到 y≈760 之后，条形与标签也整体下移，避免压住副标题。
        # （原先条形在 Y(692)，与副标题设备区间 y 1034~1122 重叠）
        bx, by, bw = 600, 880, 420
        name_dx, pct_dx = -140, 16
        label_y, arrow_y = 848, 800
    else:
        bx, by, bw = 1160, 692, 620
        name_dx, pct_dx = -168, 22
        label_y, arrow_y = 656, 598
    peak = 0.62
    for i, (name, p, col) in enumerate(items):
        # 运动设计：依次"长大"。条形宽度用单调曲线（避免回弹把宽度拉过 100%），
        # 首名的强调交给辉光强度，用弹性曲线做出"弹一下"。
        p_mono = stagger(t, i, delay=0.13, start=1.15, dur=0.72, curve="heavy")
        p_glow = stagger(t, i, delay=0.13, start=1.15, dur=0.9, curve="elastic")
        if p_mono <= 0:
            continue
        y = by + i * 52
        wfull = bw * p / peak
        w = wfull * clamp(p_mono)
        draw.rounded_rectangle([bx, y, bx + bw, y + 40], radius=10, fill=(22, 28, 50))
        col2 = col if i == 0 else mix((60, 70, 100), col, 0.75)
        draw.rounded_rectangle([bx, y, bx + max(8, w), y + 40], radius=10, fill=col2)
        if i == 0 and p_glow > 0.35:
            make_glow(img, (bx - 18, y - 18, bx + w + 18, y + 58), C_ACCENT, 22,
                      min(1.0, p_glow) * 0.75)
        d2 = sd(ImageDraw.Draw(img))
        # 文字/百分比错开一点入场，形成层次
        ta = stagger(t, i, delay=0.13, start=1.30, dur=0.5, curve="light")
        d2.text((bx + name_dx, y - 6), name, font=font(38, "bold"),
                fill=mix(C_BG_TOP, C_TEXT, clamp(ta)))
        # 百分比做数字滚动，比直接淡入更有信息感
        shown = count_up(t, 1.35 + i * 0.13, 2.0 + i * 0.13, p * 100, "light")
        d2.text((bx + wfull + pct_dx, y + 2), f"{shown:.0f}%", font=font(32, "bold"),
                fill=mix(C_BG_TOP, C_TEXT if i == 0 else C_MUTED, clamp(ta)))

    # 说明标签
    a2 = stagger(t, 0, delay=0, start=3.0, dur=0.6, curve="heavy")
    if a2 > 0:
        draw_tracked(draw, (bx + name_dx, label_y), "下一个词的候选概率", font(30, "regular"),
                     mix(C_BG_TOP, C_MUTED, clamp(a2)), 2.0)

    # 「→ 下一个词」提示箭头
    a3 = stagger(t, 0, delay=0, start=1.85, dur=0.5, curve="pop")
    if a3 > 0:
        y = arrow_y
        ax = bx - 150 if vert else 960
        draw.line([ax - 30 * (1 - a3), y, ax + 26, y],
                  fill=tuple(int(v * a3) for v in C_ACCENT), width=4)
        draw.polygon([(ax + 26, y - 12), (ax + 48, y), (ax + 26, y + 12)],
                     fill=tuple(int(v * a3) for v in C_ACCENT))
    return img


# ================================================================ 场景 3
def render_token(img, t, dur, sc, ctx):
    """Token 化：句子切成块 → 翻转为数字 ID"""
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT2)
    draw_title(draw, sc, t, C_ACCENT2)

    tokens = sc.get("tokens") or [
        ("我爱", "1024", (56, 224, 255)), ("北京", "3307", (139, 108, 255)),
        ("天安门", "8821", (94, 200, 160)), ("，", "9", (150, 160, 190)),
        ("真", "2740", (255, 138, 92)), ("壮观", "5512", (232, 176, 96))]
    f = font(64, "bold")
    f_id = font(52, "bold")
    vert = LAYOUT["is_vertical"]
    by, bh, gap = 560, 134, 24
    widths = [tw(draw, name, f, 2.0) + 78 for name, _, _ in tokens]
    margin = LAYOUT["margin"] + 8
    limit = AV() - margin * 2

    # 竖屏：6 张卡横排会超出 1080 宽，改为两行（3+3）纵向堆叠。
    # 这是「竖屏必须重新构图」的典型例子 —— 单靠缩放只会把卡片压到看不清。
    if vert:
        per_row, row_gap = 3, 46
        row_widths = [sum(widths[r:r + per_row]) + gap * (len(widths[r:r + per_row]) - 1)
                      for r in range(0, len(tokens), per_row)]
        scale = min(1.0, limit / max(row_widths))
    else:
        per_row, row_gap = len(tokens), 0
        scale = min(1.0, limit / (sum(widths) + gap * (len(tokens) - 1)))

    layout = []
    # 切词卡片错帧弹入；字的翻面用平滑曲线，与入场区分开
    tok_curves = ("pop", "heavy", "pop", "light", "pop", "heavy")
    for i, ((name, tid, col), wfull) in enumerate(zip(tokens, widths)):
        a = stagger(t, i, delay=0.13, start=1.22, dur=0.62, curve=tok_curves[i])
        w = wfull * scale
        flip = stagger(t, i, delay=0.11, start=2.95, dur=0.68, curve="soft")
        # 行列定位：竖屏分两行，每行各自居中
        r, k = i // per_row, i % per_row
        row_items = widths[r * per_row:r * per_row + per_row]
        row_w = (sum(row_items) + gap * (len(row_items) - 1)) * scale
        x = margin + (limit - row_w) / 2 + sum(
            widths[r * per_row + j] * scale + gap * scale for j in range(k))
        y_row = by + r * (bh + row_gap)
        if a > 0:
            h = bh * clamp(a)
            yy = y_row + (bh - h) / 2
            sq = abs(math.cos(flip * math.pi))
            cw = w * (0.14 + 0.86 * sq)
            cxx = x + (w - cw) / 2
            fillc = mix((26, 34, 60), col, 0.22 if flip < 0.5 else 0.34)
            rounded_glow_card(img, [int(cxx), int(yy), int(cxx + cw), int(yy + h)], 20,
                              fill=fillc,
                              border=mix((40, 52, 86), col, 0.85 if flip > 0.5 else 0.45),
                              glow=col if flip > 0.5 else None, glow_r=20)
            layout.append((name, tid, col, cxx, yy, cw, h, flip))

    # 文字 / ID 统一在卡片之后绘制，避免被卡片覆盖
    d2 = sd(ImageDraw.Draw(img))
    for name, tid, col, cxx, yy, cw, h, flip in layout:
        if flip < 0.5:
            d2.text((cxx + cw / 2 - tw(d2, name, f, 2.0) / 2, yy + 28), name, font=f,
                    fill=mix(C_BG_TOP, C_TEXT, clamp(1 - flip * 2)))
        else:
            d2.text((cxx + cw / 2 - tw(d2, tid, f_id, 2.0) / 2, yy + 36), tid, font=f_id,
                    fill=mix(C_BG_TOP, col, clamp(flip * 1.4 - 0.2)))

    # 底部说明：文字 → 数字（以可用宽度居中）
    # 竖屏两行卡片占位更高，整组说明下移，避免压住第二行
    vert = LAYOUT["is_vertical"]
    L_Y, A_Y, AH_Y, P_Y = (1082, 1104, 1093, 1115) if vert else (728, 750, 739, 761)
    avail = AV()
    a = ease_out(seg(t, 4.35, 5.0))
    if a > 0:
        f2 = font(38, "regular")
        cxx = avail / 2
        gap_legend = 300 if vert else 360
        draw_tracked(draw, (cxx - gap_legend - tw(draw, "文字", f2, 2) / 2, L_Y),
                     "文字", f2, mix(C_BG_TOP, C_ACCENT, a), 2)
        draw_tracked(draw, (cxx + gap_legend - tw(draw, "词表 ID（数字）", f2, 2) / 2, L_Y),
                     "词表 ID（数字）", f2, mix(C_BG_TOP, C_ACCENT2, a), 2)
        ax0, ax1 = cxx - 150, cxx + 150
        off = (1 - a) * 40
        draw.line([ax0 - off, A_Y, ax1 + off, A_Y],
                  fill=tuple(int(v * a) for v in C_MUTED), width=3)
        draw.polygon([(ax1 + off, AH_Y), (ax1 + off + 20, A_Y), (ax1 + off, P_Y)],
                     fill=tuple(int(v * a) for v in C_MUTED))

    # 底部小注释（以可用宽度居中）
    a3 = ease_out(seg(t, 5.4, 6.1))
    if a3 > 0:
        f3 = font(30, "light")
        note = sc.get("token_note") or "模型只能算数字，所以先把文字变成数字"
        draw_tracked(draw, ((avail - tw(draw, note, f3, 2)) / 2,
                            (1156 if vert else 802)), note, f3,
                     mix(C_BG_TOP, (108, 122, 156), a3), 2)
    return img


# ================================================================ 场景 4
def render_attention(img, t, dur, sc, ctx):
    """注意力机制：关注度连线 + 该接「喝」不是「吃」"""
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT)
    draw_title(draw, sc, t, C_ACCENT)

    words = sc.get("words") or ["我", "口", "渴", "了", "想", "喝", "水"]
    f = font(62, "bold")
    weights = sc.get("weights") or [0.08, 0.06, 0.34, 0.05, 0.10, 0.22, 1.00]
    vert = LAYOUT["is_vertical"]
    # 竖屏：词卡整体收窄居中，弧线与控制点都要收，否则会压到下方决策面板
    bw = 108 if vert else 118
    gap = 10 if vert else 22
    row_y = Y(440) if vert else 616
    pct_y = Y(392) if vert else 556
    arc_extra = 70 if vert else 92
    arc_span = 120 if vert else 158
    total = len(words) * bw + (len(words) - 1) * gap
    x0 = (AV() - total) / 2 if vert else 130

    # 第一遍：卡片与文字（错帧弹入，不同"重量"的曲线交替）
    pos = []
    curves = ("pop", "heavy", "pop", "heavy", "pop", "heavy")
    for i, wd in enumerate(words):
        a = stagger(t, i, delay=0.085, start=1.20, dur=0.62,
                    curve=curves[i % len(curves)])
        x = x0 + i * (bw + gap)
        yy = row_y + (1 - clamp(a)) * 46
        pos.append((x, yy))
        col = mix((30, 40, 70), (44, 60, 104), weights[i])
        if a > 0:
            rounded_glow_card(img, [int(x), int(yy), int(x + bw), int(yy + bw)], 18, fill=col,
                              border=mix((46, 60, 96), (120, 200, 235),
                                         clamp(weights[i] * a)))
    d2 = sd(ImageDraw.Draw(img))
    for i, wd in enumerate(words):
        x, yy = pos[i]
        d2.text((x + bw / 2 - tw(d2, wd, f, 2) / 2, yy + 22), wd, font=f, fill=C_TEXT)

    # 关注度百分比（显示在词上方）
    a3 = ease_out(seg(t, 3.15, 3.75))
    if a3 > 0:
        f3 = font(34, "bold")
        for i in range(len(words)):
            aw = weights[i]
            if aw < 0.09:
                continue
            ex = pos[i][0]
            t3 = f"{int(aw*100)}%"
            col = mix(C_ACCENT2, C_ACCENT, aw) if aw > 0.25 else C_MUTED
            draw.text((ex + bw / 2 - draw.textlength(t3, font=f3) / 2, pct_y), t3, font=f3,
                      fill=mix(C_BG_TOP, col, a3))

    # 从「水」向左发出的注意力弧线
    qi = len(words) - 1
    qx, qy = pos[qi][0] + bw / 2, pos[qi][1] + bw
    a_all = ease_out(seg(t, 2.2, 3.0))
    if a_all > 0:
        for i in range(qi):
            aw = weights[i]
            aa = ease_out(seg(t, 2.2 + i * 0.09, 2.85 + i * 0.09))
            if aa <= 0:
                continue
            ex, ey = pos[i][0] + bw / 2, pos[i][1] + bw
            thick = max(2, int(1 + 10 * aw))
            col = mix(C_ACCENT2, C_ACCENT, aw) if aw > 0.25 else C_WARM
            alpha = int(255 * aa * (0.40 + 0.60 * aw))
            # 二次贝塞尔：控制点越低、弧越平缓，避免压到上方文字
            midx = (qx + ex) / 2
            midy = max(qy, ey) + arc_extra + arc_span * (1 - aw)
            pts = []
            for k in range(33):
                u = k / 32
                px = (1 - u) ** 2 * qx + 2 * (1 - u) * u * midx + u ** 2 * ex
                py = (1 - u) ** 2 * qy + 2 * (1 - u) * u * midy + u ** 2 * ey
                pts.append((px, py))
            draw.line(pts, fill=(*col, alpha), width=thick, joint="curve")

    # 右栏：结合上下文后的决策
    render_attention_decision(img, draw, t, dur, sc, ctx)
    return img


def render_attention_decision(img, draw, t, dur, sc, ctx):
    """
    决策面板：结合上下文后「喝」压倒「吃」。
    横屏放在右栏；竖屏没有横向空间，改为在词卡下方纵向堆叠。
    """
    a4 = ease_out(seg(t, 4.15, 4.85))
    if a4 <= 0:
        return
    vert = LAYOUT["is_vertical"]
    if vert:
        sx, sw = LAYOUT["margin"], AV() - LAYOUT["margin"] * 2
        head_y, bar0_y, bar_h, bar_gap = Y(690), Y(742), 68, 92
    else:
        sx, sw = 1252, 520
        head_y, bar0_y, bar_h, bar_gap = 552, 612, 76, 106

    draw_tracked(draw, (sx, head_y), sc.get("decision_head") or "结合上下文后，下一个词：", font(30, "regular"),
                 mix(C_BG_TOP, C_MUTED, a4), 2)
    items = sc.get("decision") or [("喝", 0.71, C_ACCENT), ("吃", 0.12, C_WARM)]
    for i, (wd, p, col) in enumerate(items):
        y = bar0_y + i * bar_gap
        wid = sw * p / 0.71 * ease_out(a4)
        draw.rounded_rectangle([sx, y, sx + sw, y + bar_h], radius=16, fill=(22, 28, 50))
        draw.rounded_rectangle([sx, y, sx + max(10, wid), y + bar_h], radius=16,
                               fill=col if i == 0 else mix((74, 74, 94), col, 0.6))
        if i == 0 and a4 > 0.4:
            make_glow(img, (sx - 18, y - 18, sx + wid + 18, y + bar_h + 16),
                      C_ACCENT, 24, 0.75)
    d2 = sd(ImageDraw.Draw(img))
    f4 = font(44, "bold")
    for i, (wd, p, col) in enumerate(items):
        y = bar0_y + i * bar_gap
        d2.text((sx + 32, y + bar_h * 0.16), wd, font=f4, fill=C_TEXT)
        d2.text((sx + sw + 24, y + bar_h * 0.21), f"{int(p*100)}%", font=f4,
                fill=C_TEXT if i == 0 else C_MUTED)
    note_y = bar0_y + 2 * bar_gap + 26
    draw_tracked(d2, (sx, note_y), sc.get("decision_note") or "上下文决定了「喝」的权重", font(30, "light"),
                 mix(C_BG_TOP, C_ACCENT, a4 * 0.9), 2)


# ================================================================ 场景 5
def render_train(img, t, dur, sc, ctx):
    """训练流程：数据流 → 迭代 → 损失下降"""
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT2)
    draw_title(draw, sc, t, C_ACCENT2)

    # ---- 右上：训练损失曲线（紧凑面板，右移避开下方流程节点）
    a_c = ease_out(seg(t, 2.6, 4.2))
    if a_c > 0:
        gx0, gy0, gw, gh = 1698, 580, 108, 142
        draw.rounded_rectangle([1668, 502, 1818, 774], radius=18,
                               fill=(18, 23, 42), outline=(40, 52, 86), width=2)
        draw_tracked(draw, (1682, 520), "Loss", font(26, "regular"), C_MUTED, 1.5)
        draw.line([gx0, gy0 + gh, gx0 + gw, gy0 + gh], fill=(48, 60, 92), width=2)
        draw.line([gx0, gy0, gx0, gy0 + gh], fill=(48, 60, 92), width=2)
        pts = []
        n = 120
        for k in range(n):
            u = k / (n - 1)
            if u > a_c:
                break
            # loss 从高到低衰减；y 轴向下增大，所以用 (1 - loss) 映射
            loss = 0.10 + 0.86 * math.exp(-3.4 * u) + 0.02 * math.sin(u * 22)
            pts.append((gx0 + gw * u, gy0 + gh * (1.0 - loss)))
        if len(pts) > 1:
            draw.line(pts, fill=C_WARM, width=4, joint="curve")
            draw.ellipse([pts[-1][0] - 5, pts[-1][1] - 5, pts[-1][0] + 5, pts[-1][1] + 5],
                         fill=C_WARM)
            make_glow(img, (pts[-1][0] - 26, pts[-1][1] - 26, pts[-1][0] + 26, pts[-1][1] + 26),
                      C_WARM, 18, 0.7)
            if a_c > 0.98:
                draw.text((1682, 740), "已收敛 ↓", font=font(24, "regular"),
                          fill=(200, 160, 100))

    # ---- 左上：三个训练阶段标签（竖屏需整体收窄才放得下，否则会互相叠压）
    labels = sc.get("stage_labels") or ["1 预训练", "2 指令微调", "3 人类反馈对齐"]
    cols = [C_ACCENT, (139, 108, 255), (232, 176, 96)]
    a_s = ease_out(seg(t, 5.5, 6.3))
    if a_s > 0:
        chip_fs = 26 if LAYOUT["is_vertical"] else 32
        chip_pad = 52 if LAYOUT["is_vertical"] else 72
        chip_gap = 20 if LAYOUT["is_vertical"] else 30
        f7 = font(chip_fs, "bold")
        wids = [tw(draw, s, f7, 4) + chip_pad for s in labels]
        row_w = sum(wids) + chip_gap * (len(labels) - 1)
        cx = (AV() - row_w) / 2 if LAYOUT["is_vertical"] else 132
        for s, c, wid in zip(labels, cols, wids):
            draw.rounded_rectangle([cx, 556, cx + wid, 620], radius=32,
                                   fill=mix(C_BG_TOP, c, 0.16 * a_s),
                                   outline=(*[int(v * a_s) for v in c],), width=2)
            draw_tracked(draw, (cx + chip_pad * 0.38, 570 + (32 - chip_fs) * 0.6),
                         s, f7, mix(C_BG_TOP, c, a_s), 4)
            cx += wid + chip_gap

    # ---- 下方：流程节点（卡片先画，文字后画）
    # 竖屏：4 个节点横排需要 1460px（按横屏 1920 设计），在 1080 里放不下 ——
    # 改成 2×2 网格纵向堆叠。
    steps = sc.get("flow_steps") or [
            ("海量文本", C_ACCENT), ("预测下一个词", (90, 190, 240)),
            ("比对答案", C_ACCENT2), ("调整参数", (232, 176, 96))]
    vert = LAYOUT["is_vertical"]
    if vert:
        ny, nw, nh, ngap = 760, 440, 118, 48
        cols_per_row = 2
        row_gapy = 168
        total = cols_per_row * nw + (cols_per_row - 1) * ngap
        x0 = (AV() - total) / 2
    else:
        ny, nw, nh, ngap = 656, 320, 132, 60
        cols_per_row = len(steps)
        row_gapy = 0
        total = len(steps) * nw + (len(steps) - 1) * ngap
        x0 = 130 + (AV() - 260 - total) / 2
    boxes = []
    # 四个节点各代表不同"重量"的语义，用不同曲线错帧入场
    node_curves = ("heavy", "heavy", "pop", "pop")
    for i, (name, col) in enumerate(steps):
        a = stagger(t, i, delay=0.17, start=1.18, dur=0.66, curve=node_curves[i])
        r, k = i // cols_per_row, i % cols_per_row
        x = x0 + k * (nw + ngap)
        yy = ny + r * row_gapy + (1 - clamp(a)) * 40
        boxes.append([x, yy, x + nw, yy + nh, col, a])
        if a > 0:
            rounded_glow_card(img, [int(x), int(yy), int(x + nw), int(yy + nh)], 20,
                              fill=mix((20, 26, 48), (30, 38, 66), clamp(a)),
                              border=tuple(int(v) for v in mix((44, 56, 90), col, clamp(a) * 0.8)))
    d2 = sd(ImageDraw.Draw(img))
    f = font(38, "bold")
    for (name, col), b in zip(steps, boxes):
        a = clamp(b[5])
        if a <= 0:
            continue
        w = tw(d2, name, f, 2)
        d2.text((b[0] + nw / 2 - w / 2, b[1] + 46), name, font=f, fill=mix(C_BG_TOP, C_TEXT, a))

    # ---- 连接箭头
    for i in range(len(steps) - 1):
        a = ease_in_out(seg(t, 1.95 + i * 0.20, 2.55 + i * 0.20))
        if a <= 0:
            continue
        ax0, ax1 = boxes[i][2], boxes[i + 1][0]
        y = boxes[i][1] + nh / 2
        hx = ax0 + 6 + (ax1 - ax0 - 12) * a
        draw.line([ax0 + 6, y, hx, y], fill=tuple(int(v * a) for v in (110, 126, 165)), width=3)
        if a > 0.92:
            draw.polygon([(hx, y - 9), (hx + 16, y), (hx, y + 9)], fill=(140, 158, 200))

    # ---- 数据粒子沿流程流动
    a_p = ease_out(seg(t, 2.3, 2.9))
    if a_p > 0 and boxes[-1][5] > 0.9:
        for k in range(28):
            ph = (t * 0.30 + k / 28) % 1.0
            if ph > 0.95 or ph < 0.03:
                continue
            xx = lerp(boxes[0][0] + 30, boxes[-1][2] - 30, ph)
            y = ny + 22 + 12 * math.sin(ph * math.tau * 2 + k)
            draw.ellipse([xx, y, xx + 7, y + 7], fill=(*C_ACCENT, int(230 * a_p)))

    # ---- 循环箭头：最后 → 最前（在流程节点与字幕条之间的窄带内）
    a_loop = ease_in_out(seg(t, 4.0, 5.0))
    if a_loop > 0 and boxes[-1][5] > 0.9:
        yl = ny + nh + 18
        xs, xe = boxes[-1][0] + nw / 2, boxes[0][0] + nw / 2
        hx = xs - (xs - xe) * a_loop
        draw.line([(xs, ny + nh), (xs, yl), (hx, yl)],
                  fill=tuple(int(v * a_loop) for v in (232, 176, 96)), width=3, joint="curve")
        if a_loop > 0.97:
            draw.polygon([(xe + 13, yl), (xe - 2, yl - 10), (xe - 2, yl + 10)], fill=(232, 176, 96))
            draw.line([(xe, yl), (xe, ny + nh)], fill=(232, 176, 96), width=3)
            draw.text((xe + 26, yl - 30), "迭代亿万次", font=font(26, "regular"),
                      fill=(200, 160, 100))
    return img


# ================================================================ 场景 6
def render_outro(img, t, dur, sc, ctx):
    """总结：概率分布汇聚成「智能」"""
    draw = sd(ImageDraw.Draw(img))
    # 用参照坐标系的中心，不用设备 W/H —— 后者会把 1080 宽的设备尺寸当设计宽度
    avail = AV()
    cx, cy = avail / 2, (1080 * 0.44 if not LAYOUT["is_vertical"] else 900)

    # 环形概率分布：按角度分批生长，三种曲线轮换，避免"整圈同时弹开"的机械感
    n = 46
    ring_curves = ("light", "heavy", "pop")
    for i in range(n):
        ang = i / n * math.tau + t * 0.10
        base = 0.25 + 0.75 * abs(math.sin(i * 0.7 + 1.2))
        # 按象限分批（而非逐根），形成"一圈圈扩散"的节奏
        batch = int(i / n * 3)
        ai = stagger(t, batch, delay=0.16, start=0.30, dur=0.78,
                     curve=ring_curves[batch % len(ring_curves)])
        if i % 4 == 0:
            ai = stagger(t, batch, delay=0.16, start=0.30, dur=0.9, curve="soft")
        if ai <= 0:
            continue
        r0, r1 = 196, 196 + base * 86 * clamp(ai)
        x0, y0 = cx + math.cos(ang) * r0, cy + math.sin(ang) * r0 * 0.64
        x1, y1 = cx + math.cos(ang) * r1, cy + math.sin(ang) * r1 * 0.64
        col = mix(C_ACCENT2, C_ACCENT, base)
        draw.line([x0, y0, x1, y1], fill=(*col, int(210 * clamp(ai))), width=7)

    # 中心光核（弹性展开 + 持续呼吸）
    a2 = stagger(t, 0, delay=0, start=1.0, dur=0.9, curve="elastic")
    if a2 > 0:
        r = (70 + 40 * clamp(a2)) * pulse(t, period=2.6, amp=0.07)
        draw.ellipse([cx - r, cy - r * 0.66, cx + r, cy + r * 0.66], fill=(*C_ACCENT,))
        make_glow(img, (cx - r * 4, cy - r * 3, cx + r * 4, cy + r * 3), C_ACCENT, 52, 1.0)

    # 主结论文字（逐行浮现，重曲线收尾更长）
    # 竖屏字号收敛：120 号在 1080 宽里放不下，会自动被裁掉
    f = font(96 if LAYOUT["is_vertical"] else 120, "bold")
    lines = sc["title"].split("\n")
    concl_y = (760 if LAYOUT["is_vertical"] else 470)
    concl_lh = (150 if LAYOUT["is_vertical"] else 152)
    for i, ln in enumerate(lines):
        a3 = stagger(t, i, delay=0.30, start=1.62, dur=0.82, curve="heavy")
        if a3 <= 0:
            continue
        w = tw(draw, ln, f, 8.0)
        d = (1 - a3) * 34
        draw_tracked(draw, ((avail - w) / 2, concl_y + i * concl_lh + d), ln, f,
                     mix(C_BG_TOP, C_TEXT, a3), 8.0)

    # 副标语
    a4 = stagger(t, 0, delay=0, start=3.0, dur=0.75, curve="soft")
    sub_y = (1120 if LAYOUT["is_vertical"] else 806)
    rule_y2 = (sub_y + 68)
    if a4 > 0:
        f2 = font(52, "bold")
        text = sc["subtitle"]
        w = tw(draw, text, f2, 5.0)
        draw_tracked(draw, ((avail - w) / 2, sub_y), text, f2,
                     mix(C_BG_TOP, mix(C_ACCENT, (255, 255, 255), 0.30), clamp(a4)), 5.0)
        # 强调底线
        bw = w * clamp(wipe(t, 0, delay=0, start=3.42, dur=0.6))
        draw.rounded_rectangle([(avail - bw) / 2, rule_y2, (avail + bw) / 2, rule_y2 + 6],
                               radius=3, fill=C_ACCENT)
    return img


# ================================================================ 场景 7（第2期）
def render_conf(img, t, dur, sc, ctx):
    """
    「它觉得自己在说真话」——正确回答与胡编回答的内部置信度分布几乎一样。

    视觉：左右两组候选词概率条。左侧是正确答案（答对），右侧是编造内容（答错），
    但两组内部的"最高置信度"都很高 —— 直观说明它没有"真/假"这个内部信号。
    """
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT)
    draw_title(draw, sc, t, C_ACCENT)

    avail = AV()
    vert = LAYOUT["is_vertical"]
    # 两组并排（竖屏上下堆叠）
    groups = [
        ("它给出的答案", "光速约 30 万公里/秒", 0.94, C_ACCENT, True),
        ("同一次对话里它编的", "该书出版于 1987 年", 0.91, C_WARM, False),
    ] if not sc.get("conf_groups") else [
        (*g[:1], g[1], g[2], C_ACCENT if g[3] else C_WARM, g[3])
        for g in sc["conf_groups"]]
    f_name = font(34 if not vert else 30, "bold")
    f_tok = font(30 if not vert else 26, "bold")
    f_pct = font(30 if not vert else 26, "bold")

    if vert:
        gw, gh, gx0 = avail - LAYOUT["margin"] * 2, 300, LAYOUT["margin"]
        gys = [Y(560), Y(880)]
    else:
        gw, gh = 760, 330
        gx0 = 130
        gys = [Y(560), Y(560)]
        gx0s = [130, 1030]

    for gi, (gname, gtext, gconf, gcol, is_right) in enumerate(groups):
        gx = gx0 if vert else gx0s[gi]
        gy = gys[gi] if vert else gys[0]
        a = stagger(t, gi, delay=0.35, start=1.05, dur=0.7, curve="heavy")
        if a <= 0:
            continue
        # 组标题
        draw_tracked(draw, (gx, gy - 52), gname, f_name,
                     mix(C_BG_TOP, C_MUTED, clamp(a)), 2.0)
        # 内容卡
        rounded_glow_card(img, [gx, gy, gx + gw, gy + 96], 14,
                          fill=mix(C_BG_TOP, (28, 36, 62), clamp(a)),
                          border=mix(C_BG_TOP, gcol, clamp(a) * 0.7))
        d2 = sd(ImageDraw.Draw(img))
        tw_ = tw(d2, gtext, f_tok, 2.0)
        tag = "符合事实" if is_right else "纯属编造"
        tag_col = C_ACCENT if is_right else C_WARM
        draw_tracked(d2, (gx + 32, gy + 30), gtext, f_tok,
                     mix(C_BG_TOP, C_TEXT, clamp(a)), 2.0)
        draw_tracked(d2, (gx + 32, gy + 96 + 22), tag, f_name,
                     mix(C_BG_TOP, tag_col, clamp(a)), 2.0)
        # 置信度条（两者都很高 —— 这就是重点）
        by = gy + 190
        bw = gw - 40
        pa = stagger(t, gi, delay=0.35, start=1.6, dur=0.75,
                     curve="pop" if gi == 0 else "elastic")
        draw.rounded_rectangle([gx + 20, by, gx + 20 + bw, by + 44], radius=10,
                               fill=(22, 28, 50))
        draw.rounded_rectangle([gx + 20, by, gx + 20 + max(8, bw * gconf * clamp(pa)),
                                by + 44], radius=10,
                               fill=gcol if gi == 0 else mix((90, 80, 84), C_WARM, 0.8))
        if pa > 0.4:
            make_glow(img, (gx + 8, by - 16, gx + 20 + bw * gconf + 16, by + 60),
                      gcol, 20, min(1.0, pa) * 0.7)
        d3 = sd(ImageDraw.Draw(img))
        shown = count_up(t, 1.7 + gi * 0.1, 2.45 + gi * 0.1, gconf * 100, "light")
        draw_tracked(d3, (gx + 28, by + 8), f"置信度 {shown:.0f}%", f_pct,
                     mix(C_BG_TOP, C_TEXT, clamp(pa)), 1.0)

    # 底部结论：两者置信度几乎相同
    a5 = stagger(t, 0, delay=0, start=3.1, dur=0.7, curve="soft")
    if a5 > 0:
        msg = sc.get("conf_note") or "两组置信度几乎一样：它分不出「答对」和「编造」"
        f5 = font(32 if not vert else 28, "bold")
        w = tw(draw, msg, f5, 2.0)
        draw_tracked(draw, ((avail - w) / 2, Y(858) if not vert else Y(1200)), msg, f5,
                     mix(C_BG_TOP, C_ACCENT, clamp(a5)), 2.0)
    return img


# ================================================================ 场景 8（第2期）
def render_fluent(img, t, dur, sc, ctx):
    """
    「因为语言太顺了」——完整句子逐字长出来，强调流利 ≠ 正确。

    视觉：一段语法通顺但事实错误的句子，逐词出现、语气自然；
    随后在句子下方逐条打叉，指出"语法对/语气对/逻辑通/事实错"。
    """
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT2)
    draw_title(draw, sc, t, C_ACCENT2)

    avail = AV()
    vert = LAYOUT["is_vertical"]
    sentence = sc.get("sentence") or "根据 1987 年那篇经典论文，这个方法在低温下效率最高。"
    # 按"词块"切分，模拟逐词生成
    chunks = sc.get("sentence_chunks") or ["根据 ", "1987 年", "那篇经典论文，", "这个方法", "在低温下", "效率最高。"]
    f = font(46 if not vert else 38, "bold")

    # 句子区（逐块出现）
    base_y = Y(600)
    max_w = avail - LAYOUT["margin"] * 2

    # 逐字绘制时，相邻字符会损失字体自身的字间距，实测整句墨迹
    # （中文 38 号）比 sum(textlength) 宽约 1.8 倍。
    # 因此「分块定位」不能按 textlength 累加 —— 那会让块之间真实重叠
    # （实测邻块墨迹交叠 20%+）。改用一个经验间距系数来还原真实步进。
    CHAR_K = 1.80

    def adv_len(txt):
        """逐字绘制下该文本的实际横向占位（参照单位）"""
        return sum(draw.textlength(c, font=f) + 1.0 for c in txt) * CHAR_K

    total_w = adv_len(sentence)
    if total_w > max_w:                     # 太长就缩号
        f = font(max(18, int(f.size * max_w / total_w)), "bold")
        total_w = adv_len(sentence)
    x0 = (avail - total_w) / 2

    cursor = 0.0
    for i, ch in enumerate(chunks):
        a = stagger(t, i, delay=0.30, start=1.0, dur=0.42, curve="light")
        if a > 0:
            draw_tracked(draw, (x0 + cursor, base_y + (1 - a) * 18), ch, f,
                         mix(C_BG_TOP, C_TEXT, clamp(a)), 1.0)
        cursor += adv_len(ch)
    # 闪烁光标，强化"正在生成"
    if t > 0.9 and (t * 2) % 1 < 0.6:
        prog = clamp(seg(t, 1.0, 1.0 + len(chunks) * 0.30))
        cur_x = x0 + total_w * min(1.0, prog)
        draw.rounded_rectangle([cur_x + 4, base_y + 2, cur_x + 8, base_y + 58],
                               radius=2, fill=C_ACCENT)

    # 判定项：语法/语气/逻辑都通过，事实不通过
    items = sc.get("checks") or [("语法正确", True), ("语气自然", True),
                                  ("逻辑通顺", True), ("事实正确", False)]
    fy = Y(760) if not vert else Y(980)
    gapx = (avail - 120) / len(items) if not vert else (avail - 120) / len(items)
    f2 = font(32 if not vert else 26, "bold")
    for i, (label, ok) in enumerate(items):
        a = stagger(t, i, delay=0.16, start=2.5, dur=0.5,
                    curve="pop" if ok else "elastic")
        if a <= 0:
            continue
        col = C_ACCENT if ok else C_WARM
        cx_ = 60 + i * gapx
        line = label
        w = tw(draw, line, f2, 2.0)
        draw_tracked(draw, (cx_ + (gapx - w) / 2, fy), line, f2,
                     mix(C_BG_TOP, col, clamp(a)), 2.0)
        if not ok:
            # 错误的项加重描边，视觉上更醒目
            draw.rounded_rectangle([cx_ + 6, fy - 14, cx_ + gapx - 6, fy + 52],
                                   radius=12, outline=(*C_WARM, int(150 * clamp(a))), width=3)
    return img


# ================================================================ 场景 9（第2期）
def render_gap(img, t, dur, sc, ctx):
    """
    「它只被训练过一件事」——流利度曲线与事实正确率曲线分叉。

    视觉：两条曲线。流利度随训练一路攀升（模型优化的是它），
    事实正确率在某个点后与它分离（没人直接优化它）。
    """
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT)
    draw_title(draw, sc, t, C_ACCENT)

    avail = AV()
    vert = LAYOUT["is_vertical"]
    if vert:
        gx, gy, gw, gh = LAYOUT["margin"], Y(620), avail - LAYOUT["margin"] * 2, 420
    else:
        gx, gy, gw, gh = 1180, Y(560), 620, 380

    # 注意：横屏已不画这类面板（改由 AI 背景表达），因此这里也不再压暗底 ——
    # 否则会留下一个空的暗矩形（踩过这个坑）
    # 坐标轴
    a_box = stagger(t, 0, delay=0, start=0.9, dur=0.5, curve="soft")
    if a_box > 0:
        draw.rounded_rectangle([gx - 24, gy - 60, gx + gw + 24, gy + gh + 30],
                               radius=18, fill=(18, 23, 42), outline=(40, 52, 86), width=2)
        draw.line([gx, gy + gh, gx + gw, gy + gh], fill=(48, 60, 92), width=2)
        draw.line([gx, gy, gx, gy + gh], fill=(48, 60, 92), width=2)

    # 横屏：AI 背景本身已经画出"两条曲线分叉"，比我这张示意图更好看也更贴题。
    # 再叠一张同构的图表就成了重复表达（实测两套曲线叠在一起，观感很乱）。
    # 所以横屏不再画图表面板，只保留文字说明 + 图例；
    # 竖屏背景被裁切、走"上方文字＋下方图表"的纵向构图，仍需要这张图。
    draw_panel = vert or _bg_image is None
    if not draw_panel:
        a_lg0 = stagger(t, 0, delay=0, start=1.6, dur=0.6, curve="soft")
        if a_lg0 > 0:
            f2 = font(30, "bold")
            for i, (label, col) in enumerate((("语言流利度（模型优化的目标）", C_ACCENT),
                                              ("事实正确率（没人直接优化）", C_WARM))):
                yy = Y(700) + i * 46
                draw.rounded_rectangle([130, yy + 8, 158, yy + 18], radius=5, fill=col)
                draw_tracked(draw, (174, yy - 8), label, f2,
                             mix(C_BG_TOP, col, clamp(a_lg0)), 1.5)
        return img

    n = 100
    prog = clamp(stagger(t, 0, delay=0, start=1.2, dur=1.9, curve="heavy"))
    # 流利度：随训练快速上升并保持高位
    pts_flu = []
    pts_fact = []
    for k in range(n):
        u = k / (n - 1)
        if u > prog:
            break
        flu = 0.20 + 0.76 * (1 - math.exp(-3.6 * u))
        fact = 0.20 + 0.52 * (1 - math.exp(-3.6 * u)) - 0.30 * max(0.0, u - 0.45) ** 0.8
        pts_flu.append((gx + gw * u, gy + gh * (1 - flu)))
        pts_fact.append((gx + gw * u, gy + gh * (1 - fact)))
    if len(pts_flu) > 1:
        draw.line(pts_flu, fill=C_ACCENT, width=5, joint="curve")
        draw.line(pts_fact, fill=C_WARM, width=5, joint="curve")
        # 分叉点标注
        if prog > 0.5:
            ux = 0.45
            px = gx + gw * ux
            draw.line([(px, gy), (px, gy + gh)], fill=(90, 100, 130), width=2)
            a_note = stagger(t, 0, delay=0, start=2.2, dur=0.6, curve="pop")
            if a_note > 0:
                note = sc.get("gap_note") or "从这里开始分叉"
                f3 = font(26, "regular")
                w = tw(draw, note, f3, 1.5)
                draw_tracked(draw, (px - w / 2, gy - 46), note, f3,
                             mix(C_BG_TOP, C_MUTED, clamp(a_note)), 1.5)

    # 图例
    a_lg = stagger(t, 0, delay=0, start=2.5, dur=0.6, curve="soft")
    if a_lg > 0:
        f2 = font(30 if not vert else 26, "bold")
        lx = gx - 20 if not vert else gx
        # 横屏字幕带在 y 902~990，图例不能放那里（会与字幕叠成两行）
        ly = Y(700) if not vert else gy + gh + 46
        for i, (label, col) in enumerate((("语言流利度（模型优化的目标）", C_ACCENT),
                                          ("事实正确率（没人直接优化）", C_WARM))):
            yy = ly + i * 46
            draw.rounded_rectangle([lx, yy + 10, lx + 28, yy + 20], radius=5, fill=col)
            draw_tracked(draw, (lx + 44, yy - 6), label, f2,
                         mix(C_BG_TOP, col, clamp(a_lg)), 1.5)

    # 左侧说明（横屏）
    if not vert:
        a_txt = stagger(t, 0, delay=0, start=1.4, dur=0.7, curve="heavy")
        if a_txt > 0:
            lines = ["它被训练的是：", "预测下一个词", "", "它没被训练的是：", "这句话是不是真的"]
            f4 = font(34, "bold")
            for i, ln in enumerate(lines):
                yy = Y(600) + i * 52
                col = C_TEXT if "训练的是：" not in ln and ln else C_MUTED
                if "它被训练的是" in ln:
                    col = C_ACCENT
                if "它没被训练的是" in ln:
                    col = C_WARM
                if ln:
                    draw_tracked(draw, (130, yy), ln, f4, mix(C_BG_TOP, col, clamp(a_txt)), 2.0)
    return img


# ================================================================ 场景 10（第2期）
def render_reason(img, t, dur, sc, ctx):
    """
    「它没有『不知道』这个选项」——每个位置都必须给出一个词。
    即便没有依据，它也不会停，而是挑"最合理"的那个把话接下去。
    """
    draw = sd(ImageDraw.Draw(img))
    draw_kicker(draw, sc, t, C_ACCENT2)
    draw_title(draw, sc, t, C_ACCENT2)

    avail = AV()
    vert = LAYOUT["is_vertical"]
    # 四个位置：每个位置展示"必须选一个"的候选，最高者被选中
    # 「没有拒绝回答这一项」的信息直接写进步骤标题，不再单独加一行注释 ——
    # 竖屏下注释会压到下一列的选项上（实测交叠 1731px²），且与标题语义重复。
    steps = [
        ("它知道吗", ["知道", "不确定", "不知道"], 0.0),
        ("但必须选一个", ["大概率", "可能", "也许"], 1.0),
        ("继续接下去", ["在 1987 年", "有研究表明", "业内普遍认为"], 2.0),
        ("结果", ["一句通顺的编造"], 3.0),
    ]
    f_step = font(28 if not vert else 24, "bold")
    f_opt = font(30 if not vert else 25, "bold")

    if vert:
        cw, cx0 = avail - LAYOUT["margin"] * 2, LAYOUT["margin"]
        cys = [Y(560) + i * 210 for i in range(len(steps))]
    else:
        cw = 400
        cx0 = 130
        cys = [Y(600) for _ in steps]

    for i, (step_name, opts, _) in enumerate(steps):
        cx = cx0 if vert else 130 + i * 440
        cy = cys[i] if vert else cys[i]
        a = stagger(t, i, delay=0.42, start=1.1, dur=0.66,
                    curve=("heavy", "pop", "heavy", "elastic")[i])
        if a <= 0:
            continue
        # 步骤标题
        draw_tracked(draw, (cx, cy - 46), step_name, f_step,
                     mix(C_BG_TOP, C_MUTED, clamp(a)), 2.0)
        # 候选列表
        for k, opt in enumerate(opts):
            oy = cy + k * 54
            oa = stagger(t, i, delay=0.42, start=1.3 + k * 0.14, dur=0.5, curve="light")
            if oa <= 0:
                continue
            chosen = (k == 0)
            col = C_ACCENT if chosen else (70, 84, 118)
            badge = ("最高概率" if chosen else "")
            wbox = cw if vert else cw - 20
            draw.rounded_rectangle([cx, oy, cx + wbox, oy + 44], radius=10,
                                   fill=mix(C_BG_TOP, (30, 38, 62), clamp(oa)) if not chosen
                                   else mix(C_BG_TOP, (24, 52, 68), clamp(oa)),
                                   outline=(*col, int(180 * clamp(oa))) if chosen else None,
                                   width=2 if chosen else 0)
            d2 = sd(ImageDraw.Draw(img))
            draw_tracked(d2, (cx + 16, oy + 6), opt, f_opt,
                         mix(C_BG_TOP, C_TEXT if chosen else C_MUTED, clamp(oa)), 1.5)
            if chosen and oa > 0.6:
                make_glow(img, (cx - 10, oy - 10, cx + wbox + 10, oy + 54), C_ACCENT, 18, 0.6)

    # 收束：于是它用最合理的词把话接下去
    a_end = stagger(t, 0, delay=0, start=3.6, dur=0.8, curve="soft")
    if a_end > 0:
        msg = sc.get("reason_note") or "没有依据时，它不会停 —— 它会挑最顺的那个词，把话接下去"
        f6 = font(34 if not vert else 28, "bold")
        w = tw(draw, msg, f6, 2.0)
        draw_tracked(draw, ((avail - w) / 2, Y(780) if not vert else Y(1290)), msg, f6,
                     mix(C_BG_TOP, C_ACCENT, clamp(a_end)), 2.0)
    return img


RENDERERS = {
    "hook": render_hook,
    "predict": render_predict,
    "token": render_token,
    "attention": render_attention,
    "train": render_train,
    "outro": render_outro,
    # 第 2 期新增
    "conf": render_conf,
    "fluent": render_fluent,
    "gap": render_gap,
    "reason": render_reason,
}
