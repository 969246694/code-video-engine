# -*- coding: utf-8 -*-
"""
《什么是大语言模型》科普短片 —— 分镜脚本（单一事实来源）
改文案只需改这里，然后重新运行 build.py 即可。

每个场景:
  id       : 唯一标识
  label    : 画面右上角的章节序号
  kicker   : 小标签（标题上方）
  title    : 主标题（\n 换行）
  subtitle : 副标题（可为空）
  narration: 旁白文案（edge-tts 朗读，同时用于生成字幕）
  visual   : 视觉类型，对应 render.py 中的绘制函数
  min_dur  : 该场景最短时长（秒），最终取 max(旁白时长+pad, min_dur)
  pad      : 该场景旁白结束后的留白（秒）
"""

VIDEO = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "title": "什么是大语言模型",
    "series": "AI 科普 · 第 1 期",
    "voice": "zh-CN-XiaoxiaoNeural",   # 晓晓：通用播音女声，温暖自然
    "rate": "+16%",     # 语速；晓晓比云希慢，+16% 时成片 60.55s（多数场景由 min_dur 主导）
    # 语速-时长实测：+8%→63s  +14%→61.1s  +16%→60.73s  +18%→60.55s  +20%→60.55s
    # 注意：+18% 以上总长不再变化（各场景都被 min_dur 下限撑住），故取 +16% 更从容
    "head": 0.45,       # 片头起始留白
    "gap": 0.28,        # 场景之间的黑场间隔
    "tail": 0.90,       # 片尾定格
    "pad": 0.40,        # 默认：旁白结束后停留
}

SCENES = [
    {
        "id": "hook",
        "label": "01",
        "kicker": "开场",
        "title": "AI 是怎么「说话」的？",
        "subtitle": "拆开大语言模型的黑盒",
        "narration": "你有没有想过，那些能写文章、回答问题的 AI，是怎么学会说话的？今天我们把它拆开看看。",
        "visual": "hook",
        "min_dur": 7.6,
    },
    {
        "id": "predict",
        "label": "02",
        "kicker": "核心机制",
        "title": "本质只有一件事：\n预测下一个词",
        "subtitle": "Next Token Prediction",
        "narration": "答案很朴素：它只做一件事，预测下一个词。你给它半句话，它就算出所有可能的候选，以及每个词的概率。",
        "visual": "predict",
        "min_dur": 9.2,
    },
    {
        "id": "token",
        "label": "03",
        "kicker": "第一步",
        "title": "先切词：Token 化",
        "subtitle": "文字被切成模型能看懂的最小单位",
        "narration": "第一步是切词，把文字切成一个个小块，叫作令牌。中文常常一两个字一块，英文可能半个单词一块。",
        "visual": "token",
        "min_dur": 9.6,
    },
    {
        "id": "context",
        "label": "04",
        "kicker": "关键一步",
        "title": "再看上下文：注意力机制",
        "subtitle": "Attention — 决定哪个词更重要",
        "narration": "第二步是看上下文。模型给前面每个词分配关注度，越相关的词权重越高，所以它知道这里该接的是喝，而不是吃。",
        "visual": "attention",
        "min_dur": 10.4,
    },
    {
        "id": "train",
        "label": "05",
        "kicker": "它怎么学会的",
        "title": "训练：书读百遍，其义自见",
        "subtitle": "预训练 → 微调 → 人类反馈对齐",
        "narration": "这套能力不是写死的，是练出来的。模型读遍海量文本，每次猜错就调整内部参数，循环亿万次，再经人类反馈打磨，才变得既聪明又听话。",
        "visual": "train",
        "min_dur": 12.6,
    },
    {
        "id": "outro",
        "label": "06",
        "kicker": "一句话总结",
        "title": "它不是懂语言，\n它是在算概率",
        "subtitle": "但足够大的概率，就成了智能",
        "narration": "所以它并不是真的懂语言，它只是把概率算到了极致。而足够大的概率，看起来就像智能。",
        "visual": "outro",
        "min_dur": 8.4,
    },
]
