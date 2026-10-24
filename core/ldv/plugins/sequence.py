"""方向 C —— **序列前缀**（C0 之外的第三个方向，**顺序敏感**）。

    项 x 的序列   `seq(x)` = 从 x 出发的确定性链（沿 relations，每步取 id 最小的未访问后继）
    方向 payload  = 一组**前缀**（一个「前缀语言」，`frozenset[tuple]`）
    项 x 属于 d   ⟺ **存在** p ∈ payload 使 p 是 `seq(x)` 的前缀
    合并          = **并**（前缀集合取并）
    代价          = **前缀距离**（要砍掉多少符号才能让某个前缀对上）
    命中          = 前缀匹配

---

## 为什么要有第三个方向：它是**顺序敏感**的

A（键集）和 B（锚点集）**都是无序的** —— 它们的 payload 是集合，
把元素顺序打乱，方向一点不变。

C 不是：`('概念','案例')` 和 `('案例','概念')` 是**两个不同的前缀**。
⇒ 它压的是前两个方向压不到的位置。

## 它压出来的三个问题（见 `C5b-第三方向复核.md`）

**① §I4 的「两个」是 B-tree 的遗产，对有序方向是**人为**的。**
   有序方向的自然细化是 **trie**：一个节点按「下一个符号」分出 **k** 个子方向。
   §I4 硬编码「→ 两个方向」，所以这里只能把 k 个符号**二分** ——
   **能装下，但要多花层数**。这是「接口能容纳但形状不自然」的实例。
   代价量得出来：**判空 8 次**（`§K2 判空`），代价落在**叶容量**上 ——
   最大 10 / 平均 3.27；而 reach 是判空 **0** 次、叶容量 1。
   同一套 §K2，形状越自然，两个数都越小。

**② §I2 的 `合并({细}) = 粗` 这条**定义**是错的 —— 但**不是 C 压出来的**。**
   实测：**合并(单元素) 是恒等**（A 25/25、B 71/71、C 21/21 全部满足），
   所以「`细 ⊑ 粗` ⟺ `合并({细}) = 粗`」对**任何**严格细化都不成立 —— 三个方向一样。
   ⇒ 这是**文档写错了**，不是接口被压垮了（`C5b` §2.2）。
   C 只是让这个错**变得显眼**：它有 `('概念',) ⊐ ('概念','案例')` 这种
   「加一个符号」的细化，而「细化由加符号产生」在 C 上一眼可见。

**③ §I3 容纳第三种代价语义。**
   A 是计数、B 是图距离、C 是**前缀距离**（序列对齐）。
   三者量纲不同、算法不同，而 §I3 只要求「同层内两两可比」——
   实测 1296 个代价值全部可比，**一个数没动**。

## 它没压出来的东西（同样是结论）

`§I1 命中` 的三态、`§I5 编码/解码`、`§I6 信号`、`§I7 记录` 在 C 上**零摩擦** ——
`hit` 在这里甚至是**全函数式**的：它把 payload 覆盖的项集合**算出来**，
所以假阳率实测 **0.0%**（A 50.1%、B 29.0%）。
三个方向在同一个位置上给出三种完全不同的精度/代价组合，接口都没拦。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from ..core.direction import Direction
from ..core.interfaces import UsageSignal
from ..core.tri import Tri

Pattern = tuple[str, ...]
Lang = frozenset[Pattern]

#: 「序列到此为止」的哨兵符号。
#: 没有它，序列**恰好等于**某个前缀的项会在分裂时**两边都不落**（覆盖就断了）。
#: 哨兵不是数据里的符号，是**语言内部的记号** —— 所以它不出现在 `seq()` 里。
END = "\x00end"


def _lcp_len(a: Sequence[str], b: Sequence[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class SequencePlugin:
    """§I1–§I7 的七个方法。"""

    name = "sequence"

    def __init__(self, seqs: dict[str, Iterable[str]]) -> None:
        self.seqs: dict[str, tuple[str, ...]] = {k: tuple(v) for k, v in seqs.items()}

    # --- 内部 -------------------------------------------------------------

    def covers(self, payload: Lang, item_id: str) -> bool:
        """项是否落在方向里 —— **只在检查与命中里用，内核不用它决定归属**。

        序列按 `s + (END,)` 参与前缀匹配 —— 这样 `p = (t, END)` 能**精确**表示
        「序列就是 `(t,)` 这一项」，否则「序列恰好等于某个前缀」的项永远匹配不上。
        """
        ext = self.seqs.get(item_id, ()) + (END,)
        return any(_lcp_len(p, ext) == len(p) for p in payload)

    def covered_set(self, payload: Lang) -> frozenset[str]:
        return frozenset(i for i in self.seqs if self.covers(payload, i))

    def _lcp_of_group(self, ids: Sequence[str]) -> Pattern:
        """组内所有序列的**最长公共前缀** —— 这一步的起点 `p*`。

        ⚠️ 不写死成 `()`：写死的话第二层按首符号分完，第三层所有项的序列
           都以同一个符号开头 ⇒ 只有一个桶 ⇒ 再也分不开，树只有两层。
           用 LCP 当起点，`p*` 随层深**自然增长**。
        """
        if not ids:
            return ()
        seqs = [self.seqs.get(i, ()) for i in ids]
        first = seqs[0]
        n = len(first)
        for s in seqs[1:]:
            n = min(n, _lcp_len(first, s))
        return first[:n]

    # --- §I2 合并 ---------------------------------------------------------

    def merge(self, ds: Sequence[Direction]) -> Lang:
        """取**并**。覆盖是平凡的：并集 ⊇ 每个子集 ⇒ 原来满足的还满足（§I2）。"""
        out: set[Pattern] = set()
        for d in ds:
            out |= set(d.payload)
        return frozenset(out) if out else frozenset({()})

    # --- §I3 代价 ---------------------------------------------------------

    def penalty(self, d: Direction, item: Any) -> float:
        """**前缀距离**：要砍掉多少符号，某个前缀才能对上这项的序列。

            代价 = min over p ∈ payload  (len(p) - 最长公共前缀长度)

        返回 0 ⟺ **项已满足该方向**（某个 p 就是它的前缀）。

        ⚠️「0 ⟺ 满足」是必须守住的 —— 内核靠代价分配归属，
           而 `命中` 说「否」的健全性依赖「归属 ⊆ 满足」（否则 `B1` 红）。
        """
        s = self.seqs.get(str(item["id"]), ()) + (END,)
        best: float | None = None
        for p in d.payload:
            cost = float(len(p) - _lcp_len(p, s))
            if cost == 0.0:
                return 0.0
            best = cost if best is None else min(best, cost)
        return best if best is not None else float(len(s) + 1)

    # --- §I4 劈开 ---------------------------------------------------------

    def split(self, parent: Direction, items: Sequence[Any]) -> tuple[Lang, ...] | None:
        """把**本组的最长公共前缀** `p*` 按「下一个符号」分 —— **一个符号一个子方向**。

        这是 trie 的**自然形状**（k 叉）。`§I4` 现在照插件给的个数建子方向，
        所以这里不再把 k 个符号**压成两堆**。

        压成两堆的代价是具体的：中间那一层的 payload 是「两个符号的并」
        （`{p*+('a',), p*+('c',)}`），**它不是任何单个前缀** ⇒ 那一层的覆盖是两个锥的并
        ⇒ 假阳变多，而且还要多花一层。

        ## 父方向在这里**自动**被继承（不需要写代码，但值得说清为什么）

        `覆盖(子) ⊆ 覆盖(父)` 要求 `p*` 比父的前缀**更长**。它成立靠一条链：

            成员 ⊆ 覆盖(父)          （`B16`）
            ⇒ 每个成员的序列都以父的某个前缀开头
            ⇒ 它们的**最长公共前缀** `p*` 至少和那个前缀一样长
            ⇒ `p* + (t,)` 是父前缀的**真延长** ⇒ 它的锥 ⊆ 父的锥

        维护路径上也不破：新成员是被 `§I3 代价 = 0` 分进来的 ⇒ 它也满足父的前缀
        ⇒ 它不会把 `p*` 拉短。

        ⇒ 所以 C 接受 `parent` 但**不读它** —— 包含关系由 `p*` 的定义**保证**，
          写成代码反而是多余的一句。（反向判据：`§I4` 改成 k 叉之后，
          C 的**叶容量**与假阳率一个数没动。）

        **覆盖**（`归属 ⊆ 满足`，`B1` 依赖它）：
            组内每一项都以 `p*` 开头（LCP 的定义）
            ⇒ 它的下一个符号（或 END）必落在某个桶里
            ⇒ 该桶的 payload 是 `p* + (tok,)` ⇒ 它是这项序列的前缀
            ⇒ 代价 0 ⇒ 它**只会**被分到那个桶（其余桶的代价 ≥ 1）
        ⇒ 每个桶**非空**（桶是按键的实际成员建的），§K2 的「一侧为空」不可能在这里发生。

        **分不开**：只有一个桶 ⇒ 返回 `None`（**声明**），不返回「两个相同的 payload」。
        """
        del parent                      # 见上：包含关系由 `p*` 的定义保证
        ids = sorted(str(it["id"]) for it in items)
        p_star = self._lcp_of_group(ids)

        buckets: dict[str, list[str]] = {}
        for i in ids:
            s = self.seqs.get(i, ())
            tok = s[len(p_star)] if len(s) > len(p_star) else END
            buckets.setdefault(tok, []).append(i)
        if len(buckets) < 2:
            # 组内所有项的「下一个符号」相同 ⇒ 这一步分不开。交给 §K2 判「这一层不建」。
            return None

        # 按符号**排序**取序 —— 确定性（§8.2：只要有一个环节依赖遍历顺序就不可复现）
        # ⚠️ 必须写成**集合推导**：`frozenset(p_star + (t,))` 会把元组**拆开**当元素
        #    （`frozenset(('a',))` = `{'a'}`，不是 `{('a',)}`）——
        #    payload 里装的就成了**字符串**，`len(p)` 变成字符串长度，代价全乱。
        return tuple(frozenset({p_star + (t,)}) for t in sorted(buckets))

    # --- §I1 命中 ---------------------------------------------------------

    def hit(self, d: Direction, query: Any) -> Tri:
        """三态。**偏函数**：分辨率不够就说「未展开」。

        「否」是**算出来的**：把 payload 覆盖的项集合求出来，与 ground truth 求交 ——
        空才说否。所以没有假阴（`members ⊆ covered_set`，见模块开头）。
        """
        if d.rank < int(getattr(query, "min_rank", 1)):
            return Tri.UNEXPANDED
        ideal = frozenset(getattr(query, "ideal", frozenset()))
        return Tri.YES if (self.covered_set(frozenset(d.payload)) & ideal) else Tri.NO

    # --- §I5 编码 / 解码 --------------------------------------------------

    def encode(self, d: Direction) -> bytes:
        # 模式之间用 `|`，符号之间用 `,`；**空模式编码成空串**（不是 `("",)`）
        pats = sorted(",".join(p) for p in d.payload)
        return ("P:" + "|".join(pats)).encode("utf-8")

    def decode(self, blob: bytes) -> Lang:
        text = blob.decode("utf-8")
        body = text[2:] if text.startswith("P:") else text
        if not body:
            return frozenset({()})
        out: set[Pattern] = set()
        for chunk in body.split("|"):
            out.add(tuple(chunk.split(",")) if chunk else ())
        return frozenset(out)

    # --- §I6 信号 ---------------------------------------------------------

    def signal(self, d: Direction, interaction: Any) -> UsageSignal | None:
        outcome = interaction.get("outcome")
        if outcome is None:
            return None
        return UsageSignal(did=d.did, outcome=float(outcome),
                           note=str(interaction.get("note", "")))

    # --- §I7 记录 ---------------------------------------------------------

    def record(self, d: Direction) -> dict:
        return {"kind": self.name, "did": d.did, "rank": d.rank,
                "patterns": [".".join(p) for p in sorted(d.payload)],
                "patterns_n": len(d.payload)}
