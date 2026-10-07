/* 后期管线：2x 超采样场景 → 时间子采样累积(运动模糊) → 泛光 → 色差/颗粒/暗角/调色 → 输出 */
window.Post = (function () {
  const stage = document.getElementById('stage');
  const scene = document.getElementById('scene');
  const gl = stage.getContext('webgl2', { alpha: false, antialias: false, preserveDrawingBuffer: true });
  if (!gl) throw new Error('no webgl2');
  const half = gl.getExtension('EXT_color_buffer_float');
  gl.getExtension('OES_texture_float_linear');
  const FLOAT = !!half;

  function sh(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) + '\n' + src);
    return s;
  }
  function prog(fs) {
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, `#version 300 es
      in vec2 a; out vec2 uv; void main(){ uv = a*0.5+0.5; gl_Position = vec4(a,0.,1.); }`));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
    gl.bindAttribLocation(p, 0, 'a');
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    p.u = new Proxy({}, { get: (_, n) => gl.getUniformLocation(p, n) });
    return p;
  }
  const quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

  function tex(w, h, float) {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, float ? gl.RGBA16F : gl.RGBA8, w, h, 0, gl.RGBA, float ? gl.HALF_FLOAT : gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const f = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    return { t, f, w, h };
  }

  const P = {
    accum: prog(`#version 300 es
      precision highp float; in vec2 uv; out vec4 o; uniform sampler2D uTex; uniform float uW;
      void main(){
        vec3 c = texture(uTex, uv).rgb;
        c = pow(max(c, 0.0), vec3(2.2));      // sRGB → 线性，后期全部在线性空间
        o = vec4(c * uW, 1.0);
      }`),
    copy: prog(`#version 300 es
      precision highp float; in vec2 uv; out vec4 o; uniform sampler2D uTex;
      void main(){ o = vec4(texture(uTex, uv).rgb, 1.0); }`),
    bright: prog(`#version 300 es
      precision highp float; in vec2 uv; out vec4 o; uniform sampler2D uTex; uniform float uThresh;
      void main(){ vec3 c = texture(uTex, uv).rgb; float l = dot(c, vec3(0.2126,0.7152,0.0722));
        o = vec4(c * smoothstep(uThresh, uThresh + 0.25, l), 1.0); }`),
    blur: prog(`#version 300 es
      precision highp float; in vec2 uv; out vec4 o; uniform sampler2D uTex; uniform vec2 uDir; uniform vec2 uTexel;
      void main(){ vec2 d = uDir * uTexel; vec3 s = texture(uTex, uv).rgb * 0.227027;
        s += (texture(uTex, uv + d*1.3846).rgb + texture(uTex, uv - d*1.3846).rgb) * 0.3162162;
        s += (texture(uTex, uv + d*3.2308).rgb + texture(uTex, uv - d*3.2308).rgb) * 0.0702703;
        o = vec4(s, 1.0); }`),
    finish: prog(`#version 300 es
      precision highp float; in vec2 uv; out vec4 o;
      uniform sampler2D uScene, uBloom; uniform vec2 uTexel; uniform float uBloomAmt, uCA, uGrain, uVig, uExp, uSat, uTime;
      uniform vec3 uTint;
      float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
      void main(){
        vec2 c = uv - 0.5; float r2 = dot(c, c);
        vec2 off = c * r2 * uCA * 0.02;
        vec3 col;
        col.r = texture(uScene, uv + off).r;
        col.g = texture(uScene, uv).g;
        col.b = texture(uScene, uv - off).b;
        col += texture(uBloom, uv).rgb * uBloomAmt;
        col *= uExp;
        col *= uTint;
        float l = dot(col, vec3(0.2126,0.7152,0.0722));
        col = mix(vec3(l), col, uSat);
        col = clamp(col, 0.0, 1.0);
        col = col * col * (3.0 - 2.0 * col) * 0.35 + col * 0.65;      // 轻微 S 曲线
        col *= 1.0 - uVig * smoothstep(0.12, 0.78, r2);               // 暗角
        float g = hash(uv * vec2(1920.0, 1080.0) + uTime * 137.0) - 0.5;
        col += g * uGrain;
        o = vec4(pow(max(col, 0.0), vec3(1.0/2.2)), 1.0);              // 线性 → sRGB
      }`),
  };

  let W = 0, H = 0, SS = 2, sceneTex = null, accA = null, accB = null, bloom = [], bloomTex = [], K = 1;

  function init(w, h, q) {
    W = w; H = h; SS = q.supersample || 1; K = q.motionBlurSamples || 1;
    const SW = Math.round(W * SS), SH = Math.round(H * SS);
    stage.width = W; stage.height = H;
    scene.width = SW; scene.height = SH;
    sceneTex = tex(SW, SH, false);
    accA = tex(SW, SH, FLOAT); accB = tex(SW, SH, FLOAT);
    bloom = []; bloomTex = [];
    for (let i = 0; i < 3; i++) {
      const d = Math.pow(2, i + 1);
      const bw = Math.max(2, Math.round(W / d)), bh = Math.max(2, Math.round(H / d));
      bloom.push(tex(bw, bh, FLOAT)); bloomTex.push(tex(bw, bh, FLOAT));
    }
    return { SW, SH, float: FLOAT };
  }

  function fsq(target) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, target ? target.f : null);
    if (target) gl.viewport(0, 0, target.w, target.h);
    else gl.viewport(0, 0, W, H);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  function useQuad() { gl.bindBuffer(gl.ARRAY_BUFFER, quad); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0); }

  let first = true;
  function beginFrame() { first = true; }
  function accumulate(subT) {
    // 子帧场景 → 纹理
    gl.bindTexture(gl.TEXTURE_2D, sceneTex.t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, sceneTex.w, sceneTex.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, scene);
    useQuad();
    gl.useProgram(P.accum.u ? P.accum : P.accum);
    gl.uniform1i(P.accum.u.uTex, 0); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, sceneTex.t);
    // 第一子帧清空，其余累加
    gl.enable(gl.BLEND);
    if (first) { gl.blendFunc(gl.ONE, gl.ZERO); first = false; } else { gl.blendFunc(gl.ONE, gl.ONE); }
    gl.uniform1f(P.accum.u.uW, 1 / K);
    fsq(accA);
    gl.disable(gl.BLEND);
  }
  function bloomPass() {
    useQuad();
    gl.useProgram(P.bright); gl.uniform1i(P.bright.u.uTex, 0);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, accA.t);
    gl.uniform1f(P.bright.u.uThresh, 0.62);
    fsq(bloom[0]);
    let src = bloom[0];
    for (let i = 1; i < 3; i++) {
      useQuad(); gl.useProgram(P.blur);
      gl.uniform1i(P.blur.u.uTex, 0); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, src.t);
      gl.uniform2f(P.blur.u.uTexel, 1 / src.w, 1 / src.h);
      gl.uniform2f(P.blur.u.uDir, 2 / Math.pow(2, i), 0); fsq(bloom[i]);
      gl.uniform1i(P.blur.u.uTex, 0); gl.bindTexture(gl.TEXTURE_2D, bloom[i].t);
      gl.uniform2f(P.blur.u.uTexel, 1 / bloom[i].w, 1 / bloom[i].h);
      gl.uniform2f(P.blur.u.uDir, 0, 2 / Math.pow(2, i)); fsq(bloomTex[i]);
      src = bloomTex[i];
    }
    return bloomTex[2];
  }
  function finish(q, tNow, look) {
    const gr = (look && look.grade) || {};
    const b = bloomPass();
    useQuad(); gl.useProgram(P.finish);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, accA.t); gl.uniform1i(P.finish.u.uScene, 0);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, b.t); gl.uniform1i(P.finish.u.uBloom, 1);
    gl.uniform2f(P.finish.u.uTexel, 1 / W, 1 / H);
    gl.uniform1f(P.finish.u.uBloomAmt, q.bloom); gl.uniform1f(P.finish.u.uCA, q.chromatic);
    gl.uniform1f(P.finish.u.uGrain, q.grain); gl.uniform1f(P.finish.u.uVig, q.vignette);
    gl.uniform1f(P.finish.u.uExp, (q.exposure || 1) * (gr.exposure ?? 1));
    gl.uniform1f(P.finish.u.uSat, (q.saturation || 1) * (gr.saturation ?? 1));
    const t3 = gr.tint || [1, 1, 1];
    gl.uniform3f(P.finish.u.uTint, t3[0], t3[1], t3[2]);
    gl.uniform1f(P.finish.u.uTime, tNow);
    fsq(null);
    // 输出签名：从默认帧缓冲读三个 6x6 小块（白屏检测的 ground truth，开销可忽略）
    try {
      const px = new Uint8Array(6 * 6 * 4);
      let sum = 0, n = 0;
      for (const [fx, fy] of [[0.5, 0.5], [0.12, 0.86], [0.86, 0.14]]) {
        gl.readPixels(Math.round(W * fx), Math.round((1 - fy) * H), 6, 6, gl.RGBA, gl.UNSIGNED_BYTE, px);
        for (let i = 0; i < px.length; i += 4) { sum += (px[i] + px[i + 1] + px[i + 2]) / 3; n++; }
      }
      window.__SIG_OUT__ = Math.round(sum / Math.max(1, n));
    } catch (e) { window.__SIG_OUT__ = -1; }
  }
  return { init, beginFrame, accumulate, finish, gl, scene, stage, isFloat: () => FLOAT };
})();