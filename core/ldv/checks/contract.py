"""B1–B6 —— 契约类检查（§I1–§I6）。

六条都只做一件事：**把接口条款变成会红的判据**。

    B1 假阴     §K8  —— 假阴禁止。**唯一破坏正确性的那条**
    B2 合并覆盖 §I2  —— 粗方向不许拒掉细方向收下的东西
    B3 代价可比 §I3  —— 同层内两两可比（NaN / 非数都算不可比）
    B4 劈开划分 §I4  —— 不重不漏（**内核的构造性质**）
    B5 解码覆盖 §I5  —— `解码(编码(d)) ⊒ d`
    B6 信号不折叠 §I6 —— 「无信号」与「负信号」必须分得开

⚠️ 覆盖类（B2 / B5）用**行为式**判据，不用集合相等：

    覆盖 = 细方向说「是」的查询，粗方向**不许说「否」**

理由：`§I1` 允许假阳，所以「集合相等」这个判据根本立不住 ——
粗方向本来就允许比细方向多收。**能立住的只有单向的那一半**：
细方向收下的，粗方向不许拒。这与 §K8 的不对称契约是同一个形状。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..core.direction import ORIGIN_SPLIT, Direction
from ..core.interfaces import call_hit, call_penalty
from ..core.tri import Tri
from ._framework import Report

_QUERY_CAP = 40     # 每步最多探这么多查询 —— 够红，且不把报告淹掉


def _wrap(payload: Any, did: str, rank: int) -> Direction:
    """把插件产出的 payload 包成一个**合成方向**，只用来调 `命中`。"""
    return Direction(did=did, rank=rank, payload=payload, witness=(),
                     origin=ORIGIN_SPLIT, parent=None)


# --- §K8 的另一半：假阳计量 --------------------------------------------------

def false_positive_profile(kernel: Any, plugin: Any, queries: Sequence[Any]) -> dict[str, Any]:
    """**假阳计量** —— §K8 是「假阴禁止 / **假阳计量**」，两半都要有。

    `B1` 管前一半（假阴禁止，红了就阻断）；这里管后一半：
    假阳**允许**，但要**量出来**。

    为什么必须量：假阳的代价是性能，而性能是**会累积**的。
    一个假阳率 90% 的过滤器不违反任何一条检查，却让整套结构白建 ——
    所有方向都说「是」，等于没有方向。

    ⇒ 所以它是**度量，不是判据**：进 `report()`，不进退出码（继承 dce）。
    """
    yes_total = 0
    fp_total = 0
    worst: tuple[int, str] = (0, "")
    per_direction: dict[str, tuple[int, int]] = {}
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        y = f = 0
        for q in queries:
            if call_hit(plugin, d, q) is not Tri.YES:
                continue
            y += 1
            if not (mem & q.ideal):
                f += 1
        if y:
            per_direction[d.did] = (y, f)
            if f > worst[0]:
                worst = (f, d.did)
        yes_total += y
        fp_total += f
    rate = (fp_total / yes_total) if yes_total else 0.0
    return {
        "判「是」总数": yes_total,
        "其中假阳": fp_total,
        "假阳率": rate,
        "最差方向": worst[1] or "—",
        "有判「是」的方向数": len(per_direction),
    }


def _yes_directions(kernel: Any, plugin: Any, queries: Sequence[Any]) -> dict[str, list[Any]]:
    """每个方向说「是」的查询 —— B2 / B5 的公共输入。"""
    out: dict[str, list[Any]] = {}
    for d in kernel.all_directions():
        out[d.did] = [q for q in queries if call_hit(plugin, d, q) is Tri.YES]
    return out


def cover_nesting_profile(kernel: Any, plugin: Any,
                          queries: Sequence[Any]) -> dict[str, Any]:
    """**覆盖嵌套** —— 子方向说「是」的查询，父方向不许说「否」。

        `覆盖(子) ⊆ 覆盖(父)`  ⟺  `命中(子,q) = 是 ⇒ 命中(父,q) ≠ 否`

    ## 为什么这是**度量**，不是判据

    §1 的硬要求表里**没有**这一条（硬的是「成员层 `members(子) ⊆ members(父)`」
    与「健全性 `members ⊆ 覆盖`」）。所以违反它**不破坏正确性** ——
    剪枝仍然安全（父的「否」只在父的覆盖之外才下）。

    但它**不是无害的**：子方向收下的东西被父否掉，说明**父的过滤器比子的更松**，
    于是遍历在父那一层**白走一趟**才到子。这正是 §K8 说的「假阳的代价是性能，
    而性能会累积」的结构形态 ⇒ 进输出，不进退出码。

    ## 它为什么值得单独量（与假阳率的分工）

        假阳率      说「说了多少句不该说的**是**」—— **行为**侧
        覆盖嵌套    说「子说的『是』，父接不接得住」—— **结构**侧

    实测（36 项语料，A 方向，12 个已展开方向）：`劈开` 拿不到父方向时，
    子方向把父的 `forb` 丢了 ⇒ **越界 14 次**；把父的 `forb` 续上之后 **0 次**。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        checked += 1
        for k in kids:
            for q in queries:
                if call_hit(plugin, k, q) is not Tri.YES:
                    continue
                if call_hit(plugin, d, q) is Tri.NO:
                    bad.append(f"{k.did} 说「是」的 {q.label}，被父 {d.did} 判「否」")
                    break
    # ⚠️ `越界次数` 是**次**数（一个方向可能有多个子方向越界），不是方向数。
    return {"已展开方向数": checked, "越界次数": len(bad), "例子": bad[:2]}


# --- §1 硬要求表第三 / 第四行（第四行 ⬜ 2026-10-07 已降级）--------------------
#
# ⚠️ **健全性（第三行）与覆盖不漏（第四行）不在这里** —— 它们与 `B16` / `B18` / `B19`
#    一起放在 `checks/coverage.py`。理由只有一条：那一族**共用一个外生覆盖定义**
#    （`_fixtures.coverage_of`），拆在两处就会出现「同一条性质两种算法」——
#    而那正是**与被检查对象共用盲点**的形状（按 `§I3 代价` 重算覆盖）。
#    本模块只管 §I1–§I6 的**契约**。


# --- B1 ---------------------------------------------------------------------

def b1_no_false_negative(kernel: Any, plugin: Any, queries: Sequence[Any], rep: Report,
                         path: str = "批建") -> None:
    """`命中` 说「否」时，这个方向里**必须真的没有**命中 —— §K8 的行为式那一半。

    ground truth 是 `members(d) ∩ ideal(q)`，**外生给定**（不调插件算答案）。

    ## ★ **两条路径各判一次**

    这条判据的 ground truth 里**有成员集**，而成员集在两条路径上不同：

        一次建完     成员集在**第一次展开时就是完整的**
        维护路径     成员是**后来才长起来的** ⇒ 同一批查询问的是**另一个**成员集

    ⇒ 两条路径不是同一件事，**各占一行**。`path` 印在标题里，因为
      「只跑一条路」与「两条路都跑」在汇总里长得一模一样 ——
      判据的适用范围，**收窄要印、放宽也要印**。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        for q in queries:
            truth = mem & q.ideal
            if not truth:
                continue
            checked += 1
            if call_hit(plugin, d, q) is Tri.NO:
                bad.append(f"{d.did}×{q.label}（实际有 {sorted(truth)[:3]}）")
    if checked == 0:
        rep.add("B1", f"假阴（{path}路径）", Tri.UNEXPANDED, "没有一条「实际有命中」的样本可查")
        return
    rep.add("B1", f"假阴（{path}路径）：命中说否而实际有命中",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)}/{checked} 条假阴：{bad[:3]}" if bad else f"{checked} 个「实际有命中」样本全部未被判否")


# --- B2 ---------------------------------------------------------------------

def b2_merge_covers(plugin: Any, kernel: Any, queries: Sequence[Any], rep: Report) -> None:
    """`合并(P)` 不许拒掉 P 里任一方向说「是」的查询。"""
    yes_of = _yes_directions(kernel, plugin, queries)
    groups: list[tuple[Direction, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if len(kids) >= 2:
            key = tuple(k.did for k in kids)
            if key not in seen:
                seen.add(key)
                groups.append(kids)
    # 再补上「全层一起合」
    for rank in sorted({d.rank for d in kernel.all_directions()}):
        layer = tuple(d for d in kernel.all_directions() if d.rank == rank)
        if len(layer) >= 2:
            key = tuple(k.did for k in layer)
            if key not in seen:
                seen.add(key)
                groups.append(layer)

    bad: list[str] = []
    checked = 0
    for group in groups:
        merged = _wrap(plugin.merge(list(group)), f"M({group[0].did}..)", group[0].rank - 1)
        for d in group:
            for q in yes_of.get(d.did, [])[:_QUERY_CAP]:
                checked += 1
                if call_hit(plugin, merged, q) is Tri.NO:
                    bad.append(f"{d.did} 说「是」的 {q.label}，被 合并({len(group)} 个) 判「否」")
    if checked == 0:
        rep.add("B2", "合并覆盖", Tri.UNEXPANDED, "没有可合的分组，或没有任何方向说「是」")
        return
    rep.add("B2", "合并覆盖：粗方向不许拒掉细方向收下的",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)}/{checked} 条被拒：{bad[:2]}" if bad else f"{len(groups)} 组 × {checked} 次判定全过")


# --- B3 ---------------------------------------------------------------------

def b3_penalty_comparable(kernel: Any, plugin: Any, rep: Report) -> None:
    """同一层内，`代价` 的返回必须**两两可比**。

    ⚠️ 插件抛异常要**记成违例**，不许让检查崩掉。
       检查崩掉 = 这一条静默消失 = 「空转与通过长得一模一样」。

    ## 复杂度：`O(层内方向数 × 项数)` —— **不是** `O(层内方向数² × 项数)`

    这里原来是三层循环（层内两两配对、每对逐项比）。那是 **`O(L²n)`**：
    3907 项 / reach 有 7813 个方向 ⇒ **小时级**。而它是**判据**（`B1–B20`），
    `--no-probes` **关不掉它** ⇒ **它才是全量套件跑不完的主因**。

    那个循环**证明上不可能命中**（理由见函数体注释），已删除。
    前提由 `test_b3_reduction_premise` 钉住 —— **前提一破就得加回来**。

    ⚠️ 删掉之后 `b3` 在 3907 项上是 **38.85 s**（读数见 `MEASUREMENTS` 结果八）。
    它剩下的形状是 `O(Ln)` = **30,521,484 次** `call_penalty`，
    所以真正的瓶颈**移到了插件里**（每次调用内部跑一遍图遍历）——
    那一条由 `plugins/reach.py` 的缓存治，与本函数无关。

    ⚠️ 删循环时另外两处**必须同时看**，否则省下的时间会从别的地方回来：

    ① **排序只做一次**。`sorted(kernel.items)` 原来在 `for d in layer:` **里面**
       ⇒ 每层重排 `L` 次 ⇒ 多花 `O(L × n log n)`。
    ② **`table` 只写不读**了（原来只有那个三重循环读它）⇒ 删掉。
       留着的话，全量上是 7813 列 × 3907 项 ≈ **244 MB** 白占。

    ⚠️ 还有一条**不属于性能**的：那三行记账（`table[...]` / `checked +=` / NaN 检查）
    必须留在 `for d in layer:` **体内**。掉出去 ⇒ 每层只记最后一个方向，
    而 `B3` 依旧报「是」—— 「空转与通过长得一模一样」。
    `test_b3_reduction_premise` ⑦ 专门钉这一条。
    """
    bad: list[str] = []
    checked = 0
    #: ⚠️ **只排一次**。`kernel.items` 在 `B3` 全程不变，而它若待在方向循环里，
    #:    每层就要重排 `L` 次。列与列的**逐项对齐**靠的就是「所有列同序」——
    #:    同序由这一行保证，不是靠每列各排一次。
    items_sorted = sorted(kernel.items)
    for rank in sorted({d.rank for d in kernel.all_directions()}):
        layer = [d for d in kernel.all_directions() if d.rank == rank]
        if len(layer) < 2:
            continue
        for d in layer:
            vals: list[float] = []
            for i in items_sorted:
                try:
                    vals.append(call_penalty(plugin, d, kernel.items[i]))
                except Exception as exc:  # noqa: BLE001 - 插件是外部代码
                    bad.append(f"{d.did} 的代价抛异常：{type(exc).__name__}: {exc}")
                    vals.append(float("nan"))
            checked += len(vals)
            if any(v != v for v in vals):   # NaN
                bad.append(f"{d.did} 的代价里有 NaN（不可比）")
    # ⚠️ 这里**没有**「两两逐项」的三重循环 —— 它**证明上不可能命中**，删掉了。
    #
    #    前提：`call_penalty` 只可能返回**有限 float**。非数 / NaN / ±inf 全在它里面抛
    #          （`ldv/core/interfaces.py:190-196`）⇒ 上面每一个代价值都是有限 float。
    #    推论：**两个有限 float 之间 `a < b or a == b or a > b` 恒为真**
    #          —— IEEE-754 里「三个都不成立」当且仅当有一方是 NaN（无序）。
    #    ⇒ 那个 `O(层内方向数² × 项数)` 的循环**永远 append 不了东西**。
    #      它唯一可能命中的情形，是上面自己塞进去的 `float("nan")`，而那一行**已经报过**。
    #
    #    ⚠️ **前提必须被检查，不许靠读代码断言**：`test_b3_reduction_premise`
    #       钉住「`call_penalty` 对 NaN / 非数 / ±inf 必须抛」。那条一破，
    #       这个循环就得加回来 —— 而**加回来是 O(L²n)**（见 `MEASUREMENTS` 结果八）。
    if checked == 0:
        rep.add("B3", "代价可比", Tri.UNEXPANDED, "没有任何一层有 ≥2 个方向")
        return
    rep.add("B3", "代价可比：同层内两两可比",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处不可比：{bad[:2]}" if bad else f"{checked} 个代价值全部可比")


# --- B4 ---------------------------------------------------------------------

def b4_split_is_partition(kernel: Any, rep: Report, path: str = "批建") -> None:
    """`劈开` 的结果必须构成**划分** —— §10.2 出路 (4) 之后是**收窄**版：

        ∪members(子) ⊆ members(父)                       ← 不许**多**（重叠）
        members(父) − ∪members(子) == **账上那些滞留项**  ← 漏的必须**条条有账**

    ⚠️ 查的是**内核的归属分配**，不是插件返回的 payload。
       理由见 `core/kernel.py` 开头：`命中` 允许假阳 ⇒ 重叠永远看不出来，
       所以「不重不漏」只能由内核的分配来保证，也只能查那里。

    ## ★ `path` 参数：**判据落在哪条装配上，必须印出来**

    同一条性质在**不同的批/维护比例**下不是同一件事 —— `run_checks` 跑三条：

        batch          一次建完
        maintenance    先建 6、维护其余（`MAINT_INIT`，照 `C7` / `C10` 的读数口径）
        cli 切分       先建 n−12、维护 12（`cli --holdout` 的默认值）

    「只跑一条」与「三条都跑」在汇总里长得**一模一样** ⇒ `path` 必须印进标题。
    （与 `B1` / `B16` 的 `path` 参数同一条纪律。）

    ## 为什么是「等式 + 账」，而不是裸的 `⊆`

    裸的 `∪members(子) == members(父)` 默认**每一项都往下走**。
    §10.2 出路 (4) 之下这个前提不成立：**每个子方向都证明不收它**的项
    **停在父方向**（X-tree 的 supernode 同形："only if there is no other possibility"）。

    ⇒ 直接放宽成 `⊆` 会让「漏」这件事**重新变得看不见** —— 那正是本设计
      反复要消灭的形状。所以**两头都钉**：

        `⊆`                    ⇒ 仍然不许重叠
        漏的集合 == 账上滞留集  ⇒ 漏**只能**是「被证明收不住」的那些，一条不多

    ★ 这不是放宽，是**换了个更强的东西钉**：钉的不再是「不漏」，而是
      「**要么不漏，要么每一条漏都有证明**」。前者在滞留恒为 0 时才等价。
    """
    bad: list[str] = []
    checked = 0
    with_stay = 0
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if len(kids) < 2:
            continue
        checked += 1
        parent = kernel.members_of(d)
        union: set[str] = set()
        for k in kids:
            mk = set(kernel.members_of(k))
            dup = union & mk
            if dup:
                bad.append(f"{d.did} 的子方向重叠：{sorted(dup)[:3]}")
            union |= mk
        extra = sorted(union - set(parent))
        if extra:
            bad.append(f"{d.did} 的子方向多出父没有的项：{extra[:3]}")
            continue
        miss = set(parent) - union
        if miss:
            with_stay += 1
        accounted = set(kernel.stayed_of(d))
        if miss != accounted:
            bad.append(
                f"{d.did} 的滞留与账不符：漏 {sorted(miss)[:3]}，账上 {sorted(accounted)[:3]}")
    if checked == 0:
        rep.add("B4", f"劈开成划分（{path}）", Tri.UNEXPANDED, "没有任何方向被展开过")
        return
    rep.add("B4", f"劈开成划分（{path}）：不重，且漏的恰好是账上那些",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处破划分：{bad[:2]}" if bad
            else f"{checked} 个已展开方向：子集不重叠，"
                 f"其中 {with_stay} 个有滞留且滞留与账逐项相符")


# --- B5 ---------------------------------------------------------------------

def b5_decode_covers(plugin: Any, kernel: Any, queries: Sequence[Any], rep: Report) -> None:
    """`解码(编码(d))` 必须**覆盖** `d`（可以更粗，不可以更细）。"""
    yes_of = _yes_directions(kernel, plugin, queries)
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        blob = plugin.encode(d)
        if not isinstance(blob, (bytes, bytearray)):
            bad.append(f"{d.did} 的 §I5 编码不是 bytes：{type(blob).__name__}")
            continue
        back = _wrap(plugin.decode(bytes(blob)), f"dec({d.did})", d.rank)
        for q in yes_of.get(d.did, [])[:_QUERY_CAP]:
            checked += 1
            if call_hit(plugin, back, q) is Tri.NO:
                bad.append(f"{d.did} 说「是」的 {q.label}，被 解码(编码(d)) 判「否」")
    if checked == 0:
        rep.add("B5", "解码覆盖", Tri.UNEXPANDED, "没有任何方向说「是」")
        return
    rep.add("B5", "解码覆盖：解码(编码(d)) ⊒ d",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)}/{checked} 条被拒：{bad[:2]}" if bad else f"{checked} 次往返判定全过")


# --- B6 ---------------------------------------------------------------------

def render_usage(rec: Any) -> str:
    """使用记录的**输出形态** —— B6 查的就是这里。"""
    if rec is None:
        return "无信号"
    return f"有信号 outcome={rec.outcome:+.2f} 倾向={rec.propensity:.3f}"


def b6_signal_not_collapsed(kernel: Any, plugin: Any, rep: Report) -> None:
    """「没有观测到使用」与「使用了但为负」必须**长得不一样**。

    arena 那条「0 和『没有』分不开，就不许印成 0」在这里的形态。

    ⚠️ 生效的是 **`kernel.plugin`**，不是传进来的 `plugin`：
       `record_usage` 是**内核方法**，它调的是内核自己持有的那个插件。
       这一点是**注入验证抓出来的** —— 当时传进来的插件被换成「把 None 折成 0」，
       检查照样绿，因为那个插件**压根没被用到**。
       ⇒ 所以这里显式要求两者是同一个对象，不一致就报「未展开」而不是装作通过。
    """
    if plugin is not kernel.plugin:
        rep.add("B6", "信号不折叠", Tri.UNEXPANDED,
                "传入插件 ≠ 内核持有的插件 —— 这样测的是错对象，宁可不测")
        return
    d = kernel.all_directions()[0]
    none_rec = kernel.record_usage(d, {"outcome": None, "note": "没被点"})
    neg_rec = kernel.record_usage(d, {"outcome": -1.0, "note": "点了但没用"})
    r_none, r_neg = render_usage(none_rec), render_usage(neg_rec)
    bad: list[str] = []
    if none_rec is not None:
        bad.append(f"「没有观测到使用」被折成了记录：{r_none}")
    if neg_rec is None:
        bad.append("「使用了但为负」被折成了「无信号」")
    if r_none == r_neg:
        bad.append(f"两者渲染相同：{r_none!r}")
    if neg_rec is not None and not (0.0 < neg_rec.propensity <= 1.0):
        bad.append(f"倾向权重越界：{neg_rec.propensity}")
    rep.add("B6", "信号不折叠：无信号 ≠ 负信号",
            Tri.NO if bad else Tri.YES,
            "；".join(bad) if bad else f"「{r_none}」 ≠ 「{r_neg}」")
