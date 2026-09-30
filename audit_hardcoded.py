# -*- coding: utf-8 -*-
"""
写死文案审计（精确版）。

关键区分：
  · 危险：直接写死并被绘制 —— 复用该视觉类型的另一期会画出别人的文字
      例：text = "什么是大语言模型"      （第 2 期开场就画出了第 1 期的标题）
  · 安全：作为 sc.get() 的回退默认值 —— 当期脚本没给字段时才用
      例：text = sc.get("cover_title") or "什么是大语言模型"

所以必须看上下文，不能只数中文字符串。
"""
import ast
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "render.py"
src = SRC.read_text(encoding="utf-8")
tree = ast.parse(src)
CJK = re.compile(r"[\u4e00-\u9fff]")

RENDER_FUNCS = {n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("render_")}

# 视觉语言里的固定标签：跨期不变，属"图表标签"而非"内容"，
# 各组件的表意保持不变时无需每期重写。改语义时记得同步改这里。
UI_LABEL_WHITELIST = {
    "文字", "词表 ID（数字）", "置信度 ", "符合事实", "纯属编造",
    "语言流利度（模型优化的目标）", "事实正确率（没人直接优化）",
    "最高概率", "已收敛 ↓", "迭代亿万次", "下一个词的候选概率",
    "它被训练的是：", "它没被训练的是：", "预测下一个词", "这句话是不是真的",
    "它被训练的是", "它没被训练的是", "训练的是：",
    "它知道吗", "但必须选一个", "继续接下去", "结果",
    "知道", "不确定", "不知道", "大概率", "可能", "也许",
    "在 1987 年", "有研究表明", "业内普遍认为", "一句通顺的编造",
}


def is_docstring(node, fn):
    """函数体（含嵌套函数）首条表达式语句里的字符串 = docstring"""
    for sub in ast.walk(fn):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = sub.body
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
    return False


def has_sc_get(node):
    """该表达式里是否含 sc.get(...) 或 sc[...] —— 视为数据驱动"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr == "get":
                return True
        if isinstance(sub, ast.Subscript):
            return True
    return False


def sc_get_lines(fn):
    """收集函数内所有被 sc.get(...) 保护的字符串常量的行号"""
    guarded = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.BoolOp) or isinstance(sub, ast.IfExp):
            if has_sc_get(sub):
                for c in ast.walk(sub):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        guarded.add(id(c))
    return guarded


danger, safe = [], []
for fn in ast.walk(tree):
    if not isinstance(fn, ast.FunctionDef) or fn.name not in RENDER_FUNCS:
        continue
    guarded = sc_get_lines(fn)
    for sub in ast.walk(fn):
        if not (isinstance(sub, ast.Constant) and isinstance(sub.value, str)):
            continue
        if not CJK.search(sub.value):
            continue
        if is_docstring(sub, fn):          # 文档字符串不是画面内容
            continue
        if sub.value in UI_LABEL_WHITELIST:  # 图表固定标签
            continue
        (safe if id(sub) in guarded else danger).append((fn.name, sub.lineno, sub.value))

print("=" * 78)
print("危险：直接写死并被绘制的文案")
print("=" * 78)
if danger:
    by = {}
    for f, ln, t in danger:
        by.setdefault(f, []).append((ln, t))
    for f in sorted(by):
        print(f"\n{f}  ({len(by[f])} 处)")
        for ln, t in by[f]:
            tt = t if len(t) <= 44 else t[:41] + "…"
            print(f"    L{ln}: 「{tt}」")
else:
    print("  无 ✓")

print()
print("=" * 78)
print(f"安全：作为 sc.get() 回退默认值（{len(safe)} 处）—— 当期脚本未提供该字段时生效")
print("=" * 78)
fs = sorted({f for f, _, _ in safe})
print("  " + ", ".join(fs) if fs else "  无")

print()
print(f"结论：危险 {len(danger)} 处 / 安全 {len(safe)} 处")
sys.exit(1 if danger else 0)
