# -*- coding: utf-8 -*-
"""
期数分发：`script.py` 始终指向"当前期"，管线通过 `--ep=N` 选择具体一期。

这样新增一期只要在 episodes/ 下加一个文件，不需要改任何管线代码。
第 1 期已归档为 episodes/ep1.py。
"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EPISODES = ROOT / "episodes"

CURRENT_EP = 2


def parse_ep(argv=None):
    """从命令行取 --ep=N；缺省用 CURRENT_EP"""
    argv = sys.argv if argv is None else argv
    for a in argv[1:]:
        if a.startswith("--ep="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                sys.exit(f"--ep 需要整数，收到: {a}")
    return CURRENT_EP


def available():
    return sorted(int(p.stem[2:]) for p in EPISODES.glob("ep*.py"))


def load(ep=None):
    """载入指定期的 (VIDEO, SCENES)"""
    ep = parse_ep() if ep is None else ep
    mod = f"episodes.ep{ep}"
    sys.path.insert(0, str(ROOT))
    try:
        m = importlib.import_module(mod)
    except ModuleNotFoundError:
        sys.exit(f"找不到 {mod}；可用期数: {available()}")
    return m.VIDEO, m.SCENES


def audio_dir(ep):
    """每期独立存放配音，避免换期后误用旧音频"""
    return ROOT / "build" / f"audio_ep{ep}"


def timeline_path(ep):
    return ROOT / "build" / f"timeline_ep{ep}.json"


def video_path(ep, vertical=False):
    VIDEO, _ = load(ep)
    suffix = "-竖屏" if vertical else ""
    return ROOT / "output" / f"AI科普-{VIDEO['title']}{suffix}.mp4"
