"""视图层的内核 —— **最粗稳定细化**（流程 E，`docs/分层方向视图-抽象层.md` §3）。

    python -m ldv.run_checks            # 判据在 `(视图)` 那一组里

本模块是**纯算法**：不认识内核、不认识插件、不 import 本包的任何别的东西。
视图层要的那个对象在这里定义，判据在 `checks/abstraction.py`。

---

## 它解决的那一个问题

**抽象层只对检索器负责**（`docs/分层方向视图-抽象层.md` §0.0，2026-10-09 定义更正）：
把**结构层**（分层方向视图）**看成一个视图**，从它提取**一份认识**，交给
**下一层的检索器** —— 让检索器在**接受用户需求**时还能**保留对结构的一个持续认识**。

「从结构层提取一份认识」听起来像**摘要**，而摘要文献的默认形状是
**最小化一个目标函数**（MDL 代价 / 重建误差 / 社区保持度）。那条路在本设计里
**走不通** —— 一旦引入标量分，系统即在实质上充当裁判（E-1 / §4.3 / `B12`）。

出路不是「不要视图」，是**换一个对象**：不问「哪套视图更好」，而问
「给定一组外生约束，**有没有一个不用挑的视图集合**」。

**有，而且是唯一的。** Paige & Tarjan 1986（Princeton TR-038）§1 给的
*relational coarsest partition problem* 是：

> given a partition `P` of a set `U` and a relation `E` on `U`, find the **coarsest
> refinement `Q` of `P`** such that for each pair of blocks `B₁, B₂` of `Q`,
> either `B₁ ⊆ E⁻¹(B₂)` or `B₁ ∩ E⁻¹(B₂) = φ`.

（⚠️ 该文**没有文字层**，上面这段是**读渲染图**取到的：PDF p.4 = 论文 p.2。
取法与页码见 `docs/prior-art/README.md`；引用它的地方必须显式声明不经过
`verify_quotes`。见 `docs/分层方向视图-抽象层.md` §3。）

**最粗稳定细化唯一** ⇒ 视图集合不用挑 ⇒ **不需要目标函数**。
⇒ 这一条把「不许打分」从一条**纪律**（要靠 `B12` 扫源码守）变成一条**定理**
（唯一性来自划分的格结构，不来自任何优化）。

---

## 三条硬边界落在这个模块上的样子

    E-1 不许有全局目标函数   ⇒ 本模块**没有** `cost` / `score` / `objective` 参数，
                               也没有任何跨视图可比的数。`coarsest_stable_refinement`
                               是**确定性**的：同一个 `(P, E)` 只可能有一个答案。
    E-2 不许排序 / 打分 / 匹配 ⇒ 返回的是**块的无序集合**（按最小元素规范化排序，
                               只为可复现 §8.2，不是「按好坏排」）
    E-3 不许改外生项          ⇒ 本模块**不持有**任何东西：`ViewSpec` 是入参，
                               `Q` 是返回值。没有全局状态、没有缓存、不写盘。

---

## ⚠️ 为什么 `P` 和 `E` 必须**外生**

§3 的两条边界，逐字：

    ① 它是 `P` 的**细化**，不是任意划分 ⇒ 视图只会比初始划分更细，
       不会把两个初始块合成一个 ⇒ 初始划分是**外生的**（§K9）：
       由人声明「哪些方向同属一组」。
    ② 「稳定」是**对关系 `E`** 说的。`E` 选什么，视图就跟着变 ——
       所以 `E` 也必须外生声明，**不许由使用记录驱动**（§K9；`B14`）。

⇒ 本模块**没有默认值**。`ViewSpec` 三个字段全是必填，少一个就 `TypeError`。
   「猜一个 `P`」不是「先跑起来再说」，是**替人做 §K9 的决定**
   （`docs/分层方向视图-抽象层.md` §10 停止条件第 1 条）。

---

## 复杂度与「够不够用」

`n = |U|`，`m = |E|`。细化那一层是朴素写法：每轮对每个块求 `E⁻¹(B)`（O(m)）
并按它切一遍当前划分，直到不动点。

    ⚠️ **不引 Paige–Tarjan 的 O(m log n)** —— 理由是本项目一贯的那一条：
       checker 要**简单**（McConnell 等 2011 §5.1 的 Simplicity）。
       这里 `n` 是**一层里的方向数**，实测 36 项语料上单层最多几十个；
       而「简单」买到的是「读得懂、能注入、能对已知答案」。
       ⇒ 这是**有意的**取舍，不是没想到。要换算法时，`stable()` 与
          `coarser_stable_exists()` 这两个**独立**的 oracle 不用动 ——
          它们就是换算法时的对照。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import partial
from itertools import islice
from typing import Callable, Iterable, Iterator, Sequence

__all__ = [
    "ViewSpec",
    "coarsest_stable_refinement",
    "coarsest_stable_refinement_signature",
    "propagate",
    "propagate_naive",
    "extend_q",
    "restrict_spec",
    "stable",
    "refines",
    "refines_partition",
    "coarser_stable_exists",
    "n_coarsening_candidates",
    "partition_of",
    "reach_of",
    "view_parts",
    "MAX_COARSENING_CANDIDATES",
    # --- 折叠（`§M0`–`§M6` 的被判对象；L0 是产物） ---
    "Level",
    "MAX_LEVELS",
    "MIN_SHRINK_RATIO",
    # --- 认识的持续维护（`§A8`） ---
    "Maintenance",
    "maintain",
    "shrink_ratio",
    "block_namer",
    "quotient_spec",
    "quotient_universe",
    "fold_until",
]

#: 暴力搜「更粗的稳定划分」时，枚举上限 —— 超了就**报跳过**（不是报过）。
#: Bell 数长得快（B(8)=4140、B(10)=115975），所以这里必须有个闸。
#: ⚠️ **闸门本身也要印出来**：「搜不完所以跳过」与「搜完了没有」必须分得开。
MAX_COARSENING_CANDIDATES = 200_000


# --- 外生项 -------------------------------------------------------------------

@dataclass(frozen=True)
class ViewSpec:
    """外生的三件东西：`U` / `P` / `E`。**全部必填，没有默认值**（见模块开头）。

        `universe`    `U` —— 参与这一层视图的**方向 id**（有序元组，只为可复现）
        `partition`   `P` —— `U` 的一个划分（「哪些方向同属一组」，人声明）
        `relation`    `E` —— `U` 上的二元关系（「稳定」是对它说的，人声明）

    `__post_init__` 做**三条**检查，一条都不省：

        `P` 真的是 `U` 的划分      不重、不漏、都在 `U` 里
        `E` 的两端都在 `U` 里      落在 `U` 外的边是**声明错了**，不是「多给了」
        `U` 里没有重复            重复会让「块」与「集合」两套计数对不上

    ⚠️ 三条都**抛异常**而不是「自动修正」。自动补一个缺失的元素进 `P`
       会让「声明错了」与「声明对了」长得一模一样 —— 正是本仓库一直在防的形状。
    """

    universe: tuple[str, ...]
    partition: tuple[frozenset[str], ...]
    relation: frozenset[tuple[str, str]]

    def __post_init__(self) -> None:
        u = set(self.universe)
        if len(u) != len(self.universe):
            dup = sorted({x for x in self.universe if self.universe.count(x) > 1})
            raise ValueError(f"`universe` 里有重复元素：{dup[:5]}")
        seen: set[str] = set()
        for b in self.partition:
            if not b:
                raise ValueError("`partition` 里有空块 —— 空块不是划分的一部分")
            if seen & b:
                raise ValueError(f"`partition` 的块互相重叠：{sorted(seen & b)[:5]}")
            seen |= b
        if seen != u:
            miss = sorted(u - seen)[:5]
            extra = sorted(seen - u)[:5]
            raise ValueError(
                f"`partition` 不是 `universe` 的划分：缺 {miss}、多 {extra}")
        bad = sorted({x for pair in self.relation for x in pair} - u)[:5]
        if bad:
            raise ValueError(f"`relation` 的端点不在 `universe` 里：{bad}")

    def as_dict(self) -> dict[str, object]:
        """给落盘 / 报告用 —— **块与边都排序**，让同一个 spec 只有一个写法（§8.2）。"""
        return {
            "universe": list(self.universe),
            "partition": [sorted(b) for b in sorted(self.partition, key=min)],
            "relation": sorted(self.relation),
        }


def partition_of(blocks: Iterable[Iterable[str]]) -> tuple[frozenset[str], ...]:
    """把「一堆块」规范化成**可复现**的元组：块按最小元素排、块内元素排序。

    ⚠️ **排序不是为了「按好坏排」**（E-2 禁排序），只是为了让同一个划分
       只有一种写法 —— §8.2 的可复现性要求同一个输入产出**逐字相同**的元组。
       所以这里排的是**元素 id**，不是任何度量。
    """
    out = [frozenset(b) for b in blocks if b]
    return tuple(sorted(out, key=lambda b: (min(b), len(b))))


# --- 核心算法 -----------------------------------------------------------------

def _preimage(relation: frozenset[tuple[str, str]],
              block: frozenset[str]) -> frozenset[str]:
    """`E⁻¹(block) = {x : ∃y ∈ block, (x, y) ∈ E}`。

    ⚠️ **方向不要写反。** Paige–Tarjan 的 `E⁻¹(B₂)` 是「**能走到** `B₂` 的元素」，
       不是「`B₂` 能走到的」。写成后者会得到一个**看着也在跑、答案是另一个划分**
       的实现 —— 而两者在输出上只有一个块的区别，不逐条对**看不出来**。
       注入 `A2`（把 `Q` 粗化一格）与 `A3` 的暴力 oracle 都依赖这个方向是对的。
    """
    return frozenset(x for (x, y) in relation if y in block)


def _refine(blocks: list[frozenset[str]],
            relation: frozenset[tuple[str, str]]) -> list[frozenset[str]]:
    """把 `blocks` 对 `relation` 细到**不动点** —— `coarsest_stable_refinement`
    与 `propagate` **共用的那一层循环**。

        反复：对每个块 `B`，把每个块 `S` 切成 `S ∩ E⁻¹(B)` 与 `S \\ E⁻¹(B)`
              两半都非空才切
        到「一轮下来一个块都没被切开」为止

    终止：每次切**严格增加**块数，块数 ≤ `|U|`。

    ⚠️ **共用这一层不影响「两个 oracle 不共享盲点」那条纪律。**
       共享的是**被判对象**的构造（本来就只有一条路），不是 oracle：
       `stable()` 逐块对验定义、`coarser_stable_exists()` 暴力枚举粗化 ——
       两者都不走这里一行。
    """
    while True:
        changed = False
        # 快照：本轮的 splitter 是**这一轮开始时**的块。新切出来的块
        # 下一轮才当 splitter —— 外层 `while` 会一直跑到它们都试过为止。
        for splitter in list(blocks):
            inv = _preimage(relation, splitter)
            new: list[frozenset[str]] = []
            for s in blocks:
                inside = s & inv
                if inside and inside != s:
                    new.append(inside)
                    new.append(s - inv)
                    changed = True
                else:
                    new.append(s)
            blocks = new
        if not changed:
            return blocks


def coarsest_stable_refinement(
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`P` 对 `E` 的**最粗稳定细化** `Q`。**唯一**（§3）。

    正确性：任何**稳定**的 `P` 的细化 `R` 都被 `_refine` 的每一步保持
    （`R` 的块要么整个落在 `E⁻¹(B)` 里，要么整个在外面），
    所以极限细化每一个稳定细化 ⇒ 它就是**最粗**的那个。∎

    ⚠️ **确定性**：切分的**顺序**会影响中间划分，但**不影响**极限 ——
       这正是「唯一性」那条定理的内容。为了让输出**逐字可复现**（§8.2），
       返回值按 `partition_of` 规范化（排元素 id，不排任何度量）。
    """
    return partition_of(_refine(list(spec.partition), spec.relation))


def coarsest_stable_refinement_signature(
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`csr(P)` 的**第二条独立实现** —— 按「**可达块签名**」分块（不走 `E⁻¹` 劈）。

    ## 为什么需要它（2026-10-10，全量语料实测）

    `§A3`（视图最粗）原来的 oracle 是 `coarser_stable_exists` —— **暴力枚举**粗化，
    候选数是 `∏ Bell(组内块数)`。实测：36 项（8 块）**4140** 个候选 ⇒ 搜得完；
    全量（19 块）**5.83e12** ⇒ **搜不完** ⇒ `§A3` 只能**报跳过**。

    而**「跳过 ≠ 通过」** ⇒ 那条承重判据在全量上**瞎了**。
    ⇒ 本函数给出**多项式**的第二条路，让 `§A3` **在任何规模上都能判**。

    ## 它为什么与 `_refine` **不同路**

        `_refine`：反复拿 `E⁻¹(某个块)` 去**劈**每个块，直到劈不动
        本函数  ：反复按「**这个元素能走到哪些块**」这一组**签名**重新分组，
                  直到没有块被拆开

    同一个不动点（「最粗稳定细化唯一」那条定理），**两条不同的计算路径**。
    ⇒ 与 `propagate` / `propagate_naive`、`to_dict` / `_to_dict_storing_derived`
      同一个模式：**判据要有与被判对象不同路的参照物**。

    ## 正确性（与 `_refine` 的论证同构，但走的是签名）

        `Q` 稳定 ⟺ ∀ 块 `B`、∀ 块 `A`：`E⁻¹(A) ∩ B ∈ {∅, B}`
              ⟺ **同一块里的元素「能走到哪些块」完全一致**（本函数的签名）
        ⇒ 设 `R` 是 `P` 的**任一**稳定细化，`x, y` 同属一个 `R` 块。
          对任一 `Q` 块 `A`：`A` 是若干 `R` 块的并（因 `R ⊑ Q`）；
          而 `R` 稳定 ⇒ `E⁻¹(A) ∩ (R 的某个块)` 要么是空、要么是整块
          ⇒ **`x` 与 `y` 在「能否走到 `A`」上一致** ⇒ 它们签名相同
          ⇒ 本函数的每一步都**保持** `R` ⇒ 不动点细化**每一个**稳定细化
          ⇒ 它 ≤ 最粗的那个；而它自己稳定且细化 `P` ⇒ 最粗的那个 ≤ 它
          ⇒ **相等**。∎

    ⚠️ 复杂度：每轮 `O(|E| + |U|)`，最多 `|U|` 轮 ⇒ `O(|U|·|E|)`。
       全量（79 个方向 / 78 条边）是**微秒级**。
    """
    q: list[frozenset[str]] = [frozenset(b) for b in spec.partition]
    edges: dict[str, list[str]] = {}
    for x, y in spec.relation:
        edges.setdefault(x, []).append(y)
    while True:
        idx = {d: i for i, b in enumerate(q) for d in b}
        new: list[frozenset[str]] = []
        for b in q:
            sig_of: dict[frozenset[int], set[str]] = {}
            for d in b:
                # 签名 = 「这个元素能走到哪些块」
                sig = frozenset(idx[y] for y in edges.get(d, ()) if y in idx)
                sig_of.setdefault(sig, set()).add(d)
            new.extend(frozenset(members) for members in sig_of.values())
        # 没有任何块被拆开 ⇒ 不动点（每拆一次块数**严格增加**，上界 `|U|`）
        if len(new) == len(q):
            return partition_of(new)
        q = new


# --- `E′` 增量传播（§7） -------------------------------------------------------
#
# 视图不是一次算完的：结构一动（流程 B），视图要么重算、要么**传播**。
# 设计稿 §7 把这条定成 `E′`，并且**只定接口不定实现** —— 这里把它落下来。
#
# ⚠️ **本设计特有的那条约束**（§7 逐字）：`§10.2 B` 已裁定「增量 ≡ 全量」**不成立**，
#    且**不成立不破坏任何一条检查** ⇒ 传播**不许**声称「传播后的视图 ≡ 重算的视图」。
#    能声称的只有「传播后的视图仍**稳定**、仍是 `P′` 的**细化**」。
#    而「更细」这件事不是观察，是**定理** —— 见 `propagate` 的 docstring。

def restrict_spec(spec: ViewSpec, drop: frozenset[str]) -> ViewSpec:
    """把 `spec` 投影到 `U ∖ drop` 上 —— **外生项照原样投影，不重新声明**。

    `P` 的每个块减掉 `drop` 里落在它里面的元素，空掉的块去掉；
    `E` 只留两端都还在的边。

    ⚠️ 这个函数**不做**任何「补一个元素进去」的事 —— 那会让「声明错了」
       与「声明对了」长得一模一样（`ViewSpec.__post_init__` 的 ⚠️ 同一条）。
       投影之后仍然要过 `ViewSpec.__post_init__` 那三条检查。
    """
    keep = frozenset(spec.universe) - drop
    if not keep:
        raise ValueError("投影之后 `universe` 空了 —— 那不是一份声明")
    part = tuple(b & keep for b in spec.partition if b & keep)
    rel = frozenset((x, y) for (x, y) in spec.relation if x in keep and y in keep)
    return ViewSpec(universe=tuple(d for d in spec.universe if d in keep),
                    partition=part, relation=rel)


def refines_partition(
    coarse: Sequence[frozenset[str]],
    fine: Sequence[frozenset[str]],
) -> bool:
    """`fine` 是不是 `coarse` 的**细化** —— 每个 `coarse` 块恰好被若干 `fine` 块盖住。

    ⚠️ **「恰好」是逐字的**：既要盖满（不许漏）、又要不越界（不许把两个
       `coarse` 块并进同一个 `fine` 块）。只查「盖满」会让**合并**蒙混过关 ——
       而合并正是 `E′` 最要防的那一种（它是信息丢失，`§K8` 的假阴那一侧）。
    """
    for b in coarse:
        cover = [f for f in fine if f & b]
        merged = frozenset().union(*cover) if cover else frozenset()
        if merged != b:
            return False
    return True


def extend_q(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`Q_ext` = 旧 `Q` ∪ { `spec.universe` 里旧 `Q` **没覆盖到**的方向，各自成块 }。

    ⚠️ **`Q_ext` 的定义是 `E′` 的一部分，不是实现细节。** `§A7` 的判据 ②
       （传播只许**细分**）判的就是「结果细化 `Q_ext`」—— 所以 `Q_ext` 必须
       由**一个**函数定义，判据与实现**共用**它，否则两边各写一份、
       写着写着就不一样了（`test_injections` 第 0 条防的是同一类事）。
    """
    known = frozenset().union(*old_q) if old_q else frozenset()
    return partition_of(list(old_q) + [frozenset({d}) for d in spec.universe
                                        if d not in known])


def propagate(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`E′`：从**旧划分** `old_q` 出发，把 `spec.universe` 里的**新方向**
    （`old_q` 没覆盖到的那些）各自成块并进去，再跑到不动点。

    ## ★ 传播的结果**不一定**等于重算的结果 —— 而且这是**定理**，不是观察

        `Q_ext` = `old_q` ∪ { 每个新方向一块 }
        `Q′`    = `csr(Q_ext)`        ← 本函数
        `Q*`    = `csr(P′)`           ← 重算（`coarsest_stable_refinement`）

    `old_q` 是 `P` 的细化 ⇒ `Q_ext` 是 `P′` 的细化（新方向按 `P′` 落位）。
    `Q′` 稳定且细化 `P′` ⇒ 由**最粗**的定义，`Q′` 细化 `Q*`：

        Q′ ⊑ Q*        传播出来的只会**更细**（或相等），不会更粗

    ⚠️ **所以「传播后 == 重算后」是一句会被实测打脸的话**（§7 逐字），
       而**打脸不破坏任何一条检查** —— `§10.2 B` 已裁定。
       ⇒ 这条**只报不判**：块数差进输出，不进退出码（`abstraction.a7_reading`）。

    ⚠️ **反向也成立，而且它才是判据**：`Q′` 细化 `old_q`（**传播只细分，不合并**）。
       合并 = 信息丢失 ⇒ `§K8` 的假阴那一侧 ⇒ 那是**红**，不是读数。
       `refines_partition` 逐块查的就是这一条。
    """
    return partition_of(_refine(list(extend_q(old_q, spec)), spec.relation))


def propagate_naive(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`E′` 的**对照**实现 —— 把新方向挂到**父方向所在的那一块**上，**不重跑不动点**。

    ⚠️ **这是对照，不是备选方案。** 它复现的是「传播 = 把新东西挂上去」这个
       最自然的错觉。错觉的代价在这一条上：新边 `(父, 新)` 把 `父` 塞进了
       `E⁻¹({新})`，于是 `{新}` 那一块**跨在** `E⁻¹({新})` 的内外
       （`父` 在里面、`父` 的同块兄弟在外面）⇒ 划分**不再稳定**。

    ⇒ `§A7` 的注入就是它：真 `propagate` ⇒ 过，`propagate_naive` ⇒ 红。
       （`abstraction.a7_known_answer`。）
    """
    blocks = [set(b) for b in old_q]
    owner = {d: i for i, b in enumerate(blocks) for d in b}
    for d in spec.universe:
        if d in owner:
            continue
        parent = next((x for (x, y) in sorted(spec.relation) if y == d), None)
        home = owner.get(parent) if parent is not None else None
        if home is None:
            owner[d] = len(blocks)
            blocks.append({d})
        else:
            blocks[home].add(d)
            owner[d] = home
    return partition_of(blocks)


#: 折叠的两个**外生**参数。**必填，没有默认值** —— 与 `ViewSpec`
#: 同一个理由：猜一个就是替人做 `§K9` 的决定。
#:
#: `MIN_SHRINK_RATIO = 0.5` 的出处：METIS 论文 p.365 逐字
#: 「the number of vertices in Gi+1 cannot be less than half the number of
#: vertices in Gi」。**那一侧是结构性保证**（先验成立，阈值只兜病态）；
#: ldv **没有**这条保证 ⇒ 同一个数在这里从「兜底」变成**要求**。
#: 实测基线（36 项语料、25 个方向 ⇒ 8 张视图）收缩比约 **0.32**。
#:
#: ⚠️ **它只是 `fold_until` 的刹车**（2026-10-10 起）——**不是任何判据的阈值**。
#:   曾经有一条判据拿它判「视图层真的在缩」，那一条**已删**：`shrink` 的分子由
#:   **结构**决定（最粗稳定划分，已达下界）、分母由**键集**决定 ⇒ 系统**没有
#:   任何自由度**去改善它 ⇒ 那条判据判的是**语料**，不是**实现**。
#:   （实测：三份新语料上它是唯一的红，而 `§A3` 同时判「8 块已是最粗」
#:     ⇒ `shrink` 不可能更小 ⇒ 可证的不可能。见 `checks/multilevel.m1_view_layer`。）
#:   ⇒ 收缩比现在是**读数**（`render_levels` 逐层印出来，不进退出码），
#:     刹车仍按这个数停（停因标签写作 `"M1 收缩比"`，**保留**以免与存档输出对不上）。
#:
#: ⚠️ `MAX_LEVELS` 是**兜底**，不是主刹车 —— 已核文献里直接给层数封顶只是
#:   「configured maximum level」那种配置项。主刹车是 `MIN_SHRINK_RATIO`。
MAX_LEVELS = 8
MIN_SHRINK_RATIO = 0.5

# --- 认识的**持续**维护：传播优先 + 阈值重建（`§A8`） -----------------------------
#
# ## 为什么「传播」单独一条不够 —— 2026-10-10 实测
#
# `propagate` 是**单调细化**的（只分不合，它自己那段 ⚠️ 有证明）。所以
# **反复**传播会一路细化下去，而细化的终点是「一个方向一张视图」：
#
#     真语料（36 项 / keyset），逐项插入、每步只传播：
#         末态 |Q| = 25 = |U|  ⇒ shrink = **1.000** ⇒ 粗粒度被**磨光**
#         同一条轨迹上「重算」始终是 8 块 ⇒ shrink = 0.320
#
# ⇒ **「模糊掌握」会被磨光**，而那时 `T1`（候选集 ≡ 不用视图）**照样绿** ——
#   候选集本来就与 `Q` 无关 ⇒ 退化与「正常」在只看判据时**长得一模一样**。
#
# ## 文献：三段式 + 阈值摊销 + 回收失效
#
# Acar/Blelloch/Harper POPL 2002（`acar02`，**已核**）把这件事定成三段：
#
#     「`val init: unit -> unit` / `val change: 'a mod * 'a -> unit` /
#       `val propagate: unit -> unit`」           ← **攒变更**，再一次传播
#     「the trace … is used by the change propagation algorithm」
#     「change propagation yields **essentially the same result as a complete
#       re-execution** on the changed inputs」      ← ★ **正确性 = 等于全量重算**
#
# ★★ 而**本仓库的 `E′` 只有这条的一半**：`Q′ ⊑ Q*`（只细化，实测 11 vs 8），
#    **不是** `Q′ == Q*`。⇒ 想让它「跟上」，**必须有一步把差距收回来** ——
#    那就是重建。所以阈值重建**不是优化，是补齐**。
#
# 两条给出「怎么攒/什么时候付」：
#
#     `betree`（Bender 等 2015，**已核**）：「Moving messages down the tree **in batches**
#       is the **key** … the B ε-tree **only moves messages to a subtree when enough
#       messages have accumulated for that subtree to amortize** the I/O cost」
#     `lsmsurvey`（Luo & Carey，**已核**）：「a **background process** called the
#       **vacuum cleaner** to **continuously garbage-collect obsolete records**」
#         ★ 后者正对流程 B 的**情形 ③**（「曾由 W 强制，现已不成立」的标记）——
#           那些标记只在**重建**时才会被回收。
#
# `mvselect2026`（**已核**）给的是纪律：「incorporating **incremental view maintenance
#   cost directly into the optimization objective**」⇒ 维护代价要**进决策**，
#   不能当免费的。本函数把「代价」表达成**一个质量预算**（`shrink_max`），
#   而预算取的正是**已有的一处定义** `MIN_SHRINK_RATIO` ⇒ **不新增参数**。
#   （⚠️ 那个预算参数**后来被删了** —— 见 `maintain` 的 docstring：交付物
#     **必须**是 `csr`，与传播多细无关 ⇒ 旋钮用不上。这里留着的是**文献理由**。）
#
# ## ⚠️ 被否掉的那条路：**延迟 / 批量维护**
#
# `betree` 的批量指的是**代价**（攒够再付），很容易被读成「**别每次都维护**」。
# 本设计**不采纳后者**，理由是一条安全性质：
#
#     延迟 ⇒ 窗口内**盘上的认识是陈旧的** ⇒ 检索层 `T2`（「用到的认识的指纹
#     == 当前结构的指纹」）会判否 ⇒ 要么拒用（**退化成重算，本层的影响归零**），
#     要么**允许用陈旧的认识** ⇒ 那就要放松 `T2` ——
#     而 `T2` 存在的**全部理由**就是「陈旧的认识与当前的认识不许混」
#     （`基线§6`：不比对的话两者在只看输出时长得一模一样）。
#
# ⇒ 所以本设计的「批量」只体现在**维护自身的动作选择**上：
#   **能传播就传播（便宜），超预算才重建（贵）**。频率仍是「每次结构变动」。


@dataclass(frozen=True)
class Maintenance:
    """一次认识维护的**全部可判内容** —— 判据 `§A8` 只读这里，不去别处现算。

        `q`                    维护后的划分（**交付物**）
        `action`               `重建` —— 或者 `传播`**仅当**它与重建逐块相同
        `shrink`               交付物的收缩比 `|q| / |U|`
        `propagate_equal`      `propagate(seed, spec)` 是否**逐块等于** `csr(spec)`
        `shrink_if_propagated` **只传播**会是多少（读数；交付**不许**用它）
        `reason`               判据要印的那句话
    """

    q: tuple[frozenset[str], ...]
    action: str
    shrink: float
    propagate_equal: bool
    shrink_if_propagated: float
    reason: str


def shrink_ratio(q: Sequence[frozenset[str]], spec: ViewSpec) -> float:
    """`shrink = |Q| / |U|`（0 最好，1.0 = 一点没缩）—— **读数**，不是判据。

    ⚠️ 它**不再被任何判据判**（2026-10-10）：分子由**结构**决定（最粗稳定划分，
       已达下界）、分母由**键集**决定 ⇒ 系统**没有自由度**改善它。
       拿它当判据等于判**语料**。见 `checks/multilevel.m1_view_layer`。
       `fold_until` 仍用它当**刹车**（阈值 `MIN_SHRINK_RATIO`）。
    """
    u = len(spec.universe)
    return (len(q) / u) if u else 0.0


def maintain(
    seed: Sequence[frozenset[str]] | None,
    spec: ViewSpec,
) -> Maintenance:
    """`§A8` —— 把认识维护到当前结构。**交付物是 `csr(spec)`**，不是传播结果。

    ## ★★ 为什么交付物必须是 `csr` —— 2026-10-10 **实测**（这条是原则，不是偏好）

        `§A3`（视图最粗）要枚举**更粗的**候选划分数。候选数随块数**组合爆炸**：

            `csr(spec)` 8 块   ⇒ 候选 **4140**   ⇒ `§A3` **判得了**（枚举完，已是最粗）
            传播结果    12 块  ⇒ 候选 **4213597** ⇒ `§A3` **判不了**（超上限）⇒ **跳过**
            （全量语料上更极端：19 块 ⇒ 5.83e12）

        ⇒ 交付一个**比 `csr` 更细**的划分，会让「最粗」这条承重性质
          从**可判**退化成**判不了** —— 而本仓库的规矩是 **「跳过 ≠ 通过」**。
        ⇒ 所以「**用传播结果当交付**」不是「差一点的方案」，是**把一条判据弄瞎**。
          ★ 这一条是**量的结果**，不是口径之争：`§A3` 在 8 块上过、在 12 块上跳。

    ## `propagate` 在图里的位置（诚实版）

        `propagate`（`E′`）**不是**交付路径。设计稿 §7.3 逐字已经说了它不是省算力的优化
        （实测 `11 > 8`、`25 > 8`）；本函数给出**第二条独立理由**：
        它交出来的东西**让承重判据失去可判性**。
        ⇒ 它的结果只进**读数**（`shrink_if_propagated` / `propagate_equal`）。

        ⚠️ **唯一的例外**：若 `propagate(seed, spec)` **逐块等于** `csr(spec)`
          （真语料上确实会发生 —— 结构变动没有真的劈开任何块时），
          那两条路**同一个东西** ⇒ 记 `action="传播"` 也只是**如实**，不是省了什么
          （`csr` 反正已经算出来比对了）。

    ## 两个读数（进输出，不进退出码）

        `shrink_if_propagated`  只传播会到多少 —— 与 `§7.3` 的实测同族
        `propagate_equal`       两条路这一趟是否同构

    ⚠️ **没有「预算」参数**（上一版有，已删）：它的前提是「传播够不够细由预算说了算」，
       而实测否掉了那个前提 —— 交付物**必须**是 `csr`，与传播多细**无关**。
       留一个用不上的旋钮比删掉它更坏：它会让读者以为存在一个可以调的折中。
    """
    q_reb = partition_of(coarsest_stable_refinement(spec))
    s_reb = shrink_ratio(q_reb, spec)
    if not seed:
        return Maintenance(q=q_reb, action="重建", shrink=s_reb, propagate_equal=False,
                           shrink_if_propagated=s_reb,
                           reason="没有旧认识 ⇒ 没有可传播的起点（那是重建）")
    q_prop = partition_of(propagate(seed, spec))
    s_prop = shrink_ratio(q_prop, spec)
    same = q_prop == q_reb
    return Maintenance(
        q=q_reb,
        action="传播" if same else "重建",
        shrink=s_reb,
        propagate_equal=same,
        shrink_if_propagated=s_prop,
        reason=(f"交付 `csr`（{len(q_reb)} 块，shrink {s_reb:.3f}）"
                + (f"；只传播**这一趟同构**（{len(q_prop)} 块）⇒ 如实记作「传播」" if same
                   else f"；只传播会是 {len(q_prop)} 块（shrink {s_prop:.3f}）——"
                        f"**不采**：那会让 `§A3` 的可判性退化（候选数随块数爆炸）")))


# --- 两个**独立**的 oracle -----------------------------------------------------
#
# 判据要能红，就必须有一个**与被判对象不同路**的参照物（本仓库的老纪律：
# 拿被测对象自己的运算记录当见证不算 certifying，McConnell 等 2011 §5.5）。
#
# `stable()` 与 `coarser_stable_exists()` 是**两个不同的问题**：
#
#     stable()                  这个划分**稳定吗**       —— 逐个块对验定义，O(k²)
#     coarser_stable_exists()   还有**更粗的**稳定划分吗 —— 暴力枚举粗化，与
#                               `coarsest_stable_refinement` **不共用代码**
#
# ⇒ `§A3` 用的是后者。用「再跑一遍 `coarsest_stable_refinement` 比一比」
#   当 oracle 会与被判对象**共享盲点**（两边同时错、判据永远绿）——
#   那正是 `false-green` 形状 3。

def stable(spec: ViewSpec, q: Sequence[frozenset[str]]) -> tuple[bool, list[str]]:
    """`Q` 对 `E` 稳定吗 —— 逐字按定义验，返回 `(稳定?, 反例)`。

        对每对块 `B₁, B₂`：`B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ`

    ⇒ 「存在一块**跨**在 `E⁻¹(B₂)` 内外」就是反例。返回的反例写成
      「`B₁` 有 x 在 `E⁻¹(B₂)` 里、y 不在」—— 这样它**指得出**是哪两个元素。
    """
    inv = {b: _preimage(spec.relation, b) for b in q}
    bad: list[str] = []
    for b1 in q:
        for b2 in q:
            inter = b1 & inv[b2]
            if inter and inter != b1:
                inside = sorted(inter)[:1]
                outside = sorted(b1 - inv[b2])[:1]
                bad.append(f"{sorted(b1)[:3]} 里 {inside} 走得到 {sorted(b2)[:3]}，"
                           f"而 {outside} 走不到")
    return (not bad), bad


def refines(spec: ViewSpec, q: Sequence[frozenset[str]]) -> bool:
    """`Q` 是不是 `P` 的**细化** —— 每块 `Q` 都落在**某一个** `P` 块里。

    ⚠️ 这一条**不是形式**：§3 边界 ① 说视图**只会比初始划分更细**，
       不会把两个初始块合成一个。少了它，一个「把两个初始块并起来」的
       `Q` 照样可以**稳定**（见 `coarser_stable_exists` 里那段），
       于是 `§A2` 绿而它根本不是 `P` 的细化。
    """
    for b in q:
        if not any(b <= p for p in spec.partition):
            return False
    return True


def _set_partitions(n: int) -> Iterator[tuple[tuple[int, ...], ...]]:
    """枚举 `0..n-1` 的**所有集合划分**，每个划分是一串「组」，每组是一串下标。

    `n` 个元素 ⇒ 产出 `Bell(n)` 个划分。⚠️ **不是** `2^n`：
       「把块合并成哪些组」是划分问题，不是子集问题 —— 写成子集枚举会
       把同一个粗化数很多遍（`{A,B}` 与 `{B,A}`），而**重复枚举看不出来**
       （结论一样），只看得见它慢。

    ⚠️ **组号必须留下来。** 早先的写法产出「每个元素一个组号」然后只按下标重排，
       于是**两个不同的划分渲染成同一个东西**（都退化成恒等划分）——
       后果是「更粗的稳定划分」永远搜不到，`§A3` 变成一条**永远绿**的判据。
       实测踩到：`E = ∅`、`P = {U}` 上明明没有更粗的稳定划分，却报「有」。
    """
    if n <= 0:
        yield ()
        return
    groups: list[list[int]] = [[0]]

    def rec(i: int) -> Iterator[tuple[tuple[int, ...], ...]]:
        if i == n:
            yield tuple(tuple(g) for g in groups)
            return
        for g in groups:              # 放进一个已有的组
            g.append(i)
            yield from rec(i + 1)
            g.pop()
        groups.append([i])            # 或者自己开一个新组
        yield from rec(i + 1)
        groups.pop()

    yield from rec(1)


def _bell(n: int) -> int:
    """`Bell(n)` —— `n` 个元素的**集合划分数**。只**数**，不建。

        B(0)=1, B(n+1) = Σ_{k=0..n} C(n,k)·B(k)

    ⚠️ **为什么单列一个「只数不建」的函数**：枚举上限必须在**动手之前**就判。
       **先物化、再拿物化出来的 `len` 去比上限**的写法在 `k = 25` 时
       **不是报跳过，是挂住** —— `Bell(25) ≈ 4.6e18`，那个 `list` 一辈子建不完
       （`outputs/_probe_cap.py`：20 秒超时、`timeout` 退出码 124）。

       「**搜不完**」与「**还在搜**」在只看输出的时候长得一模一样 ——
       这正是本仓库一直在防的形状，只不过这一次它出现在**闸门自己**身上。
       所以闸门的判据不能是「跑完再比」，只能是「**先算规模再跑**」。
       `test_views` ⑥ 段守着这一条（大组 ⇒ 毫秒级报 `None`，且不许一律跳过）。
    """
    row = [1]
    for i in range(n):
        row.append(sum(math.comb(i, k) * row[k] for k in range(i + 1)))
    return row[n]


def _coarsenings(
    bs: Sequence[frozenset[str]],
) -> Iterator[tuple[frozenset[str], ...]]:
    """`bs` 的全部**粗化** —— 每个粗化是一串「合并后的块」。

    ⚠️ 按**组号**合并：只按下标重排（丢掉组号）会让每个划分都退化成恒等 ——
       见 `_set_partitions` 的 ⚠️。
    """
    for grp in _set_partitions(len(bs)):
        yield tuple(frozenset().union(*[bs[i] for i in g]) for g in grp)


class _LazyPool:
    """**可重复迭代、但不物化**的池子 —— 每次 `iter()` 现算一遍。

    ⚠️ 两个性质**缺一不可**，而 `list` 与生成器各只有一半：

        生成器   不物化，但**一次性** —— 笛卡尔积的外层每换一个元素都要
                 **重头**取内层池子，第二次取就空了
        `list`   可重复迭代，但**物化** —— 那就又绕回「闸门拦不住内存」

    ⇒ 要的是「可重复迭代 **且** 不物化」，所以这里自己写一个。
       （`_product` 的 docstring 早就在说这件事，只是原先的调用方传的是 `list`。）
    """

    def __init__(self, make: Callable[[], Iterator]) -> None:
        self._make = make

    def __iter__(self) -> Iterator:
        return self._make()


def n_coarsening_candidates(
    spec: ViewSpec,
    q: Sequence[frozenset[str]],
    cap: int = MAX_COARSENING_CANDIDATES,
) -> int:
    """`∏ Bell(组内 Q 块数)` —— 「枚举更粗的稳定划分」要试多少个候选。**只数不建**。

    ## 为什么要单列一个函数（2026-10-08）

    这个量原来**只**是 `coarser_stable_exists` 内部的一个局部变量，也就是
    「`§A3` 判得了还是判不了」的私事。折叠的 `§M4` 要用**同一个量**做
    **另一件事**：每一层折叠之前，先看这一层的验证代价爆没爆。

    ⇒ 两处必须**逐字同一个数**，否则会出现「`§A3` 说判得了、`§M4` 说爆了」
       —— 而那个矛盾的症状不是红，是**两个判据各自看着都对**。
       这与 `reach` 的根口径在两份装配之间分岔过的那次是同一个形状。

    ⚠️ **`cap` 只影响「早不早停」，不影响返回值**：超了就返回那个**已经超过**的数
       （不是 `None`）—— 调用方拿它跟 `cap` 比。返回 `None` 会让
       「爆了」与「算不出来」合并成一种。

    ⚠️ 计数器**不叫** `total` —— `B12`（`checks/source.py`）按**名字**扫内核源码，
       `total` 在它的禁用表里（`score|objective|utility|global|importance|total`）。
       这里数的是「**枚举了多少个候选粗化**」，与跨方向评分毫无关系 ⇒ 那是**假红**。
       处置按纪律：**改代码不改扫描器**（扫描器是唯一守 E-1 的东西，放宽它才是真损失）。
    """
    groups: dict[frozenset[str], list[frozenset[str]]] = {p: [] for p in spec.partition}
    for b in q:
        home = next((p for p in spec.partition if b <= p), None)
        if home is None:
            # `Q` 不是 `P` 的细化 ⇒ 这个量**问不出来**。返回 `0`（而不是一个大数）：
            # 「问不出来」与「很大」是两件事，混起来会让 `§M4` 把一种**声明错误**
            # 报成「验证代价爆了」。
            return 0
        groups[home].append(b)
    n = 1
    for p in spec.partition:
        n *= _bell(len(groups[p]))
        if n > cap:
            return n
    return n


def coarser_stable_exists(
    spec: ViewSpec,
    q: Sequence[frozenset[str]],
    cap: int = MAX_COARSENING_CANDIDATES,
) -> tuple[bool | None, str, int]:
    """**存在**比 `Q` 更粗的、`P` 的稳定细化吗 —— 暴力枚举。

    返回 `(有没有, 说明, 枚举了多少个候选)`；`None` = **搜不完**（超 `cap`）。

    ## 为什么是暴力，而不是「再算一遍最粗稳定细化比一比」

    后者与被判对象**共用同一段代码** ⇒ 两边**同时错**时判据照样绿
    （`false-green` 形状 3「共享盲点」）。暴力枚举走的是**另一条路**：
    它不依赖 `coarsest_stable_refinement` 的任何一行。

    ## 枚举的是什么

    `Q` 比 `Q*` 粗（`Q*` = 最粗稳定细化）⟺ `Q*` 由 `Q` **合并若干块**得到。
    而合并**只允许发生在同一个 `P` 块内部**（否则就不再是 `P` 的细化了）。

    ⇒ 按 `P` 块分组，对每一组里的 `Q` 块枚举**集合划分**（`Bell(k)` 个），
      笛卡尔积就是全部候选。**恒等划分**（每组都是单块）就是 `Q` 自己，
      要排除掉 —— 否则「`Q` 稳定」会把自己报成「存在更粗的稳定划分」。

    ## ⚠️ 顺序：**先算规模，再动手**（这条是判据的一部分）

    规模 `n_cand = ∏ Bell(组内 Q 块数)` 用 `_bell` **只数不建**地先算出来，
    超 `cap` 立刻返回 `None`；过了这一关才开始枚举，而且枚举走 `_LazyPool`
    （可重复迭代、不物化）。

    ⇒ 「搜不完 ⇒ 报跳过」这句话**在时间与内存上都成立**。
      「先物化、再比 `cap`」的写法在**大组上挂住**（不是跳过）—— 见 `_bell` 的 ⚠️。

    ⚠️ **`cap` 撞上时返回 `None`，调用方必须报「跳过」而不是「过」。**
       搜不完与搜完了没有，在只看 `bool` 的时候**长得一模一样**。
    """
    groups: dict[frozenset[str], list[frozenset[str]]] = {p: [] for p in spec.partition}
    for b in q:
        home = next((p for p in spec.partition if b <= p), None)
        if home is None:
            # `Q` 不是 `P` 的细化 ⇒ 「更粗的稳定划分」这个问题**问不出来**。
            # 说清是这一条，不要退化成「没有」—— 那是两件事。
            return None, "`Q` 不是 `P` 的细化（有块跨出了 `P` 的块）⇒ 搜不了", 0
        groups[home].append(b)

    n_cand = n_coarsening_candidates(spec, q, cap)
    if n_cand > cap:
        return None, f"候选数 {n_cand} 超过上限 {cap} ⇒ **没搜完**", n_cand

    pools = [_LazyPool(partial(_coarsenings, sorted(groups[p], key=min)))
             for p in spec.partition]

    q_norm = partition_of(q)
    seen = 0
    for combo in _product(pools):
        seen += 1
        # `combo` 是「每个 `P` 块一个粗化」，每个粗化是一串**合并后的块**。
        # ⚠️ 恒等判定要拿**规范化后的块集合**比，不能去数「组里有几个块」——
        #    后者会把 `frozenset({'a'})` 当成「一个元素、长度为 1」，
        #    于是**每一个**候选都被当成恒等跳过，结论正好反了。
        #    （实测踩到：`E = ∅` 上 `Q = {U}` 明明是**没有**更粗的稳定划分，
        #      却报「有」。）
        merged = partition_of([b for part in combo for b in part])
        if merged == q_norm:
            continue                      # 恒等 = `Q` 自己（它当然稳定，但不是「更粗的」）
        if not refines(spec, merged):
            continue
        ok, _ = stable(spec, merged)
        if ok:
            return True, (f"把 {len(q)} 块并成 {len(merged)} 块之后**仍然稳定**："
                          f"{[sorted(b)[:3] for b in merged[:3]]} …"), seen
    return False, f"枚举了 {seen} 个粗化，没有一个稳定", seen


def _product(pools: Sequence[Iterable]) -> Iterator[tuple]:
    """笛卡尔积 —— 手写一份，避免 `itertools.product` 在超大池上先物化。

    `itertools.product` 会**先把每个池子转成 tuple**（它就是这么写的），
    于是「`cap` 拦住了枚举」这件事在内存上**没有**被拦住。这里逐层递归，
    池子按需取。

    ⚠️ **`pools[i]` 必须可重复迭代**：外层每换一个元素都要重头取内层池子
       （笛卡尔积的定义）。传一次性生成器会在第二次取时拿到空池子 ——
       而「少枚举了一些」与「枚举完了」在结论上**看不出来**。
       ⇒ 不物化又可重复迭代的池子见 `_LazyPool`。
    """
    if not pools:
        yield ()
        return

    def rec(i: int, acc: tuple) -> Iterator[tuple]:
        if i == len(pools):
            yield acc
            return
        for x in islice(pools[i], 0, None):
            yield from rec(i + 1, acc + (x,))

    yield from rec(0, ())


# --- 视图的「下界」与「部件」 --------------------------------------------------
#
# `§A4`（类别不许说错）要问的是「这张视图的读数能不能**只从下层算出来**」。
# 那需要先定义「下层」是什么。这里两个函数就是那个定义。
#
# ⚠️ **不许把「下层」直接定义成「块 ⊆ reach(本块) 的那些块」。** 视图划分
#    **会横跨树**：实测（36 项语料、`P = {U}`）块 `{D10,D15,D22,D3}` 里 `D22`
#    不在 `D2` 的子树里 ⇒ 那个关系**不构成嵌套**，按它取「极大子块」会漏掉
#    横跨的那些 ⇒ 恒等式根本不成立（实测 `|block| = 1` 而 `Σ|子块| = 24`）。
#    ⇒ 改成**截断成部件**：部件恰好**划分** `reach`，恒等式按构造成立。


def reach_of(
    block: Iterable[str],
    subtree_of: Callable[[str], frozenset[str]],
) -> frozenset[str]:
    """一块方向的**下界** `reach(B) = ∪{ 子树(d) : d ∈ B }`。

    `subtree_of` 由调用方给（内核侧是「`d` 及其全部后代」）。
    ⚠️ 本模块**不认识内核**，所以树是**参数**，不是 import。
    """
    out: set[str] = set()
    for d in block:
        out |= subtree_of(d)
    return frozenset(out)


def view_parts(
    q: Sequence[frozenset[str]],
    subtree_of: Callable[[str], frozenset[str]],
) -> dict[frozenset[str], tuple[frozenset[str], ...]]:
    """每块 `B` 的**部件**：`B` 自己 + 其余块与 `reach(B)` 的交（非空的那些）。

    这些部件**恰好划分** `reach(B)` —— 不是巧合，是「块两两不交、并集是 `U`」
    的直接推论（`ViewSpec` 已经保证）。所以

        reach(B) = B ⊎ p₁ ⊎ p₂ ⊎ …

    是**恒等式**，任何「在集合上可下推」的读数都必须满足它。

    ⚠️ 于是这条恒等式**不能**当成判据用（恒真的东西不提供信息）——
       `§A4` 判的是**声称的那个合成函数对不对**：声称 `distributive` 就要给出
       `G`，实测 `读数(reach) == G(读数(B), 读数(p₁), …)`。`G` 给错了就红。
       这正是设计稿 §9 那句「声称 distributive 但**实测 ≠** ⇒ 红」。
    """
    out: dict[frozenset[str], tuple[frozenset[str], ...]] = {}
    for b in q:
        reach = reach_of(b, subtree_of)
        parts = [b]
        for other in q:
            if other is b:
                continue
            cut = other & reach
            if cut:
                parts.append(cut)
        out[b] = tuple(parts)
    return out


# --- 折叠（`§M0`–`§M6` 的被判对象） --------------------------------------------
#
# ⚠️ **折叠不是产物，是一条「被检验过、判定了不产生第二层」的支线。**
#    抽象层**只对检索器负责**（`docs/分层方向视图-抽象层.md` §0.0，2026-10-09
#    定义更正）：把**结构层**看成一个视图，提取**一份认识**交给**下一层的检索器**。
#    ⚠️ **多层是结构层自己就有的**（层 = 方向的分级，秩 = 1 + max(父方向秩)）
#    ⇒「视图的**视图**」再折一次，折的是**已经折过的东西**。实测（36 项 / 三方向）
#    **真折 0 层**（第 1 层的商不缩，`|Q₁| == |U₁|`）⇒ 折叠在此终止。
#    ★ 它的价值是**关掉一个选项**（「视图的视图会不会是另一层」→ 答案：不是）。
#    见 `MEASUREMENTS.md` 结果二十。
#
# ⇒ 下面这一段是**机制**，不是产物、也不是抽象层的交付物。它仍然要跑（第 0 层之后
#    到底折不折得动，得试过才知道），所以刹车（`§M2`）、账（`§M6`）、
#    出口（`§M5`）都还是**契约** —— 那四条照旧守，但它们守的是机制。
#
# 「把流程 E 反复套在自己身上」的**前作核验**（`docs/分层方向视图-多层抽象-前作核验.md`）
# 把成熟范式（METIS 的三段式 / LSM 的层数）与 ldv 的处境逐条对过，结论是：
#
#     MGP 与 LSM 能「反复套自己」，是因为那个操作是**自同态**且**收缩比有保证**。
#     ldv 的归组**两条都不满足**：
#       **不是自同态**（`方向结构 → 划分`，要套自己得先造一个**商规格**）
#       **收缩比无保证**（抽象层 §4 的 `E3` 明说「块数 == 方向数」是可能的）
#     ⇒ ldv 的折叠 = 「反复套自己 + **每层重新外生声明一次尺度** + 阈值当**主刹车**」。
#
# 三条边界照旧（E-1/E-2/E-3）：**没有全局目标函数、不排序、不写外生项**。
# 折叠的每一层都只由 `csr`（Paige–Tarjan，解唯一）产出 ⇒ 不需要目标函数。


#: ⚠️ **`MAX_LEVELS` / `MIN_SHRINK_RATIO` 已上移到「认识的持续维护」那一节**
#:    （2026-10-10）—— 它们的**第二个**消费者 `maintain`（`§A8`）在那个位置，
#:    而模块级常量必须在**第一次使用之前**定义。**定义只有一处**，就在上面。


@dataclass(frozen=True)
class Level:
    """折叠出来的一层。**判据要的每一件事都在这里**，不许去别处现算。

        `spec`      这一层的外生规格（第 0 层是调用方给的，之后每层是新声明的）
        `q`         这一层的视图划分（`csr(spec)`，第 0 层由调用方给）
        `shrink`    `|q| / |spec.universe|` —— `§M1` 的输入
        `n_cand`    `∏ Bell(组内块数)` —— `§M4` 的输入（与 `§A3` **同一个函数**）
        `stopped`   这一层为什么停：`""` / `"M1 收缩比"` / `"M2 层数"`
        `ans`       这一层**答得出**的原始项（由调用方的 `answer` 给）
        `lost`      从上一层到这一层**丢掉**的：`ans(上) − ans(本)`（第 0 层为 ∅）
        `ledger`    **携带**下来的账：`ledger(上) ∪ lost`

    ⚠️ `lost` 与 `ledger` 是**分开**两个字段，不许用一个算另一个。
       这正是 `§M6` 判的东西：**账必须被携带**，而不是「用的时候现推」。
       现推的话，「账被丢了」与「账本来就是空的」**长得一模一样** ——
       而前者是把误差**偷偷优化掉**，正是设计稿 §1 花一整节挡掉的那条路。
    """

    spec: ViewSpec
    q: tuple[frozenset[str], ...]
    shrink: float
    n_cand: int
    stopped: str = ""
    ans: frozenset[str] = frozenset()
    lost: frozenset[str] = frozenset()
    ledger: frozenset[str] = frozenset()

    @property
    def n_blocks(self) -> int:
        return len(self.q)


def block_namer(q: Sequence[frozenset[str]]) -> dict[frozenset[str], str]:
    """块 → 视图 id。**按 `partition_of` 的同一套排序**（最小元素、块大小）⇒ 可复现。

    ⚠️ 排序**不是**「按好坏排」（E-2 禁排序），只是为了让同一个划分只有一种写法。
    """
    return {b: f"V{i}" for i, b in enumerate(partition_of(q))}


def quotient_universe(q: Sequence[frozenset[str]]) -> tuple[str, ...]:
    """`q` 折成一层之后的 `universe` —— 就是那些**视图 id**，按名字里的数字排。

    ⚠️ 单列一个函数是为了 `fold_until` 能**先**把新 `universe` 算出来、再交给
       `next_partition` —— 下一层的 `P` 是**人声明的**，而人要声明它就得先看见
       它是对**哪些东西**的划分。把这一步藏在 `quotient_spec` 里面，调用方
       拿到的就永远是**上一层**的 `universe`（实测踩到：`P = {上一层 25 个方向}`
       被当成新层的划分 ⇒ `ViewSpec` 抛「不是划分」）。
    """
    return tuple(sorted((b for b in block_namer(q).values()), key=lambda s: int(s[1:])))


def quotient_spec(
    spec: ViewSpec,
    q: Sequence[frozenset[str]],
    partition: Iterable[Iterable[str]],
) -> ViewSpec:
    """把一层折成下一层的**规格** —— 商。

        `universe`  := 视图 id（由 `q` 的块规范化命名）
        `relation`  := { (块(a), 块(b)) : ∃(a,b) ∈ E, **块(a) ≠ 块(b)** }
        `partition` := **不由这里产出** —— 是**参数**

    ## ★ 为什么 `partition` 是参数，不是算出来的（`§M0` 的兄弟条款）

    设计稿 §三 3.1 与 §四 (3) 定死了这一条：`E` 套在自己身上时，
    **第 k 层的 `P` 就是第 k−1 层的 `Q`** ⇒ **尺度在每一层被重新外生化一次**。
    对照：MGP 的「目标块数 k」全程不变、LSM 的 `T` 全程不变 ——
    **「每层重新外生声明一次尺度」这个形态，文献里没有。**

    ⇒ 把它做成参数（而不是默认 `{U}`），是为了让「谁声明了这一层的尺度」
      在**调用点**上看得见。默认一个值就等于把它**偷偷内生化**了（违反 `§K9`）。

    ## ⚠️ `A ≠ B` 那个过滤（`§M0`）

    `A == B` 的边会让「稳定」条件退化成**恒真** —— 一个**空转的判据**。
    所以自环必须被挡在这里，而不是靠下游判据「顺便不管它」。
    """
    namer = block_namer(q)
    home = {x: namer[b] for b in partition_of(q) for x in b}
    relation = frozenset(
        (home[a], home[b])
        for (a, b) in spec.relation
        if home.get(a) is not None and home.get(b) is not None and home[a] != home[b]
    )
    return ViewSpec(universe=quotient_universe(q),
                    partition=partition_of(partition),
                    relation=relation)


def fold_until(
    spec: ViewSpec,
    q0: Sequence[frozenset[str]],
    next_partition: Callable[[int, tuple[str, ...]], Iterable[Iterable[str]]],
    answer: Callable[[ViewSpec, Sequence[frozenset[str]], dict[str, frozenset[str]]],
                     frozenset[str]],
    *,
    max_levels: int = MAX_LEVELS,
    min_shrink_ratio: float = MIN_SHRINK_RATIO,
    cap: int = MAX_COARSENING_CANDIDATES,
) -> list[Level]:
    """把 `E` 反复套在自己身上，**到「收缩比」或「层数」刹车为止**。

    `next_partition(k, universe_k)` 给出第 `k` 层（`k` 从 1 起）的 `P` ——
    ⚠️ 它拿到的是**新一层**的 `universe`（视图 id），不是上一层的方向 id
    （见 `quotient_universe` 的 ⚠️）。`P` 是**外生**的，见 `quotient_spec`。

    `answer(spec_k, q_k, down_k)` 给出这一层答得出的原始项（**由调用方定义** ——
    本模块不认识内核，也不认识覆盖 oracle）。⚠️ `down_k` 是**第三个数**：
    第 `k` 层的一个视图 id 对应**第 0 层**的哪些方向 —— 没有它，调用方拿到
    `V0` 这种名字**根本无从下手**（实测踩到：`KeyError: 'V0'`）。

    ## `§M5`：终止**只许**由 `§M1` / `§M2` 触发

    不许有第三条路（「跑到不动点」）。⚠️ 这条不是形式主义：
    `csr` 完全可以返回一个**不比 `P` 更细**的划分（抽象层 §4 `E3`：
    「块数 == 方向数」是**可能**的），那时下一层的 `universe` 与这一层**一样大**
    ⇒ 层数**不会自己终止**。⇒ 那种情形必须被 `§M1`（收缩比 = 1.0 > 阈值）接住，
    **恰好返回 1 层** —— 而那一层**就是视图层**（它自己撞了刹车，`§M1` 直接判它）。
    所以「不动点」不是一条独立出路，它是 `§M1` 的一个特例。

    ## ⚠️ `stopped` 是**判据的输入**，不是日志

    `§M5` 判的就是「最后一层的 `stopped` 非空且取值合法」。若把它写成日志，
    判据就只能**重新推断**为什么停 —— 而「推断出来的原因」与「真实原因」
    在只看布尔值时长得一模一样。
    """
    levels: list[Level] = []
    cur: ViewSpec = spec
    q = partition_of(q0)
    down: dict[str, frozenset[str]] = {d: frozenset({d}) for d in spec.universe}
    prev_ans: frozenset[str] | None = None
    ledger: frozenset[str] = frozenset()

    while True:
        shrink = len(q) / len(cur.universe) if cur.universe else 1.0
        n_cand = n_coarsening_candidates(cur, q, cap)
        stop = ""
        if shrink > min_shrink_ratio:
            stop = "M1 收缩比"
        elif len(levels) + 1 >= max_levels:
            stop = "M2 层数"

        ans = frozenset(answer(cur, q, down))
        lost = frozenset() if prev_ans is None else (prev_ans - ans)
        ledger = ledger | lost
        levels.append(Level(spec=cur, q=q, shrink=shrink, n_cand=n_cand,
                            stopped=stop, ans=ans, lost=lost, ledger=ledger))
        if stop:
            return levels

        # ⚠️ 先算**新一层**的 `universe`，再交给 `next_partition` ——
        #    `P` 是对**新**那一层的划分（见 `quotient_universe` 的 ⚠️）。
        part = next_partition(len(levels), quotient_universe(q))
        down = {name: frozenset().union(*(down[x] for x in block))
                for block, name in block_namer(q).items()}
        cur = quotient_spec(cur, q, part)
        q = partition_of(coarsest_stable_refinement(cur))
        prev_ans = ans
