/* 确定性画面：整片就是一个 seek(t)。分层绘制 → 2x 场景画布 → Post 后期。
   本层负责"美术"：景深、字形扫光、编辑排版、逐字动效。 */
(() => {
  const scene = document.getElementById('scene');
  const ctx = scene.getContext('2d', { alpha: false });
  let W = 1920, H = 1080, S = 1, F = null;
  let bg = null, bgx = null, glyph = null, glyphx = null;

  const clamp = (v, a = 0, b = 1) => v < a ? a : v > b ? b : v;
  const lerp = (a, b, t) => a + (b - a) * t;
  const easeOutExpo = t => t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
  const easeOutBack = (t, s = 1.9) => { const c = s + 1; return 1 + c * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2); };
  const easeInOutQuint = t => t < .5 ? 16 * t ** 5 : 1 - Math.pow(-2 * t + 2, 5) / 2;
  const easeOutCubic = t => 1 - Math.pow(1 - t, 3);
  const easeInOut = t => t < .5 ? 4 * t ** 3 : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const seg = (t, a, b) => clamp((t - a) / Math.max(1e-6, b - a));
  const mulberry32 = a => () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const MK = (w, h) => { const c = document.createElement('canvas'); c.width = Math.max(1, w | 0); c.height = Math.max(1, h | 0); return c; };

  // 版面盒登记：模板把自己的占位报上来，供 check_layout.py 做"元素碰撞"机检
  // （这次"注脚压住坐标轴"的事故就是靠眼睛发现的，不该再靠眼睛）
  let LAYOUT = [];
  const box = (tag, x, y, w, h) => LAYOUT.push({ tag, x: Math.round(x / S), y: Math.round(y / S), w: Math.round(w / S), h: Math.round(h / S) });

  const font = (w, s) => w + ' ' + s + 'px ' + F.style.font;
  function tw(text, size, weight, spacing = 0) {
    ctx.font = font(weight, size); let w = 0;
    for (const ch of text) w += ctx.measureText(ch).width + spacing;
    return w - spacing;
  }
  function tracked(text, x, y, size, weight, color, spacing, alpha = 1, align = 'left') {
    const w = tw(text, size, weight, spacing);
    let cx = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
    ctx.save(); ctx.globalAlpha *= alpha; ctx.font = font(weight, size);
    ctx.fillStyle = color; ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left';
    for (const ch of text) { ctx.fillText(ch, cx, y); cx += ctx.measureText(ch).width + spacing; }
    ctx.restore(); return w;
  }

  // ---------------- 景深背景（远景模糊 / 中景微虚） ----------------
  function background(t, look) {
    const S0 = F.style;
    if (!bg) { bg = MK(W * 0.5, H * 0.5); bgx = bg.getContext('2d'); }
    const bw = bg.width, bh = bg.height, k = bw / W;
    const bx = bgx;
    bx.setTransform(1, 0, 0, 1, 0, 0); bx.globalAlpha = 1; bx.filter = 'none';
    const g = bx.createLinearGradient(0, 0, bw * 0.3, bh);
    g.addColorStop(0, S0.bg0); g.addColorStop(0.55, look.bg1); g.addColorStop(1, look.bg2);
    bx.fillStyle = g; bx.fillRect(0, 0, bw, bh);
    // 远景网格
    bx.save();
    bx.translate(bw / 2, bh / 2); bx.rotate(look.tilt || 0); bx.translate(-bw / 2, -bh / 2);
    bx.strokeStyle = 'rgba(120,170,235,0.075)'; bx.lineWidth = 1;
    const step = (look.grid || 150) * k, off = (t * 4 * k) % step;
    bx.beginPath();
    for (let x = -off - bw; x < bw * 2; x += step) { bx.moveTo(x, -bh); bx.lineTo(x, bh * 2); }
    for (let y = -off * 0.6 - bh; y < bh * 2; y += step) { bx.moveTo(-bw, y); bx.lineTo(bw * 2, y); }
    bx.stroke(); bx.restore();
    // 柔光斑
    [[0.26, 0.28, 640, 'rgba(94,200,255,0.26)'], [0.78, 0.74, 560, 'rgba(255,179,71,' + (look.blobWarm ?? 0.13).toFixed(2) + ')']]
      .forEach(([rx, ry, rad, col], i) => {
        const cx = rx * bw + Math.sin(t * 0.33 + i * 2) * 30 * k;
        const cy = ry * bh + Math.cos(t * 0.27 + i * 1.4) * 22 * k;
        const rg = bx.createRadialGradient(cx, cy, 0, cx, cy, rad * k);
        rg.addColorStop(0, col); rg.addColorStop(1, 'rgba(0,0,0,0)');
        bx.fillStyle = rg; bx.fillRect(0, 0, bw, bh);
      });
    // 星点
    const rng = mulberry32(91); bx.save();
    for (let i = 0; i < 90; i++) {
      const x = rng() * bw, y = rng() * bh, r = (0.5 + rng() * 1.1) * k;
      const a = 0.14 + 0.22 * (0.5 + 0.5 * Math.sin(t * (0.9 + rng() * 1.6) + i));
      bx.fillStyle = 'rgba(210,232,255,' + a.toFixed(3) + ')';
      bx.beginPath(); bx.arc(x, y, r, 0, 6.2832); bx.fill();
    }
    bx.restore();
    // 远景：放大 + 模糊 = 真实景深
    ctx.save();
    ctx.filter = 'blur(' + (5 * S) + 'px)';
    ctx.globalAlpha = 0.98;
    ctx.drawImage(bg, 0, 0, W, H);
    ctx.restore();

    // 中景粒子（轻微虚化，速度更快）
    const rng2 = mulberry32(23);
    ctx.save(); ctx.filter = 'blur(' + (1.6 * S) + 'px)';
    for (let i = 0; i < 30; i++) {
      const bx0 = rng2(), by0 = rng2(), sz = (2 + rng2() * 3.4) * S;
      const x = ((bx0 * W + t * (52 + rng2() * 40) * S) % (W + 90)) - 45;
      const y = by0 * H + Math.sin(t * 0.6 + i) * 14 * S;
      ctx.fillStyle = 'rgba(94,200,255,0.15)';
      ctx.fillRect(x, y, sz, sz);
    }
    ctx.restore();
  }

  function lightSweep(t) {
    const k = seg(t, 0.05, F.duration * 0.95);
    const x = lerp(-0.3, 1.3, easeInOutQuint(k)) * W;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.translate(x, H / 2); ctx.rotate(-0.20); ctx.translate(-x, -H / 2);
    const g = ctx.createLinearGradient(x - 300 * S, 0, x + 300 * S, 0);
    g.addColorStop(0, 'rgba(255,255,255,0)');
    g.addColorStop(0.5, 'rgba(198,228,255,0.075)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.fillRect(x - 340 * S, -H, 680 * S, H * 3);
    ctx.restore();
  }

  function rule(x, y, w, color, alpha = 1, h = 2) {
    ctx.save(); ctx.globalAlpha *= alpha; ctx.fillStyle = color;
    ctx.fillRect(x, y, w, h * S); ctx.restore();
  }
  // 字形扫光：只作用在字身上（source-atop）
  function glyphSheen(drawFn, size, t, dur) {
    if (!glyph) { glyph = MK(W, H); glyphx = glyph.getContext('2d'); }
    glyphx.setTransform(1, 0, 0, 1, 0, 0); glyphx.globalCompositeOperation = 'source-over';
    glyphx.clearRect(0, 0, W, H);
    drawFn(glyphx);
    const p = seg(t, dur * 0.08, dur * 0.95);
    const gx = lerp(-0.25, 1.25, easeInOut(p)) * W;
    glyphx.globalCompositeOperation = 'source-atop';
    const g = glyphx.createLinearGradient(gx - 420 * S, 0, gx + 420 * S, 0);
    g.addColorStop(0, 'rgba(255,255,255,0)');
    g.addColorStop(0.46, 'rgba(210,238,255,0.0)');
    g.addColorStop(0.5, 'rgba(255,255,255,0.55)');
    g.addColorStop(0.54, 'rgba(210,238,255,0.0)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    glyphx.fillStyle = g; glyphx.fillRect(0, 0, W, H);
    glyphx.globalCompositeOperation = 'source-over';
    return glyph;
  }

  // ---------------- 镜头模板 ----------------
  const templates = {
    title(p, t, d) {
      const cx = W / 2, size = 176 * S, sp = 14 * S;
      const base = H / 2 + 6 * S;
      const drawChars = (x2, y2, alpha) => {
        const chars = [...p.text];
        let x = cx - tw(p.text, size, '900', sp) / 2;
        chars.forEach((ch, i) => {
          const st = 0.015 + i * 0.058;
          const k = easeOutBack(seg(t, st, st + 0.54));
          const a = alpha * clamp(seg(t, st - 0.02, st + 0.20));
          const cw = tw(ch, size, '900');
          x2.save();
          x2.globalAlpha = a;
          x2.translate(x + cw / 2, (y2 ?? base) - 160 * S * (1 - k));
          x2.rotate((1 - k) * (i % 2 ? 0.05 : -0.05));
          x2.font = font('900', size);
          x2.textAlign = 'center'; x2.textBaseline = 'middle';
          x2.fillStyle = '#ffffff';
          x2.fillText(ch, 0, 0);
          x2.restore();
          x += cw + sp;
        });
      };
      // 深度残影（虚化 + 位移，制造纵深）
      ctx.save();
      ctx.filter = 'blur(' + 18 * S + 'px)';
      ctx.globalAlpha = 0.20;
      ctx.translate(0, 26 * S);
      drawChars(ctx, null, 0.5);
      ctx.restore();
      // 主体 + 扫光
      const gcanvas = glyphSheen(g => drawChars(g, null, 1), size, t, d);
      ctx.save();
      ctx.globalAlpha = 1;
      ctx.shadowColor = 'rgba(94,200,255,0.22)'; ctx.shadowBlur = 18 * S;
      ctx.drawImage(gcanvas, 0, 0);
      ctx.restore();

      // 副题：小号、宽字距、独立呼吸空间
      const subY = base + 150 * S;
      const sa = easeOutCubic(seg(t, 0.44, 1.05));
      const subW = tw(p.sub, 26 * S, '600', 11 * S);
      box('title.hero', cx - tw(p.text, size, '900', sp) / 2, base - 168 * S, tw(p.text, size, '900', sp), 236 * S);
      box('title.sub', cx - subW / 2, subY - 34 * S, subW, 56 * S);
      box('title.index', 150 * S, 118 * S, 150 * S, 62 * S);
      tracked(p.sub, cx - subW / 2, subY + 14 * S * (1 - sa), 26 * S, '600', F.style.muted, 11 * S, sa * 0.95);
      rule(cx - subW / 2 - 74 * S, subY - 14 * S, 58 * S * easeOutExpo(seg(t, 0.5, 1.0)), 'rgba(140,175,215,0.6)', 1, 1.4);
      rule(cx + subW / 2 + 16 * S, subY - 14 * S, 58 * S * easeOutExpo(seg(t, 0.5, 1.0)), 'rgba(140,175,215,0.6)', 1, 1.4);

      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.15, 0.55)) * 0.9, '700');
      rule(150 * S, 168 * S, 92 * S * clamp(seg(t, 0.3, 0.9)), F.style.accent, 0.55, 2);

      // token 行：12 个小方块依次落位（对应"词元"），填住下半幅
      const n = 12, sq = 22 * S, sg = 14 * S;
      const rowW = n * sq + (n - 1) * sg, rx = cx - rowW / 2, ry = base + 224 * S;   // 抬高：整块必须离开字幕带
      for (let i = 0; i < n; i++) {
        const st = 0.62 + i * 0.028;
        const k = easeOutBack(seg(t, st, st + 0.42));
        const a = clamp(seg(t, st - 0.05, st + 0.16));
        ctx.save();
        ctx.globalAlpha = a * (0.30 + 0.55 * (i === 4 ? 1 : 0.45));
        ctx.translate(rx + i * (sq + sg) + sq / 2, ry - 42 * S * (1 - k));
        ctx.scale(k, k);
        ctx.fillStyle = i === 4 ? F.style.accent : 'rgba(150,190,235,0.85)';
        ctx.beginPath(); ctx.roundRect(-sq / 2, -sq / 2, sq, sq, 5 * S); ctx.fill();
        ctx.restore();
      }
      box('title.tokens', rx, ry - 18 * S, rowW, 96 * S);
      const ta = clamp(seg(t, 0.78, 1.15)) * 0.55;
      tracked('词元序列', cx - tw('词元序列', 20 * S, '600', 6 * S) / 2, ry + 62 * S, 20 * S, '600', F.style.muted, 6 * S, ta);
    },

    bars(p, t, d) {
      const x0 = 330 * S, x1 = 1560 * S, trackW = x1 - x0;
      const top = 352 * S, rh = 78 * S, gap = 92 * S;
      const ha = easeOutCubic(seg(t, 0.0, 0.20));
      box('bars.title', 150 * S, 186 * S, 520 * S, 100 * S);
      box('bars.block', x0 - 120 * S, top - 52 * S, trackW + 320 * S, (p.bars.length - 1) * gap + rh + 96 * S);
      box('bars.footnote', x0, top + p.bars.length * gap + 52 * S, 760 * S, 44 * S);
      label(p.title, 150 * S - 26 * S * (1 - ha), 232 * S, 56, F.style.fg, 6 * S, 0.35 + 0.65 * ha, '800');
      rule(150 * S, 262 * S, 190 * S * easeOutExpo(seg(t, 0.0, 0.34)), F.style.accent, 0.65, 2);
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.35)) * 0.9, '700');
      box('bars.right', x1 - 470 * S, 206 * S, 470 * S, 44 * S);
      tracked('SOFTMAX 输出 · 单个查询向量', x1, 232 * S, 22 * S, '600', F.style.muted, 6 * S,
              clamp(seg(t, 0.55, 0.95)) * 0.55, 'right');

      // 刻度轴（舞台元素：切过来就在）
      ctx.save(); ctx.globalAlpha = 0.5;
      for (let i = 0; i <= 4; i++) {
        const x = x0 + trackW * (i / 4);
        ctx.strokeStyle = 'rgba(150,185,230,0.20)'; ctx.lineWidth = 1 * S;
        ctx.beginPath(); ctx.moveTo(x, top - 46 * S); ctx.lineTo(x, top + p.bars.length * gap - (gap - rh) - 6 * S); ctx.stroke();
        ctx.fillStyle = 'rgba(150,185,230,0.42)'; ctx.font = font('600', 24 * S);
        ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
        ctx.fillText((i * 25) + '%', x, top + p.bars.length * gap - (gap - rh) + 34 * S);
      }
      ctx.restore();

      p.bars.forEach(([lab, v], i) => {
        const st = 0.10 + i * 0.16;
        const k = easeOutExpo(seg(t, st, st + 0.62));
        const y = top + i * gap;
        ctx.save();
        // 轨道：从第 0 帧就成立
        ctx.fillStyle = 'rgba(255,255,255,0.045)';
        ctx.beginPath(); ctx.roundRect(x0, y, trackW, rh, 10 * S); ctx.fill();
        ctx.strokeStyle = 'rgba(150,185,230,0.18)'; ctx.lineWidth = 1 * S; ctx.stroke();
        ctx.globalAlpha = clamp(seg(t, st - 0.06, st + 0.14));
        // 条
        const wBar = Math.max(6 * S, trackW * v * k);
        const g = ctx.createLinearGradient(x0, y, x0 + wBar, y);
        g.addColorStop(0, 'rgba(94,200,255,0.92)'); g.addColorStop(1, 'rgba(124,243,192,0.85)');
        ctx.save();
        ctx.shadowColor = 'rgba(94,200,255,0.5)'; ctx.shadowBlur = 22 * S;
        ctx.fillStyle = g; ctx.beginPath(); ctx.roundRect(x0, y, wBar, rh, 10 * S); ctx.fill();
        ctx.restore();
        ctx.fillStyle = 'rgba(240,250,255,0.85)';
        ctx.fillRect(x0 + wBar - 2.5 * S, y + 6 * S, 2.5 * S, rh - 12 * S);
        // 标签在轨道左侧，数值贴着条的前缘
        ctx.font = font('800', 46 * S); ctx.textBaseline = 'middle'; ctx.textAlign = 'right';
        ctx.fillStyle = F.style.fg;
        ctx.fillText(lab, x0 - 34 * S, y + rh / 2);
        ctx.textAlign = 'left';
        const valX = Math.min(x0 + wBar + 26 * S, x1 + 6 * S);
        ctx.font = font('800', 50 * S);
        ctx.shadowColor = 'rgba(94,200,255,0.35)'; ctx.shadowBlur = 16 * S;
        ctx.fillText(Math.round(v * 100 * k) + (p.unit || ''), valX, y + rh / 2);
        ctx.restore();
      });
      // 底部横向标尺说明（远离字幕条，不再与坐标轴重叠）
      const fa = clamp(seg(t, 0.72, 1.1)) * 0.55;
      tracked('注意力权重越高，模型越"看"这个位置', x0, top + p.bars.length * gap + 76 * S, 22 * S, '600', F.style.muted, 6 * S, fa);
    },

    outro(p, t, d) {
      const cx = W / 2, cy = H / 2 + 30 * S;
      const k = easeOutBack(seg(t, 0.06, 0.62));
      const ka = easeOutCubic(seg(t, 0.02, 0.30));
      // 背后的柔光
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const rg = ctx.createRadialGradient(cx, cy + 20 * S, 0, cx, cy + 20 * S, 760 * S);
      rg.addColorStop(0, 'rgba(94,200,255,' + (0.10 * (0.45 + 0.55 * ka)).toFixed(3) + ')');
      rg.addColorStop(0.55, 'rgba(60,130,200,0.045)');
      rg.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = rg; ctx.fillRect(0, 0, W, H);
      ctx.restore();

      const kw = tw(p.kicker, 34 * S, '700', 12 * S);
      box('outro.kicker', cx - kw / 2, cy - 208 * S, kw, 54 * S);
      box('outro.hero', cx - tw(p.text, 128 * S, '900') / 2, cy - 46 * S, tw(p.text, 128 * S, '900'), 168 * S);
      tracked(p.kicker, cx - kw / 2, cy - 176 * S, 34 * S, '700', F.style.accent2, 12 * S, 0.45 + 0.55 * ka);
      rule(cx - 110 * S, cy - 148 * S, 220 * S * easeOutExpo(seg(t, 0.15, 0.75)), F.style.accent2, 0.65, 2);

      const size = 128 * S;
      ctx.save();
      ctx.translate(cx, cy + 34 * S);
      ctx.scale(lerp(0.955, 1, k), lerp(0.955, 1, k));
      ctx.globalAlpha = 0.40 + 0.60 * clamp(seg(t, 0.0, 0.28));
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = font('900', size);
      ctx.shadowColor = 'rgba(94,200,255,0.35)'; ctx.shadowBlur = 30 * S;
      ctx.fillStyle = F.style.fg; ctx.fillText(p.text, 0, 0);
      ctx.restore();

      // 跟随的下划扫线
      const uw = tw(p.text, size, '900');
      const uk = easeOutExpo(seg(t, 0.40, 1.0));
      ctx.save();
      ctx.shadowColor = 'rgba(124,243,192,0.55)'; ctx.shadowBlur = 18 * S;
      rule(cx - uw / 2, cy + 34 * S + size * 0.62, uw * uk, F.style.accent2, 0.75, 3);
      ctx.restore();

      const sw = tw('Softmax(QKᵀ/√d)·V —— 权重决定看哪里', 24 * S, '600', 7 * S);
      box('outro.formula', cx - sw / 2, cy + 34 * S + 128 * 0.62 * S + 66 * S, sw, 44 * S);
      tracked('Softmax(QKᵀ/√d)·V —— 权重决定看哪里', cx - sw / 2, cy + 34 * S + size * 0.62 + 92 * S,
              24 * S, '600', F.style.muted, 7 * S, clamp(seg(t, 0.52, 0.95)) * 0.75);
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.1, 0.5)) * 0.9, '700');
    },

    stat(p, t, d) {
      const x = 220 * S, cy = H / 2 - 30 * S;
      box('stat.label', x, cy - 208 * S, 620 * S, 62 * S);
      box('stat.value', x, cy - 150 * S, 1000 * S, 250 * S);
      box('stat.bar', x, cy + 136 * S, 900 * S, 44 * S);
      box('stat.note', x, cy + 206 * S, 700 * S, 44 * S);
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      const la = easeOutCubic(seg(t, 0.0, 0.30));
      label(p.label, x, cy - 168 * S, 44, F.style.muted, 7 * S, 0.45 + 0.55 * la, '700');
      rule(x, cy - 140 * S, 120 * S * easeOutExpo(seg(t, 0.02, 0.45)), F.style.accent, 0.6, 2);

      const k = easeOutExpo(seg(t, 0.05, 0.95));
      const shown = p.value * k;
      const big = (p.decimals ? shown.toFixed(p.decimals) : Math.round(shown).toString());
      ctx.save();
      ctx.font = font('900', 236 * S); ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left';
      ctx.shadowColor = 'rgba(94,200,255,0.42)'; ctx.shadowBlur = 34 * S;
      ctx.fillStyle = F.style.fg;
      const vw = tw(big, 236 * S, '900');
      ctx.fillText(big, x, cy + 70 * S);
      ctx.shadowBlur = 20 * S; ctx.font = font('800', 96 * S); ctx.fillStyle = F.style.accent2;
      ctx.fillText(p.unit || '', x + vw + 18 * S, cy + 70 * S);
      ctx.restore();

      const bw = 900 * S, ba = clamp(seg(t, 0.35, 1.05));
      ctx.save();
      ctx.fillStyle = 'rgba(255,255,255,0.05)';
      ctx.beginPath(); ctx.roundRect(x, cy + 150 * S, bw, 16 * S, 8 * S); ctx.fill();
      const g = ctx.createLinearGradient(x, 0, x + bw * (p.pct || 1) * ba, 0);
      g.addColorStop(0, F.style.accent); g.addColorStop(1, F.style.accent2);
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.roundRect(x, cy + 150 * S, Math.max(8 * S, bw * (p.pct || 1) * ba), 16 * S, 8 * S); ctx.fill();
      ctx.restore();
      tracked(p.note || '', x, cy + 232 * S, 26 * S, '600', F.style.muted, 6 * S, clamp(seg(t, 0.55, 1.0)) * 0.85);
    },

    compare(p, t, d) {
      const x0 = 200 * S, x1 = W - 170 * S, mid = (x0 + x1) / 2;
      const top = 296 * S;
      const ha = easeOutCubic(seg(t, 0.0, 0.30));
      label(p.title, x0, 236 * S, 56, F.style.fg, 6 * S, 0.4 + 0.6 * ha, '800');
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      rule(x0, 266 * S, 190 * S * easeOutExpo(seg(t, 0.0, 0.4)), F.style.accent, 0.65, 2);
      ctx.save(); ctx.globalAlpha = 0.35 * ha;
      rule(mid, top - 40 * S, 1.4 * S, 'rgba(150,185,230,0.6)', 1, H * 0.62 / S);
      ctx.restore();

      box('compare.title', x0 - 30 * S, 196 * S, 560 * S, 90 * S);
      box('compare.left', x0, top - 30 * S, mid - x0 - 90 * S, 520 * S);
      box('compare.right', mid + 130 * S, top - 30 * S, W - (mid + 130 * S) - 120 * S, 520 * S);
      [[p.left, x0, F.style.muted, '左'], [p.right, mid + 130 * S, F.style.accent, '右']].forEach(([side, sx, col], si) => {
        if (!side) return;
        const sst = 0.10 + si * 0.10;
        label(side.title, sx, top + 10 * S, 48, col, 7 * S, easeOutCubic(seg(t, sst, sst + 0.30)), '800');
        (side.items || []).forEach((it, i) => {
          const st = sst + 0.16 + i * 0.14;
          const k2 = easeOutCubic(seg(t, st, st + 0.36));
          const y = top + 116 * S + i * 104 * S;
          ctx.save();
          ctx.globalAlpha = k2;
          ctx.fillStyle = col;
          ctx.beginPath(); ctx.arc(sx + 9 * S, y - 14 * S, 8 * S, 0, 6.2832); ctx.fill();
          ctx.translate(0, 16 * S * (1 - k2));
          tracked(it, sx + 40 * S, y, 42 * S, '600', F.style.fg, 4 * S, 0.92);
          ctx.restore();
        });
      });
    },

    steps(p, t, d) {
      const x0 = 240 * S, x1 = W - 240 * S, y = H / 2 + 30 * S;
      const n = p.steps.length, step = (x1 - x0) / Math.max(1, n - 1);
      box('steps.title', x0 - 30 * S, 250 * S, 560 * S, 90 * S);
      box('steps.row', 140 * S, y - 70 * S, W - 280 * S, 240 * S);
      label(p.title, x0, 300 * S, 56, F.style.fg, 6 * S, easeOutCubic(seg(t, 0.0, 0.30)) * 0.95, '800');
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      ctx.save();
      ctx.strokeStyle = 'rgba(150,185,230,0.22)'; ctx.lineWidth = 2 * S;
      const lk = easeOutExpo(seg(t, 0.08, 0.85));
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x0 + (x1 - x0) * lk, y); ctx.stroke();
      ctx.restore();
      p.steps.forEach((st, i) => {
        const k = easeOutBack(seg(t, 0.12 + i * 0.20, 0.52 + i * 0.20));
        const a = clamp(seg(t, 0.08 + i * 0.20, 0.32 + i * 0.20));
        const cx2 = x0 + step * i;
        ctx.save(); ctx.globalAlpha = a;
        ctx.translate(cx2, y);
        ctx.scale(k, k);
        ctx.save();
        ctx.shadowColor = 'rgba(94,200,255,0.5)'; ctx.shadowBlur = 22 * S;
        ctx.fillStyle = i === 0 ? F.style.accent : 'rgba(94,200,255,0.9)';
        ctx.beginPath(); ctx.arc(0, 0, 27 * S, 0, 6.2832); ctx.fill();
        ctx.restore();
        ctx.restore();
        ctx.save(); ctx.globalAlpha = a;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.font = font('800', 32 * S); ctx.fillStyle = '#04060d';
        ctx.fillText(String(i + 1), cx2, y + 1 * S);
        ctx.restore();
        const w = tw(st, 38 * S, '700', 5 * S);
        tracked(st, cx2 - w / 2, y + 104 * S, 38 * S, '700', F.style.fg, 5 * S, a * 0.92);
      });
    },

    chart_line(p, t, d) {
      const x0 = 360 * S, x1 = W - 320 * S, y0 = H - 320 * S, y1 = 330 * S;
      const ha = easeOutCubic(seg(t, 0.0, 0.28));
      label(p.title, x0 - 80 * S, 250 * S, 52, F.style.fg, 6 * S, 0.4 + 0.6 * ha, '800');
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      ctx.save(); ctx.globalAlpha = 0.45 * ha;
      ctx.strokeStyle = 'rgba(150,185,230,0.22)'; ctx.lineWidth = 1 * S;
      for (let i = 0; i <= 4; i++) {
        const y = y0 - (y0 - y1) * (i / 4);
        ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      }
      ctx.strokeStyle = 'rgba(150,185,230,0.5)';
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y0); ctx.moveTo(x0, y0); ctx.lineTo(x0, y1); ctx.stroke();
      ctx.restore();

      box('chart.title', x0 - 120 * S, 200 * S, 640 * S, 90 * S);
      box('chart.plot', x0 - 130 * S, y1 - 40 * S, (x1 - x0) + 300 * S, (y0 - y1) + 130 * S);
      const pts = p.points, n = pts.length;
      const px = i => x0 + (x1 - x0) * (i / (n - 1));
      const py = v => y0 - (y0 - y1) * v;
      const prog = easeInOut(seg(t, 0.15, 0.90)) * (n - 1);
      ctx.save();
      ctx.strokeStyle = F.style.accent2; ctx.lineWidth = 6.5 * S;
      ctx.shadowColor = 'rgba(124,243,192,0.55)'; ctx.shadowBlur = 20 * S;
      ctx.beginPath(); ctx.moveTo(px(0), py(pts[0]));
      for (let i = 1; i < n; i++) {
        const seg2 = clamp(prog - (i - 1));
        if (seg2 <= 0) break;
        ctx.lineTo(lerp(px(i - 1), px(i), seg2), lerp(py(pts[i - 1]), py(pts[i]), seg2));
      }
      ctx.stroke(); ctx.restore();

      pts.forEach((v, i) => {
        const a = clamp(prog - (i - 0.2));
        if (a <= 0) return;
        ctx.save(); ctx.globalAlpha = a;
        ctx.fillStyle = '#04060d'; ctx.beginPath(); ctx.arc(px(i), py(v), 11 * S, 0, 6.2832); ctx.fill();
        ctx.fillStyle = F.style.accent2; ctx.beginPath(); ctx.arc(px(i), py(v), 7.5 * S, 0, 6.2832); ctx.fill();
        ctx.restore();
      });
      ctx.save(); ctx.globalAlpha = 0.5 * ha;
      ctx.font = font('600', 22 * S); ctx.fillStyle = F.style.muted; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      [0, 0.5, 1].forEach(v => ctx.fillText(Math.round(v * 100) + '%', x0 - 20 * S, py(v)));
      ctx.restore();
      const lastA = clamp(seg(t, 0.62, 0.95));
      if (lastA > 0) {
        ctx.save(); ctx.globalAlpha = lastA;
        ctx.font = font('800', 40 * S); ctx.fillStyle = F.style.accent2;
        ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        ctx.fillText(Math.round(pts[n - 1] * 100) + '%', x1 + 110 * S, py(pts[n - 1]));
        ctx.restore();
      }
      if (p.xlabel) tracked(p.xlabel, (x0 + x1) / 2 - tw(p.xlabel, 24 * S, '600', 6 * S) / 2, y0 + 62 * S, 24 * S, '600', F.style.muted, 6 * S, clamp(seg(t, 0.5, 1.0)) * 0.7);
    },

    quote(p, t, d) {
      const cx = W / 2, size = 66 * S;
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      const la = easeOutCubic(seg(t, 0.0, 0.30));
      ctx.save();
      ctx.globalAlpha = 0.26 * la;
      ctx.font = font('900', 230 * S); ctx.fillStyle = F.style.accent;
      ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
      ctx.fillText('\u201C', 196 * S, 470 * S);
      ctx.restore();
      const lines = p.lines || [p.text || ''];
      box('quote.text', 330 * S, 400 * S, W - 660 * S, lines.length * 100 * S + 40 * S);
      lines.forEach((ln, i) => {
        const k = easeOutCubic(seg(t, 0.12 + i * 0.16, 0.46 + i * 0.16));
        const w = tw(ln, size, '700', 2 * S);
        tracked(ln, cx - w / 2, 486 * S + i * 100 * S + 24 * S * (1 - k),
                size, '700', F.style.fg, 2 * S, k * 0.96);
      });
      const ra = easeOutExpo(seg(t, 0.48, 0.96));
      box('quote.attr', cx - 300 * S, 672 * S, 600 * S, 70 * S);
      rule(cx - 44 * S, 700 * S, 88 * S * ra, F.style.accent, 0.7, 2);
      const aw = tw(p.attr || '', 28 * S, '600', 8 * S);
      tracked(p.attr || '', cx - aw / 2, 772 * S, 28 * S, '600', F.style.muted, 8 * S, ra * 0.9);
    },

    code(p, t, d) {
      const mono = px => '600 ' + px + 'px "Cascadia Mono","Consolas","Courier New",monospace';
      const x0 = 240 * S, y0 = 336 * S, pw = W - 480 * S, lh = 66 * S;
      const lines = p.lines || [];
      const ph = lines.length * lh + 72 * S;
      const KEY = ['def', 'return', 'if', 'else', 'for', 'while', 'import', 'from', 'class', 'with', 'as',
                   'in', 'not', 'and', 'or', 'None', 'True', 'False', 'self', 'lambda', 'yield'];
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      label(p.title || '', 240 * S, 272 * S, 48, F.style.fg, 6 * S, 0.4 + 0.6 * easeOutCubic(seg(t, 0, 0.25)), '800');
      box('code.title', 240 * S, 228 * S, 680 * S, 84 * S);
      box('code.panel', x0, y0, pw, ph);
      const pa = easeOutCubic(seg(t, 0.02, 0.28));
      ctx.save(); ctx.globalAlpha = pa;
      ctx.fillStyle = 'rgba(8,14,26,0.74)';
      ctx.strokeStyle = 'rgba(120,160,210,0.22)'; ctx.lineWidth = 1.4 * S;
      ctx.beginPath(); ctx.roundRect(x0, y0, pw, ph, 16 * S); ctx.fill(); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,0.035)';
      ctx.beginPath(); ctx.roundRect(x0, y0, pw, 42 * S, 16 * S); ctx.fill();
      ctx.restore();
      lines.forEach((ln, i) => {
        const k = easeOutCubic(seg(t, 0.10 + i * 0.10, 0.32 + i * 0.10));
        const y = y0 + 84 * S + i * lh;
        ctx.save(); ctx.globalAlpha = k;
        ctx.font = mono(24 * S); ctx.fillStyle = 'rgba(140,170,210,0.45)';
        ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        ctx.fillText(String(i + 1).padStart(2, '0'), x0 + 56 * S, y);
        const toks = ln.split(/(\s+|#.*$)/).filter(s => s !== '');
        let cx2 = x0 + 88 * S;
        toks.forEach((tk, j) => {
          let col = F.style.fg;
          if (tk.startsWith('#')) col = 'rgba(140,170,210,0.55)';
          else if (/^["'].*["']$/.test(tk)) col = F.style.warm;
          else if (KEY.includes(tk.trim())) col = F.style.accent2;
          else if ((toks[j + 1] || '').startsWith('(')) col = F.style.accent;
          ctx.font = mono(28 * S); ctx.fillStyle = col;
          ctx.textAlign = 'left';
          ctx.fillText(tk, cx2, y);
          cx2 += ctx.measureText(tk).width;
        });
        ctx.restore();
      });
      if (t > 0.10 + lines.length * 0.10) {
        const lastY = y0 + 84 * S + (lines.length - 1) * lh;
        ctx.save();
        ctx.globalAlpha = Math.sin(t * 6.0) > 0 ? 0.95 : 0.12;
        ctx.fillStyle = F.style.accent;
        ctx.fillRect(x0 + pw - 56 * S, lastY - 17 * S, 3 * S, 36 * S);
        ctx.restore();
      }
    },

    timeline(p, t, d) {
      const items = p.items || [];
      const x0 = 280 * S, x1 = W - 280 * S, y = H / 2 + 20 * S;
      const step = (x1 - x0) / Math.max(1, items.length - 1);
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      label(p.title || '', 280 * S, 304 * S, 52, F.style.fg, 6 * S, 0.4 + 0.6 * easeOutCubic(seg(t, 0, 0.25)), '800');
      box('timeline.title', 280 * S, 258 * S, 680 * S, 86 * S);
      box('timeline.axis', x0 - 90 * S, y - 140 * S, (x1 - x0) + 180 * S, 320 * S);
      const lk = easeOutExpo(seg(t, 0.06, 0.78));
      ctx.save();
      ctx.strokeStyle = 'rgba(150,185,230,0.30)'; ctx.lineWidth = 2.5 * S;
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x0 + (x1 - x0) * lk, y); ctx.stroke();
      ctx.restore();
      items.forEach((it, i) => {
        const k = easeOutBack(seg(t, 0.14 + i * 0.17, 0.50 + i * 0.17));
        const a = clamp(seg(t, 0.10 + i * 0.17, 0.34 + i * 0.17));
        const cx2 = x0 + step * i;
        ctx.save(); ctx.globalAlpha = a;
        ctx.save();
        ctx.shadowColor = 'rgba(94,200,255,0.55)'; ctx.shadowBlur = 20 * S;
        ctx.beginPath(); ctx.arc(cx2, y, 16 * S * k, 0, 6.2832);
        ctx.fillStyle = F.style.accent; ctx.fill();
        ctx.restore();
        ctx.restore();
        const yr = Array.isArray(it) ? it[0] : '';
        const lb = Array.isArray(it) ? it[1] : it;
        const wa = tw(yr, 30 * S, '800', 4 * S);
        tracked(yr, cx2 - wa / 2, y - 58 * S, 30 * S, '800', F.style.accent2, 4 * S, a);
        const wb = tw(lb, 26 * S, '600', 3 * S);
        tracked(lb, cx2 - wb / 2, y + 80 * S, 26 * S, '600', F.style.muted, 3 * S, a * 0.9);
      });
    },

    split_screen(p, t, d) {
      const midX = W / 2, wipe = easeInOutQuint(seg(t, 0.0, 0.32));
      label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, clamp(seg(t, 0.0, 0.4)) * 0.9, '700');
      box('split.left', 200 * S, 262 * S, midX - 280 * S, 556 * S);
      box('split.right', midX + 60 * S, 262 * S, W - midX - 260 * S, 556 * S);
      ctx.save(); ctx.globalAlpha = 0.9 * wipe;
      ctx.fillStyle = 'rgba(255,255,255,0.028)';
      ctx.fillRect(200 * S, 262 * S, midX - 280 * S, 556 * S);
      ctx.fillStyle = 'rgba(94,200,255,0.045)';
      ctx.fillRect(midX + 60 * S, 262 * S, W - midX - 260 * S, 556 * S);
      ctx.fillStyle = 'rgba(150,185,230,0.45)';
      ctx.fillRect(midX - 1 * S, 262 * S, 2 * S, 556 * S * wipe);
      ctx.restore();
      [[p.left, 240 * S, F.style.muted], [p.right, midX + 100 * S, F.style.accent]].forEach(([side, sx, col], si) => {
        if (!side) return;
        const sst = 0.12 + si * 0.10;
        label(side.title, sx, 348 * S, 44, col, 6 * S, easeOutCubic(seg(t, sst, sst + 0.28)), '800');
        (side.items || []).forEach((it, i) => {
          const k = easeOutCubic(seg(t, sst + 0.14 + i * 0.13, sst + 0.44 + i * 0.13));
          ctx.save(); ctx.globalAlpha = k;
          ctx.fillStyle = col;
          ctx.beginPath(); ctx.arc(sx + 8 * S, 432 * S + i * 96 * S, 7 * S, 0, 6.2832); ctx.fill();
          ctx.translate(0, 14 * S * (1 - k));
          tracked(it, sx + 32 * S, 444 * S + i * 96 * S, 36 * S, '600', F.style.fg, 3 * S, 0.92);
          ctx.restore();
        });
      });
    },

    transition(p, t, d) {
      const cy = H / 2 - 10 * S, bh = 250 * S;
      const sweep = easeOutExpo(seg(t, 0.0, 0.34));
      const outk = easeInOutQuint(seg(t, 0.82, 1.0));
      const x = lerp(-W * 1.06, 0, sweep) + outk * W * 1.06;
      ctx.save();
      ctx.fillStyle = 'rgba(10,22,38,0.94)';
      ctx.fillRect(x, cy - bh / 2, W, bh);
      ctx.fillStyle = 'rgba(150,185,230,0.18)';
      ctx.fillRect(x, cy - bh / 2, W, 1.5 * S);
      ctx.fillRect(x, cy + bh / 2, W, 1.5 * S);
      ctx.fillStyle = F.style.accent;
      ctx.fillRect(x + W - 3 * S, cy - bh / 2, 3 * S, bh);
      ctx.fillRect(x - 1 * S, cy - bh / 2, 3 * S, bh);
      ctx.restore();
      const ta = clamp(seg(t, 0.32, 0.58));
      if (ta > 0) {
        ctx.save();
        ctx.globalAlpha = ta;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.font = font('900', 92 * S); ctx.fillStyle = F.style.fg;
        ctx.fillText(p.text || '', W / 2, cy - 20 * S);
        ctx.restore();
        const sw = tw(p.sub || '', 26 * S, '700', 13 * S);
        tracked(p.sub || '', W / 2 - sw / 2, cy + 76 * S, 26 * S, '700', F.style.accent2, 13 * S, ta * 0.9);
        label(p.index, 150 * S, 148 * S, 28, F.style.accent, 6 * S, ta * 0.9, '700');
      }
      box('transition.text', W / 2 - 580 * S, cy - 88 * S, 1160 * S, 196 * S);
    },
  };

  function caption(t) {
    const c = (F.captions || []).find(c => t >= c.start && t < c.end);
    if (!c) return;
    const a = clamp(seg(t, c.start, c.start + 0.20));
    const size = 44 * S, pad = 46 * S, ch = 88 * S, y0 = H - 176 * S;
    const w = tw(c.text, size, '700'), bw = w + pad * 2, x0 = (W - bw) / 2;
    box('caption', x0, y0, bw, ch);
    ctx.save(); ctx.globalAlpha = a;
    ctx.fillStyle = 'rgba(6,10,20,0.80)';
    ctx.strokeStyle = 'rgba(110,140,185,0.5)'; ctx.lineWidth = 1.5 * S;
    ctx.beginPath(); ctx.roundRect(x0, y0, bw, ch, 12 * S); ctx.fill(); ctx.stroke();
    ctx.textBaseline = 'middle';
    tracked(c.text, x0 + pad + 1.5 * S, y0 + ch / 2 + 2 * S, size, '700', 'rgba(0,0,0,0.32)', 0, 1);
    tracked(c.text, x0 + pad, y0 + ch / 2, size, '700', '#eef4ff', 0, 1);
    ctx.restore();
  }

  function shotAt(t) {
    const hit = F.shots.find(s => t >= s.start && t < s.end);
    if (hit) return hit;
    if (t < F.shots[0].start) return F.shots[0];                 // 片头归首镜
    let prev = F.shots[0];                                       // 片尾/缝隙归**前一**镜头（绝不能落到末镜）
    for (const s of F.shots) if (s.start <= t) prev = s;
    return prev;
  }
  function drawScene(t, shotIn) {
    const shot = shotIn || shotAt(t);
    const d = shot.end - shot.start;
    // ★ 子帧时间必须夹在本镜头区间内：否则运动模糊会跨过切点，硬切被糊成叠化
    const local = Math.min(Math.max(t - shot.start, 0), d - 1e-6);
    const look = Object.assign({ bg1: F.style.bg1, bg2: F.style.bg2, grid: 150, tilt: 0, blobWarm: 0.13, push: 0.045, flash: 0.18 },
                               shot.look || {});
    background(t, look);
    const u = seg(local, 0, d);
    const push = lerp(1.0, 1.0 + look.push, easeOutExpo(u)) * (1 + 0.030 * (1 - easeOutExpo(seg(local, 0, 0.20))));
    const dx = Math.sin(local * 0.85 + shot.start * 3) * 9 * S;
    const dy = Math.cos(local * 0.63 + shot.start * 2) * 7 * S;
    ctx.save();
    ctx.translate(W / 2 + dx, H / 2 + dy); ctx.scale(push, push); ctx.translate(-W / 2, -H / 2);
    templates[shot.template](shot.props, local, d);
    ctx.restore();
    const fl = clamp(1 - local / 0.070);
    if (fl > 0) {   // 切点冲击：让"切"被看见
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = 'rgba(198,228,255,' + (look.flash * fl).toFixed(3) + ')';
      ctx.fillRect(0, 0, W, H); ctx.restore();
    }
    lightSweep(t);
    caption(t);
    return look;
  }

  window.seek = function (t) {
    if (!F) {
      F = window.__FILM__;
      const q = F.quality;
      S = q.supersample || 1; W = F.size[0] * S; H = F.size[1] * S;
      const info = Post.init(F.size[0], F.size[1], q);
      window.__ENGINE__ = { supersample: S, motionBlurSamples: q.motionBlurSamples, hdr: info.float, scene: [info.SW, info.SH] };
    }
    const q = F.quality, K = q.motionBlurSamples || 1, shutter = q.shutter ?? 0.5;
    const step = shutter / F.fps;
    Post.beginFrame();
    const shot = shotAt(t);
    LAYOUT = [];
    let look = null;
    for (let s = 0; s < K; s++) {
      const tt = Math.min(Math.max(t + ((s + 0.5) / K - 0.5) * step, shot.start), shot.end - 1e-6);
      look = drawScene(tt, shot);
      Post.accumulate(tt);
    }
    // 画面签名：中心 + 两个角落的亮度均值（白屏检测用；getImageData 只读小块，开销可忽略）
    try {
      let sum = 0, n = 0;
      for (const [px, py] of [[W * 0.5, H * 0.5], [W * 0.12, H * 0.86], [W * 0.86, H * 0.14]]) {
        const d = ctx.getImageData(Math.round(px), Math.round(py), 6, 6).data;
        for (let i = 0; i < d.length; i += 4) { sum += (d[i] + d[i + 1] + d[i + 2]) / 3; n++; }
      }
      window.__SIG__ = Math.round(sum / Math.max(1, n));
    } catch (e) { window.__SIG__ = -1; }
    Post.finish(q, t, look);
    window.__LAYOUT__ = LAYOUT;
    return true;
  };

  function label(text, x, y, size, color, spacing, alpha, weight) {
    tracked(text, x, y, size * S, weight, color, spacing, alpha);
  }
})();