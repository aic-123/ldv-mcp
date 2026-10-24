"""「增量 ≡ 全量」—— 只报不判的**度量**（设计文档 §7.1 的度量族 / §10 的留白）。

    python -m ldv.run_checks            # 度量随报告一起输出

---

## 为什么这条必须存在，而且必须**只报不判**

「增量维护后的结构」与「从零重建同一集合的结构」**该不该一样**？
两篇独立文献把它当**正确性标准**在证：

    Acar 2002   「the adapted output is the same as the output of a complete
                 re-evaluation with the changed inputs」（p.1）
    Godin 1995  把增量算法的结果与**三个 batch 算法**对比（p.1 摘要）

而我们**实测它不成立**（`C7` §3.1.3：尺度相同、指纹不同）。
⇒ 所以它不能直接进退出码 —— 那会让整个套件常红，红成噪声。
但**也不能不报** —— 不报就等于假装它成立。

    ⇒ 进 `report()`，不进退出码（与「假阳率 / §K2 判空 / 叶容量」同一族）

它是 `C8` §7 第 1 步要的那条检查，也是第 2 / 3 步的**决策依据**：
哪几个 k 同构、哪几个不同构。

---

## 比的是什么（以及为什么这样比才有意义）

    全量   `build(全部项)`              —— 内核**一开始就认识**所有项
    增量   `build(前 k 项)` + 后添 N−k  —— 内核**后来才认识**它们

⚠️ **两侧结束时项集必须完全相同**，否则比的是「项多寡」不是「结构」。
本模块把这一条**显式断言**（`项集一致`），不一致就报红 —— 那是度量本身坏了，
不是被测对象坏了。这一条防的是**假的不同构**。

⚠️ **根 payload 必须跟着 subset 走**（见 `_fixtures.make_builder`）：
`reach` 的根是「全部项当锚点」，增量时只该是「当时已知的锚点」。
用全集就等于给增量那侧偷看答案。

---

## ★ 参照侧必须按**同一个项集**重建（不是拿全集当参照）

`§10.2` 出路 (1) 落地之后，「认识」与「**纳入**」成了两件事：
落在**根覆盖之外**的项内核认识、但**不塞进结构**（`Kernel._outside_root`）。
`reach` 的增量侧因此**合法地**少纳入一些项 —— 实测 `k=2` 时根只盖住 8 / 36 项。

⇒ 这时候再拿**全集**当参照，比的就是「项多寡」—— 正是本模块要防的那件事。
⇒ 所以每个 k 的参照侧是 `build(纳入项集)`：**同一个项集**的一次建完。

    实测（36 项语料，reach）：k=2 增量侧纳入 8 项 ⇒ 参照侧也是那 8 项
    ⇒ 「同构」问的是「这 8 项上，先建后添与一次建完一不一样」——
      那才是「增量 ≡ 全量」的原意。

⚠️ 顺带：`全量`（36 项）那一栏仍然报全集，因为它是**参照的参照**。
   两者要分开看 —— 把「根覆盖之外」并进「不同构」就等于把**范围**当成**结构**。

---

## ★ 比较器自检：它必须**能报出两个值**

一个永远报「不同构」的度量，和一个永远报「同构」的度量，
**信息量都是 0** —— 这正是「空转与通过长得一模一样」在度量上的形态。

所以每次跑都带两条自检：

    同路径必须同构      `build(全部)` vs `build(全部)`          → 必须 True
    项集不同必须不同构   `build(全部)` vs `build(全部[:-1])`      → 必须 True

两条一起成立 ⇒ 比较器**不是常量**。任一条不成立 ⇒ 本次所有比对都不可信。

---

## 实测读数（36 项语料，2026-10-05；N = 36，扫 k ∈ {2,6,12,18,24,30,34,35}）

    方向       全量方向/叶容量   k=2   k=6  k=12 k=18 k=24 k=30 k=34 k=35   最早同构的 k
    ─────────────────────────────────────────────────────────────────────────────────────
    keyset        25 / 10      24=24 24=24 24=24 23=23 23=23  4=4  4=4  0=0   **35**（= N−1）
    reach         71 / 1       69=69 71=71 67=67 59=59 48=48 49=49 32=32 28=28 **无**
    sequence      16 / 10      12=17 14=18  7=10  6=8   0=0  0=0  0=0  0=0   **24**

（表里的数字是「全量独有 = 增量独有」的方向个数，0 就是同构。）

⇒ **三个结论，都是决策依据**：

    ① 没有一个方向能在「后添 ≥ 2 项」时与全量同构。
       keyset 要 k = N−1，sequence 要 k = 24，reach 到 N−1 都不同构。
    ② 差异**不在分辨率**：三个方向的**叶容量逐项相同**（keyset 10、reach 1、sequence 10）。
       ⇒ 想让它同构，**加大初始批次没用**，得做**规范重建**（`§10` 第 3 步）。
       ⚠️ 注意 keyset 的差从 23 直接掉到 4（k=24→30）再卡在 4 不动 ——
          「差变小」与「同构」是两件事，**不能拿差当收敛判据**。
    ③ ⚠️ **方向数只有 A / B 逐项相同**：`sequence` 随 k 变（**21 → 16**）。
       这是 k 叉的**结构后果**，不是记账差异 —— 扇出在**首次展开**时定形（§K2），
       初始批次小 ⇒ 当时看得见的符号少 ⇒ 得在更深处再分一次 ⇒ 方向数更多。
       **变的是记账，不变的是分辨率。**
       ⚠️ `sequence` 的差还**不对称**（k=2：全量独有 12 / 增量独有 17）——
       增量侧的方向**更多**，与「初始批次小 ⇒ 多分一层」同向。

机制上的必然：`expand(d)` 劈的是 `d` 的**当前**成员集，一旦劈过，分区就定死；
后添的项只能顺着已有的树往下走。⇒ 「知道未来」在这里**确实**改变结构。
"""

from __future__ import annotations

from typing import Any

from ._fixtures import build_incremental, items
from .structure import _shape

#: 扫描的初始批大小。**不扫 1**：k=1 时根只有一项，连劈都劈不动（§K2 情形①），
#: 报出来的差异是「根是叶」，与「结构不同构」不是一回事，混进来会污染读数。
#:
#: ⚠️ **末尾那两个（`N−2` / `N−1`）不能省** —— 它们才是「最早同构的 k」有没有值的关键。
#: 实测：keyset / sequence 恰好要到 `k = N−1` 才同构，只扫到 30 会误报成「全部不同构」。
DEFAULT_KS: tuple[int, ...] = (2, 6, 12, 18, 24, 30)


def _scan_points(n: int, ks: tuple[int, ...]) -> list[int]:
    pts = {k for k in ks if 1 < k < n}
    pts |= {k for k in (n - 2, n - 1) if 1 < k < n}
    return sorted(pts)


def _stat(kernel: Any) -> dict[str, Any]:
    st = kernel.stats()
    return {"方向数": st["方向"], "最大叶容量": st["最大叶容量"], "叶": st["叶"],
            # ★ `叶容量` **看不见**停在内部节点的项（§10.2 出路 (4)）；
            #   `最大停留数` 才是分辨率。两者在批建路径上相等 —— 见 `kernel.stats()`。
            "最大停留数": st["最大停留数"], "滞留": st["滞留"]}


def _diff(ref: frozenset, got: frozenset) -> dict[str, int]:
    """两个指纹差在哪儿 —— 只报数，不解释。"""
    return {"全量独有": len(ref - got), "增量独有": len(got - ref)}


def divergence_profile(build: Any, nodes: dict[str, Any], all_ids: list[str],
                       ks: tuple[int, ...] = DEFAULT_KS) -> dict[str, Any]:
    """扫描 `k`，比对「先建 k 再维护」与「**同项集**一次建完」的结构。

    `build(subset_ids)` 由 `_fixtures.make_builder` 造。
    返回的东西**全部是度量**，没有一项该进退出码。

    ⚠️ 参照侧是 `build(纳入项集)`，**不是** `build(全部项)` —— 见模块开头。
       拿全集当参照，会在「根覆盖之外」存在时把**范围**读成**结构**。
    """
    ids = sorted(all_ids)
    full, _ = build(ids)
    ref = _shape(full)
    n = len(ids)

    # --- 比较器自检（两条，必须都成立） ---
    same_path, _ = build(ids)
    diff_items, _ = build(ids[:-1]) if n > 1 else (None, None)
    selfcheck = {
        "同路径必须同构": _shape(same_path) == ref,
        "项集不同必须不同构": (diff_items is None
                              or _shape(diff_items) != ref),
    }

    rows: dict[int, dict[str, Any]] = {}
    first_iso: int | None = None
    pts = _scan_points(n, ks)
    for k in pts:
        inc = build_incremental(build, nodes, ids[:k], ids[k:])
        placed = sorted(inc.placed)
        # ⚠️ 参照侧用**同一个项集**重建 —— 否则比的是项多寡（见模块开头）。
        ref_k, _ = build(placed)
        sig, sig_ref = _shape(inc), _shape(ref_k)
        iso = sig == sig_ref
        if iso and first_iso is None:
            first_iso = k
        rows[k] = {
            "同构": iso,
            # 参照侧按 `placed` 建 ⇒ 这一条现在是**构造上成立**的；
            # 留着它是为了让「度量坏了」这件事仍然能被看见（若哪天不成立）。
            "项集一致": set(placed) == set(ref_k.placed),
            "根覆盖之外": len(set(inc.items) - set(placed)),
            "纳入": len(placed),
            **_stat(inc),
            "参照": _stat(ref_k),
            "差": _diff(sig_ref, sig),
        }

    return {
        "项数": n,
        "全量": _stat(full),
        "各 k": rows,
        "最早同构的 k": first_iso,
        "比较器自检": selfcheck,
        "扫描的 k": pts,
    }


def render_divergence(prof: dict[str, Any], label: str = "") -> str:
    """把度量渲染成**一行到三行**，随报告一起打印。"""
    sc = prof["比较器自检"]
    broken = [k for k, v in sc.items() if not v]
    head = f"增量 ≡ 全量（{label}）" if label else "增量 ≡ 全量"
    if broken:
        return (f"{head}：⚠️ **比较器自检未过**（{broken}）—— "
                f"本次比对**不可信**，先修比较器再看读数")

    rows = prof["各 k"]
    marks = " ".join(f"k={k}:{'✓' if r['同构'] else '✗'}" for k, r in rows.items())
    bad_universe = [k for k, r in rows.items() if not r["项集一致"]]
    out_of_scope = [k for k, r in rows.items() if r["根覆盖之外"]]
    f = prof["全量"]
    iso = prof["最早同构的 k"]
    tail = (f"最早同构的 k={iso}" if iso is not None
            else f"扫描到 k={max(rows) if rows else '-'} **全部不同构**")
    warn = (f"；⚠️ k={bad_universe} 的**项集不一致**（度量坏了，不是被测对象坏了）"
            if bad_universe else "")
    scope = (f"；§10.2 出路 (1)：k={out_of_scope} 有项落在**根覆盖之外**"
             f"（不塞进结构）⇒ 参照侧按**同一项集**重建"
             if out_of_scope else "")
    # ★ 滞留：项**停在内部节点** ⇒ `叶容量` 看不见这部分代价（见 `kernel.stats()`）。
    #   它不是「度量坏了」，是「分辨率要换个尺子看」—— 所以必须报出来。
    stayed = {k: r["滞留"] for k, r in rows.items() if r["滞留"]}
    blind = [k for k, r in rows.items() if r["滞留"] and r["最大停留数"] > r["最大叶容量"]]
    stay = (f"；§10.2 出路 (4)：k={sorted(stayed)} 有项**留在父方向**"
            f"（各 k 滞留数 {stayed}）；其中 k={blind} 的"
            f"`叶容量` **小于** `最大停留数` ⇒ 那部分代价叶容量看不见"
            if stayed else "")
    return (f"{head}：全量 {f['方向数']} 方向 / 叶容量 {f['最大叶容量']}"
            f" / 停留数 {f['最大停留数']}"
            f"｜{marks}｜{tail}{warn}{scope}{stay}"
            f"（度量，不进退出码）")

