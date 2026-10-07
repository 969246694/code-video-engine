/* 逐帧渲染器：Edge(headless) + CDP + ffmpeg。零 npm 依赖（Node 24 自带 fetch/WebSocket）。
   长跑可靠性（都是实测踩出来的）：
     · rAF 会停摆               → 每步带超时兜底
     · 截图会撕裂/残影          → 连续两拍字节一致才算数
     · 画面会变纯白（标签失效）  → 空白检测 + 会话失效自动重启浏览器并从当前帧续渲
     · 长跑会累积失效           → 每 N 帧预防性重载，且每帧检查页面心跳 */
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const argv = Object.fromEntries(process.argv.slice(2).map(a => {
  const [k, ...v] = a.replace(/^--/, '').split('='); return [k, v.join('=') || true];
}));
const FILM = argv.film || 'film.json';
const OUT = argv.out || 'build/sample_frames';
const LIMIT = Number(argv.frames || 0);
const ENCODE = argv.encode !== 'false';
const FFMPEG = argv.ffmpeg || 'ffmpeg';
const PORT = Number(argv.port || 9333);
const EDGE = argv.browser || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const RELOAD_EVERY = Number(argv.reloadEvery || 150);   // 预防性重载间隔
const CALL_TIMEOUT = Number(argv.callTimeout || 20000);

const film = JSON.parse(readFileSync(FILM, 'utf8'));
if (argv.fast) film.quality = Object.assign({}, film.quality, { supersample: 1, motionBlurSamples: 2, grain: 0, bloom: 0.4 });
const FPS = film.fps, [W, H] = film.size;
const total = LIMIT ? LIMIT / FPS : film.duration;
mkdirSync(OUT, { recursive: true });
const url = pathToFileURL(path.resolve('engine/render.html')).href;

const sleep = ms => new Promise(r => setTimeout(r, ms));
let proc = null, ws = null, seq = 0, pending = new Map();

function send(method, params = {}) {
  const id = ++seq;
  return new Promise((res, rej) => {
    const timer = setTimeout(() => { pending.delete(id); rej(new Error('CDP 超时: ' + method)); }, CALL_TIMEOUT);
    pending.set(id, {
      res: v => { clearTimeout(timer); res(v); },
      rej: e => { clearTimeout(timer); rej(e); },
    });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function waitTarget() {
  for (let i = 0; i < 160; i++) {
    try {
      const r = await fetch('http://127.0.0.1:' + PORT + '/json/list');
      const page = (await r.json()).find(t => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(250);
  }
  throw new Error('等不到 Edge 调试端口');
}

async function launch() {
  const userDir = path.join(process.env.TEMP || '.', 'dsh-edge-' + PORT + '-' + Date.now());
  proc = spawn(EDGE, [
    '--headless=new', '--remote-debugging-port=' + PORT, '--user-data-dir=' + userDir,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars',
    '--force-device-scale-factor=1', '--font-render-hinting=none', '--window-size=' + W + ',' + H, url,
  ], { stdio: 'ignore' });
  ws = new WebSocket(await waitTarget());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws error')); });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    }
  };
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  for (let i = 0; i < 200; i++) {
    const r = await send('Runtime.evaluate', { expression: 'document.readyState + "|" + String(window.__READY__ === true)', returnByValue: true });
    if (String(r.result.value).endsWith('|true')) break;
    await sleep(100);
  }
  await send('Runtime.evaluate', { expression: 'window.__FILM__ = ' + JSON.stringify(film) + '; true', returnByValue: true });
}

async function restart(reason) {
  restarts++;
  console.log('  [驱动] 重启浏览器（' + reason + '）');
  try { ws && ws.close(); } catch {}
  try { proc && proc.kill(); } catch {}
  pending.clear();
  await sleep(1400);
  await launch();
}

const MIN_PNG = 120000;          // 空白面会被压得极小
async function seekAndSync(t) {
  await send('Runtime.evaluate', { expression: 'window.seek(' + t.toFixed(6) + ')', returnByValue: true });
  await Promise.race([
    send('Runtime.evaluate', { expression: 'new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(1))))', awaitPromise: true, returnByValue: true }),
    sleep(1500),
  ]);
}
async function grab(t) {
  let shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  for (let attempt = 0; attempt < 3; attempt++) {
    const again = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
    if (shot.data.length > MIN_PNG && again.data === shot.data) return shot;
    if (again.data.length <= MIN_PNG) blanks++; else unstable++;
    await sleep(180);
    shot = again;
    if (shot.data.length <= MIN_PNG) { await seekAndSync(t); shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true }); }
  }
  return shot;
}

const nFrames = Math.round(total * FPS);
const times = argv.times
  ? String(argv.times).split(',').map(Number)
  : Array.from({ length: nFrames }, (_, k) => k / FPS);

let unstable = 0, blanks = 0, restarts = 0, LAYOUT = argv.layout ? [] : null;
await launch();

const t0 = Date.now();
for (let k = 0; k < times.length; k++) {
  if (k > 0 && k % RELOAD_EVERY === 0) await restart('每 ' + RELOAD_EVERY + ' 帧预防性重载');
  let shot = null;
  for (let attempt = 0; attempt < 3 && !shot; attempt++) {
    try {
      if (attempt) await seekAndSync(times[k]);
      await seekAndSync(times[k]);
      // 画面签名：带颗粒的"白屏"体积不小，体积判据抓不到 → 让页面自己报中心/角落的亮度
      const sig = await send('Runtime.evaluate', { expression: '(window.__SIG_OUT__ !== undefined ? window.__SIG_OUT__ : (window.__SIG__ || 0))', returnByValue: true });
      if (Number(sig.result.value) > 250) throw new Error('画面全白（签名 ' + sig.result.value + '）');
      shot = await grab(times[k]);
    } catch (e) {
      shot = null;
      await restart('会话失败: ' + String(e.message).slice(0, 60));
    }
  }
  if (!shot) throw new Error('第 ' + k + ' 帧连续失败，放弃');
  const name = argv.times ? ('t' + String(Math.round(times[k] * 1000)).padStart(5, '0') + '.png') : ('f' + String(k).padStart(4, '0') + '.png');
  writeFileSync(path.join(OUT, name), Buffer.from(shot.data, 'base64'));
  if (LAYOUT) {
    try {
      const r2 = await send('Runtime.evaluate', { expression: 'JSON.stringify(window.__LAYOUT__||[])', returnByValue: true });
      LAYOUT.push({ frame: k, t: +times[k].toFixed(3), boxes: JSON.parse(r2.result.value || '[]') });
    } catch { await restart('取版面盒失败'); }
  }
  if (k % 30 === 0 || k === times.length - 1) {
    const el = (Date.now() - t0) / 1000;
    process.stdout.write('  帧 ' + (k + 1) + '/' + times.length + '  ' + ((k + 1) / el).toFixed(1) + ' fps  ETA ' + (((times.length - k - 1) / ((k + 1) / el)) | 0) + 's\n');
  }
}

if (LAYOUT) { writeFileSync(argv.layout, JSON.stringify(LAYOUT)); console.log('版面盒 -> ' + argv.layout); }
const wall = (Date.now() - t0) / 1000;
console.log('渲染 ' + times.length + ' 帧 / ' + wall.toFixed(1) + 's = ' + (times.length / wall).toFixed(2) + ' fps'
  + (unstable ? '   撕裂重拍 ' + unstable : '   截图稳定')
  + (blanks ? '   空白重拍 ' + blanks : '')
  + (restarts ? '   浏览器重启 ' + restarts + ' 次' : ''));
ws.close(); proc.kill();

if (ENCODE && !argv.times) {
  const outMp4 = argv.mp4 || 'output/sample.mp4';
  mkdirSync(path.dirname(outMp4), { recursive: true });
  await new Promise((res, rej) => {
    const AUDIO = argv.audio || film.audioFile || '';
    const args = ['-y', '-v', 'error', '-framerate', String(FPS), '-i', path.join(OUT, 'f%04d.png')];
    if (AUDIO) args.push('-i', AUDIO, '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2');
    args.push('-c:v', 'libx264', '-preset', argv.preset || 'medium', '-tune', 'film',
              '-crf', String(argv.crf || 22), '-pix_fmt', 'yuv420p');
    if (AUDIO) args.push('-shortest', '-movflags', '+faststart');
    args.push(outMp4);
    const ff = spawn(FFMPEG, args, { stdio: 'inherit' });
    ff.on('exit', c => c === 0 ? res() : rej(new Error('ffmpeg exit ' + c)));
  });
  console.log('成片 -> ' + outMp4);
}
