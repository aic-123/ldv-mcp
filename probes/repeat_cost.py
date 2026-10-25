"""重复任务 / 历史检索：token 成本的另一种形状。

跑法（需要 tiktoken）：
    C:/Users/19253/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe probes/repeat_cost.py

## 为什么单独测（上一组数字的软肋）

`token_cost.py` 每个问题只问一次 ⇒ 只测到「首次取回」的成本。
真实使用里有两种完全不同的形状：

    A 会话内重复   同一个问题问 5 次
                   WITHOUT：窗口常驻 **可以命中 prompt cache**（前缀不变）⇒ 边际接近 0
                   WITH：每次都要重新取回（取回的结果不进缓存，因为是新一轮工具调用）

    B 历史检索     问一条**很久以前**的笔记（在窗口外）
                   WITHOUT：**答不了**（窗口里没有，压缩摘要也没数值）
                   WITH：成本与「多久以前」无关

    C 跨会话重复   新开一次对话，再问同一件事
                   WITHOUT：**从零开始**（什么都没存）
                   WITH：库还在，取回成本不变

⇒ 三种形状对应三种结论，不能用一个平均数概括。
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY = sys.executable
WINDOW_NOTES = 12
N_NOTES = 140
REPEAT = 5

import tiktoken                                    # noqa: E402

ENC = tiktoken.get_encoding("cl100k_base")


def tok(s: str) -> int:
    return len(ENC.encode(s))


class MCP:
    def __init__(self, store: Path):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LDV_MCP_STORE"] = str(store)
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=env, cwd=str(HERE))
        self.n = 0
        self.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "rep", "version": "0"}})

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        # ★ 探针验的是**结构**（支序 / 答案集 / 计数）⇒ 统一要 structure=True。
        #   2026-10-24 起 ask 默认只交正文 + 句柄（id 留在本地）。
        #   ⚠️ 2026-10-25 注：本探针的 WITH 读数是**含结构列**（它要读 answer_ids），
        #   与 token_cost 的「默认列」不同口径 —— 比绝对值前先对齐列。
        if name == "ask":
            args = {"structure": True, **args}
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        return "".join(i.get("text", "") for i in (res.get("content") or []))

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


PROJS = ["alpha", "beta", "gamma"]
KINDS = ["实验", "决定", "坑", "笔记"]
TEMPLATES = [
    "lr={v1}，第 {v2} 小时 loss 尖峰 {v3}，之后回落到 {v4}",
    "组 {v1} baseline {v2}，改动后 {v3}（+{v4}%）",
    "复跑 {v1} 次，{v2} 次复现；差异在 batch={v3} 那一档",
    "把 clip 上界从 {v1} 提到 {v2}，梯度范数从 {v3} 涨到 {v4}",
    "把温度从 {v1} 降到 {v2}，熵从 {v3} 掉到 {v4}",
]


def make_notes(n: int, seed: int = 11):
    r = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        tpl = TEMPLATES[i % len(TEMPLATES)]
        vals = [f"{r.uniform(0.1, 9.9):.2f}" if k % 2 else str(r.randint(2, 99))
                for k in range(4)]
        out.append((f"n{i:04d}", PROJS[i % len(PROJS)], KINDS[i % len(KINDS)],
                    tpl.format(v1=vals[0], v2=vals[1], v3=vals[2], v4=vals[3])))
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ldv-rep-"))
    store = tmp / "store"
    notes = make_notes(N_NOTES)
    nodes = store / "_data" / "c" / "nodes"
    nodes.mkdir(parents=True)
    for nid, proj, kind, body in notes:
        (nodes / f"{nid}.md").write_text(
            f'---\nid: "{nid}"\ntype: {kind}\nproject: {proj}\n---\n\n{body}\n',
            encoding="utf-8")

    c = MCP(store)
    try:
        # derive project（一次性）
        rd = c.tool("ask", {"corpus": "c", "require": [], "with_body": True,
                            "limit": N_NOTES + 10})
        d0 = json.loads(rd)
        # ⚠️ bodies 已并入 items；id 走 answer_ids 与 items 按位置 zip
        #   （少了这一步 values 是空的 ⇒ derive 没建成 ⇒ project= 全是未知键）
        vals = {nid: (it.get("fields") or {}).get("project")
                for nid, it in zip(d0.get("answer_ids") or [],
                                   d0.get("items") or [])}
        vals = {k: v for k, v in vals.items() if v}
        c.tool("derive", {"corpus": "c", "name": "project", "values": vals,
                          "rationale": "按项目看", "derived_from": "材料字段 project"})

        window = [n[3] for n in notes[-WINDOW_NOTES:]]
        win_tok = tok("\n".join(window))

        print("=" * 78)
        print(f"重复 / 历史检索｜{N_NOTES} 条笔记，窗口 {WINDOW_NOTES} 条（{win_tok} tokens）")
        print("=" * 78)

        # ── A 会话内重复：同一个问题问 5 次 ────────────────────────
        Q = "alpha 的坑有哪些"
        req = ["project=alpha", "type=坑"]
        per = []
        for _ in range(REPEAT):
            res = c.tool("ask", {"corpus": "c", "require": req,
                                 "with_body": True, "limit": 10})
            d = json.loads(res)
            per.append((tok(res) + tok(Q), d.get("n_answer") or 0))
        w_once = per[0][0]
        print(f"\nA 会话内重复（同一问题问 {REPEAT} 次）")
        print(f"   WITH     每次重取：{w_once} × {REPEAT} = {w_once * REPEAT}"
              f"（答案 {per[0][1]} 项）")
        print(f"   WITH     **答案留在上下文**（第 2-{REPEAT} 次不再取回）：{w_once}")
        print(f"   WITHOUT  无缓存：{win_tok} × {REPEAT} = {win_tok * REPEAT}")
        print(f"   WITHOUT  **有 prompt cache**（前缀不变）："
              f"{win_tok} + 0 × {REPEAT - 1} = {win_tok}")
        print(f"   ⇒ 最省的两种：WITH {w_once} vs WITHOUT {win_tok}"
              f" ⇒ **{'上下文赢' if win_tok < w_once else '插件赢'}**"
              f"（取回量 {w_once} {'>' if w_once > win_tok else '<'} 窗口 {win_tok}）")
        print(f"   ⚠️ 关键条件：**取回量 > 窗口量时，重复多少次都救不了插件** ——"
              f" 因为每一问的取回都比整个窗口还大。")

        # ── B 历史检索：问一条很久以前的笔记（窗口外）──────────────
        old = notes[0]                       # 第 1 条，早就在窗口外
        q_old = f"{old[0]} 那条记的是什么"
        res = c.tool("ask", {"corpus": "c", "require": ["type=决定"],
                             "with_body": True, "limit": 10})
        # 直接用 id 更贴近「按记录号找」：用 type 过滤后确认它在结果里
        d = json.loads(res)
        ids = [nid for nid, _it in zip(d.get("answer_ids") or [],
                                       d.get("items") or [])]
        found = old[0] in ids
        print(f"\nB 历史检索（问第 1 条 {old[0]}，它在窗口外）")
        print(f"   WITH     {tok(res) + tok(q_old)} tokens｜找到了：{found}"
              f"（成本与「多久以前」无关）")
        print(f"   WITHOUT  **答不了**（窗口里没有，摘要也没有数值）"
              f"⇒ 成本再低也没意义")

        # ── C 跨会话重复：新进程，再问同一件事 ────────────────────
        c.close()
        c2 = MCP(store)
        res2 = c2.tool("ask", {"corpus": "c", "require": req,
                               "with_body": True, "limit": 10})
        d2 = json.loads(res2)
        print(f"\nC 跨会话重复（新开一次对话，问同一个问题）")
        print(f"   WITH     {tok(res2) + tok(Q)} tokens｜答案 {d2.get('n_answer')} 项"
              f"（库还在，成本不变）")
        print(f"   WITHOUT  **从零开始** —— 什么都没存，要人重新贴一遍")

        print(f"\n=== 结论 ===")
        print("   三种形状，三种答案：")
        print("     A 会话内重复   上下文 + prompt cache 赢（边际接近 0）")
        print("     B 历史检索     插件是**唯一能做**的，成本与时间无关")
        print("     C 跨会话       插件是**唯一能做**的")
        print("   ⇒ 不是谁替代谁，是分工：近期重复用上下文，久远/跨会话用插件。")
    finally:
        try:
            c.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
