# -*- coding: utf-8 -*-
"""把 19 个镜头拼成一张联络表（contact sheet），便于一次性检查构图与节奏"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "verify" / "cut2"
files = sorted([p for p in SRC.glob("[0-9][0-9]_*.png")], key=lambda p: p.name)
print(f"找到 {len(files)} 张镜头静帧")

cols, rows = 4, 5
tw, th = 460, 259
pad = 8
label_h = 22
sheet = Image.new("RGB", (cols * (tw + pad) + pad,
                          rows * (th + pad + label_h) + pad), (12, 14, 20))
d = ImageDraw.Draw(sheet)
f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 15)

for i, p in enumerate(files):
    img = Image.open(p).convert("RGB").resize((tw, th), Image.LANCZOS)
    cx = pad + (i % cols) * (tw + pad)
    cy = pad + (i // cols) * (th + pad + label_h)
    sheet.paste(img, (cx, cy))
    d.text((cx + 2, cy + th + 3), p.stem, font=f, fill=(180, 195, 220))

out = SRC / "_contact_sheet.png"
sheet.save(out)
print(f"已拼合 -> {out}  ({sheet.width}x{sheet.height})")
