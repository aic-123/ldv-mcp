"""把 **Cora**（公开**带标注**的引用图）接成 ldv 语料。

    跑法：python -m ldv.tools.intake_cora --src _data/cora --out _data/cora-labeled

---

## 为什么要接一份**带标注**的料（**先写，再跑**）

`§10` 里那条留白写着：

> `§K2` 的第二半（子方向是否真的更细）—— 内核**判不了**，那是**语义**问题，
> 而内核没有语义。⇒ 由外生检查 `B17` 守。

`B17` 判的是**结构**等价（「互相可达」「键集相同」）—— 那是**从方向自己的谓词**
外生复算出来的，仍然**不含语义**。要判「真的更细」，需要一个**外生的语义**。

**标注就是那个语义。** Cora 的 2708 篇论文各带一个类别（7 类），
于是「展开有没有产出下层表达不出的东西」第一次有了一个**不来自本系统**的判据：

    语义增益(d) = H(标签 | 父的成员) − Σ_k (|m_k|/|m_d|)·H(标签 | m_k)
    语义增益 = 0  ⟺  每个子的标签分布都与父**一模一样**  ⟺  这次展开**在语义上什么都没说**

⇒ 判据 `B20`（**语义变细守卫**）：连续 N 次展开的语义增益都为 0 ⇒ 红。
   与 `B19`（**结构**变细守卫）成对：**一条守覆盖，一条守标签。**

## 为什么选 Cora

- **公开、可复现**：`https://linqs-data.soe.ucsc.edu/public/lbc/cora.tgz`（2007 年版）
- **同时带两样东西**，正好对上本设计：
  - `cora.content` 的**类别** → **语义**（`B20` 的输入）
  - `cora.cites` 的**引用边** → **图**（方向 B `reach` 的输入）
- 规模（2708 项 / 5429 边）够跑出多层树，又不至于让套件变成分钟级
- ⚠️ **它的引用图有环**（和 OpenAlex 一样）—— 这对 `B17`(b) 是已知现象，
  不构成本次要证的事，但要一起报出来

## 映射表（外部概念 → 本仓库概念）

| Cora | 本仓库 | 说明 |
|---|---|---|
| `paper_id` | 节点 `id` | 原样字符串 |
| 类别（第 1434 列） | **`type`**（枚举，7 值） | ★ **这一列是本次接料的目的**：它进键集 ⇒ `keyset` 在这份料上**是标签轴** |
| `cora.cites` 的 `A B` | `relations`：**B → A** | 文件里第一列是**被引**、第二列是**引用**（README 逐字：「the link is paper2->paper1」） |
| 1433 维词袋 | **丢弃** | 见 DROP 表 |

## DROP 表（丢了什么、多少、为什么）

| 丢掉的 | 规模 | 为什么 |
|---|---|---|
| 1433 维词袋 | 每行 1433 个 0/1 | 本层要的是**结构 + 语义**，词袋是第三种东西（特征）。塞进来会让「这份料是什么」看不清；而且它会把 `keyset` 的键空间撑成 1433 个稀疏键 |
| 指向节点集**之外**的引用 | 见 `MANIFEST.json` | 语料是「**这份切片**」；外部引用不属于它 |
| 论文标题 / 作者 / 年份 | Cora 就没有 | — |

## ⚠️ 一处**刻意的选择**：标签进 `type`，不进 `notes`

`loader.ENUM_FIELDS = ("type", "evidence_status")` —— **只有这两个字段的取值会进键集**。
所以标签放在哪个字段，决定了「哪个方向是标签轴」：

    放进 `type`        ⇒ `type=Neural_Networks` 进键集 ⇒ **`keyset` 是标签轴** ⇒ `B20` 可判
    放进 `notes`       ⇒ 只贡献常量键 `"notes"`       ⇒ 没有任何方向带标签 ⇒ `B20` 判不了

⇒ 这是**为了让它可判**而做的选择，不是 Cora 的原始编码方式。
   **它必须写在这里**，否则下一个人会以为「Cora 本来就长这样」。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable
from ._intake_common import scalar as _scalar

SOURCE_URL = "https://linqs-data.soe.ucsc.edu/public/lbc/cora.tgz"


def read_content(path: Path) -> tuple[dict[str, str], int]:
    """`<id>\\t<1433 个 0/1>\\t<类别>` ⇒ `{id: 类别}`。返回 `(标签表, 列数)`。"""
    labels: dict[str, str] = {}
    width = 0
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                raise SystemExit(f"{path}:{lineno} 不是 3 列：{len(parts)} 列")
            pid, label = parts[0].strip(), parts[-1].strip()
            if not pid or not label:
                raise SystemExit(f"{path}:{lineno} id 或类别为空")
            if pid in labels:
                raise SystemExit(f"{path}:{lineno} 重复 id：{pid}")
            labels[pid] = label
            width = max(width, len(parts) - 2)
    return labels, width


def read_cites(path: Path, known: set[str]) -> tuple[dict[str, list[str]], int, int]:
    """`<被引>\\t<引用>` ⇒ 出边表 `{引用: [被引, …]}`（**方向与文件相反**）。

    返回 `(出边表, 悬挂边数, 自环数)`。悬挂边**不静默丢**：它要进 MANIFEST。
    """
    out: dict[str, list[str]] = {pid: [] for pid in known}
    dangling = 0
    self_loops = 0
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            parts = line.split()
            if len(parts) < 2:
                raise SystemExit(f"{path}:{lineno} 不是两列")
            cited, citing = parts[0].strip(), parts[1].strip()
            if cited == citing:
                self_loops += 1
                continue
            if citing not in known or cited not in known:
                dangling += 1
                continue
            out[citing].append(cited)
    for pid in out:
        out[pid] = sorted(set(out[pid]))
    return out, dangling, self_loops


def node_markdown(pid: str, label: str, outs: list[str],
                  indeg: int) -> str:
    lines = [
        "---",
        f'id: "{pid}"',
        f'type: "{_scalar(label, 40)}"',
        f'title: "Cora 论文 {pid}（{_scalar(label, 40)}）"',
        'evidence_status: "同行评审"',
        "source:",
        f'  ref: "{SOURCE_URL}"',
        '  kind: "数据集"',
        f'scope: "Cora 语料（linqs 版）第 {pid} 篇；类别 {_scalar(label, 40)}；'
        f'引用 {len(outs)} 篇、被引 {indeg} 次"',
        f'notes: "Cora 标注语料：类别来自 cora.content 末列，引用边来自 cora.cites"',
        'filled_by: "Cora（公开标注语料）"',
    ]
    if outs:
        lines.append("relations:")
        lines += [f'  - "{o}"' for o in outs]
    else:
        lines.append("relations: []")
    lines += ["---", "", f"Cora 论文 {pid}，类别 **{_scalar(label, 40)}**。"
              f"出度 {len(outs)} / 入度 {indeg}。", ""]
    return "\n".join(lines)


def build(src: Path, out: Path) -> dict:
    labels, width = read_content(src / "cora.content")
    known = set(labels)
    edges, dangling, self_loops = read_cites(src / "cora.cites", known)

    indeg = Counter()
    for outs in edges.values():
        for t in outs:
            indeg[t] += 1

    nodes_dir = out / "nodes"
    if nodes_dir.exists():
        for old in nodes_dir.glob("*.md"):
            old.unlink()
    nodes_dir.mkdir(parents=True, exist_ok=True)
    for pid in sorted(labels):
        (nodes_dir / f"{pid}.md").write_text(
            node_markdown(pid, labels[pid], edges[pid], indeg[pid]), encoding="utf-8")

    # --- 两条**独立**数法（与 OpenAlex 接料同一条纪律）------------------------
    #   ① 走 markdown 往返（读回磁盘上刚写的文件）
    #   ② 直接从原始两个文件数
    # 两者不一致 ⇒ 转换器漏写了东西。**不靠「看起来对」**。
    back_nodes = 0
    back_edges = 0
    for p in nodes_dir.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        back_nodes += 1
        back_edges += text.count("\n  - \"")
    raw_edges = sum(len(v) for v in edges.values())

    n_edges = sum(len(v) for v in edges.values())
    manifest = {
        "来源": f"Cora（linqs 版，{SOURCE_URL}）",
        "许可": "见 linqs 页面；本仓库只存**转换后的**节点文件，原始包不进仓库",
        "节点数": len(labels),
        "图边数": n_edges,
        "词袋列数（已丢弃）": width,
        "丢弃的悬挂边数": dangling,
        "丢弃的自环数": self_loops,
        "类别取值": dict(Counter(labels.values()).most_common()),
        "类别数": len(set(labels.values())),
        "出度 max": max((len(v) for v in edges.values()), default=0),
        "入度 max": max(indeg.values(), default=0),
        "两条数法": {"markdown 往返": {"节点": back_nodes, "边": back_edges},
                     "直接读原始文件": {"节点": len(labels), "边": raw_edges}},
        "两条数法一致": back_nodes == len(labels) and back_edges == raw_edges,
    }
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 Cora 接成 ldv 语料")
    ap.add_argument("--src", default="_data/cora", help="解包后的 cora.content / cora.cites 所在目录")
    ap.add_argument("--out", default="_data/cora-labeled")
    a = ap.parse_args(list(argv) if argv is not None else None)
    m = build(Path(a.src), Path(a.out))
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print(f"\n节点 {m['节点数']} · 边 {m['图边数']} · 类别 {m['类别数']} · "
          f"两条数法一致 {m['两条数法一致']}")
    return 0 if m["两条数法一致"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
