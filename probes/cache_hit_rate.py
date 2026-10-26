# -*- coding: utf-8 -*-
"""缓冲命中率 —— 三个口径分开测（2026-10-26 用户要的读数）。

**为什么不能只报一个数**：命中率完全取决于**问题重复率**。
同一问连问五次是 80%，每问都不同是 0% —— 报一个不带工作负载的「命中率」是剧场。

三个口径（混着说就是含糊）：

| 口径 | 算法 | 它回答什么 | 在哪 |
|---|---|---|---|
| **答案缓存命中率** | 命中 / `ask` | 省了多少次问 agent | ① |
| **缓冲曝光命中率** | `promoted` / 摆出的支 | 摆出来的支多少**被证明有用** | ③ |
| **漏检率** | 重新提到 / 挤出数 | 会话缓冲挤错了多少 | ③ |

② 测**键的等价性**：顺序无关（`sorted`），但**空白有关** —— 那是「该命中却错过」的来源。
④ 拿 `orient` 的聚合读数跟探针自己数的对账 —— 两处必须一致，否则读数不可信。

⚠️ 只用**真实存在**的键：`project` 不在 `ENUM_FIELDS` 里，
   除非先 `derive` 过，否则 `project=alpha` 是「未知键」—— 第一版探针在这上面栽过。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent      # ldv-mcp/
PY = sys.executable
FAIL = 0
#: 每一次 `ask` 的命中与否 —— ④ 的对账用（**探针自己数**，不读 orient 的）
ASKED: list[bool] = []


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}"
          + (f"\n        {detail}" if detail else ""))


def hit_of(out: dict) -> bool:
    return bool((out.get("cached_answer") or {}).get("from_cache"))


class MCP:
    """最小 MCP 客户端（与 smoke 同款：写一行、读一行）。"""

    def __init__(self, store: Path):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LDV_MCP_STORE"] = str(store)
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", env=env, cwd=str(HERE))
        self.n = 0
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "hit", "version": "0"}})

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n,
                                       "method": method,
                                       "params": params or {}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name: str, args: dict) -> dict:
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        txt = ""
        for item in res.get("content") or []:
            if item.get("type") == "text":
                txt += item.get("text", "")
        try:
            out = json.loads(txt) if txt else {}
        except ValueError:
            out = {"_raw": txt}
        return out

    def ask(self, require: list[str], corpus: str = "hit",
            session: str = "") -> dict:
        """**唯一**发 ask 的入口 —— 每次都进 `ASKED`，④ 才对得上账。"""
        args = {"corpus": corpus, "require": require}
        if session:
            args["session"] = session
        out = self.tool("ask", args)
        ASKED.append(hit_of(out))
        return out

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:                              # noqa: BLE001
            pass


def seed(nodes: Path, n: int = 12) -> None:
    """12 条：`type` × `source.kind` 两个**真**枚举字段 ⇒ 6 个可查键。"""
    types = ("立场", "判断点", "例外")
    kinds = ("论文", "笔记")
    for i in range(n):
        (nodes / f"n{i:02d}.md").write_text(
            f'---\nid: "n{i:02d}"\ntype: {types[i % 3]}\n'
            f'source.kind: {kinds[i % 2]}\n---\n\n第 {i} 条材料，内容互不相同。\n',
            encoding="utf-8", newline="\n")


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    store = tmp / "s"
    nodes = store / "_data" / "hit" / "nodes"
    nodes.mkdir(parents=True)
    seed(nodes)
    m = MCP(store)
    try:
        print("=" * 78)
        print("缓冲命中率｜三个口径分开测")
        print("=" * 78)

        # ── ① 答案缓存命中率 × 4 个工作负载 ──────────────────────────
        print("\n① 答案缓存命中率（同一套代码，四个工作负载）")
        print("   口径：命中 / ask。**数字随工作负载变，所以必须分开报。**\n")

        # W1 完全重复：问 → 编进树 → 连问四次
        m.ask(["type=立场"])
        m.tool("remember_answer", {"corpus": "hit", "require": ["type=立场"],
                                   "answer": "立场类的结论：损失聚合有问题。",
                                   "source": ["n00"]})
        w1 = [hit_of(m.ask(["type=立场"])) for _ in range(4)]
        n1, h1 = 5, sum(w1)
        print(f"   W1 同一问连问 5 次          ask={n1:2d} 命中={h1:2d}"
              f"  命中率={h1 / n1:.0%}")

        # W2 每问都不同（探索型用法，都是真键）
        uniq = [["type=判断点"], ["type=例外"], ["source.kind=论文"],
                ["type=立场", "source.kind=笔记"], ["source.kind=笔记"]]
        w2 = [hit_of(m.ask(q)) for q in uniq]
        n2, h2 = len(uniq), sum(w2)
        print(f"   W2 每问都不同（探索型）    ask={n2:2d} 命中={h2:2d}"
              f"  命中率={h2 / n2:.0%}")

        # W3 真实轨迹：8 个新问题各编进树 + 4 次回头问
        news = [["type=判断点", "source.kind=论文"], ["type=例外", "source.kind=笔记"],
                ["type=立场", "source.kind=论文"], ["type=判断点", "source.kind=笔记"],
                ["type=例外", "source.kind=论文"], ["type=立场", "source.kind=笔记"],
                ["type=判断点"], ["source.kind=笔记"]]
        hits3 = 0
        for q in news:
            hits3 += hit_of(m.ask(q))
            m.tool("remember_answer", {"corpus": "hit", "require": q,
                                       "answer": f"对 {q} 的回答。",
                                       "source": ["n01"]})
        back = [news[0], news[2], news[4], news[6]]
        hits3 += sum(hit_of(m.ask(q)) for q in back)
        n3, h3 = len(news) + len(back), hits3
        print(f"   W3 真实轨迹（8 新 + 4 回头）ask={n3:2d} 命中={h3:2d}"
              f"  命中率={h3 / n3:.0%}")

        # W4 语料变了：写一条新材料 ⇒ 全部作废
        (nodes / "n99.md").write_text(
            '---\nid: "n99"\ntype: 立场\nsource.kind: 论文\n---\n\n新写的一条。\n',
            encoding="utf-8", newline="\n")
        w4 = [hit_of(m.ask(q)) for q in (news[0], news[2], news[4])]
        n4, h4 = 3, sum(w4)
        print(f"   W4 语料变了之后重问        ask={n4:2d} 命中={h4:2d}"
              f"  命中率={h4 / n4:.0%}   ← 作废，不是 bug")

        # ── ② 键的等价性 ────────────────────────────────────────────
        print("\n② 键的等价性（同一个问题、写法不同）")
        base = ["type=立场", "source.kind=论文"]
        m.ask(base)
        m.tool("remember_answer", {"corpus": "hit", "require": base,
                                   "answer": "基线答案。", "source": ["n00"]})
        same = m.ask(["source.kind=论文", "type=立场"])
        chk("②-a", "换顺序 ⇒ **同一个问题**（`sorted` 过），必须命中",
            hit_of(same), f"query={['source.kind=论文', 'type=立场']}")
        # ⚠️ 空白：**不**等价。报出来，不当判据 —— 它要不要改是语义决定，不是 bug
        sp = m.ask(["type = 立场"])
        print(f"   ⚠️ 写法 `type = 立场`（等号带空格）⇒ "
              f"out_of_structure={sp.get('out_of_structure')}"
              f"｜命中={hit_of(sp)}")
        print("      ⇒ 空白**不**归一化：同一个问题会被当成两个键 / 一个未知键。")

        # ── ③ 缓冲曝光命中率 + 漏检率 ──────────────────────────────
        print("\n③ 缓冲曝光命中率（摆出来的支，后来多少被证明有用）")
        sid = "hit-s1"
        m.tool("orient", {"corpus": "hit", "session": sid})
        a_all = m.ask([], session=sid)
        exposed = int(a_all.get("branches_shown") or 0)
        for k in (1, 2):
            m.tool("drill", {"corpus": "hit", "n": k, "require": [], "session": sid})
        st = (m.tool("orient", {"corpus": "hit", "session": sid}).get("session") or {})
        promoted = int(st.get("promoted") or 0)
        if exposed:
            print(f"   摆出的支={exposed}｜已 promote={promoted}"
                  f"｜缓冲曝光命中率={promoted / exposed:.0%}")
        else:
            print(f"   摆出的支={exposed}（没有可钻的支，曝光命中率不适用）")
        print(f"   漏检率={st.get('漏检率')}（None = 还没挤掉过，不是 0）")

        # ── ④ 对账：orient 的聚合 vs 探针自己数 ─────────────────────
        print("\n④ 对账：`orient` 的聚合读数 vs 本探针自己数的 ask/命中")
        agg = m.tool("orient", {"corpus": "hit"}).get("cache") or {}
        self_asks = len(ASKED)
        self_hits = sum(1 for h in ASKED if h)
        print(f"   orient 报：ask={agg.get('ask 数')} 命中={agg.get('命中数')}"
              f" 命中率={agg.get('答案缓存命中率')}")
        print(f"   探针自数：ask={self_asks} 命中={self_hits}")
        chk("④-a", "orient 的 ask 数 == 探针发出的 ask 数（多一个少一个都不行）",
            agg.get("ask 数") == self_asks,
            f"orient={agg.get('ask 数')}｜探针={self_asks}")
        chk("④-b", "orient 的命中数 == 探针自己数的命中数",
            agg.get("命中数") == self_hits,
            f"orient={agg.get('命中数')}｜探针={self_hits}")
        chk("④-c", "有 ask 就一定算得出命中率（None 只出现在「一次都没问」）",
            agg.get("答案缓存命中率") is not None,
            f"命中率={agg.get('答案缓存命中率')}")

        # ── ⑤ 结论 ────────────────────────────────────────────────
        print("\n" + "=" * 78)
        print("结论")
        print("=" * 78)
        print("  命中率不是一个数，是**问题重复率**的函数：")
        print(f"    同一问重复 → {h1 / n1:.0%}｜每问都不同 → {h2 / n2:.0%}"
              f"｜真实轨迹 → {h3 / n3:.0%}｜语料变 → {h4 / n4:.0%}")
        print("  ⇒ 它是**复用率**，不是智能指标。别拿它当能力读数。")
        print("  ⇒ 空白写法不归一化 ⇒ 同一个问题算成另一个键（②-a 说明顺序是归一化的，空白不是）。")
        return 1 if FAIL else 0
    finally:
        m.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
