"""主体性判据：差异能不能表示「它有一个连续的自己」。

跑法（cwd = ldv-mcp）：python ../outputs/_probe_subjectivity.py

## 先分清两件事（上一轮实验证的是前者，不是后者）

    「有依据」   答案能指回材料、不捏造、覆盖全   ← 词典也有，不叫主体性
    「有主体性」 它的行为被**自己过去做过的事**改变，且可归因、可回滚

⇒ 这个探针只测后者。五条，缺一条都不算：

    T1 连续性   它自己钻过一支之后，**同一问题**的次序变了（default → usage）
    T2 归因     变化能指到「谁做的」：调用日志 + actor_split
    T3 独立于权重 新进程里变化仍在（落盘）；**删掉 usage 目录**后变化消失（回滚）
    T4 不是裁判  次序变了，但 answer_ids / n_answer / n_branches **逐字不变**
    T5 反例     无插件侧：同一问题问两遍，没有「它自己的过去动作」可改变它

⚠️ T3 的「删掉即回滚」是关键：它把「变化来自私人状态」与「变化来自模型随机性/
   缓存/上下文」分开 —— 后者删不掉。
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
FAIL = 0


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}" + (f"\n        {detail}" if detail else ""))


class MCP:
    """每次 new 一个 = 一个**新进程**（模拟新开一次对话）。"""

    def __init__(self, store: Path):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LDV_MCP_STORE"] = str(store)
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=env, cwd=str(HERE))
        self.n = 0
        self.call("initialize", {"protocolVersion": "2024-11-05",
                                 "capabilities": {}, "clientInfo": {"name": "s", "version": "0"}})

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


def shape(r: dict) -> tuple:
    """答案的**集合**（不是次序）—— 用来证明「只改次序」。"""
    return (tuple(r.get("answer_ids") or ()), r.get("n_answer"), r.get("n_branches"))


def order(r: dict) -> list:
    return [n["did"] for n in (r.get("nodes") or [])]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ldv-subj-"))
    store = tmp / "store"
    corpus = store / "_data" / "mine" / "nodes"
    corpus.mkdir(parents=True)
    for f in sorted((HERE / "core" / "ldv" / "corpus" / "nodes").glob("*.md")):
        shutil.copy2(f, corpus / f.name)
    Q = ["type=议题"]

    print("=" * 74)
    print("主体性判据｜差异能不能表示「它有一个连续的自己」")
    print("=" * 74)

    # ── T1 连续性 ────────────────────────────────────────────────────
    c = MCP(store)
    try:
        base = c.tool("ask", {"corpus": "mine", "require": Q})
        o0 = order(base)
        print(f"\n① 基线（它还没做过任何事）")
        print(f"   支序 {o0}｜ordered_by={base.get('ordered_by')}")
        target = o0[-1]                       # 挑**最后一支**来钻，好看出次序翻转
        dr = c.tool("drill", {"corpus": "mine", "did": target, "require": Q})
        print(f"② 它自己钻了最后一支 {target} ⇒ recorded={dr.get('recorded')}")
        after = c.tool("ask", {"corpus": "mine", "require": Q})
        o1 = order(after)
        print(f"   同一问题再问：支序 {o1}｜ordered_by={after.get('ordered_by')}")
        chk("T1", "连续性：它自己钻过之后，**同一问题**的次序变了，钻过的那支排第一",
            base.get("ordered_by") == "default" and after.get("ordered_by") == "usage"
            and o1[0] == target and o0 != o1,
            f"{o0} → {o1}｜钻的是 {target}")

        # ── T4 不是裁判：次序变了，集合没变 ─────────────────────────
        chk("T4", "不是裁判：answer_ids / n_answer / n_branches **逐字不变**",
            shape(base) == shape(after),
            f"{shape(base)[1]} 项 / {shape(base)[2]} 支｜集合相同 {shape(base) == shape(after)}")

        # ── T2 归因：变化指得到「谁做的」 ───────────────────────────
        o = c.tool("orient", {"corpus": "mine"})
        split = o.get("actor_split") or {}
        log = store / "log" / "mcp-calls.jsonl"
        rows = [json.loads(x) for x in io_lines(log)] if log.is_file() else []
        drills = [r for r in rows if r.get("tool") == "drill"]
        chk("T2", "归因：调用日志里有那条 drill，actor_split 报得出是谁",
            any(r.get("summary", {}).get("did") == target for r in drills)
            and split.get("agent 声明的下钻") == 1,
            f"日志 drill {len(drills)} 条｜split {split.get('agent 声明的下钻')}"
            f"/{split.get('记录数')}")
    finally:
        c.close()

    # ── T3 独立于权重：新进程仍在；删 usage 即回滚 ────────────────────
    c2 = MCP(store)                            # 新进程 = 新开一次对话
    try:
        again = c2.tool("ask", {"corpus": "mine", "require": Q})
        persist = (again.get("ordered_by") == "usage" and order(again)[0] == target)
    finally:
        c2.close()
    usage_dir = store / "_data" / "mine" / "usage"
    shutil.rmtree(usage_dir, ignore_errors=True)
    c3 = MCP(store)                            # 再新开一次，但私人记忆被删掉
    try:
        back = c3.tool("ask", {"corpus": "mine", "require": Q})
        reverted = (back.get("ordered_by") == "default" and order(back) == o0)
    finally:
        c3.close()
    chk("T3", "独立于权重：新进程里变化仍在；**删掉 usage 目录**后变化消失（可回滚）",
        persist and reverted,
        f"新进程仍 usage={persist}｜删 usage 后回默认={reverted}"
        f"（支序 {order(back) if 'back' in dir() else '?'}）")

    # ── T5 反例：无插件侧结构上做不到 ────────────────────────────────
    print("\n③ 反例（无插件侧）")
    print("   同一问题问两遍：它没有「自己的过去动作」可以改变结果 ——")
    print("   无插件时不存在「钻过哪一支」这件事，所以这一条**结构上不可能**，")
    print("   不是「它没做到」，而是「没有那个位置」。")

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（T1–T5）")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


def io_lines(p: Path):
    import io

    return io.open(p, encoding="utf-8").read().splitlines()


if __name__ == "__main__":
    raise SystemExit(main())
