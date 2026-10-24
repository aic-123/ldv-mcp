# -*- coding: utf-8 -*-
"""生成派生维度 `influence` 的 sidecar（`_data/<corpus>/derived/influence.json`）。

## 它是什么

    influence = max(它指向的那些节点的入度)，分四档：0 / 1-9 / 10-99 / ≥100

一句话：**你关注的人里，最大的那个有多大**。

## 为什么它切得开（这是它能立住的原因）

实测（`ext-wiki-vote`，7115 项）：最粗的一叶是 `入度0 × 出度1-3`（**3191 项 / 44.8%**）。
在这一叶**内部**，任何**只由自身度数**派生的量都是常量 —— 而且不是数据碰巧，是**数学必然**：

    入度 0 ⇒ 没人指向它 ⇒ 不可能互惠（必然「单向」）
    出度 ≥1 且入度 = 0 ⇒ 必然「出 > 入」

⇒ 互惠性 / 出入度倾向 / 强连通分量：方向数都涨，**最大叶一分不降**（37→59/49/57，叶恒 3191）。

`influence` 问的是「**你指向的人**有多大」，与自身度数**不必相关** ⇒ 能切开：

    方向 37 → 61｜层 8｜最大叶 3191 → 1888

命名由人给（用户 2026-10-20：`influence`，取 B 档）。

## 为什么写成 sidecar，而不是写回每个节点的 front matter

`_data/*` 是**所有语料基线的输入**（`expected_reds.json` / `run_checks` / `run_tests`）。
往 7115 个节点里加一个字段 ⇒ 键集变 ⇒ 树变 ⇒ **所有基线一起漂**。
而这份派生量的用途只是「入口能按它分带」，不需要污染材料。

⇒ 落在 `_data/<corpus>/derived/influence.json`，`entry.py` 启动时叠加。
   删掉这个文件 = 完全回到原状（**材料未被修改**）。

## 留痕

文件里的 `_meta` 逐条写着：算法 · 输入（真实边表）· 生成时间 · 复算命令 · 命名人 · 可逆。
**只有计数，没有模型填的内容**（立项红线：不许模型代填内容）。

跑法：

    python -m ldv.tools.make_influence ext-wiki-vote
"""
from __future__ import annotations

import io
import json
import os
import time
from collections import Counter
from pathlib import Path

from ..checks._fixtures import find_corpus
from ..corpus.loader import load_edges, load_nodes

#: 分档（从高到低判，先中先返回）
BANDS = ((100, "≥100"), (10, "10-99"), (1, "1-9"), (0, "0"))


def band(n: int) -> str:
    for lo, name in BANDS:
        if n >= lo:
            return name
    return "0"


def build(corpus: str) -> dict:
    """算出 `{id: 档}` 与 `_meta`。**纯函数**（判据可以直接调它，不用起服务）。"""
    os.environ["LDV_CORPUS"] = corpus
    import importlib

    import ldv.checks._fixtures as F
    importlib.reload(F)                       # 语料名是环境变量，重载才能生效
    root = F.find_corpus()
    if root is None:
        raise SystemExit(f"找不到语料 {corpus!r}")
    nodes = load_nodes(root)
    edges, _ = load_edges(nodes)
    ids = set(nodes)

    indeg: Counter = Counter()
    for u, vs in edges.items():
        for v in vs:
            if v in ids:
                indeg[v] += 1

    vals = {u: band(max((indeg[v] for v in edges.get(u, ()) if v in ids), default=0))
            for u in sorted(ids)}
    return {"values": vals, "dist": Counter(vals.values()), "root": root}


def write(corpus: str) -> Path:
    r = build(corpus)
    vals, dist, root = r["values"], r["dist"], r["root"]
    out_dir = Path(root).parent / "derived"
    out_dir.mkdir(exist_ok=True)
    doc = {
        "_meta": {
            "field": "influence",
            "what": "max(它指向的节点的入度)，分档 0 / 1-9 / 10-99 / ≥100",
            "why": ("与「自身度数」无关 ⇒ 能切开 `入度0 × 出度1-3` 那一叶（3191 项 / 44.8%）。"
                    "实测：方向 37 → 61，最大叶 3191 → 1888。"),
            "derived_from": "本语料 `edges`（每条边都由原始数据摄入，非模型生成）",
            "not_authored_by": "模型 —— 本文件**不含**任何模型填写的内容，只有计数",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "generator": "ldv/tools/make_influence.py",
            "recompute": f"cd repo && python -m ldv.tools.make_influence {corpus}",
            "named_by": "用户 2026-10-20（命名 `influence`，取 B 档）",
            "reversible": "删掉本文件即可完全回到原状（材料未被修改）",
        },
        "values": vals,
    }
    p = out_dir / "influence.json"
    io.open(p, "w", encoding="utf-8").write(json.dumps(doc, ensure_ascii=False, indent=0))
    print(f"语料 {corpus}｜{len(vals)} 项｜分布：" +
          " · ".join(f"{k} {n}" for k, n in dist.most_common()))
    print(f"→ {p}（{p.stat().st_size / 1024:.0f} KB）")
    return p


def main(argv: list[str] | None = None) -> int:
    import sys
    a = list(sys.argv[1:] if argv is None else argv)
    write(a[0] if a else "ext-wiki-vote")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
