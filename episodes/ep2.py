# -*- coding: utf-8 -*-
"""
《AI 为什么会一本正经地胡说八道》—— 第 2 期分镜脚本

相比第 1 期，这一期按短视频钩子结构写文案：**前 3 秒直接给反直觉结论**。

视觉内容全部在场景数据里给出（title / candidates / groups / sentence / steps ...），
渲染函数只负责画，不再写死任何文案 —— 第 2 期第一版就是因为 render_hook 写死了
第 1 期的标题，导致开场画出「什么是大语言模型」。
"""

VIDEO = {
    "episode": 2,
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "title": "AI 为什么会一本正经地胡说八道",
    "series": "AI 科普 · 第 2 期",
    "voice": "zh-CN-XiaoxiaoNeural",
    # 语速 +20%：实测 +16% 时旁白合计 58.6s，成片被 min_dur 撑到 66.7s；
    # +20% 后旁白 56.7s，配合收紧的 min_dur 落到 ~62s。
    "rate": "+20%",
    "head": 0.45,
    "gap": 0.28,
    "tail": 0.90,
    "pad": 0.40,
}

SCENES = [
    {
        "id": "hook",
        "label": "01",
        "kicker": "开场",
        "title": "AI 最危险的时候，\n不是它说不知道",
        "subtitle": "而是它一本正经地编",
        # 封面文案（render_hook 直接用这两条）
        "cover_title": "AI 最危险的时候",
        # 封面副标题刻意不同于旁白：旁白已经由字幕条呈现，
        # 副标题若重复同一句，画面下方会出现两层几乎一样的文字（观感很差）。
        "cover_sub": "它不是在骗你，是真的觉得自己在说真话",
        "narration": "AI 最危险的时候，不是它说不知道，而是它一本正经地编。这六十秒，讲清它为什么这样。",
        "visual": "hook",
        "bg": "assets/image/ep2_bg_hook_20260929185814.png",
        "min_dur": 7.6,
    },
    {
        "id": "conf",
        "label": "02",
        "kicker": "关键差别",
        "title": "它觉得自己在说真话",
        "subtitle": "对它来说，编和答没有区别",
        # 两组对照：答对 vs 编造，内部置信度都很高
        "conf_groups": [
            ("它给出的答案", "光速约 30 万公里/秒", 0.94, True),
            ("同一次对话里它编的", "该书出版于 1987 年", 0.91, False),
        ],
        "conf_note": "两组置信度几乎一样：它分不出「答对」和「编造」",
        "narration": "关键在于，它编的时候，和它答对的时候，内部状态几乎一样。它不是在骗你，它是真的觉得自己在说真话。",
        "visual": "conf",
        "bg": "assets/image/ep2_bg_conf_20260929185916.png",
        "min_dur": 9.6,
    },
    {
        "id": "fluent",
        "label": "03",
        "kicker": "看起来很可信",
        "title": "因为语言太顺了",
        "subtitle": "流利，不等于正确",
        # 一句语法通顺但来源存疑的话，逐词长出来
        "sentence": "根据 1987 年那篇经典论文，这个方法在低温下效率最高。",
        "sentence_chunks": ["根据 ", "1987 年", "那篇经典论文，", "这个方法", "在低温下", "效率最高。"],
        "checks": [("语法正确", True), ("语气自然", True), ("逻辑通顺", True), ("事实正确", False)],
        "narration": "而且它说的话非常顺。语法正确，语气自然，逻辑听上去也通顺。可流利只是语言能力，跟事实对不对，是两回事。",
        "visual": "fluent",
        "bg": "assets/image/ep2_bg_fluent_20260929185916.png",
        "min_dur": 9.9,
    },
    {
        "id": "gap",
        "label": "04",
        "kicker": "为什么会这样",
        "title": "它只被训练过一件事",
        "subtitle": "预测下一个词，不是查证事实",
        "gap_note": "从这里开始分叉",
        "narration": "为什么会这样？因为它从头到尾只被训练过一件事：预测下一个词。它从来没被训练过判断这句话是不是真的。",
        "visual": "gap",
        "bg": "assets/image/ep2_bg_gap_20260929185916.png",
        "min_dur": 9.4,
    },
    {
        "id": "reason",
        "label": "05",
        "kicker": "真正的原因",
        "title": "它没有「不知道」这个选项",
        "subtitle": "每个位置都必须给出一个词",
        "reason_note": "没有依据时，它不会停 —— 它会挑最顺的那个词，把话接下去",
        "narration": "更麻烦的是，它没有不知道这个选项。每一个位置，它都必须给出一个词。于是当它没有依据时，它不会停，它会用最合理的词，把话接下去。",
        "visual": "reason",
        "bg": "assets/image/ep2_bg_reason_20260929185916.png",
        "min_dur": 11.0,
    },
    {
        "id": "outro",
        "label": "06",
        "kicker": "一句话总结",
        "title": "它最擅长的事，\n恰恰是编得像真的",
        "subtitle": "所以关键结论，永远自己再查一遍",
        "narration": "所以让它胡说八道的，正是它最擅长的能力。它不是在撒谎，只是太擅长把话说顺。记住：关键结论，永远自己再查一遍。",
        "visual": "outro",
        "bg": "assets/image/ep2_bg_outro_20260929185916.png",
        "min_dur": 9.4,
    },
]
