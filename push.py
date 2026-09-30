#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键推送工具套件到 GitHub。

为什么需要这个脚本（而不是直接 git push）：
  1) 本机 git 全局配置了 socks5://127.0.0.1:7897 代理，但代理软件经常没开。
     一旦没开，git push 会卡住并报 "Failed to connect to 127.0.0.1"。
     这里先探测代理端口，不通就自动绕过，不改动全局配置。
  2) 推送前要做安全检查：本机绝对路径、账号 ID、令牌不能进公开仓库。
     手工检查容易漏（实测漏过一次硬编码的 F:\\ 绝对路径）。
  3) 顺手提醒未提交的改动，避免"以为推了其实没提"。

用法：
  python push.py                 # 检查 + 提交全部改动 + 推送
  python push.py -m "改了什么"    # 指定提交信息
  python push.py --check         # 只做检查，不提交不推送
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 不该出现在公开仓库里的模式
FORBIDDEN = [
    (r"F:\\deepseek", "本机绝对路径 F:\\deepseek"),
    (r"C:\\Users\\[^\\]+", "本机用户目录路径"),
    (r"gho_[A-Za-z0-9]{20,}", "GitHub OAuth token"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub personal access token"),
    (r"sk-[A-Za-z0-9]{20,}", "API key"),
    (r'"user_id"\s*:\s*"\S{8,}"', "Maker 账号 user_id"),
]
# ★ 只匹配键名会误报：这个项目里 "token" 是 NLP 的词元（"token": render_token），
#   不是密钥。所以对"键值型"敏感字段必须看**值**长什么样 ——
#   真正的密钥通常是一串足够长的高熵字符。
SECRET_KEY = re.compile(
    r'"?(token|secret|password|api_?key|pat)"?\s*[:=]\s*["\']?([A-Za-z0-9_\-\.]{24,})["\']?',
    re.I)
# 值里含这些词的，明显是代码里的标识符而非密钥
SECRET_KEY_SAFE = ("render_", "tokenize", "make_", "def ", "import ", "self.")

# 允许出现在说明文档里的词（只报路径与密钥，不报单纯提及）
TEXT_EXT = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg"}


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def git(*args, proxy_off=False):
    cmd = ["git"]
    if proxy_off:
        cmd += ["-c", "http.proxy=", "-c", "https.proxy="]
    cmd += list(args)
    return run(cmd)


def proxy_alive(port=7897):
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def scan():
    """扫描将被跟踪的文件，找不该公开的内容"""
    files = git("ls-files").stdout.split()
    # 也扫描尚未提交的新文件
    untracked = git("ls-files", "--others", "--exclude-standard").stdout.split()
    problems = []
    for rel in sorted(set(files) | set(untracked)):
        p = ROOT / rel
        if not p.exists() or p.suffix.lower() not in TEXT_EXT:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat, why in FORBIDDEN:
            for m in re.finditer(pat, text):
                ln = text[:m.start()].count("\n") + 1
                problems.append((rel, ln, why, m.group(0)[:40]))
        # 键值型密钥：必须看值（见 SECRET_KEY 的注释说明为何不能只看键名）
        for m in SECRET_KEY.finditer(text):
            val = m.group(2)
            if any(s in text[max(0, m.start() - 40):m.end() + 10] for s in SECRET_KEY_SAFE):
                continue
            # 高熵判断：字符种类足够多才算密钥（纯小写单词是标识符）
            kinds = sum(bool(re.search(p, val)) for p in
                        (r"[a-z]", r"[A-Z]", r"\d", r"[_\-\.]"))
            if kinds >= 3 and len(val) >= 28:
                ln = text[:m.start()].count("\n") + 1
                problems.append((rel, ln, "疑似密钥", f"{m.group(1)}={val[:12]}…"))
    return problems, len(set(files) | set(untracked))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message", default=None)
    ap.add_argument("--check", action="store_true", help="只检查，不提交不推送")
    a = ap.parse_args()

    print("=" * 70)
    print("推送前检查")
    print("=" * 70)

    # ---- 1) 敏感内容
    problems, n = scan()
    print(f"\n[1] 敏感内容扫描（{n} 个文件）")
    if problems:
        for rel, ln, why, sample in problems[:30]:
            print(f"    ✗ {rel}:{ln}  {why}  -> {sample}")
        print(f"\n    共 {len(problems)} 处。请先清理再推送。")
        if not a.check:
            sys.exit("已中止推送")
    else:
        print("    ✓ 未发现本机路径 / 令牌 / 账号 ID")

    # ---- 2) 不该入库的路径
    print("\n[2] 关键路径排除检查")
    for must_ignore, why in ((".maker-mcp/config.json", "含 project_id/user_id"),
                             ("build/timeline_ep2.json", "中间产物"),
                             ("output/", "成片体积大")):
        r = git("check-ignore", "-q", must_ignore)
        ok = r.returncode == 0
        print(f"    {'✓' if ok else '✗'} {must_ignore}  ({why})")

    # ---- 3) 工作区状态
    print("\n[3] 工作区状态")
    st = git("status", "--short").stdout.strip()
    if st:
        n_ch = len([l for l in st.split("\n") if l.strip()])
        print(f"    有 {n_ch} 项未提交改动")
    else:
        print("    ✓ 工作区干净")

    if a.check:
        print("\n（--check 模式，不提交不推送）")
        return 0

    # ---- 4) 提交
    if st:
        print("\n[4] 提交")
        git("add", "-A", "-c", "core.autocrlf=false")
        git("add", "-A")
        msg = a.message or "更新：工具套件同步"
        r = git("-c", "core.autocrlf=false", "commit", "-q", "-m", msg)
        if r.returncode != 0:
            print("    提交失败：", (r.stdout + r.stderr).strip()[:300])
            return 1
        print(f"    ✓ 已提交：{msg}")
    else:
        print("\n[4] 无改动，跳过提交")

    # ---- 5) 推送（自动处理代理）
    print("\n[5] 推送")
    off = not proxy_alive(7897)
    if off:
        print("    代理 127.0.0.1:7897 未监听 -> 本次绕过代理（不改全局配置）")
    r = git("push", "origin", "HEAD", proxy_off=off)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        print("    推送失败：")
        for line in out.split("\n")[-8:]:
            print("      " + line)
        return 1
    for line in out.split("\n")[-4:]:
        if line.strip():
            print("    " + line.strip())

    # ---- 6) 结果
    url = git("remote", "get-url", "origin").stdout.strip()
    url = re.sub(r"\.git$", "", url).replace("https://", "https://")
    print(f"\n完成 -> {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
