"""§I4 扇出改动的对照测量（**只报不判**）。

    python -m ldv.tools.measure_fanout

比两组：

    二分（改前）  C 把 k 个符号**贪心压成两堆**（B-tree 形状）
    k 叉（改后）  C **一个符号一个子方向**（trie 的自然形状）

A / B 两边**同一个实现** —— 它们本来就是二分（自然形状），
所以它们的行是**反向判据**：改签名**不该动**它们，动了就是改错了。
"""

from __future__ import annotations

import sys
from typing import Any

from ..checks._fixtures import items, keyset_queries, load, reach_queries, sequences
from ..core.kernel import Kernel
from ..plugins.reach import ReachPlugin
from ..plugins.sequence import END, SequencePlugin
from ..plugins.keyset import KeysetPlugin


# --- 改前的 sequence：k 个符号贪心压成两堆 -----------------------------------

class BinarySequence(SequencePlugin):
    """`§I4` 改造**之前**的 C —— 把 k 个符号贪心分成两堆。

    ⚠️ 只有 `split` 与现在不同，其余六个方法**逐字相同** ⇒ 单变量对照。
    """

    def split(self, parent, items_):  # noqa: ANN001, ANN201
        ids = sorted(str(it["id"]) for it in items_)
        p_star = self._lcp_of_group(ids)
        buckets: dict[str, list[str]] = {}
        for i in ids:
            s = self.seqs.get(i, ())
            tok = s[len(p_star)] if len(s) > len(p_star) else END
            buckets.setdefault(tok, []).append(i)
        if len(buckets) < 2:
            return None
        order = sorted(buckets, key=lambda t: (-len(buckets[t]), t))
        left: list[str] = []
        right: list[str] = []
        for tok in order:
            (left if len(left) <= len(right) else right).append(tok)
        if not left or not right:
            return None
        return (frozenset({p_star + (t,) for t in left}),
                frozenset({p_star + (t,) for t in right}))


# --- 建法 --------------------------------------------------------------------

def _build(mk_plug: Any, mk_root: Any, all_items: dict[str, Any],
           ids: list[str], init_n: int) -> Kernel:
    plug = mk_plug()
    k = Kernel(plug, {i: all_items[i] for i in ids[:init_n]})
    k.build(mk_root(plug))
    for i in ids[:init_n]:
        k.insert(i)
    for i in ids[init_n:]:
        k.insert(i, all_items[i])
    return k


def _profile(k: Kernel, which: str, nodes: dict, edges: dict) -> dict[str, Any]:
    st = k.stats()
    leaves = [d for d in k.all_directions() if not k.children_of(d)]
    depths = {}
    for d in k.all_directions():
        depths[d.rank] = depths.get(d.rank, 0) + 1
    qs = keyset_queries(nodes) + reach_queries(nodes, edges)
    from ..checks.contract import false_positive_profile

    fp = false_positive_profile(k, k.plugin, qs)
    return {
        "方向数": st["方向"],
        "层数": max(depths) if depths else 0,
        "各层": "/".join(str(depths[r]) for r in sorted(depths)),
        "最大扇出": st["最大扇出"],
        "§K2 判空": st["§K2 判空"],
        "判空·声明": st["判空·声明"],
        "判空·切不动": st["判空·切不动"],
        "叶容量max": st["最大叶容量"],
        "叶容量avg": st["平均叶容量"],
        "假阳率": f"{fp['假阳率']:.1%}",
        "叶数": len(leaves),
    }


CASES: dict[str, Any] = {
    "keyset": (lambda: KeysetPlugin(), lambda p: p.merge([])),
    "reach": (lambda: ReachPlugin(EDGES), lambda p: frozenset(NODES)),
    "sequence": (lambda: SequencePlugin(sequences(NODES, EDGES)), lambda p: frozenset({()})),
    "sequence(二分)": (lambda: BinarySequence(sequences(NODES, EDGES)),
                       lambda p: frozenset({()})),
}

NODES: dict = {}
EDGES: dict = {}


def main() -> int:
    global NODES, EDGES
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 未展开（跳过），这不是通过。")
        return 0
    nodes, edges, _ = loaded
    NODES, EDGES = nodes, edges
    ids = sorted(nodes)
    all_items = items(nodes)
    n = len(ids)

    print(f"语料 {n} 项 / {sum(len(v) for v in edges.values())} 边\n")

    print("═══ 一次建完（36 项） ═══")
    print(f"{'方向':<14}{'方向数':>5}{'层数':>5}{'最大扇出':>9}{'判空':>5}{'声明':>5}"
          f"{'切不动':>7}{'叶max':>7}{'叶avg':>7}{'假阳率':>8}   各层")
    for label, (mk_plug, mk_root) in CASES.items():
        k = _build(mk_plug, mk_root, all_items, ids, n)
        p = _profile(k, label, nodes, edges)
        print(f"{label:<14}{p['方向数']:>5}{p['层数']:>5}{p['最大扇出']:>9}"
              f"{p['§K2 判空']:>5}{p['判空·声明']:>5}{p['判空·切不动']:>7}"
              f"{p['叶容量max']:>7}{p['叶容量avg']:>7}{p['假阳率']:>8}   {p['各层']}")

    print("\n═══ 分批维护：先建 k 项、其余后添 ═══")
    print("（**反向判据**：A / B 不该动；C 动是本次改动的**效果**）\n")
    ks = (2, 6, 12, 18, 24, 30, 34)
    head = "".join(f"{'k=' + str(k):>10}" for k in ks)
    print(f"{'方向':<14}{'一次建完':>10}{head}")
    for label, (mk_plug, mk_root) in CASES.items():
        full = _profile(_build(mk_plug, mk_root, all_items, ids, n), label, nodes, edges)
        cells = ""
        for k in ks:
            p = _profile(_build(mk_plug, mk_root, all_items, ids, k), label, nodes, edges)
            cells += f"{p['方向数']:>10}"
        print(f"{label:<14}{full['方向数']:>10}{cells}")

    print(f"\n{'方向':<14}{'一次建完':>10}" + "".join(f"{'k=' + str(k):>10}" for k in ks))
    print("（同一批，报**最大叶容量** —— 它才是分辨率；方向数是记账）")
    for label, (mk_plug, mk_root) in CASES.items():
        full = _profile(_build(mk_plug, mk_root, all_items, ids, n), label, nodes, edges)
        cells = ""
        for k in ks:
            p = _profile(_build(mk_plug, mk_root, all_items, ids, k), label, nodes, edges)
            cells += f"{p['叶容量max']:>10}"
        print(f"{label:<14}{full['叶容量max']:>10}{cells}")

    print("\n═══ 「增量 ≡ 全量」最早同构的 k ═══")
    from ..checks.divergence import divergence_profile
    from ..checks._fixtures import make_builder

    print(f"{'方向':<14}{'全量方向数':>11}{'最早同构的 k':>13}")
    for label, which in (("keyset", "keyset"), ("reach", "reach"), ("sequence", "sequence")):
        prof = divergence_profile(make_builder(which, nodes, edges), nodes, ids)
        print(f"{label:<14}{prof['全量']['方向数']:>11}{str(prof['最早同构的 k']):>13}")
    print("  sequence(二分) = 35（改前实测；见 `C10-签名改动复核.md`）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
