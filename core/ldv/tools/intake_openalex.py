"""把 **OpenAlex** 的一个切片接成 ldv 语料。

    跑法：python -m ldv.tools.intake_openalex --out _data/openalex-citations

---

## 这份料要证明什么（**先写，再跑**）

    接进来之后：
      ① 装载读数 `节点数 >= 3000` 且 `图边数 >= 节点数`（内部引用密度 ≥ 1.0）
      ② `run_checks` 在三个方向的**批建路径**上红数为 0
      ③ `B17` 若报「跳过」，必须同时给出「等价类全是单点」的**实测计数**

    任一条不成立 ⇒ 报出**具体哪一条**、以及当时的读数。
    拿不准的量（假阳率 / 叶容量 / 细化量 / 耗时）**只披露、不判过**。

### ★ 跑完之后的对照 —— **② 被证伪了，而且证伪得有价值**

    ① 成立    3907 项 / 24156 边（两条独立数法一致）
    ② **不成立**  `run_checks` 报红 **1** —— `B17`（`reach`）
    ③ 未触发    `B17` 在 `reach` 上**不再跳过**（这份料有环），直接红

`B17` 红**不是新缺陷**，是设计文档 `§10 留白` 早就记下的「`§K2` 第二半」缺口
在**真实语料上**露了出来。设计文档原话是：

> `reach` 在**合环图**上会把互相可达的两项劈成两个**覆盖完全相同**的子方向。
> **真实语料是 DAG，所以这个缺口在语料上不露**

⇒ 这份公开数据集**证伪了最后半句**：引用图**有环**（论文互引），
   281 项切片里有 **6** 个大小 ≥2 的强连通分量，而 `B17` 报的「被拆开的等价类」
   **正好 6 个**。

⇒ 所以验收那边（`tests/test_intake.py`）的判据 ② 从「红数为 0」改成
   **基线守卫**（与 `B18` 同一形状）：新增红 ⇒ 失败，基线条目失效 ⇒ 也失败。
   「红数为 0」写成判据，等于**要求一条已知的缺口消失**。

⚠️ 判据**先写再跑**的全部意义就在这里：事后写「红 1 是预期的」是**圆场**，
   事前写「红数应为 0」才是**可证伪**的。这条留着，不删。

---

## 为什么选 OpenAlex

- **公开且 CC0**，不需要密钥；有正式 API 与批量快照两条路
- 一份记录里**同时**带三种结构，正好对上三个方向：

      `type` + `source.type`      → 键集（有界的枚举轴）      → 方向 A
      `referenced_works`          → 图（引用边）              → 方向 B
      `type` 沿引用链的**序列**    → 序列（trie）              → 方向 C

  ⚠️ 方向 C 的 `seq(x)` 由 `_fixtures.sequences()` 从**图 + `type` 值**算出来，
     **不读正文**。所以这份料**不需要**摘要/标题就能压满三个方向。

---

## 切片怎么取的（可复现）

1. **种子**：`publication_year:2020-2021` 且 `cited_by_count:>200`，按被引降序取前 200。
2. **核心**：种子们 `referenced_works` 的并集里，**被 ≥K 个种子共引**的那些。
   `K=2` ⇒ 约 3900 篇 —— 这是这批工作共同的知识底座，是**可复现的判据**，
   不是「随便抽 4000 篇」。
3. **节点** = 种子 ∪ 核心；**边** = 落在节点集内的引用。

---

## 映射表（外部概念 → 本仓库概念）

| OpenAlex | 本仓库 | 说明 |
|---|---|---|
| `id`（`W…`） | 节点 `id` | 去 URL 前缀 |
| `type` | `type`（枚举，**19 值**） | 直接搬：article / review / conference-paper / preprint / … |
| `primary_location.source.type` | `source.kind`（枚举，**6 值**） | 出处种类；无 source ⇒ `无出处` |
| ↳ 由 `source.kind` 归成三类 | `evidence_status`（枚举，**3 值**） | 同行评审 / 仓储 / 未知 |
| `title` | `title`（标量，**载荷**） | 截 120 字；进不了键 |
| `doi` | `source.ref` | 出处引用 |
| `publication_year` | `scope`（标量，**载荷**） | |
| `primary_topic.field.display_name` | `notes`（标量，**载荷**） | **刻意不进键** —— 见下 |
| `referenced_works` ∩ 节点集 | `relations`（块列表） | 内部引用边 |

## DROP 表（丢了什么、多少、为什么）

| 丢掉的 | 规模 | 为什么 |
|---|---|---|
| 指向节点集**之外**的引用 | 每批实测，见 `MANIFEST.json` | 语料是「**这份切片**」；外部引用不属于它。塞进来会让「这份切片是什么」看不清 |
| `authorships` / `institutions` / `funders` | 全部 | **无界** —— 拿它当键就是枚举爆炸。作者是**载荷**，不是键 |
| `primary_topic.field` 当**键** | 26 个有界取值 | ⚠️ 它**本可以**当第四个键轴，但 loader 的枚举词表固定是三个（`type` / `evidence_status` / `source.kind`）。开第四个轴要改 `loader.ENUM_FIELDS` 这个**格式契约**，那是另一个决定 ⇒ 本轮当载荷 |
| `abstract_inverted_index` | 全部 | 方向 C 的序列来自**图**，不读正文。留着只是让文件大 20 倍 |
| `cited_by_count` / `counts_by_year` / `fwci` | 全部 | 它们是**读数**，不是结构。接进来会把「被引高」混进「结构位置」 |
| `is_retracted` | 全部 | 实测**全为 `False`** —— 一个恒定值不是轴，是噪声 |
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ._intake_common import scalar as _scalar

BASE = "https://api.openalex.org/works"
MAILTO = "ldv-intake@example.org"
UA = f"ldv-intake/0.1 (mailto:{MAILTO})"

#: 只取这几个字段 —— 每条记录从 ~82 KB 降到 ~3 KB。
#: ⚠️ 这里**必须**逐个列出来：不列 `select` 的话一次 200 条就 16 MB，
#: 而多出来的 40 多个字段**一个都没用上**。
SELECT = ("id,doi,title,publication_year,type,primary_location,"
          "primary_topic,referenced_works")

SEED_FILTER = "publication_year:2020-2021,cited_by_count:>200"
SEED_N = 200
COCITE_K = 2
ID_BATCH = 50                 # `filter=openalex_id:` 每批多少个（URL 长度与请求数的折中）
SLEEP = 0.15                  # 礼貌间隔

#: `source.kind` → `evidence_status` 的归类。**这条映射是判据，不是装饰**：
#: 「出处是不是一个经过同行评审的载体」正是 `evidence_status` 想说的那句话。
REVIEWED = {"journal", "conference", "book series"}


def _get(params: dict[str, Any]) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _short(wid: str) -> str:
    """`https://openalex.org/W123` → `W123`。"""
    return wid.rsplit("/", 1)[-1]


def fetch_seeds(cache: Path, n: int = SEED_N) -> list[dict]:
    cache.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha1(f"{SEED_FILTER}|{n}".encode("utf-8")).hexdigest()[:12]
    f = cache / f"seeds-{tag}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    d = _get({"filter": SEED_FILTER, "sort": "cited_by_count:desc",
              "per-page": n, "select": SELECT, "mailto": MAILTO})
    f.write_text(json.dumps(d["results"], ensure_ascii=False), encoding="utf-8")
    return d["results"]


def fetch_by_ids(ids: list[str], cache: Path) -> dict[str, dict]:
    """按 id 批量取记录。**每批落盘** —— 断了不用从头再来。

    ⚠️ 缓存文件名带**这一批 id 的哈希**，不是序号。
       按序号命名是错的：换一个共引阈值 K，同一序号对应的 id 完全不同 ——
       缓存会**静默地**把上一次的料当成这一次的料，
       而症状是「语料看着好好的，只是内容不对」。那是接料里最难查的一类。
    """
    cache.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    todo: list[str] = []
    for i in range(0, len(ids), ID_BATCH):
        chunk = ids[i:i + ID_BATCH]
        tag = hashlib.sha1("|".join(chunk).encode("utf-8")).hexdigest()[:12]
        f = cache / f"batch-{tag}.json"
        if f.exists():
            recs = json.loads(f.read_text(encoding="utf-8"))
        else:
            q = "|".join(chunk)
            d = _get({"filter": f"openalex_id:{q}", "per-page": ID_BATCH,
                      "select": SELECT, "mailto": MAILTO})
            recs = d["results"]
            f.write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
            time.sleep(SLEEP)
        got = {_short(r["id"]): r for r in recs}
        out.update(got)
        todo += [c for c in chunk if _short(c) not in got]
    if todo:
        # **不静默丢** —— 取不到的 id 要报出来，否则「语料少了一批」和「语料就这么多」长得一样
        print(f"  ⚠ 有 {len(todo)} 个 id 没取到（例：{todo[:3]}）", file=sys.stderr)
    return out


def source_type(rec: dict) -> str:
    loc = rec.get("primary_location") or {}
    src = loc.get("source") or {}
    return str(src.get("type") or "无出处")


def evidence_of(kind: str) -> str:
    return "同行评审" if kind in REVIEWED else ("仓储" if kind != "无出处" else "未知")


def node_markdown(rec: dict, internal: list[str]) -> str:
    wid = _short(rec["id"])
    kind = source_type(rec)
    field = ((rec.get("primary_topic") or {}).get("field") or {}).get("display_name")
    lines = [
        "---",
        f'id: "{wid}"',
        f'type: "{_scalar(rec.get("type") or "other", 40)}"',
        f'title: "{_scalar(rec.get("title"))}"',
        f'evidence_status: "{evidence_of(kind)}"',
        "source:",
        f'  ref: "{_scalar(rec.get("doi") or rec["id"], 90)}"',
        f'  kind: "{_scalar(kind, 40)}"',
        f'scope: "发表年 {rec.get("publication_year")}；出处 {_scalar(kind, 40)}"',
        f'notes: "{_scalar(field, 60)}"',
        f'filled_by: "OpenAlex 切片（CC0）"',
    ]
    if internal:
        lines.append("relations:")
        lines += [f'  - "{r}"' for r in internal]
    else:
        lines.append("relations: []")
    lines += ["---", "", f'{_scalar(rec.get("title"), 200)}', ""]
    return "\n".join(lines)


def build(out: Path, cocite_k: int = COCITE_K, seeds_n: int = SEED_N) -> dict:
    raw = out / "raw"
    nodes_dir = out / "nodes"
    seeds = fetch_seeds(raw, seeds_n)
    print(f"种子 {len(seeds)} 篇")

    co = Counter()
    for w in seeds:
        for r in w.get("referenced_works") or []:
            co[r] += 1
    core = sorted(r for r, c in co.items() if c >= cocite_k)
    print(f"共引 ≥{cocite_k} 的核心 {len(core)} 篇（去重被引 {len(co)} 篇）")

    recs: dict[str, dict] = {_short(w["id"]): w for w in seeds}
    missing_core = [r for r in core if _short(r) not in recs]
    print(f"取核心记录（{len(missing_core)} 个 id）…")
    recs.update(fetch_by_ids(missing_core, raw))

    ids = set(recs)
    edges: dict[str, list[str]] = {}
    dropped = 0
    for wid, rec in recs.items():
        inside = sorted({_short(r) for r in rec.get("referenced_works") or []} & ids)
        edges[wid] = inside
        dropped += len(set(rec.get("referenced_works") or [])) - len(inside)

    if nodes_dir.exists():
        for old in nodes_dir.glob("*.md"):
            old.unlink()
    nodes_dir.mkdir(parents=True, exist_ok=True)
    for wid in sorted(recs):
        (nodes_dir / f"{wid}.md").write_text(
            node_markdown(recs[wid], edges[wid]), encoding="utf-8")

    n_edges = sum(len(v) for v in edges.values())
    manifest = {
        "来源": "OpenAlex (https://openalex.org)，CC0",
        "种子过滤": SEED_FILTER,
        "种子数": len(seeds),
        "共引阈值 K": cocite_k,
        "去重被引数": len(co),
        "节点数": len(recs),
        "图边数": n_edges,
        "丢弃的外部引用数": dropped,
        "type 取值": dict(Counter(str(r.get("type")) for r in recs.values()).most_common()),
        "source.kind 取值": dict(Counter(source_type(r) for r in recs.values()).most_common()),
        "evidence_status 取值": dict(
            Counter(evidence_of(source_type(r)) for r in recs.values()).most_common()),
        "notes（topic field，载荷）取值数": len(
            {(r.get("primary_topic") or {}).get("field", {}).get("display_name")
             for r in recs.values()}),
        "年份范围": [min(r.get("publication_year") or 0 for r in recs.values()),
                     max(r.get("publication_year") or 0 for r in recs.values())],
    }
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 OpenAlex 切片接成 ldv 语料")
    ap.add_argument("--out", default="_data/openalex-citations")
    ap.add_argument("--cocite-k", type=int, default=COCITE_K,
                    help="共引阈值：被 ≥K 个种子引用才进核心（K 越大语料越小）")
    ap.add_argument("--seeds", type=int, default=SEED_N)
    a = ap.parse_args(list(argv) if argv is not None else None)
    m = build(Path(a.out), a.cocite_k, a.seeds)
    print(json.dumps(m, ensure_ascii=False, indent=2))
    # ⚠️ 判据在这里**只报读数**，不判过 —— 「够不够大」由调用方（报告/测试）判，
    #    否则这条工具自己既是运动员又是裁判。
    print(f"\n节点 {m['节点数']} · 边 {m['图边数']} · 丢弃外部引用 {m['丢弃的外部引用数']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
