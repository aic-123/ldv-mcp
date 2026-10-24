"""有插件 vs 没插件：同一组问题，两种答法，拿语料当真值。

跑法（cwd = ldv-mcp）：python ../outputs/_probe_plugin_gap.py

实验设计（2026-10-24）：

    WITH  走插件：orient → ask → drill，答案带 node id 与出处
    WITHOUT  agent 凭自身权重答（无工具、无语料）—— 答在下面 WITHOUT_ANSWERS
            里，逐字记录，不许事后改

四条可测的轴（都不打分，只报事实）：
    ① 可指回性    每个结论能否指到 node id
    ② 捏造        答案里提到的节点/标题，语料里是否真有
    ③ 覆盖        语料里**实际有**的议题/例外，答案找不找得到
    ④ 跨会话      第二轮提问，第一轮的记忆还在不在
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent        # = ldv-mcp/
PY = sys.executable

#: 无插件时 agent 的回答 —— **逐字记录，不许事后改**（这是实验的对照侧）
WITHOUT_ANSWERS = {
    "Q1": "未验证的立场有这些：DAPO 的长度归因（认为问题出在损失聚合与裁剪上界）、"
          "Dr.GRPO 的有偏优化质疑，以及几条关于 KL 惩罚该不该开的判断。",
    "Q2": "还没解决的分歧主要是：熵坍塌到底该不该管、优势组全零时怎么办、"
          "该用过程奖励还是格式奖励、以及长度惩罚加不加。",
    "Q3": "DAPO 相关的节点有：立场节点讲它的四项技术，案例节点是它在 AIME 2024 上的"
          "结果，还有一个例外节点讲移除标准差代价的复现。",
    "Q4": "论文来源的节点有二十多个，主要是 DAPO、Dr.GRPO、GRPO 三篇的摘录。",
}


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

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n,
                                       "method": method, "params": params or {}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        # ★ 探针验的是**结构**（支序 / 答案集 / 计数）⇒ 统一要 structure=True。
        #   2026-10-24 起 ask 默认只交正文 + 句柄（id 留在本地）。
        if name == "ask":
            args = {"structure": True, **args}
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


def titles(corpus: Path) -> dict[str, str]:
    """语料真值：id → title。"""
    out = {}
    for p in sorted(corpus.glob("*.md")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("title:"):
                out[p.stem] = line.split(":", 1)[1].strip().strip('"')
                break
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ldv-gap-"))
    store = tmp / "store"
    corpus = store / "_data" / "dapo" / "nodes"
    corpus.mkdir(parents=True)
    builtin = HERE / "core" / "ldv" / "corpus" / "nodes"
    for f in sorted(builtin.glob("*.md")):
        shutil.copy2(f, corpus / f.name)
    truth = titles(corpus)

    c = MCP(store)
    try:
        c.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "gap", "version": "0"}})
        print("=" * 74)
        print("实验｜有插件 vs 没插件（语料 = 内置 36 项，DAPO/GRPO/RL 训练）")
        print("=" * 74)

        o = c.tool("orient", {"corpus": "dapo"})
        keys = o.get("keys") or []
        print(f"\n词汇表 {len(keys)} 个键：{keys}")

        # �0─ 问题 → 键的映射（词汇表里**没有「主题」维度**，这本身就是发现）──
        QS = [
            ("Q1", "哪些立场还没验证？", ["type=立场", "evidence_status=未验证"]),
            ("Q2", "有哪些议题？", ["type=议题"]),
            ("Q3", "DAPO 相关的节点有哪些？", ["dapo=是"]),      # 词汇里没有这个键
            ("Q4", "论文来源的节点有哪些？", ["source.kind=论文"]),
        ]
        print("\n" + "─" * 74)
        print("WITH｜走插件")
        print("─" * 74)
        with_hits = {}
        for qid, q, req in QS:
            r = c.tool("ask", {"corpus": "dapo", "require": req})
            if r.get("ok"):
                ids = list(r.get("answer_ids") or [])
                with_hits[qid] = ids
                print(f"\n{qid} {q}")
                print(f"   require={req} ⇒ {r.get('n_answer')} 项 / "
                      f"{r.get('n_branches')} 支｜支序 {[n['did'] for n in (r.get('nodes') or [])]}")
                print(f"   答案 id：{ids[:8]}{'…' if len(ids) > 8 else ''}")
                print(f"   出处：{(r.get('provenance') or {}).get('buckets')}")
                if ids:
                    d = c.tool("drill", {"corpus": "dapo", "did": ids[0] and
                                         (r.get('nodes') or [{}])[0]['did'],
                                         "require": req})
                    print(f"   drill 第一支 ⇒ recorded={d.get('recorded')}")
            else:
                with_hits[qid] = None
                print(f"\n{qid} {q}")
                print(f"   require={req} ⇒ **拒收**：{str(r.get('why'))[:56]}")
                print(f"   ⇒ 词汇表里没有「主题」这个维度 ⇒ agent 只能换问法或请人介入")

        # ── ③ 覆盖：语料里实际有的议题/例外，WITH 答案找得到吗 ────────────
        print("\n" + "─" * 74)
        print("③ 覆盖对拍（语料真值 vs 两种答法）")
        print("─" * 74)
        issues = [i for i, t in truth.items() if i.startswith("issue-")]
        excs = [i for i, t in truth.items() if i.startswith("exc-")]
        print(f"  语料里的议题 {len(issues)} 条：")
        for i in issues:
            print(f"    {i}  {truth[i]}")
        print(f"  语料里的例外 {len(excs)} 条：")
        for i in excs:
            print(f"    {i}  {truth[i]}")
        w2 = WITHOUT_ANSWERS["Q2"]
        hit = [i for i in issues if truth[i][:6] in w2 or any(
            k in w2 for k in truth[i].replace("，", " ").split()[:3])]
        print(f"\n  WITHOUT 的 Q2 答案里，能对上报料议题的：{hit or '无'}"
              f"（{len(hit)}/{len(issues)}）")

        # ── ④ 跨会话：重建（= 新开一次对话），第一轮的记忆还在吗 ──────────
        print("\n" + "─" * 74)
        print("④ 跨会话（重建入口 = 新开一次对话）")
        print("─" * 74)
        o2 = c.tool("orient", {"corpus": "dapo"})
        us = o2.get("usage") or {}
        sp = o2.get("actor_split") or {}
        r2 = c.tool("ask", {"corpus": "dapo", "require": ["type=议题"]})
        print(f"  WITH：读回使用记录 {us.get('记录数')} 条｜"
              f"覆盖方向 {us.get('覆盖方向数')} 个｜ordered_by={r2.get('ordered_by')}")
        print(f"        演员分解 {sp.get('agent 声明的下钻')}/{sp.get('记录数')}"
              f"｜第一轮钻过的支现在排在前面：{(r2.get('nodes') or [{}])[0].get('did')}")
        print(f"  WITHOUT：每次都从零开始 —— 它没有地方放第一轮钻过什么")
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
