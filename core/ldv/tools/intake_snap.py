# -*- coding: utf-8 -*-
"""把 **SNAP** 的三个数据集接成 ldv 语料（形状互补，用来压系统的不同路径）。

    跑法：
      python -m ldv.tools.intake_snap --dataset email-eu
      python -m ldv.tools.intake_snap --dataset hepth
      python -m ldv.tools.intake_snap --dataset hepth-dag

---

## 为什么接这三份（**先写，再跑**）

已有语料的形状是**同质的**：36 项（人工）· 281 / 3907 项（OpenAlex 引文）·
Cora（引文 + 7 类标签）—— 它们**都有环**（OpenAlex 281 项切片里 6 个大小 ≥2 的 SCC）、
**都带标签**、密度都在 2–6 边/项。

而设计文档里有一句**可证伪的声称**（§10 留白 + §7.2）：

> `reach` 在**合环图**上会把互相可达的两项劈成两个覆盖完全相同的子方向。
> **真实语料是 DAG，所以这个缺口在语料上不露**

OpenAlex 已经证伪了后半句（引用图**有环**）。但「缺口 == 环」这条**因果**
**从没做过单变量对照** —— 两次观测都是「有环 ⇒ 红」，缺「无环 ⇒ 不红」那一半。

⇒ 这三份料就是为了补那一半，并且**形状上互不相同**：

    数据集         规模          密度      环                    标签
    ───────────────────────────────────────────────────────────────────────
    email-Eu-core  1005 项       25.44     1 个 803 项巨 SCC     42 个部门（外生）
    cit-HepTh      1200 项切片   ~12       多个中小 SCC + 悬挂边 **年份**（8 值，外生）
    cit-HepTh-dag  同上          同上      **无环（数学保证）**   同上
                                           ↑ 与上一行**只差这一点**

★ `hepth` 与 `hepth-dag` 是**同源**的（同一批节点、同一个切片），
  唯一差别是**边**：`-dag` 只保留 `年份(引用者) > 年份(被引者)` 的边。
  年份沿边**严格递减** ⇒ 沿任何路径都不回环 ⇒ **必然无环**（不需要跑 SCC 去确认）。
  于是「`B17` 红不红」的差异只能来自「有没有环」—— 一次**单变量对照**。

⚠️ 这不是「人造 DAG」：`年份(引用者) > 年份(被引者)` 正是**引文本来的语义**
  （引用只能引更早的）。实测被丢掉的是**同年互引**（40273 条 / 24.1%）——
  同年内**可能**成环，所以严格版把它们一并丢掉，换来「无环」这个**可证**的性质。
  ⚠️ 反向边（引用者更早）只有 **73 条**（0.04%）—— 那是数据本身的噪声，
     不是「引文可以是双向的」。

## 映射表（外部概念 → 本仓库概念）

| SNAP | 本仓库 | 说明 |
|---|---|---|
| `email-Eu-core` 的 `A B` | `relations`：**A → B** | 邮件 A 发给 B（有向） |
| `department-labels` 的 `节点 部门` | **`type`**（枚举，42 值） | ★ 外生标签 ⇒ `keyset` 在这份料上**是标签轴** |
| `cit-HepTh` 的 `A B` | `relations`：**A → B** | **A 引用 B**（实测 A 的年份 ≥ B 的年份占 99.96%） |
| arXiv id 前 2 位（`YYMMNNN`） | **`type`**（枚举，8 值：1992–1999） | ★ 外生；只取 7 位 id 的节点（年份可读） |

## DROP 表（丢了什么、多少、为什么）

    丢弃项                                       为什么
    ────────────────────────────────────────────────────────────────────
    email-Eu-core 中两端不在 1005 节点里的边     外部引用（本切片不含那些节点）
    自环                                         进不了 `relations`（自引不成边）
    cit-HepTh 中非 7 位 id 的节点（10463 个）     年份不可读 ⇒ 拿不到外生标签轴
    cit-HepTh 切片外的边（悬挂边）                本切片不含那些节点 —— **不静默丢**，进 MANIFEST
    cit-HepTh-dag 的同年互引（40273 条）          同年内可能成环；丢掉换来「无环」这个可证性质
    cit-HepTh-dag 的反向边（73 条）               引用者比被引者更早 —— 数据噪声

⚠️ 两条**独立**数法（与 `intake_openalex` / `intake_cora` 同一条纪律）：
  ① 走 markdown 往返（读回磁盘上刚写的文件）；② 直接从原始文件数。
  不一致 ⇒ 转换器漏写了东西。**不靠「看起来对」**。
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Iterable
from ._intake_common import scalar as _scalar

SOURCE_URL = "https://snap.stanford.edu/data/"

#: 每个年份取入度最高的多少项 —— 切片用（让 8 个年份都有代表，且保住图的「核心」）
YEAR_TOPN = 150


def read_pairs(path: Path) -> list[tuple[str, str]]:
    """读两列的边表（空格或 tab 分隔，`#` 是注释）。"""
    op = gzip.open if path.suffix == ".gz" else open
    out: list[tuple[str, str]] = []
    with op(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            p = s.split()
            if len(p) >= 2:
                out.append((p[0], p[1]))
    return out


def read_labels(path: Path) -> dict[str, str]:
    """`<节点> <标签>` ⇒ 字典。重复 id 报错（不猜）。"""
    op = gzip.open if path.suffix == ".gz" else open
    out: dict[str, str] = {}
    with op(path, "rt", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            p = s.split()
            if len(p) < 2:
                raise SystemExit(f"{path}:{lineno} 不是两列")
            if p[0] in out:
                raise SystemExit(f"{path}:{lineno} 重复 id：{p[0]}")
            out[p[0]] = p[1]
    return out


def year_of(pid: str) -> str | None:
    """arXiv 老格式 7 位 `YYMMNNN` ⇒ `'19YY'`；其它长度 ⇒ `None`（不猜）。"""
    if len(pid) != 7 or not pid.isdigit():
        return None
    yy = int(pid[:2])
    return f"19{yy:02d}" if yy >= 90 else f"20{yy:02d}"


def node_markdown(nid: str, label: str, title: str, outs: list[str],
                  indeg: int, note: str) -> str:
    lines = [
        "---",
        f'id: "{nid}"',
        f'type: "{_scalar(label, 40)}"',
        f'title: "{_scalar(title, 100)}"',
        'evidence_status: "原始记录"',
        "source:",
        f'  ref: "{SOURCE_URL}"',
        '  kind: "数据集"',
        f'scope: "SNAP 数据集的一个切片；本节点出度 {len(outs)}、入度 {indeg}"',
        f'notes: "{_scalar(note, 140)}"',
        'filled_by: "SNAP（公开数据集）"',
    ]
    if outs:
        lines.append("relations:")
        lines += [f'  - "{o}"' for o in outs]
    else:
        lines.append("relations: []")
    lines += ["---", "",
              f"SNAP 节点 `{nid}`，标签 **{_scalar(label, 40)}**。"
              f"出度 {len(outs)} / 入度 {indeg}。", ""]
    return "\n".join(lines)


def _write(nodes_dir: Path, rows: list[tuple[str, str, str, list[str], int, str]]) -> None:
    if nodes_dir.exists():
        for old in nodes_dir.glob("*.md"):
            old.unlink()
    nodes_dir.mkdir(parents=True, exist_ok=True)
    for nid, label, title, outs, indeg, note in rows:
        (nodes_dir / f"{nid}.md").write_text(
            node_markdown(nid, label, title, outs, indeg, note), encoding="utf-8")


def _count_back(nodes_dir: Path) -> tuple[int, int]:
    """① 走 markdown 往返 —— 读回磁盘上刚写的文件。"""
    back_nodes = back_edges = 0
    for p in nodes_dir.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        back_nodes += 1
        back_edges += text.count('\n  - "')
    return back_nodes, back_edges


def _manifest(source: str, rows, edges: dict, extra: dict) -> dict:
    indeg = Counter()
    for outs in edges.values():
        for t in outs:
            indeg[t] += 1
    n_edges = sum(len(v) for v in edges.values())
    return {
        "来源": source,
        "许可": "见 SNAP 页面；本仓库只存**转换后的**节点文件，原始包不进仓库",
        "节点数": len(rows),
        "图边数": n_edges,
        "标签取值": dict(Counter(r[1] for r in rows).most_common()),
        "标签数": len({r[1] for r in rows}),
        "出度 max": max((len(v) for v in edges.values()), default=0),
        "入度 max": max(indeg.values(), default=0),
        **extra,
    }


def build_email_eu(src: Path, out: Path) -> dict:
    labels = read_labels(src / "email-Eu-core-department-labels.txt.gz")
    known = set(labels)
    raw = read_pairs(src / "email-Eu-core.txt.gz")

    out_edges: dict[str, list[str]] = {n: [] for n in known}
    dangling = self_loops = 0
    for a, b in raw:
        if a == b:
            self_loops += 1
            continue
        if a not in known or b not in known:
            dangling += 1
            continue
        out_edges[a].append(b)
    for n in out_edges:
        out_edges[n] = sorted(set(out_edges[n]))

    indeg = Counter()
    for outs in out_edges.values():
        for t in outs:
            indeg[t] += 1

    rows = [(n, f"部门{labels[n]}", f"欧盟邮件网络 成员 {n}（部门{labels[n]}）",
             out_edges[n], indeg[n],
             "email-Eu-core：有向邮件图（A→B = A 给 B 发过信）；"
             "标签来自 department-labels")
            for n in sorted(labels, key=lambda x: int(x))]
    _write(out / "nodes", rows)
    back_n, back_e = _count_back(out / "nodes")
    raw_e = sum(len(v) for v in out_edges.values())
    m = _manifest(f"email-Eu-core（SNAP，{SOURCE_URL}）", rows, out_edges, {
        "原始边行数": len(raw),
        "丢弃的悬挂边数": dangling,
        "丢弃的自环数": self_loops,
        "两条数法": {"markdown 往返": {"节点": back_n, "边": back_e},
                     "直接读原始文件": {"节点": len(known), "边": raw_e}},
        "两条数法一致": back_n == len(known) and back_e == raw_e,
    })
    (out / "MANIFEST.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def build_hepth(src: Path, out: Path, *, dag: bool) -> dict:
    raw = read_pairs(src / "cit-HepTh.txt.gz")

    # ── 切片：只取 7 位 id（年份可读），每年取入度最高的 YEAR_TOPN 项 ──────
    all7 = [(a, b) for a, b in raw if year_of(a) and year_of(b)]
    indeg_all = Counter(b for _, b in all7)
    picked: set[str] = set()
    for yr in sorted({year_of(a) for a, _ in all7}):
        pool = [n for n in {a for a, _ in all7} | {b for _, b in all7}
                if year_of(n) == yr]
        pool.sort(key=lambda n: (-indeg_all.get(n, 0), n))
        picked.update(pool[:YEAR_TOPN])

    inside = [(a, b) for a, b in all7 if a in picked and b in picked]
    kept, dropped_back, dropped_same, self_loops = [], 0, 0, 0
    for a, b in inside:
        if a == b:
            self_loops += 1
            continue
        ya, yb = year_of(a), year_of(b)
        if ya < yb:
            dropped_back += 1
            continue
        if ya == yb:
            if dag:
                dropped_same += 1
                continue
        kept.append((a, b))

    out_edges: dict[str, list[str]] = {n: [] for n in picked}
    for a, b in kept:
        out_edges[a].append(b)
    for n in out_edges:
        out_edges[n] = sorted(set(out_edges[n]))

    indeg = Counter()
    for outs in out_edges.values():
        for t in outs:
            indeg[t] += 1

    rows = [(n, year_of(n), f"arXiv {n}（{year_of(n)}）", out_edges[n], indeg[n],
             "cit-HepTh：有向引文图（A→B = A 引用 B）；年份取自 arXiv id 前 2 位")
            for n in sorted(picked)]
    _write(out / "nodes", rows)
    back_n, back_e = _count_back(out / "nodes")
    raw_e = sum(len(v) for v in out_edges.values())
    m = _manifest(f"cit-HepTh（SNAP，{SOURCE_URL}）", rows, out_edges, {
        "切片规则": f"7 位 id 节点；每个年份取入度最高的 {YEAR_TOPN} 项",
        "原始边行数": len(raw),
        "非 7 位 id 被排除的边数": len(raw) - len(all7),
        "切片内的边数": len(inside),
        "切片外的悬挂边数": len(all7) - len(inside),
        "丢弃的反向边数（引用者更早）": dropped_back,
        "丢弃的同年互引数": dropped_same,
        "丢弃的自环数": self_loops,
        "dag（年份严格递减）": dag,
        "两条数法": {"markdown 往返": {"节点": back_n, "边": back_e},
                     "直接读原始文件": {"节点": len(picked), "边": raw_e}},
        "两条数法一致": back_n == len(picked) and back_e == raw_e,
    })
    (out / "MANIFEST.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


DATASETS = {
    "email-eu": ("_data/snap-email-eu", lambda s, o: build_email_eu(s, o)),
    "hepth": ("_data/snap-hepth", lambda s, o: build_hepth(s, o, dag=False)),
    "hepth-dag": ("_data/snap-hepth-dag", lambda s, o: build_hepth(s, o, dag=True)),
}


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 SNAP 数据集接成 ldv 语料")
    ap.add_argument("--dataset", required=True, choices=sorted(DATASETS))
    ap.add_argument("--src", default="_data/snap-hepth/raw",
                    help="原始 .gz 所在目录（email-eu 与 hepth 各在自己的 raw/）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(list(argv) if argv is not None else None)
    default_out, fn = DATASETS[a.dataset]
    src = Path(a.src)
    if a.dataset == "email-eu" and src == Path("_data/snap-hepth/raw"):
        src = Path("_data/snap-email-eu/raw")
    out = Path(a.out) if a.out else Path(default_out)
    m = fn(src, out)
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print(f"\n{a.dataset}：节点 {m['节点数']} · 边 {m['图边数']} · "
          f"标签 {m['标签数']} · 两条数法一致 {m['两条数法一致']}")
    return 0 if m["两条数法一致"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
