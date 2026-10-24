"""同一份语料、同一个需求：**普通检索（BM25）** 与 **ldv** 各交付什么 —— 实跑对照。

    跑法（cwd = repo）：
        python -m ldv.tools.compare_bm25

⚠️ **零第三方**：BM25 自己写（`k1=1.2` / `b=0.75`，中文走**字符二元组** ——
   不引 jieba；二元组是中文免分词检索的常规做法）。标准库跑完。

⚠️ **不编读数**：两边都**真跑**，逐字贴。这份输出是
   `docs/与普通检索系统的质量对照.md` 里那些数的**来源**。

⚠️ **它只报不判**：不进 `run_checks` 的退出码，也不是一条判据 ——
   它是**对照实验**，读数由那份文档解释。

---

## 为什么索引的文本**不含栏位值**

首版把栏位值（`type: 判断点` 那一行）也编进 BM25 的索引，于是它「全对」——
那不是因为它会匹配，是因为**它拿的就是答案**。改掉之后露出**对称实证**：

    `type=判断点`   栏位里 14 条 / 正文里命中 **0** 条
    `type=案例`     栏位里 5 条  / 正文里命中 **7** 条（其中 2 条不是案例）

⇒ 这才是「词袋看不见没写出来的属性」的形状。**真值由栏位逐项筛**得到
（`key ∈ item.keys`），不拿任何一方的输出当答案。

---

## ldv 那一侧走的是**公开接口**

`build_keyset` → `spec_for` / `coverage_of` → `view_persist` → `retriever.retrieve`
→ `kernel.query`。`Query` 里那个 `ideal` 是**外生声明的 ground truth**
（内核不猜 —— 有它 `B1` 才有得比）。
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .. import retriever
from ..checks._fixtures import build_keyset, coverage_of, find_corpus, load
from ..checks.abstraction import build_views, load_spec_file, spec_for
from ..core import view_persist
from ..core.kernel import Query

ASCII = re.compile(r"[a-z0-9]+")
BAR = "─" * 78


# ═══ ① 最小 BM25（零依赖，中文走字符二元组） ═══════════════════════════════════


def tokenize(text: str) -> list[str]:
    """ASCII 词 + 汉字**二元组**。二元组是中文免分词的常规做法（不引 jieba）。"""
    low = text.lower()
    out = ASCII.findall(low)
    for seg in re.findall(r"[\u4e00-\u9fff]+", low):
        if len(seg) == 1:
            out.append(seg)
        else:
            out += [seg[i:i + 2] for i in range(len(seg) - 1)]
    return out


class BM25:
    """经典 BM25（Robertson–Walker 那套 `k1` / `b`）—— **只排不判**。"""

    def __init__(self, docs: dict[str, str], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tf = {d: Counter(tokenize(t)) for d, t in docs.items()}
        self.len = {d: sum(c.values()) for d, c in self.tf.items()}
        self.avg = sum(self.len.values()) / max(1, len(self.len))
        self.n = len(docs)
        df: Counter = Counter()
        for c in self.tf.values():
            df.update(c.keys())
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def rank(self, query: str) -> list[tuple[str, float]]:
        qs = tokenize(query)
        scored = []
        for d, tf in self.tf.items():
            s = 0.0
            for t in qs:
                f = tf.get(t, 0)
                if f:
                    s += (self.idf.get(t, 0.0) * f * (self.k1 + 1)
                          / (f + self.k1 * (1 - self.b + self.b * self.len[d] / self.avg)))
            if s > 0:
                scored.append((d, s))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored


def doc_text(n) -> str:
    """索引的**文本** = 正文 + 标题 / 范围 / 备注 / 别名 / 线索。**不含栏位**（见模块开头）。"""
    f = n.fields
    return " ".join([str(f.get("title") or ""), str(f.get("scope") or ""),
                     str(f.get("notes") or ""), " ".join(map(str, f.get("aliases") or [])),
                     " ".join(map(str, f.get("cues") or [])), n.body])


# ═══ ② ldv 侧 ═════════════════════════════════════════════════════════════════


def ldv_run(nodes, edges, need: dict):
    kernel, plugin = build_keyset(nodes)
    corpus = {"项数": len(nodes), "边数": sum(len(v) for v in edges.values())}
    spec, why = spec_for(load_spec_file(find_corpus()), "keyset", corpus)
    assert spec is not None, why
    cover = coverage_of("keyset", nodes, edges)
    disk = view_persist.read(view_persist.default_path(find_corpus()))
    rec = view_persist.from_dict(disk, kernel, cover, plugin) if disk else None
    if rec is None:
        rec = view_persist.from_dict(
            view_persist.to_dict(build_views(kernel, spec, cover, plugin)),
            kernel, cover, plugin)
    r = retriever.retrieve(kernel, plugin, need, spec=spec, recognition=rec)
    bar = need["keyset"]
    q = Query(ideal=frozenset(bar.get("ideal") or ()),
              require=frozenset(bar.get("require") or ()),
              forbid=frozenset(bar.get("forbid") or ()),
              min_rank=int(bar.get("min_rank", 1)), label=str(bar.get("label", "")))
    return r, kernel, kernel.query(q)


def show_ldv(tag: str, need: dict, nodes, edges, truth: list[str]) -> None:
    r, kernel, qr = ldv_run(nodes, edges, need)
    got = sorted(qr.hit_items)
    print(f"\n{BAR}\n【ldv】需求 {tag}：栏 = `require={need['keyset']['require']}`"
          f"\n{BAR}")
    print(f"  **项级答案**（`hit_items`，状态 {qr.status}）：{len(got)} 项 {got}")
    print(f"  ★ 与真值比对：{'**完全一致**' if got == sorted(truth) else '**不一致**'}"
          f"（真值 {len(truth)} 项）")
    print(f"  **方向级候选**（= `T1` 的「不用视图直接遍历」那条）：{len(r.candidates)} 个方向")
    print(f"  **解释** {len(r.reasons)} 条（每条 = 方向 + 它覆盖里的一个项，**指得到实际结果**）：")
    for did, item in r.reasons[:4]:
        print(f"     {did} 覆盖 {item}")
    if len(r.reasons) > 4:
        print(f"     … 其余 {len(r.reasons) - 4} 条同类")
    print(f"  **块级画像**（{len(r.profiles)} 块 —— 「你那一带是什么」）：")
    for p in r.profiles[:4]:
        head = ", ".join(sorted(p.block)[:3]) + ("…" if len(p.block) > 3 else "")
        print(f"     [{head}] 成员 {p.n_members}｜读法 **{p.reading}**｜建议 **{p.advice}**")
    if len(r.profiles) > 4:
        print(f"     … 其余 {len(r.profiles) - 4} 块")


def show_bm25(tag: str, query: str, docs: dict[str, str], truth: list[str],
              top: int = 8) -> None:
    res = BM25(docs).rank(query)
    ids = [d for d, _ in res]
    miss = [t for t in truth if t not in ids]
    extra = [d for d in ids if d not in truth]
    print(f"\n{BAR}\n【BM25】需求 {tag}：查询词 = {query!r}\n{BAR}")
    print(f"  命中 {len(res)} 项，前 {min(top, len(res))} 名：")
    for d, s in res[:top]:
        mark = "" if d in truth else "   ← **不在真值里**"
        print(f"     {s:8.4f}  {d}{mark}")
    if len(res) > top:
        print(f"     … 其余 {len(res) - top} 项")
    print(f"  ★ 与真值比对：**漏 {len(miss)}** {miss}｜**多 {len(extra)}** {extra}")


def main() -> int:
    nodes, edges, _ = load()
    docs = {nid: doc_text(n) for nid, n in nodes.items()}
    n_edge = sum(len(v) for v in edges.values())
    print(f"语料：{find_corpus()}｜{len(nodes)} 项 / {n_edge} 边")
    print("索引的文本 = 正文 + `title` / `scope` / `notes` / `aliases` / `cues`"
          "（**不含栏位值** —— 见模块 docstring）")

    cases = [
        ("A", "判断点", "type=判断点", "我要**判断点**（`type=判断点`）"),
        ("B", "案例", "type=案例", "我要**案例**（`type=案例`）"),
        ("C", "有争议", "evidence_status=有争议", "我要**有争议**的项"),
    ]
    for tag, word, key, human in cases:
        truth = sorted(nid for nid, n in nodes.items() if key in n.keys)
        print(f"\n{'═' * 78}\n需求 {tag} —— {human}　真值 {len(truth)} 项\n{'═' * 78}")
        print(f"  真值：{truth}")
        show_bm25(tag, word, docs, truth)
        show_ldv(tag, {"keyset": {"ideal": truth, "require": [key], "label": human}},
                 nodes, edges, truth)

    # ── 需求 D：**话题性**（BM25 的主场；ldv 表达不了） ──────────────────────
    print(f"\n{'═' * 78}\n需求 D —— 「DAPO 的消融实验」（**话题性**需求）\n{'═' * 78}")
    print("  ★ 这条**没有对应的栏位**：`type` / `evidence_status` / `source.kind` 里")
    print("     没有「话题」这个轴 ⇒ ldv 的 `Query`（只有 ideal/require/forbid/min_rank）")
    print("     **表达不出来**。而它明写「不做匹配」⇒ 这不是「没做」，是**不该它做**。")
    res = BM25(docs).rank("DAPO 消融 增量")
    print(f"\n【BM25】命中 {len(res)} 项，前 8 名：")
    for d, s in res[:8]:
        print(f"     {s:8.4f}  {d}")
    print("\n【ldv】—— **不参与**。（硬要问只能退化成结构问法，例如「`type=论据` 那一带")
    print("  有哪些方向盖住了它」—— 那答的是**结构**，不是「哪几项在讲 DAPO」。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
