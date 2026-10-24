"""真模型评测：位置偏差（NIAH）+ 有/无插件的命中与编造率。

跑法：
    DEEPSEEK_API_KEY=... python probes/real_model.py
    （或把 key 放在仓库外 ~/.workbuddy-ai/secrets/deepseek.key，脚本自己去读）

## ★ 密钥纪律（2026-10-24）

    **绝不写进仓库**：key 只从环境变量或 `~/.workbuddy-ai/secrets/deepseek.key` 读；
    本文件不含任何 key；两个仓库的 `.gitignore` 都挡了 `*.key` / `.env` / `secrets/`；
    `smoke_test.py` 里还有一条 `sk-` 扫描（扫**已跟踪文件**，命中就红）。

## 这是唯一必须接真模型的一族

前面所有探针测的都是**检索侧**（给了什么、花了多少）。这三件事**测不了**，因为它们是模型的性质：

    ① 位置偏差（Lost-in-the-Middle）：同一个针放在上下文头/中/尾，命中率呈 U 形
    ② 编造（faithfulness 的反面）：答案里的数字有没有出现在给定上下文里
    ③ 有/无插件的实际差距：同一问题，给窗口 vs 给取回结果

## 指标（对齐 RAGAS，不自造）

    faithfulness（近似）  答案里的数字**全都**来自给定上下文 ⇒ 不编造
                          （真 RAGAS 用 LLM 判，这里用数字比对做下界近似 —— **说清楚**）
    context_recall        答案里是否含针的精确值
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SECRET = Path.home() / ".workbuddy-ai" / "secrets" / "deepseek.key"
API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
REPEATS = 2


def load_key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    if SECRET.is_file():
        return SECRET.read_text(encoding="utf-8").strip()
    return ""


def ask_model(key: str, system: str, user: str) -> tuple[str, int]:
    body = json.dumps({"model": MODEL, "temperature": 0,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=90) as r:
        doc = json.loads(r.read().decode("utf-8"))
    txt = doc["choices"][0]["message"]["content"]
    used = (doc.get("usage") or {}).get("total_tokens", 0)
    return txt, used


def nums(s: str) -> list[str]:
    return re.findall(r"\d+\.\d+|\d+", s)


def main() -> int:
    key = load_key()
    if not key:
        print("⊘ 没有 key（设 DEEPSEEK_API_KEY，或放到 ~/.workbuddy-ai/secrets/deepseek.key）")
        print("   —— 跳过 ≠ 通过")
        return 0
    print(f"key 已读入（{len(key)} 字符，只用于本次请求，不落盘）")

    # ★ 造 12 条笔记，**跳过第 7 条** —— 针要插进去当第 7 条。
    #   ⚠️ 第一版没跳过 ⇒ haystack 里本来就有一条「第 7 条（7.07）」，
    #      我又插一条「第 7 条（7.77）」⇒ 两条都自称第 7 条。
    #      模型答 7.07 是**对的**（先出现的那个），却被我读成「位置偏差」。
    #      ⇒ 探针自己错的时候，比被测对象错更危险。
    notes = []
    for i in range(1, 13):
        if i == 7:
            continue
        notes.append(f"{['alpha','beta','gamma'][i%3]} 第 {i} 条：指标 {i}.{i:02d}，"
                     f"batch={16*(1+i%3)}")
    NEEDLE_VAL = "7.77"
    needle = f"alpha 第 7 条：指标 {NEEDLE_VAL}，batch=64"
    Q = "第 7 条笔记的指标是多少？只回一个数字。"

    print("\n" + "=" * 76)
    print(f"真模型评测｜{MODEL}｜位置扫描 3 档 × {REPEATS} 次")
    print("=" * 76)

    # ── ① 位置扫描：针放在窗口的头 / 中 / 尾 ──────────────────────
    print("\n① 位置扫描（Lost-in-the-Middle：同一个针，位置不同）")
    print("   位置   命中   答案")
    total_tok = 0
    n_calls = 0
    # ⚠️ 用 len(notes) 算，别写死 —— 跳掉第 7 条之后只剩 11 条（写死 11 会越界）
    for label, idx in (("头", 0), ("中", len(notes) // 2), ("尾", len(notes) - 1)):
        win = list(notes)
        win[idx] = needle
        ctx = "\n".join(win)
        hit, outs = 0, []
        for _ in range(REPEATS):
            try:
                txt, used = ask_model(
                    key, "你只能依据下面的笔记回答；找不到就说「没有」。",
                    f"笔记：\n{ctx}\n\n问题：{Q}")
                total_tok += used
                n_calls += 1
            except (urllib.error.URLError, KeyError, TimeoutError) as e:
                print(f"   请求失败：{type(e).__name__}: {str(e)[:60]}")
                return 1
            hit += int(NEEDLE_VAL in txt)
            outs.append(txt.strip().replace("\n", " ")[:34])
        print(f"   {label}     {hit}/{REPEATS}   {outs[0]}")

    # ── ② 编造率：答案里的数字有没有出现在上下文里 ────────────────
    print("\n② 编造率（faithfulness 的下界近似：答案里的数字必须来自上下文）")
    # ★ 这里必须**带针**：不带针的 haystack 里根本没有 7.77，
    #   模型答 7.07 反而是对的 —— 那样测出来的「编造 0」是假的。
    ctx = "\n".join(notes[:6] + [needle] + notes[6:])
    try:
        txt, used = ask_model(
            key, "你只能依据下面的笔记回答；没有就说「没有」。",
            f"笔记：\n{ctx}\n\n问题：第 7 条笔记的指标和 batch 分别是多少？")
        total_tok += used
        n_calls += 1
    except (urllib.error.URLError, KeyError, TimeoutError) as e:
        print(f"   请求失败：{type(e).__name__}: {str(e)[:60]}")
        return 1
    got = nums(txt)
    fabricated = [n for n in got if n not in ctx]
    print(f"   答案：{txt.strip()[:60]}")
    print(f"   答案里的数字 {got}｜不在上下文里的：{fabricated or '无'}"
          f"（针 7.77 在上下文里：{NEEDLE_VAL in ctx}）")
    print(f"   ⇒ 编造 {len(fabricated)} 个"
          f"（这是**下界**：真 RAGAS 用 LLM 判语义，这里只比数字）")

    # ── ③ 长度扫描：位置偏差只在**长上下文**里才出现 ──────────────
    # 12 条那一档三处位置全中 ⇒ 说明「12 条太短，测不出偏差」。
    # 要复现 context rot / Lost-in-the-Middle，必须把长度拉上去。
    print("\n③ 长度扫描（12 / 60 / 200 条 × 头/中/尾，各 2 次）")
    print("   条数    头    中    尾    上下文约 tokens")
    for n in (12, 60, 200):
        row = []
        for label, idx in (("头", 0), ("中", n // 2), ("尾", n - 1)):
            # ⚠️ filler 里**也要跳过 7** —— 否则又出现第二个自称「第 7 条」的东西，
            #   模型答它是对的，却被读成「位置偏差」。同一个坑踩两次了。
            hay = [f"filler 第 {i} 条：指标 {i % 97}.{i % 89:02d}，batch={16 * (1 + i % 3)}"
                   for i in range(n) if i != 7]
            while len(hay) < n:
                hay.append(f"filler 第 {len(hay) + 100} 条：指标 1.11，batch=32")
            hay[idx] = needle
            ctx2 = "\n".join(hay)
            hit = 0
            for _ in range(REPEATS):
                try:
                    txt, used = ask_model(
                        key, "你只能依据下面的笔记回答；找不到就说「没有」。",
                        f"笔记：\n{ctx2}\n\n问题：{Q}")
                    total_tok += used
                    n_calls += 1
                except (urllib.error.URLError, KeyError, TimeoutError) as e:
                    print(f"   {n} 条 {label} 请求失败：{type(e).__name__}")
                    return 1
                hit += int(NEEDLE_VAL in txt)
            row.append(f"{hit}/{REPEATS}")
        print(f"   {n:>4}    {row[0]:>4}  {row[1]:>4}  {row[2]:>4}    ~{n * 12}")

    print("\n④ 成本")
    # ⚠️ 次数必须**数出来**，不能写死：写死过两次（先 15 后 7，实际 25）
    print(f"   本次共 {total_tok} tokens（{n_calls} 次调用，"
          f"均值 {total_tok / max(1, n_calls):.0f}/次）")
    print("\n④ 说明")
    print("   · 位置偏差要**看三档的差**：只有「中」明显低于两端才算复现 Lost-in-the-Middle")
    print(f"   · {REPEATS} 次太少，只能看方向，不能当统计结论")
    print("   · 编造率这里只比数字，是下界；语义层面的编造要 LLM 判")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
