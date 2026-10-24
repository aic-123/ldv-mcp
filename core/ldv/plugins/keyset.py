"""方向 A —— **键集包含**（C0 §2）。

    方向 payload = (require, forbid) 两个键集合
    项 x 属于方向 d  ⟺  `keys(x) ⊇ require` 且 `keys(x) ∩ forbid = ∅`
    合并 = 分量各自**交**（要求变少 ⇒ 方向变粗）
    代价 = **计数**（把 x 放进来要丢掉几条要求）
    命中 = 结构包含检查，`O(1)`，**只看节点自己**，不需要任何世界

---

## 为什么 `命中` 在这里是**全函数**（永不返回「未展开」）

因为「键要求」是一个**健全的界**：

    否  ⟸  `require ∩ q.forbid ≠ ∅`   —— 方向里每个项都带 q 禁止的键 ⇒ 不可能命中
    否  ⟸  `forbid ∩ q.require ≠ ∅`   —— 方向里每个项都缺 q 要求的键 ⇒ 不可能命中
    是  ⟸  其余一切情况

两个「否」都是**证明**出来的，所以**没有假阴**（§K8）；其余一律「是」，
所以**允许假阳** —— 代价只是性能。

⚠️ 这条「其余一律是」不是偷懒，是 §K8 的**正确形状**：
   把不确定的判成「否」才是错的（假阴），判成「是」只是多走几步。

## 反过来，方向 B 的 `命中` 是**偏函数**（会返回「未展开」）

B 要遍历整张图才能回答，遇到大方向它会**拒绝遍历**。所以
「未展开」这个第三值由 **B** 来行使 —— 这是 C0 里两个方向的关键差异之一。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from ..core.direction import Direction
from ..core.interfaces import UsageSignal
from ..core.tri import Tri

Payload = tuple[frozenset[str], frozenset[str]]   # (require, forbid)
EMPTY: Payload = (frozenset(), frozenset())


class KeysetPlugin:
    """§I1–§I7 的七个方法。"""

    name = "keyset"

    # --- §I2 合并 ---------------------------------------------------------

    def merge(self, ds: Sequence[Direction]) -> Payload:
        """分量各自取交。**必须覆盖**所有输入（§I2）。

        覆盖的证明：x 满足 d_i ⟺ `keys(x) ⊇ R_i` 且 `keys(x) ∩ F_i = ∅`。
        合并后要求 `keys(x) ⊇ ∩R_i`（因为 ⊇ 每个 R_i ⊇ ∩R_i ✓）
        且 `keys(x) ∩ ∩F_i = ∅`（因为 ⊆ 每个 F_i，各自都空 ✓）。
        ⇒ x 仍满足合并结果 ⇒ 覆盖成立。**所以 A 的合并在结构上就不会丢。**
        """
        if not ds:
            return EMPTY
        req = frozenset.intersection(*[frozenset(d.payload[0]) for d in ds])
        forb = frozenset.intersection(*[frozenset(d.payload[1]) for d in ds])
        return (req, forb)

    # --- §I3 代价 ---------------------------------------------------------

    def penalty(self, d: Direction, item: Any) -> float:
        """**计数**：要把 item 收进来，这个方向得丢掉几条要求。

            丢要求  = `require - keys(item)`   —— 方向要求了、项却没有的键
            犯禁    = `keys(item) & forbid`    —— 项带了方向禁止的键

        返回 0 ⟺ 项**已经满足**该方向。

        ⚠️「返回 0 ⟺ 满足」是这里最要紧的性质 —— 内核靠 `代价` 分配归属，
           而 `命中` 的健全性依赖「归属 ⊆ 满足」。两者必须**同一套判据**，
           否则 `命中` 会说「否」而方向里真有命中（假阴，`B1` 会红）。
        """
        req, forb = d.payload
        keys = frozenset(item["keys"])
        return float(len(req - keys) + len(keys & forb))

    # --- §I4 劈开 ---------------------------------------------------------

    def split(self, parent: Direction, items: Sequence[Any]) -> tuple[Payload, Payload] | None:
        """按一个**最能分开**的键二分：一半要求有它，一半要求没有它。

        选键标准：在 `items` 里出现率最接近 1/2 的键（信息量最大）。
        出现率是 0 或 1 的键**分不开任何东西**，排除。

        ## ★ 子方向只能从**父方向的 payload** 派生 —— 不许掺当前成员

        这一条比「继承 `forb`」强，它是**唯一**能保证

            `members(d) ⊆ 覆盖(d)`            （§1 的硬要求表第三行，`B16` 守）

        在**维护路径**上成立的办法。理由是一条链：

            一个项**什么时候**落进 d，不由 d 决定 —— 由**下降**决定
            （`§I3 代价` 取最小；取不到 0 也照落，落点就是那个代价最小的子方向）
            ⇒ 只要 `覆盖(父) ⊄ ∪覆盖(子)`，就**一定**有项落进「它不满足」的方向
            ⇒ `members(d) ⊄ 覆盖(d)` ⇒ `命中` 的「否」就**证不住** ⇒ 假阴（`§K8` 禁）

        ⇒ 所以子方向的 payload 必须盖住**父方向的整个覆盖**，而不是「当前这批成员」。

        ---

        ### 三个字段，逐个说

            req(子) ⊇ req(父)      ← **必须显式续**：`req(子) = req(父) ∪ {best}`
            forb(子) ⊇ forb(父)    ← **必须显式续**：一半续、另一半加 `best`
            当前成员的 `common`     ← **不许进来**

        **为什么 `common` 不许进来**（这是本轮修掉的那处）：

            `common` = 当前成员的键集之交，它是**对未来的声称** ——
            「落进这个方向的东西都带这些键」。而劈开**兑现不了**这个声称：
            后来的项是按代价分配落进来的，代价 > 0 也照落。

            掺进去的后果实测（36 项语料，keyset）：

                建法              破 `members ⊆ 覆盖` 的成员   假阴
                ──────────────────────────────────────────────────
                一次建完 36       0 / 159                     0
                先建 2 维护 34    123 / 227                   42 / 385

            ⇒ 而且它还会让**劈开自己切不动**：`common` 与 `p_req` 一起把子方向的 `req`
              抬得比父高，于是某个项在两个子方向上**代价打平**（都是 1），
              平手判给靠前那个 ⇒ 一侧为空 ⇒ `§K2` 判「切不动」⇒ 这一层不建。
              实测：先建 2 时出现 **3 次**「切不动」，方向数因此比全量少 2。

        **`forb` 不续**的后果（同样实测）：父说「不含键 f」，子方向却把 `forb` 清空了
        ⇒ 子方向重新收下**含 f 的项** ⇒ `覆盖(子) ⊄ 覆盖(父)`，而那些项在父那里
        本来已经可以被否掉 ⇒ **纯假阳**（性能损失，且会累积）。

        实测 `覆盖(子) ⊆ 覆盖(父)`（`cover_nesting_profile`，36 项语料，12 个已展开方向）：

            不续 `forb`   越界 14 次
            续上 `forb`   越界  0 次

        ---

        **为什么这里仍然是两个**（不跟 C 一起改成 k 叉）：这个方向的 payload 是
        「一个键要求有 / 一个键要求没有」，它的**语义分辨率就是二分的** ——
        多切几刀不会让这一层更细，只会多一层。二分在这里是**自然形状**，不是妥协。
        （实测的反向判据：`§I4` 改成 k 叉之后，A 的 `§K2 判空`、叶容量、
        **各层方向数**一个数都没动 —— 动到了就是改错了。）

        **分不开**时返回 `None` —— **声明**「这一层分不开」，不是返回两个相同 payload
        让内核去猜。判定因此可记账（§K2 / §I4）。
        """
        p_req, p_forb = parent.payload
        keysets = [frozenset(it["keys"]) for it in items]
        common = frozenset.intersection(*keysets) if keysets else frozenset()
        universe = frozenset().union(*keysets) if keysets else frozenset()
        # ⚠️ `common` **只用来挑判别键**（信息量最大的那个），**不进 payload**。
        #    父已禁止的键本来就不在 `universe` 里（成员 ⊆ 覆盖(父)），所以不必再减一次。
        candidates = sorted(universe - common)
        if not candidates:
            return None                             # 所有项键集相同 ⇒ 分不开
        n = len(items)
        # ★ **一次遍历算全部键的频次**（2026-10-18 优化，与旧式**逐字等价**）：
        #
        #     旧：min(candidates, key=lambda k: sum(1 for ks in keysets if k in ks))
        #         ⇒ O(|候选| × |成员|) —— 对每个键把全部成员各扫一遍
        #     新：Counter 一次 update 全部成员 ⇒ O(|成员| × avg_keys)
        #
        #     `counts[k]` 与 `sum(1 for ks in keysets if k in ks)` 是**同一个数**
        #     （每个 `ks` 是 frozenset，`update` 为其中每个键计一次）。
        #     ⇒ 选出的 `best` 逐字相同（判据 `test_split_cost` ㊒ 钉这条）。
        #     ⚠️ 并列时的次序也必须一样：键仍是 `candidates`（已 sorted），
        #        `min` 取第一个最小值 ⇒ 字典序最小的那个，与旧式同。
        counts: Counter[str] = Counter()
        for ks in keysets:
            counts.update(ks)
        best = min(candidates, key=lambda k: (abs(counts.get(k, 0) / n - 0.5), k))
        # ★ 两个字段都**只从父续**。这样 `覆盖(父) ⊆ 覆盖(子1) ∪ 覆盖(子2)` 是
        #   **结构上成立**的：x 满足父 ⇒ 要么有 `best`（落子 1，代价 0），
        #   要么没有（落子 2，代价 0）—— 两半合起来恰好是父的覆盖。
        return ((p_req | {best}, p_forb), (p_req, p_forb | {best}))

    # --- §I1 命中 ---------------------------------------------------------

    def hit(self, d: Direction, query: Any) -> Tri:
        """三态里的**三个**：两个证明出来的「否」+ 一个分辨率不够的「未展开」。"""
        if d.rank < int(getattr(query, "min_rank", 1)):
            # 方向太粗，回答不了这个分辨率 —— 说「不知道」，不说「没有」
            return Tri.UNEXPANDED
        req, forb = d.payload
        q_req = frozenset(getattr(query, "require", frozenset()))
        q_for = frozenset(getattr(query, "forbid", frozenset()))
        if (req & q_for) or (forb & q_req):
            return Tri.NO
        return Tri.YES

    # --- §I5 编码 / 解码 --------------------------------------------------

    def encode(self, d: Direction) -> bytes:
        req, forb = d.payload
        return ("R:" + ",".join(sorted(req)) + "|F:" + ",".join(sorted(forb))).encode("utf-8")

    def decode(self, blob: bytes) -> Payload:
        text = blob.decode("utf-8")
        head, _, tail = text.partition("|")
        req = frozenset(x for x in head[2:].split(",") if x)
        forb = frozenset(x for x in tail[2:].split(",") if x)
        return (req, forb)

    # --- §I6 信号 ---------------------------------------------------------

    def signal(self, d: Direction, interaction: Any) -> UsageSignal | None:
        """`outcome is None` ⇒ **没有观测到使用** ⇒ 返回 `None`。

        ⚠️ 返回 `None` 和返回 `outcome=0.0` 是**两件事**：
           前者是「没看见」，后者是「看见了、不好不坏」。§B6 会查这个区分。
        """
        outcome = interaction.get("outcome")
        if outcome is None:
            return None
        return UsageSignal(did=d.did, outcome=float(outcome),
                           note=str(interaction.get("note", "")))

    # --- §I7 记录 ---------------------------------------------------------

    def record(self, d: Direction) -> dict:
        req, forb = d.payload
        return {"kind": self.name, "did": d.did, "rank": d.rank,
                "require": sorted(req), "forbid": sorted(forb)}
