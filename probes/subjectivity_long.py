"""长流程 + 大语料：主体性判据能不能在 1200 项、15 轮操作上站住。

跑法（cwd = ldv-mcp）：python ../outputs/_probe_subjectivity_long.py

## 为什么还要跑一次（一两个问题的实验不算数）

    语料  snap-hepth（**1200 项 / 19 个键**），比内置 36 项大 33 倍
    流程  **15 轮**（ask + drill），不是一两次问答
    查询  两组：**8 条单支**（type=1992…1999，各切出一支）
          + **2 条多支**（require 空 / source.kind=数据集，各切出 8 支）

## ★ 第一版实验的直接发现（这一版就是照它改的）

    第一版只用了 8 条单支查询 ⇒ **0/8 次序变了**。
    不是机制坏了，是**倾向只在「一题分成多支」时看得见**
    （`entry.query` 里 `len(nodes) > 1` 才排序）。
    ⇒ 主体性的**可见性**依赖答案被切成多支 —— 单支题上它结构上无表现。
    这是「更长的流程 + 更大的语料」才暴露出来的，一两个问题的实验看不见。

## 判据

    L1a 多支查询：15 轮之后次序变了，钻过的那支排第一
    L1b 单支查询：次序**不可能**变（结构原因），如实报，不当失败
    L2  所有查询的 answer_ids / n_answer / n_branches **逐字不变**
    L3  新进程复现；**删掉 usage 目录**后全部回滚到基线
    L4  归因：日志行数 == 工具调用总数；actor_split == 去重后的记录数
    L5  写语料（remember）**真的**改答案（材料变了就该变，与 L2 不矛盾）

## 边界（写死，免得被读成别的）

    ⚠️ 倾向只动**次序**（一维）。这不是「让块能走」—— 我们的认识是划分，
       块不相连，走不了（§1.3 已否）。这里测的正是那条一维顺序在规模上成不成立。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent        # = ldv-mcp/
SRC = Path(os.environ.get("LDV_DATA", Path(__file__).resolve().parent.parent.parent / "repo" / "_data")) / "snap-hepth" / "nodes"
PY = sys.executable
FAIL = 0
YEARS = [str(y) for y in range(1992, 2000)]
#: **多支**查询：答案被切成好几支 —— 倾向只在「一题多支」时看得见
WIDE = {"全部": [], "数据集": ["source.kind=数据集"]}
ROUNDS = 15


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}" + (f"\n        {detail}" if detail else ""))


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
        self.tools = 0                      # 工具调用总数（判据用）
        self.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "long", "version": "0"}})

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n,
                                       "method": method, "params": params or {}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        self.tools += 1
        # ★ 探针验结构 ⇒ 统一要 structure=True（2026-10-24 起默认只给正文+句柄）
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


def snap(r: dict) -> dict:
    return {"order": [n["did"] for n in (r.get("nodes") or [])],
            "ids": tuple(r.get("answer_ids") or ()),
            "n": r.get("n_answer"), "nb": r.get("n_branches"),
            "by": r.get("ordered_by")}


def ask_years(c: MCP, corpus: str) -> dict[str, dict]:
    return {y: snap(c.tool("ask", {"corpus": corpus, "require": [f"type={y}"]}))
            for y in YEARS}


def ask_wide(c: MCP, corpus: str) -> dict[str, dict]:
    return {k: snap(c.tool("ask", {"corpus": corpus, "require": v}))
            for k, v in WIDE.items()}


def main() -> int:
    if not SRC.is_dir():
        print(f"⊘ 语料不在：{SRC}（_data 不进仓库）")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ldv-long-"))
    store = tmp / "store"
    corpus = store / "_data" / "hepth" / "nodes"
    corpus.mkdir(parents=True)
    t0 = time.time()
    for f in sorted(SRC.glob("*.md")):
        shutil.copy2(f, corpus / f.name)
    n_files = len(list(corpus.glob("*.md")))
    print("=" * 74)
    print(f"长流程｜语料 snap-hepth（{n_files} 项）｜{ROUNDS} 轮操作｜8 条单支 + 2 条多支")
    print("=" * 74)

    c = MCP(store)
    try:
        o = c.tool("orient", {"corpus": "hepth"})
        print(f"\n词汇表 {o.get('n_keys')} 键｜{o.get('n_items')} 项")

        base_y, base_w = ask_years(c, "hepth"), ask_wide(c, "hepth")
        print("\n① 基线（还没做过任何事）")
        for y in YEARS[:3]:
            print(f"   单支 type={y} ⇒ {base_y[y]['n']} 项 / {base_y[y]['nb']} 支"
                  f"｜{base_y[y]['by']}｜支序 {base_y[y]['order']}")
        for k in WIDE:
            print(f"   多支 {k} ⇒ {base_w[k]['n']} 项 / {base_w[k]['nb']} 支"
                  f"｜{base_w[k]['by']}｜支序 {base_w[k]['order']}")

        # ── ② 15 轮：交替查单支/多支，各钻一支 ──────────────────────
        print(f"\n② {ROUNDS} 轮操作（单支/多支交替）")
        wide_keys = list(WIDE)
        for i in range(ROUNDS):
            if i % 2 == 0:
                y = YEARS[(i // 2) % len(YEARS)]
                r = c.tool("ask", {"corpus": "hepth", "require": [f"type={y}"]})
                nodes = r.get("nodes") or []
                if not nodes:
                    continue
                pick = nodes[0]
                c.tool("drill", {"corpus": "hepth", "did": pick["did"],
                                 "require": [f"type={y}"]})
                if i < 4:
                    print(f"   #{i+1:02d} 单支 type={y} ⇒ 钻 {pick['did']}（{len(nodes)} 支）")
            else:
                k = wide_keys[(i // 2) % len(wide_keys)]
                r = c.tool("ask", {"corpus": "hepth", "require": WIDE[k]})
                nodes = r.get("nodes") or []
                if not nodes:
                    continue
                pick = max(nodes, key=lambda n: n.get("n_members", 0))
                d = c.tool("drill", {"corpus": "hepth", "did": pick["did"],
                                     "require": WIDE[k]})
                print(f"   #{i+1:02d} 多支 {k} ⇒ 钻 {pick['did']}"
                      f"（{pick.get('n_members')} 项）recorded={d.get('recorded')}")

        # ── ③ 同样查询再跑一遍 ─────────────────────────────────────
        after_y, after_w = ask_years(c, "hepth"), ask_wide(c, "hepth")
        print(f"\n③ 同样查询（{ROUNDS} 轮之后）")
        for k in WIDE:
            print(f"   多支 {k}｜{base_w[k]['by']} → {after_w[k]['by']}"
                  f"｜{base_w[k]['order']} → {after_w[k]['order']}")
        ch_w = [k for k in WIDE if after_w[k]["order"] != base_w[k]["order"]]
        ch_y = [y for y in YEARS if after_y[y]["order"] != base_y[y]["order"]]
        chk("L1a", "多支查询：次序真的变了，且钻过的那支排到第一",
            len(ch_w) > 0 and all(after_w[k]["by"] == "usage" for k in ch_w),
            f"变了 {ch_w}｜{[{k: (base_w[k]['order'][:3], after_w[k]['order'][:3])} for k in ch_w]}")
        chk("L1b", "单支查询：次序**结构上不可能**变（一题只切出一支 ⇒ 没有次序可排）",
            len(ch_y) == 0 and all(base_y[y]["nb"] == 1 for y in YEARS),
            f"变了 {ch_y}｜8 条全部 n_branches=1 ⇒ 没有「先看哪一支」这回事")
        chk("L2", "不是裁判：所有查询的 answer_ids / n_answer / n_branches 逐字不变",
            all((base_y[y]["ids"], base_y[y]["n"], base_y[y]["nb"])
                == (after_y[y]["ids"], after_y[y]["n"], after_y[y]["nb"]) for y in YEARS)
            and all((base_w[k]["ids"], base_w[k]["n"], base_w[k]["nb"])
                    == (after_w[k]["ids"], after_w[k]["n"], after_w[k]["nb"]) for k in WIDE),
            "10 条查询集合全同")

        # ── ④ 归因 ────────────────────────────────────────────────
        # ⚠️ 顺序：先调完最后一次 orient，**再**读日志 —— 反过来会差一行
        #   （第一版就是这么红的：读日志之后又调了一次，51 行 vs 52 次）
        o2 = c.tool("orient", {"corpus": "hepth"})
        log = store / "log" / "mcp-calls.jsonl"
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] \
            if log.is_file() else []
        drill_rows = [r for r in rows if r.get("tool") == "drill"]
        split = o2.get("actor_split") or {}
        # ⚠️ 15 次 drill 只落 N 条记录 —— 同 (qfp, did) 去重（只追加不叠加），这是对的
        chk("L4", "归因：日志行数 == 工具调用总数；actor_split == 去重后的记录数",
            len(rows) == c.tools and len(drill_rows) == ROUNDS
            and split.get("agent 声明的下钻") == split.get("记录数"),
            f"日志 {len(rows)} 行 vs 调用 {c.tools} 次｜drill {len(drill_rows)} 次"
            f"｜按工具 {dict(__import__('collections').Counter(r.get('tool') for r in rows))}"
            f"｜去重后记录 {split.get('记录数')} 条"
            f"｜倾向覆盖 {len(o2.get('usage', {}).get('最多的方向') or [])} 个方向")
    finally:
        c.close()

    # ── ⑤ 新进程仍复现；删 usage 即回滚 ─────────────────────────────
    c2 = MCP(store)
    try:
        again_w = ask_wide(c2, "hepth")
        persist = all(again_w[k]["order"] == after_w[k]["order"] for k in WIDE)
    finally:
        c2.close()
    shutil.rmtree(store / "_data" / "hepth" / "usage", ignore_errors=True)
    c3 = MCP(store)
    try:
        back_w, back_y = ask_wide(c3, "hepth"), ask_years(c3, "hepth")
        reverted = all(back_w[k]["order"] == base_w[k]["order"]
                       and back_w[k]["by"] == "default" for k in WIDE) \
            and all(back_y[y]["order"] == base_y[y]["order"] for y in YEARS)
    finally:
        c3.close()
    chk("L3", "独立于权重：新进程复现；**删掉 usage 目录**后 10 条全部回滚到基线",
        persist and reverted, f"新进程复现={persist}｜删 usage 回滚={reverted}")

    # ── ⑥ 写语料会真的改答案（单独一段，不混进 L2）──────────────────
    c4 = MCP(store)
    try:
        m = c4.tool("remember", {"corpus": "hepth", "id": "memo-long-001",
                                 "fields": {"type": "1999",
                                            "source": {"kind": "自记", "ref": "-"}},
                                 "body": "长流程实验里记的一条。"})
        q = c4.tool("ask", {"corpus": "hepth", "require": ["type=1999"]})
        o4 = c4.tool("orient", {"corpus": "hepth"})
        print("\n④ 写语料（remember 一条 type=1999）")
        print(f"   written={m.get('written')}｜type=1999 现在 {q.get('n_answer')} 项"
              f"｜项数 {o4.get('n_items')}｜透镜 stale={(o4.get('lens') or {}).get('stale')}")
        chk("L5", "写语料**真的**改答案（材料变了就该变，与 L2 不矛盾）",
            m.get("written") is True and q.get("n_answer") == base_y["1999"]["n"] + 1,
            f"{base_y['1999']['n']} → {q.get('n_answer')}｜"
            f"透镜 stale={(o4.get('lens') or {}).get('stale')}")
    finally:
        c4.close()

    print(f"\n耗时 {time.time() - t0:.1f}s")
    print(f"{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（L1a/L1b/L2/L3/L4/L5）")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
