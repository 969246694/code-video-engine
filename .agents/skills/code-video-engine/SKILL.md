---
name: code-video-engine
description: 用本项目的 IR + headless Chromium 渲染核制作科普短片（画面、动效、字幕全部由代码生成）。当任务涉及"做一段视频/短片/镜头/动效/字幕条/分镜"，或要改 film.json、engine/、verify.py、guards.py 时使用。含镜头模板库、质量参数、快速静帧自检与量化判据。
whenToUse: 需要生成或修改视频内容、镜头、动效、排版、配色、时长节奏；或需要验收一段成片是否达标。
---

# code-video-engine · 用代码生成高质量视频

**这个引擎的用户是模型，不是人。** 所以一切设计围绕三件事：**表达带宽**（一次能写出多少画面）、**自检能力**（不用人看图就能发现事故）、**知识可继承**（换会话/换模型仍能用）。

## 铁律

1. **先静帧，后全片。** 全片 4~5 秒/帧，静帧预览档 0.5 秒/帧。**每次改画面，先出 3~7 张静帧看图**，确认后再跑全片。
2. **必须看图。** 判据只覆盖"能测的"；撞版、糊、构图失衡只能靠眼睛。用 `read_image` 看静帧和联络表，一次看多张。
3. **不许为了让判据过关而改判据阈值。** 判据红了就改画面。
4. **切点必须是硬切，且落在"已经成立的画面"上。** 子帧时间必须夹在本镜头区间内（见踩坑 ①）。
5. **任何一次交付都要跑 `verify.py`，改动画面后还要跑 `guards.py`。**

## 架构

```
films/*.json  (IR：唯一的"源")
   │
   ├─ audio/build_audio.py   ← 旁白 TTS(SentenceBoundary) → 时间轴 → 母带(-16 LUFS)
   │                            产出 build/<film>_timed.json（镜头起止由旁白决定）
   ▼
node engine/driver.mjs  ← CDP 逐帧驱动（零 npm 依赖）
   ▼
headless Edge/Chrome → Canvas2D 场景层(2× 超采样) → WebGL2 后期 → ffmpeg(+母带)
                         engine/render.js              engine/gl.js
```

| 文件 | 作用 |
|---|---|
| `engine/render.js` | 画面层：背景/景深/排版/12 个镜头模板/字形扫光/切点冲击 |
| `engine/gl.js` | 后期：时间子采样运动模糊 → 泛光 → 色差/颗粒/暗角/每镜头调色（线性空间） |
| `engine/driver.mjs` | 逐帧驱动；`--fast` 预览档；`--times` 只出静帧；**长跑韧性**（会话超时 / 白屏检测 / 自动重启续渲 / 每 150 帧预防性重载） |
| `engine/render.html` | 画布宿主（stage 可见 + scene 超采样） |
| `films/*.json` | 影片 IR |
| `verify.py` | 全片量化判据 |
| `guards.py` | 静帧自检（安全边距/字幕带/曝光/对比度） |
| `audio/build_audio.py` | 旁白 TTS → 时间轴 → 母带；输出**定时版 IR** |
| `verify_audio.py` | 音频判据（响度/峰值/时长/静音/字幕对齐） |
| `check_boxes.py` | **版面机检**：元素碰撞 / 安全边距 / 字幕带侵入（读 driver 导出的版面盒） |

## IR（film.json）

```jsonc
{
  "fps": 30, "size": [1920, 1080], "duration": 7.0,
  "quality": { "supersample": 2, "motionBlurSamples": 10, "shutter": 0.5,
               "bloom": 0.55, "bloomThreshold": 0.68, "chromatic": 0.32,
               "grain": 0.028, "vignette": 0.5, "exposure": 1.02, "saturation": 1.05 },
  "style": { "bg0":"#04060d","bg1":"#0a1322","bg2":"#122036","fg":"#eef4ff","muted":"#8ea6c8",
             "accent":"#5ec8ff","accent2":"#7cf3c0","warm":"#ffb347","font":"'Microsoft YaHei',..." },
  "captions": [ { "start": 0.4, "end": 7.0, "text": "字幕文本" } ],
  "shots": [ {
      "id": "d1", "start": 0.0, "end": 1.0, "template": "title",
      "props": { },                                   // 见模板库
      "narration": { "text": "这句话会被念出来，并决定这个镜头的时长。" },   // 可选
      "look": { "bg1":"#0a1322","bg2":"#122036", "grid":150, "tilt":0, "blobWarm":0.13,
                "push":0.045, "flash":0.10,           // 每镜头外观：切点差异靠它
                "grade": { "exposure":1.02, "saturation":1.05, "tint":[1,1,1] } }
  } ]
}
```

**音频优先**：镜头有 `narration` 时，其起止由旁白时长决定（`build_audio.py` 写出定时版 IR），不要手填时长。影片级 `audio` 块：

```jsonc
"audio": { "voice": "zh-CN-XiaoxiaoNeural", "rate": "+6%", "cueMaxChars": 13,
           "lead": 0.55, "tail": 0.42, "gap": 0.30, "bgm": 0.055, "lufs": -16 }
```

- **每个镜头的 `look` 必须不同**（背景色/网格间距/倾角/暖光强度/调色）——这是切点强度的主要来源。
- **片头第一镜 `flash: 0`**（开场不需要"切点冲击"，否则会削顶）。
- 字幕同时只允许一条（区间不得重叠，用半开区间 `[start, end)`）。

## 镜头模板库（`props`）

| 模板 | props | 用途 |
|---|---|---|
| `title` | `text, sub, index` | 片头/章节大标题（逐字砸入 + 字形扫光） |
| `stat` | `label, value, decimals, unit, pct, note, index` | 单个大数字（count-up + 进度条） |
| `compare` | `title, left{title,items[]}, right{title,items[]}, index` | 左右对照 |
| `steps` | `title, steps[], index` | 3~4 步流程（连线生长 + 点号依次点亮） |
| `chart_line` | `title, points[], xlabel, index` | 折线趋势（绘制动画 + 末值标注） |
| `bars` | `title, unit, bars[[label,0~1]], index` | 权重/占比条 |
| `outro` | `kicker, text, index` | 结论页（柔光 + 下划扫线） |
| `quote` | `lines[], attr, index` | 引文卡（引号装饰 + 逐行浮入 + 出处） |
| `code` | `lines[], title, index` | 代码块（行号 + 语法着色 + 逐行出现 + 闪烁光标） |
| `timeline` | `items[[年,标签]], title, index` | 时间线（轴生长 + 节点依次点亮） |
| `split_screen` | `left{title,items}, right{title,items}, index` | 左右分屏对照（分隔线自上而下展开） |
| `transition` | `text, sub, index` | 章节卡（横幅扫入 → 停留 → 扫出，用于换章） |

加模板：在 `engine/render.js` 的 `templates` 对象里加一个 `(p, t, d)` 函数即可（`t` 是本镜头内时间，`d` 是镜头时长）。**新模板必须：第 0 帧就有"舞台元素"（切点要落在成立的画面上）、镜头内始终有变化（不许静止帧）、元素留在安全边距内。**

## 工作流

```bash
FF=$(uv run --python 3.12 --with imageio-ffmpeg python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())")

# 0) 有旁白时：先出音频与时间轴（它决定镜头时长与字幕切分）
uv run --python 3.12 --with edge-tts --with numpy python audio/build_audio.py films/x.json "$FF"
uv run --python 3.12 --with numpy python verify_audio.py "$FF"

# 1) 快速静帧（预览档：ss=1 / 2 段模糊 / 无颗粒）——0.5s 一帧，先看图
node engine/driver.mjs --film=films/x.json --out=build/stills --times=0.5,1.5,2.5 --fast --ffmpeg="$FF"

# 2) 静帧自检（像素级）
uv run --python 3.12 --with numpy --with pillow python guards.py build/stills

# 2b) 版面机检（元素级，抓"注脚压字幕"这类碰撞；--layout 导出每帧版面盒）
node engine/driver.mjs --film=build/x_timed.json --out=build/stills --times=2.4,6.2 --fast --layout=build/layout.json --ffmpeg="$FF"
uv run --python 3.12 python check_boxes.py build/layout.json

# 3) 全片（交付档，带母带；渲染 4~5s/帧，用后台作业跑）
node engine/driver.mjs --film=build/x_timed.json --out=build/frames --ffmpeg="$FF" --mp4=output/x.mp4 --audio=build/master.wav

# 4) 全片判据
uv run --python 3.12 --with numpy python verify.py "$FF"
```

联络表（一次看多帧）：用 ffmpeg `select=not(mod(n\,4)),scale=420:-1,tile=6x5`，或 Pillow 拼图；然后用 `read_image` 看。

## 判据阈值

`verify.py`（全片）：

| 判据 | 阈值 | 含义 |
|---|---|---|
| 镜头内平均运动 | > 0.05 | 有动效 |
| 镜头内最小运动 | > 0 | **无完全静止帧** |
| 完全静止帧数 | = 0 | |
| **切点 / 镜头内运动** | **≥ 3x** | "切"要被看见 |
| 字幕覆盖 | > 95% | |
| 字幕带上方安静带 | < 2% | 防内容撞字幕 |
| 同刻多条字幕 | = 0 | |

`verify_audio.py`（音频）：

| 判据 | 阈值 |
|---|---|
| 整片响度 | 目标 ±1 LU（默认 -16 LUFS） |
| 真峰值 | ≤ -1.0 dBFS |
| 母带时长 vs 时间轴 | ≤ 0.35s |
| 旁白首尾静音 | ≤ 0.60s（死气/吞字） |
| 旁白内部最长停顿 | ≤ 0.90s（自然停顿允许） |
| 字幕落在旁白区间内 | 全部 |
| 字幕时长 ≥0.55s 且 ≥3 字 | 全部 |
| 字幕不重叠 | = 0 |
| 镜头包住旁白 | 全部 |

`guards.py`（静帧，像素级）：安全边距（亮元素 bbox 在 56/44 px 内）、字幕带安静 <2%、曝光 mean 12~110 且削顶 <8%、字幕板对比度 ≥ 90。

`check_boxes.py`（版面盒，元素级）：

| 规则 | 说明 |
|---|---|
| 碰撞 | 两盒在 x、y 上重叠 >2px 且**互不包含** → 违规（"包含"合法：`bars.block` 包住 `bars.title`） |
| 安全边距 | 所有盒子在 56 / 44 px 内 |
| 字幕带 | 非 caption 盒不得进入 y ∈ [H-176, H-88] |

> 模板里的 `box(tag, x, y, w, h)` 就是登记占位；**加模板时必须登记盒子**，否则机检等于没覆盖。

## 踩过的坑（都是实测，别再犯）

1. **运动模糊会跨过切点**：子帧时间 `t ± shutter/2` 若跨镜头边界，硬切被糊成叠化，切点强度从 3.2x 掉到 1.8x。必须把子帧时间夹进本镜头区间（`render.js` 的 `drawScene(tt, shot)`）。
2. **切到"空台"很丑**：新镜头第 0 帧若什么都没有（只有背景），切过去像掉帧。**舞台元素（标题/轨道/坐标轴）必须在第 0 帧就在**。
3. **切点需要 2 帧冲击**：光脉冲 `flash` 0.24~0.26、衰减 0.07s，配合 3% 缩放回落。没有它，切点/镜头内运动只有 2.3x（不达标）。
4. **字幕带是禁区**：底部 176~264 px 只能有字幕。注脚、坐标轴刻度必须抬到它上方 44 px 之外。
5. **开场帧容易削顶**：首镜大标题 + 光晕 + 冲击叠加会过曝（实测 clip 4.15%）。首镜 `flash: 0`，大字 shadowBlur ≤ 18。
6. **超采样值 1.9 倍代价**：`ss=1.5` 比 `ss=1` 慢 1.9x；预览一律 `--fast`。
7. **TTS 端点会瞬时失败**（`NoAudioReceived`）：必须重试（默认 4 次退避），并且**失败就报错，绝不静默产出无声片**。旁白已缓存（mp3 + 句子边界 json），改画面不会重复打端点。
8. **母带长度必须等于时间轴长度**：多写缓冲区会让成片尾部多出静音（实测 +1.0s），`-shortest` 会把它剪掉但判据会红。缓冲按 `round(total*SR)` 精确分配。
9. **句间字幕会重叠几毫秒**：`SentenceBoundary` 区间首尾相接，必须强制单调并留 20ms 缝（半开区间 `[start,end)`）。字幕还要去掉尾部标点。
10. **守卫必须自证能抓到问题**：加了一条判据，就用一份人为构造的违规数据跑一遍，确认它真的会红（`check_boxes.py` 已用"注脚压字幕 / 两栏重叠"验证过）。只会亮绿灯的守卫等于没有。
11. **截图可能抓到"尚未提交完"的画面**：实测同一帧重渲，一张底部多出残影（亮像素一直延到 y=1077），另一张干净。无人值守的全片渲染会静默把废帧写进成片。driver 已加**双拍校验**（同一 t 连续两次截图必须字节一致，否则重拍，并报告重拍次数）。
12. **headless 标签会周期性失效**：长跑时约每 40~100 帧标签就会无响应（`Runtime.evaluate` 超时），之后**整片后半段全部变白**——而"连续两张白图字节相同"会骗过稳定性判据。必须：① 每次 CDP 调用带超时；② 页面报告**输出签名**（`gl.readPixels` 采三个小块），亮度 >250 判为白屏；③ 失效就重启浏览器并**从当前帧续渲**；④ 每 150 帧预防性重载。实测 468 帧需要重启 7 次，全部自动恢复。
13. **别用文件体积判断白屏**：纯白帧只有 8KB，但**带颗粒的白帧有 1~3MB**，体积判据抓不到——只有输出签名可靠。
14. **响度口径**：短视频用 -16 LUFS、真峰值 ≤ -1.5 dBTP（两遍 loudnorm，第二遍带 measured_* 才是线性不失真）；第一遍必须 `-v info` 才有 JSON 摘要。

## 美术约定

- 暗底 + 三色体系：底 `#04060d→#122036`，主色 `accent #5ec8ff`，辅色 `accent2 #7cf3c0`，暖光 `#ffb347` 只作点缀。
- 字号阶梯（1080p）：大标题 176、结论 128、页标题 56、大数字 236、条目 42、小字 22~28。**中文字距 4~7px，拉丁小字 9~12px**。
- 缓动词汇：入场 `easeOutBack`、生长 `easeOutExpo`、镜头 `easeInOutQuint`、错峰 0.06~0.20s/元素。
- 每个镜头至少两层纵深：远景模糊（`ctx.filter blur`）+ 中景粒子 + 前景清晰主体。
- 每镜头结尾不要"停住"：留一点持续推进，切点才有落差。
