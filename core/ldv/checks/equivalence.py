"""B17 —— 叶 = **不可分等价类**（`§K2` 判空的下界）。

    python -m ldv.run_checks            # 三个插件都跑

---

## 这条检查回答的是什么（`C8` §7 第 2 步）

第 1 步（`checks/divergence.py`）把问题定住了：三个方向**没有一个**能在后添 ≥2 项时
与全量同构，而且差异**不在尺度**。第 2 步问的是 `§K2` 的**第三个选项**
（ART 的 `Path Compression`）：不建那一层，但把「区分的键」并进父方向的 payload ——
**三个方向里哪几个能这么做？**

**实测答案：一个都不能，而且原因是可证的。**

    方向      判空点数   其中「域内有差异」   最大等价类   最大叶容量   叶数   等价类数
    ─────────────────────────────────────────────────────────────────────────────
    keyset       7             0              10          10        13      13
    sequence     8             0              10          10        11      11
    reach        0             —               1           1        36      36

四个结论，逐条都是决策依据：

**① 判空点上**没有**区分信息可搬** —— 项在该方向的**语义下就是同一个东西**。
   `keyset` 的 7 个判空点，成员**键集完全相同**；`sequence` 的 8 个，**序列完全相同**。
   ⇒ 不是「payload 表示不了」，是**没有东西可表示**。

**② 所以「代价必须落到叶容量」不是前提，是定理。**
   等价项在任何 payload 上都给同一答案 ⇒ 它们永远落在同一个叶
   ⇒ `最大叶容量 ≥ 最大等价类`；而判空点上它们是等价的 ⇒ 叶不比分得更细
   ⇒ `最大叶容量 ≤ 最大等价类`。两边合起来是**等式** —— 那就是该方向能表达的
   **分辨率下界**，任何 payload 设计都越不过去。

**③ ART 的 `Path Compression` 之所以有用，是因为它的键是字符串。**
   被跳过的单子节点链上那些字符**本来就是区分的** —— 信息从未丢失，只是没建节点，
   所以把它**存下来**是**恢复**。我们的判空点上没有这样的信息，所以存无可存。

**④ 而且 (c) 在 A / C 上**已经实现了**。**
   `split` 用的 `common`（keyset 的 `∩ keysets`）/ `p_star`（sequence 的组内 LCP）
   就是**沿树吸收的那一段** —— 那正是 path compression 的等价物。
   剩下的判空是**不可约**的部分，不是没做。

⇒ **`§K2` 的第三个选项在本设计里不需要做**（`C8` §7 第 2 步到此结束）。
   要降叶容量，只剩第 3 步那条路：**改方向本身**（让 `hit` 用上更多信息），
   而那不是 `§K2` 的选项，是**换一个方向**。

---

## 为什么这条检查不是「度量」

`可搬的判空点 = 0` 看起来像个读数，但它**能红**：
插件在**能分开**的一组项上谎报「分不开」时它就 > 0。所以它是**判据**，进退出码。
（一个永远报 0 的读数才是度量；这一条有明确的、能构造出来的反例。）

## ★ 为什么不能只用「叶容量」这个度量来守它

实测（`test_injections.py` 的 B17 注入）：插件在 ≤4 项的组上谎报「分不开」之后 ——

    叶容量 最大 **10 → 10**（**没变**！）      判空点 7 → 8
    叶数   13 → 8                            可搬的判空点 0 → **3** ← 缺陷在这里露出来

叶容量没变，是因为最大的那个等价类（10 项）本来就在另一个方向上，
局部合并只是把几个**小**类并起来，够不到最大值。
⇒ **叶容量是「最坏情况」的数，它对局部退化不敏感**；
  而这条检查看的是**成分**（每个叶里有没有混进不同的类），所以抓得到。
这正是「判据与度量分工」的一个具体例子：**度量报趋势，判据守成分。**

## 两条断言，缺一不可

    (a) 叶 ⊆ 等价类    叶里不许混进两个**能分开**的项（插件谎报「分不开」）
    (b) 等价类 ⊆ 叶    同一个等价类不许被拆到两个叶里（划分比该方向更细）

(a) 单独成立是不够的：一个把所有项都塞进一个叶的内核，只要那些项恰好互相等价就过。
两条合起来才是**等式** `叶容量 = 最大等价类` —— 那也是该方向的**分辨率下界**：

    等价项对任何 payload 都给同一答案 ⇒ 必落同一个叶 ⇒ 叶容量 **≥** 最大等价类
    每个叶都是单个等价类              ⇒ 叶容量 **≤** 最大等价类
    ────────────────────────────────────────────────────────────────────
    ⇒ 等式。任何 payload 设计都越不过去。

⚠️ **(b) 的注入在**内核**那一侧，不在插件那一侧。** 原因：等价项在任何 payload 上
   **代价都相同**，所以内核只要「平手时按方向定序」它们就必然同侧 ——
   要拆开它们，得让内核在**平手时按别的东西定序**（比如「哪边桶空放哪边」）。
   ⇒ 这是**内核**的性质，不是插件的。`run_tests.test_equivalence` 两条都注了。

## 与 `B15` 同一条纪律：退化形态要报「跳过」，不报「过」

`reach` 的等价类**全是单点**（本语料是 DAG，没有环）⇒ 两条断言都**恒真**。
那时这条检查什么都没检查，所以报「**跳过**」——
「空转与通过长得一模一样」在这条检查上的形态。
"""

from __future__ import annotations

from typing import Any, Iterable

from ..core.tri import Tri
from ._framework import Report, result_of as _result


def _leaves(kernel: Any) -> list[Any]:
    return [d for d in kernel.all_directions() if not kernel.children_of(d)]


def _class_sizes(classes: dict[str, frozenset[str]]) -> list[int]:
    return sorted({len(c) for c in set(classes.values())}, reverse=True)


def _split_classes(kernel: Any, leaves: list[Any],
                   classes: dict[str, frozenset[str]]) -> list[frozenset[str]]:
    """被拆到多个叶里的等价类 —— (b) 那一半的读数。"""
    leaf_of: dict[str, str] = {}
    for d in leaves:
        for i in kernel.members_of(d):
            leaf_of[i] = d.did
    by_class: dict[frozenset[str], set[str]] = {}
    for i, c in classes.items():
        by_class.setdefault(c, set()).add(leaf_of.get(i, "<未落叶>"))
    return [c for c, ls in by_class.items() if len(ls) > 1]


def b17_leaf_is_equivalence_class(kernel: Any, classes: dict[str, frozenset[str]],
                                  rep: Report, which: str = "") -> None:
    """叶必须**恰好**是一个不可分等价类（两条断言合起来 = 等式）。

    `classes` 由 `_fixtures.equiv_classes()` 造 —— **外生**，不调插件。

    ## ⚠️ 它**只该跑批建路径** —— 因为等式的**前提是 `滞留 == 0`**

    「`叶容量 = 最大等价类`」这一半（分辨率下界）靠的是「等价项必落同一个**叶**」。
    §10.2 出路 (4) 之后这句话**不成立**：等价项会一起**停在内部节点**上，
    而 `叶容量` 看不见它们。实测（`sequence`，先建 2 维护 34）：

        滞留 32 ｜ 最大叶容量 **2** ｜ 最大等价类 **10** ｜ 最大停留数 **32**
        两个叶各自仍然只有**一个**等价类 ⇒ 上面两条断言**都过**

    ⇒ 那两份读数自相矛盾（`2 = 10`），但**判据是绿的**。所以调用方**只跑批建**
      （那里滞留恒为 0）。维护路径上的下界改由 `最大停留数` 承担（§7.1 / §10.2 B）。
      **「这个数只在一条路上是下界」必须写在数自己的定义里**，否则下一个人会拿它
      去维护路径上比 —— 然后得到「分辨率比下界还高」这种不可能的好消息。

    ## ⚠️ 2026-10-07：**(b) 那一半在 `reach` 上从判据降为度量**

    两条断言的强度**不一样**：

        (a) 叶 ⊆ 等价类   判空点上不许有**能分开**的项 —— **三条方向都成立**，保留
        (b) 等价类 ⊆ 叶   结构不许**比语义更细** —— **在 `reach` 上不成立**

    (b) 在 `reach` 上不成立**不是实现 bug，是判据选错了**：

        `reach` 的 `split` 按 `(汇合签名, 距离, id)` 排序后取中点切，
        而 `id` 是**并列的** ⇒ 互相可达的两项（对**任何** payload 同进同出）
        会被中点切开 ⇒ 结构比语义更细。
        而 §K8 **允许假阳**（更细只赔性能，不赔正确性）⇒ (b) 在这里**过强**。

    实测（`_rc_small_noprobe` / `_rc_full_after_cache`，都是**既有行为**）：

        36 项     0 个类被拆开（该方向退化，判据本来就走「未展开」）
        281 项    **6** 个
        3907 项   **28** 个      ← 随尺度**长**，是系统性的，不是噪声

    ⇒ 处理：**(b) 在 `reach` 上只报不判**（读数 `被拆开的等价类`，见 `absorption_profile`）。
      ⚠️ **不把它记成「已知红」** —— 一条**常驻的红**等于没人再看红，
      而「基线里有一格永远红」正是「空转与通过长得一模一样」在**基线**上的形态。
    """
    leaves = _leaves(kernel)
    if not leaves:
        rep.add("B17", "叶 = 不可分等价类", Tri.UNEXPANDED,
                "没有任何叶（结构没建起来）—— 判不了，不是通过")
        return

    sizes = _class_sizes(classes)
    if not sizes or sizes[0] < 2:
        rep.add("B17", "叶 = 不可分等价类", Tri.UNEXPANDED,
                f"每个等价类都是单点（{len(set(classes.values()))} 个类）"
                f"⇒ 两条断言都恒真，本方向退化。**跳过 ≠ 通过**")
        return

    bad: list[str] = []

    # (a) 叶 ⊆ 等价类 —— 判空点上不许有能分开的项
    mixed = 0
    for d in leaves:
        ms = sorted(kernel.members_of(d))
        if not ms:
            continue
        cls = {classes[i] for i in ms if i in classes}
        if len(cls) > 1:
            mixed += 1
            if len(bad) < 2:
                bad.append(f"{d.did} 的叶里混了 {len(cls)} 个等价类"
                           f"（{len(ms)} 项）：{ms[:4]}")

    # (b) 等价类 ⊆ 叶 —— 同一个类不许被拆开。**`reach` 上只报不判**（见 docstring）。
    torn = _split_classes(kernel, leaves, classes)
    judged = which != "reach"
    if torn and judged:
        bad.append(f"{len(torn)} 个等价类被拆到多个叶："
                   f"{[sorted(c)[:2] for c in torn[:2]]}")

    caps = [len(kernel.members_of(d)) for d in leaves]
    cap_max = max(caps) if caps else 0
    floor = sizes[0]
    tail = ""
    if torn and not judged:
        tail = (f"｜（b）**本方向不判**：{len(torn)} 个等价类被拆到多个叶"
                f"（`reach` 的 `split` 有意比语义更细，§K8 允许假阳）—— 只报不判")
    n_cls = len(set(classes.values()))
    if bad:
        detail = "；".join(bad[:2])
    else:
        if judged:
            detail = (f"{len(leaves)} 个叶 = {n_cls} 个等价类；"
                      f"最大叶容量 {cap_max} = 最大等价类 {floor}"
                      f"（该方向的分辨率**已到下界**）")
        else:
            # ⚠️ **`(b)` 不判时这两个数本来就不相等、也不该相等** —— 把它们印成等式是
            #    **输出说谎**。实测（`pypi-deps`，有环）：
            #        「最大叶容量 1 = 最大等价类 7（该方向的分辨率**已到下界**）」
            #    —— `1 ≠ 7` 是**假等式**，而「已到下界」是**反的**
            #    （`(b)`「等价类 ⊆ 叶」在 `reach` 上**只报不判** ⇒ 下界那一半没人守）。
            #    ⚠️ **为什么以前没露**：随仓库那份 36 项语料是 **DAG** ⇒ `reach` 上它
            #    **直接跳过**，这一行根本不印。**换成有环语料才现形**
            #    （`run_tests.test_b17_cyclic_corpus` 守着这一处）。
            detail = (f"{len(leaves)} 个叶 = {n_cls} 个等价类；"
                      f"最大叶容量 {cap_max}、最大等价类 {floor}"
                      f"（⚠️ **不相等、也不该相等**）")
        detail += tail
    rep.add("B17", "叶 = 不可分等价类（叶容量 = 分辨率下界）",
            Tri.NO if bad else Tri.YES, detail)


def absorption_profile(kernel: Any, classes: dict[str, frozenset[str]]) -> dict[str, Any]:
    """`C8` §7 第 2 步的读数：**判空点上有没有可搬的区分信息**。

    一个判空点「可搬」⟺ 它的成员在该方向语义下**不属于同一个等价类**
    （即插件在能分开的组上判了「分不开」）。正确插件上这个数必须是 0。
    """
    rows: list[dict[str, Any]] = []
    tried = getattr(kernel, "_tried", {})           # noqa: SLF001
    for did, members in sorted(tried.items()):
        ms = sorted(members)
        cls = {classes[i] for i in ms if i in classes}
        rows.append({"方向": did, "成员": len(ms), "等价类数": len(cls),
                     "可搬": len(cls) > 1})
    sizes = _class_sizes(classes)
    leaves = _leaves(kernel)
    caps = [len(kernel.members_of(d)) for d in leaves]
    return {
        "判空点": len(rows),
        "可搬的判空点": sum(1 for r in rows if r["可搬"]),
        "最大叶容量": max(caps) if caps else 0,
        "最大等价类": sizes[0] if sizes else 0,
        "叶数": len(leaves),
        "等价类数": len(set(classes.values())),
        "被拆开的等价类": len(_split_classes(kernel, leaves, classes)),
    }


def render_absorption(prof: dict[str, Any], label: str = "") -> str:
    """一行读数。**与判据分开**：这是「第 2 步实测到了什么」的证据行。"""
    head = f"不可分等价类（{label}）" if label else "不可分等价类"
    return (f"{head}：{prof['等价类数']} 个等价类 / {prof['叶数']} 个叶"
            f"｜最大叶容量 {prof['最大叶容量']} = 最大等价类 {prof['最大等价类']}"
            f"（分辨率下界）"
            f"｜判空点 {prof['判空点']} 个，其中**域内有差异（可搬）** "
            f"{prof['可搬的判空点']} 个")


# --- `B17` 的**已知答案对照**（合成图，三态各一例） ------------------------------
#
# ⚠️ 与 `§A1`–`§A7` 的合成对照同一条纪律：**不碰内核、不碰语料**。
#    这里要对照的是「这条判据会不会红」，不是「真语料上是什么数」——
#    后者由 `run_tests.test_equivalence` 读真语料跑。**两件事分开，各自说清。**
#
# ⚠️ **为什么非有不可**：随仓库那份 36 项语料是 **DAG** ⇒ `reach` 的等价类（SCC）
#    **全是单点** ⇒ 这条判据在那里报「**跳过**」。而「跳过」与「这条判据根本不会红」
#    在汇总里长得**一模一样**（`跳过 ≠ 通过` 的反面）。
#    ⇒ 用一张**有环**的合成小图把「非退化 ⇒ 判得了，而且该红时红」当场演一遍。
#
# 手推（图 `a ⇄ b`，`c` 孤立；`which="reach"`）：
#
#     等价类（SCC）   {a, b} ｜ {c}     ⇒ 最大 2 > 1 ⇒ **非退化**，判得了
#
#     过·叶排对      叶₁ = {a, b} ｜ 叶₂ = {c}
#                    (a) 每个叶只含一个等价类 ✓                    ⇒ **过**
#     红·叶排错      叶₁ = {a} ｜ 叶₂ = {b, c}
#                    (a) 叶₂ 里混了两个类（{a,b} 与 {c}）⇒ **红**
#                    （`which="reach"` 时 (b) 不判 —— 那是有意收窄的，见模块
#                      docstring 末；所以这里**只**靠 (a) 就能红）
#     跳过·全是单点  等价类 {a} {b} {c} ⇒ 最大 1 < 2 ⇒ 两条断言恒真 ⇒ **跳过**
#                    （= 随仓库那份语料上的形态 —— 对照自己也要认得它）


class _StubDir:
    """只提供 `B17` 真正用到的两样东西：`did` 与成员集。**不冒充内核。**"""

    def __init__(self, did: str, members: Iterable[str]) -> None:
        self.did = did
        self._members = frozenset(members)


class _StubKernel:
    """`B17` 合成对照的最小替身 —— 只提供它调的那三个访问器。

    ⚠️ 用真内核要建一棵树、跑一遍语料，而这里要对照的是「判据会不会红」。
       与 `§A6` 的最小替身同一条纪律：**最便宜的那一档**。
    """

    def __init__(self, root: _StubDir, kids: dict[str, list[_StubDir]]) -> None:
        self.root = root
        self._kids = kids
        self._all = [root] + [d for v in kids.values() for d in v]

    def all_directions(self) -> list[_StubDir]:
        return list(self._all)

    def children_of(self, d: _StubDir) -> list[_StubDir]:
        return list(self._kids.get(d.did, ()))

    def members_of(self, d: _StubDir) -> frozenset[str]:
        return d._members


def _b17_stub(kind: str) -> tuple[_StubKernel, dict[str, frozenset[str]]]:
    """三种合成形态：`ok`（叶排对）/ `broken`（叶排错）/ `degenerate`（类全是单点）。"""
    root = _StubDir("R", ("a", "b", "c"))
    if kind == "degenerate":
        cls = {i: frozenset({i}) for i in ("a", "b", "c")}
        kids = {"R": [_StubDir("L1", ("a",)), _StubDir("L2", ("b",)),
                      _StubDir("L3", ("c",))]}
    elif kind == "broken":
        cls = {"a": frozenset({"a", "b"}), "b": frozenset({"a", "b"}),
               "c": frozenset({"c"})}
        kids = {"R": [_StubDir("L1", ("a",)), _StubDir("L2", ("b", "c"))]}
    else:
        cls = {"a": frozenset({"a", "b"}), "b": frozenset({"a", "b"}),
               "c": frozenset({"c"})}
        kids = {"R": [_StubDir("L1", ("a", "b")), _StubDir("L2", ("c",))]}
    return _StubKernel(root, kids), cls


#: `(标签, 形态, 手推的答案)` —— `want` 是**手推**出来的，不是跑出来抄的。
_B17_CASES: list[tuple[str, str, Tri]] = [
    ("过·叶排对（等价类非单点）", "ok", Tri.YES),
    ("红·叶排错（一个叶里混了两个等价类）", "broken", Tri.NO),
    ("跳过·等价类全是单点（= 随仓库那份语料上的形态）", "degenerate", Tri.UNEXPANDED),
]


def b17_known_answer() -> list[str]:
    """`B17` 的合成对照 —— 三态各一例。返回**失败清单**（空 = 全对）。

    ⚠️ 三态**都**要对照：只验「该红时红」不够 —— 那样一条**永远红**的判据也会通过。
       要的是「**该红时红、该绿时绿、该跳过时跳过**」。
    ⚠️ 而且**期望值必须两两不同**：三例都落在同一态，这个对照就什么都没证明
       （「空转与通过长得一模一样」在**对照表**上的形态）⇒ 这里当场自检。
    """
    fails: list[str] = []
    seen: list[Tri] = []
    for label, kind, want in _B17_CASES:
        kernel, cls = _b17_stub(kind)
        rep = Report(plugin="(合成)")
        b17_leaf_is_equivalence_class(kernel, cls, rep, "reach")
        got = _result(rep, "B17")
        seen.append(got)
        if got is not want:
            fails.append(f"{label}：期望 {want}，实测 {got}")
    if len(set(seen)) != len(seen):
        fails.append(f"对照表退化：三例落在同一态 {seen} —— 这个对照证明不了什么")
    return fails
