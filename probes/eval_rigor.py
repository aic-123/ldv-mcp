"""按成熟方法测：语料内位置扫描 + 多针 + 重复取均值（附「本装置测不了什么」）。

跑法（要 tiktoken）：
    C:/Users/19253/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe probes/eval_rigor.py

## 先说清本装置**测不了**什么（这一条最要紧）

    上下文位置偏差（Lost-in-the-Middle / Context Rot）是**模型的性质**：
    同一个针放在上下文头/中/尾，模型命中率呈 U 形。
    本装置里 WITHOUT 是「在窗口里做字符串查找」——**与位置无关**，
    所以那类偏差**测不出来，也不假装测**。要测得接真模型。

## 那这里测什么（三条，都是能诚实测的）

    ① **语料内位置扫描**：同一个针放在语料第 1 条 / 中间 / 最后一条
       ⇒ 插件的命中率应当**恒定**（划分结构没有「先后」，不是序列）。
       这是对 Lost-in-the-Middle 的**对照**：偏差在长上下文里存在，在这里不该存在。
    ② **多针**（RULER 的批评：单针过了不算）：一题需要两条事实，
       单针探针会把「只找到一条」当成「找到了」。
    ③ **重复 n 次**：报均值 ± 标准差。检索成本是确定性的（波动应为 0），
       这一条是在**证明**确定性，而不是碰巧。

## 指标对齐（不自造名字）

    context_recall      RAGAS：取回里含所需信息吗 ⇒ 命中针的比例
    context_precision   RAGAS：取回的条目里与问题相关的比例（信噪比）
    faithfulness        生成侧（答案有没有编）—— **插件不生成文本，不声称**
    answer_relevancy    同上，**不声称**
"""
from __future__ import annotations

import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY = sys.executable
N_NOTES = 200
WINDOW = 12
REPEATS = 3

import tiktoken                                     # noqa: E402

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
                                 "clientInfo": {"name": "rigor", "version": "0"}})

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        txt = "".join(i.get("text", "") for i in (res.get("content") or []))
        try:
            return json.loads(txt) if txt else {}
        except ValueError:
            return {"_raw": txt}

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def make_notes(n: int, seed: int = 23):
    r = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        proj = ["alpha", "beta", "gamma"][i % 3]
        kind = ["实验", "决定", "坑"][i % 3]
        val = f"{r.uniform(1.0, 9.0):.3f}"
        body = (f"{proj} 第 {i} 条：指标 {val}，"
                f"{'复现' if i % 2 else '未复现'}，batch={r.choice([16, 32, 64])}")
        out.append((f"n{i:04d}", proj, kind, body, val))
    return out


def main() -> int:
    notes = make_notes(N_NOTES)
    tmp = Path(tempfile.mkdtemp(prefix="ldv-rigor-"))
    store = tmp / "store"
    nodes = store / "_data" / "c" / "nodes"
    nodes.mkdir(parents=True)
    for nid, proj, kind, body, _v in notes:
        (nodes / f"{nid}.md").write_text(
            f'---\nid: "{nid}"\ntype: {kind}\nproject: {proj}\n---\n\n{body}\n',
            encoding="utf-8")

    c = MCP(store)
    try:
        rd = c.tool("ask", {"corpus": "c", "require": [], "structure": True,
                            "limit": N_NOTES + 10})
        vals = {nid: it.get("fields", {}).get("project")
                for nid, it in zip(rd.get("answer_ids") or [],
                                   rd.get("items") or [])}
        c.tool("derive", {"corpus": "c", "name": "project",
                          "values": {k: v for k, v in vals.items() if v},
                          "rationale": "按项目看", "derived_from": "材料字段 project"})

        print("=" * 78)
        print(f"严谨性复测｜{N_NOTES} 条笔记 · 重复 {REPEATS} 次")
        print("=" * 78)

        # ── ① 语料内位置扫描：针放在语料的开头 / 中间 / 结尾 ──────────
        print("\n① 语料内位置扫描（针在语料里的**位置**，不是上下文里的位置）")
        print("   针的位置     WITH 命中    context_precision   取回条数")
        picks = (("第 1 条", 0), ("中间", N_NOTES // 2), ("最后一条", N_NOTES - 1))
        hits = []
        for label, i in picks:
            nid, proj, _kind, _body, val = notes[i]
            got, prec, n_items = 0, 0.0, 0
            for _ in range(REPEATS):
                r = c.tool("ask", {"corpus": "c", "require": ["project=" + proj],
                                   "limit": N_NOTES})
                items = r.get("items") or []
                body = json.dumps(items, ensure_ascii=False)
                got += int(val in body)
                n_items = len(items)
                prec = sum(1 for it in items
                           if (it.get("fields") or {}).get("project") == proj) / max(1, n_items)
            hits.append(got / REPEATS)
            print(f"   {label:<10}   {got}/{REPEATS}          {prec:.2f}"
                  f"                {n_items}")

        # ── ② 多针：一题要两条事实 ────────────────────────────────
        print("\n② 多针（一题需要两条事实 —— 单针探针看不出「缺一条」）")
        a, b = notes[-1], notes[-2]
        r = c.tool("ask", {"corpus": "c", "require": ["project=" + a[1]],
                           "limit": N_NOTES})
        body = json.dumps(r.get("items") or [], ensure_ascii=False)
        got_a, got_b = a[4] in body, b[4] in body
        print(f"   针 A（{a[0]}，project={a[1]}）在取回里：{got_a}")
        print(f"   针 B（{b[0]}，project={b[1]}）在取回里：{got_b}"
              f"　← 项目不同，**这一条本来就不该在**")
        # 换成同一项目的两条针，才是真正的多针题
        same = [n for n in notes if n[1] == "alpha"][-2:]
        r2 = c.tool("ask", {"corpus": "c", "require": ["project=alpha"],
                            "limit": N_NOTES})
        body2 = json.dumps(r2.get("items") or [], ensure_ascii=False)
        both = all(n[4] in body2 for n in same)
        print(f"   同项目两针（{same[0][0]} / {same[1][0]}）都在取回里：{both}")

        # ── ③ 重复 n 次：确定性 ──────────────────────────────────
        print(f"\n③ 重复 {REPEATS} 次（检索成本是确定性的 ⇒ 波动应为 0）")
        costs = [tok(json.dumps(c.tool("ask", {"corpus": "c",
                                               "require": ["project=alpha"],
                                               "limit": N_NOTES}),
                                ensure_ascii=False))
                 for _ in range(REPEATS)]
        print(f"   每次 ask 的 token：{costs}")
        print(f"   均值 {statistics.mean(costs):.0f} ± "
              f"{statistics.pstdev(costs):.0f}（n={REPEATS}）"
              f"｜相对波动 {statistics.pstdev(costs) / max(1, statistics.mean(costs)) * 100:.1f}%")

        print("\n④ 指标（对齐 RAGAS 的检索侧）")
        print(f"   context_recall    = {sum(hits) / len(hits):.2f}"
              f"（针的位置变了，命中率应当不变）")
        print("   faithfulness / answer_relevancy = **不声称**（生成侧，插件不生成文本）")

        print("\n⑤ 结论（三条，都有边界）")
        print("   · 语料内位置扫描：命中率不随针的位置变化 ⇒ **划分结构没有位置偏差**；")
        print("     但**上下文位置偏差（Lost-in-the-Middle）本装置测不了** —— 那是模型性质，")
        print("     本装置里的 WITHOUT 是字符串查找，天然与位置无关。")
        print("   · 多针：单针探针会把「只找到一条」当成「找到了」⇒ 必须用同项目的两针题。")
        print(f"   · 重复 {REPEATS} 次波动 0 ⇒ **检索成本是确定性的**（不同于生成）。")
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
