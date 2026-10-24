# -*- coding: utf-8 -*-
"""语料索引 —— **短期 / 长期两层缓冲**（2026-10-17）。

## 为什么需要它（实测逼出来的）

    ext-coauth（9875 节点）：首次装载 4.69s ｜ 进程内缓存命中 0.268s（17×）
    拆解：每万节点 **读盘 3.90s**、解析 0.14s ⇒ **瓶颈是 IO 读盘**（96%）
    stat 校验：9875 个文件只要 **0.47s**（vs 读盘 4.69s）

⇒ 「短期/长期」的真实形态不是「工作区换入换出」（查询期只花 0.02s、
新长 0 个方向），而是**避免重复读盘**：

    短期（工作区）  `loader._NODE_CACHE` —— 进程内，按 (路径, mtime, size)
    长期（存档）    `<缓存目录>/<语料>.index.json` —— 跨进程的**派生索引**

## 索引里存什么、不存什么

存：`id` / `fields` / `keys` / 每个文件的 `(mtime_ns, size)`
不存：**`body`（Markdown 正文）**

⚠️ 为什么不存 body：主流程（kernel / checks / cli）**一处都不用 body**
（唯一用它的 `tools/compare_bm25.py` 是独立对照工具）⇒ 索引路径造出的
`Node.body` 是空串。**等价判据只比 id / keys / fields**，body 差异如实标注。

## 为什么索引不进语料目录

「一租户一目录 + 备份 = 拷贝目录」是服务化的地基。索引写进语料目录会
污染备份、也会进 git。⇒ 写到**缓存目录**（`~/.cache/ldv/`，可用
`LDV_INDEX_DIR` 覆盖），**随时可删、可重建**。

## 失效规则（可复现）

    stat 全量 → 与索引里的 (mtime_ns, size) 逐个比
    全部匹配 ⇒ 直接用索引（不读任何 .md）
    任一不匹配 ⇒ **只重读那几个文件**，其余仍用索引
    索引里有、目录里没有 ⇒ 丢弃；目录里有、索引没有 ⇒ 新文件，读
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .loader import CorpusError, Node, keys_of, parse_front_matter

#: 缓存根（派生数据，随时可删）
def cache_root() -> Path:
    env = os.environ.get("LDV_INDEX_DIR")
    return Path(env) if env else (Path.home() / ".cache" / "ldv")


def _index_path(directory: Path, root: Path | None = None) -> Path:
    """索引文件路径 —— 用**目录绝对路径的哈希**命名（同名语料不互相覆盖）。"""
    import hashlib
    h = hashlib.sha256(str(directory.resolve()).encode("utf-8")).hexdigest()[:16]
    return (root or cache_root()) / f"{directory.resolve().name}-{h}.index.json"


def load_nodes_indexed(directory: str | Path,
                       root: Path | None = None) -> tuple[dict[str, Node], dict[str, Any]]:
    """带索引的装载。返回 `(nodes, 统计)`。

    `统计`：`命中` / `重读` / `丢弃` / `新增` / `索引`（路径或 None）。
    """
    d = Path(directory)
    # ⚠️ **`rglob` 而不是 `glob`**（2026-10-18）：与 `loader.load_nodes` 保持一致 ——
    #    摄入侧可以把节点分片到子目录。第一版用 `glob`，分片语料读出 **0 个节点**，
    #    而且**没报错**（下文没有空值守卫）⇒ 下游全部判据在空语料上「通过」＝假绿。
    files = sorted(d.rglob("*.md"))
    if not files:
        raise CorpusError(f"{d} 里没有 .md 节点（分片目录也要用 rglob 读）")
    # ① stat 全量（0.47s/万节点 —— 比读盘便宜一个数量级）
    stat: dict[str, tuple[int, int]] = {}
    for f in files:
        st = f.stat()
        # ⚠️ key 是**相对路径**（`arg/arg-0001.md`），不是文件名 ——
        #    分片目录下文件在子目录里，用 `f.name` 会在 `d / fname` 处找不到它。
        stat[str(f.relative_to(d))] = (st.st_mtime_ns, st.st_size)

    ip = _index_path(d, root)
    old: dict[str, dict] = {}
    if ip.is_file():
        try:
            old = json.loads(ip.read_text(encoding="utf-8")).get("节点", {})
        except Exception:
            old = {}                        # 索引坏了 ⇒ 当作没有（可重建）

    nodes: dict[str, Node] = {}
    hit = reread = dropped = 0
    fresh: dict[str, dict] = {}

    for fname, (mt, sz) in stat.items():
        ent = old.get(fname)
        if ent is not None and ent.get("m") == mt and ent.get("s") == sz:
            # ② 命中：用索引造 Node，**不读文件**
            nodes[ent["id"]] = Node(id=ent["id"], fields=ent["f"],
                                    body="", keys=frozenset(ent["k"]))
            fresh[fname] = ent
            hit += 1
        else:
            # ③ 未命中：读这一个文件
            f = d / fname          # fname 是相对路径 ⇒ 分片也找得到
            fields, body = parse_front_matter(
                f.read_text(encoding="utf-8", errors="replace"), str(f))
            nid = str(fields.get("id") or f.stem)
            node = Node(id=nid, fields=fields, body=body, keys=keys_of(fields))
            nodes[nid] = node
            fresh[fname] = {"id": nid, "f": fields, "k": sorted(node.keys),
                            "m": mt, "s": sz}
            reread += 1

    dropped = len(set(old) - set(stat))
    stats = {"命中": hit, "重读": reread, "丢弃": dropped, "新增": 0,
             "索引": str(ip)}

    # ④ 写回索引（只有变了才写 —— 避免无谓 IO）
    if reread or dropped or not ip.is_file():
        ip.parent.mkdir(parents=True, exist_ok=True)
        ip.write_text(json.dumps({"目录": str(d.resolve()), "节点": fresh},
                                 ensure_ascii=False) + "\n", encoding="utf-8")
        stats["已写"] = True
    else:
        stats["已写"] = False
    return nodes, stats
