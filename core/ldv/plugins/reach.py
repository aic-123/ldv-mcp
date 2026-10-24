"""方向 B —— **锚点可达**（C0 §2）。

    方向 payload = 一个**锚点集合** `S`（项 id 的集合）
    项 x 属于方向 d  ⟺ 存在路径 `x →* s`（`s ∈ S`），含 `x ∈ S` 自身
    合并 = **并**（锚点变多 ⇒ 方向变粗）
    代价 = **距离**（x 到最近锚点走几步）
    命中 = 可达性闭包，**需要遍历整张图**

---

## 与方向 A 的关键差异（这是 C0 那张表的落地）

| | A 键集包含 | B 锚点可达 |
|---|---|---|
| 命中要看什么 | 节点**自己**的字段 | 节点在图里的**位置** |
| 合并 | 交（收缩） | **并**（扩张） |
| 代价 | 计数 | **距离** |
| 「未知」从哪来 | — | **拒绝遍历**（性能）／分辨率不够 |

**世界在哪里**：`§I1` 的签名是 `命中(方向, 查询)` —— **没有世界参数**。
所以 B 只能把**图闭包进插件实例**。这不是绕路，是接口的必然结果：
GiST 的键是自包含的，本设计的方向允许不自包含 ⇒ 插件必须自带世界。

---

## 一条必须守住的性质：`归属 ⊆ 可达`

`命中` 说「否」的健全性依赖 `members(d) ⊆ reachable(payload(d))`：

    若 `reachable(S) ∩ q.ideal = ∅`，则 `members(d) ∩ q.ideal = ∅` ⇒ 「否」成立

而这个包含关系由**两条**保证，缺一不可：

1. `劈开` 给每一组的 payload = 该组各项**签名之并**（签名 = 能到达的汇点集合）
2. `代价` = 到最近锚点的距离 ⇒ 距离 0 的项归自己那组

⇒ 组内每一项到自己的 payload 距离都是 0 ⇒ 必在 `reachable` 里。
**若这两条不一致，`B1`（假阴）就会红。**

---

## 缓存：键是 `payload`，值是**整条 BFS 输出**

这个插件每次 `代价` / `命中` / `劈开` 都要跑一遍图遍历。全量语料上
`b3` 一块要调 `call_penalty` **30,521,484** 次（7813 个方向 × 3907 项）——
**它一块就超过 71 分钟**。修法不是「预计算」，是**把键选对**：

    键 `(item, payload)`   →  30.5M 个键  ⇒  复用 ≈ 1×   ⇒  缓存无用
    键 `payload`           →  ≤ 7813 个键 ⇒  复用 ≈ 3907×  ⇒  缓存成立

⇒ **同一个键必须能回答「这个 payload 下所有项的代价」**，所以缓存的**值**
不是标量，是**这一遍遍历的全部结果**（`{项: 距离}`）。
键与值的粒度是同一件事的两面 —— 只换键不换值，缓存仍然无用。

**为什么 1 格就够**：`b3` 的访问顺序是 `for d in layer: for i in items`
（`checks/contract.py`）—— 同一个 payload **连续用满 n 次**。
这里留 `CACHE_MAXSIZE` 格只是为了别的调用顺序不至于来回打穿，代价 < 3 MB。

⚠️ **成立的前提是「图在 `__init__` 之后只读」**（Acar / Blelloch / Harper 2002 §2
把它点名为**关键要求**：「值算没算都一样」由**数据持久**保证）。
这条**必须被检查，不许靠读代码断言** —— 图一旦可变，缓存就是**假绿**
（stale 值 = 「空转与通过长得一模一样」）。见 `test_reach_cache_premise`。

⚠️ 缓存**不改变数值语义**：`test_reach_cache_transparency` 三臂对照钉住这一条。
"""

from __future__ import annotations

from collections import OrderedDict, deque
from typing import Any, Iterable, Sequence

from ..core.direction import Direction
from ..core.interfaces import UsageSignal
from ..core.tri import Tri

EMPTY: frozenset[str] = frozenset()


class ReachPlugin:
    """§I1–§I7 的七个方法。"""

    name = "reach"

    #: 缓存最多留几个 payload 的遍历输出。**1 格就能覆盖 `b3` 的访问顺序**
    #: （同一 payload 连续用满 n 次），留几格只是为了让别的调用顺序不打穿。
    CACHE_MAXSIZE = 8

    def __init__(self, edges: dict[str, Iterable[str]],
                 traverse_budget: int = 10_000, cache: bool = True) -> None:
        self.edges: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in edges.items()}
        self._rev: dict[str, set[str]] = {k: set() for k in self.edges}
        for src, dsts in self.edges.items():
            for dst in dsts:
                self._rev.setdefault(dst, set()).add(src)
        #: 汇点（无出边的项）—— **图的函数**，原来每次 `signature` 都重算一遍（O(n)）。
        self._sinks: frozenset[str] = frozenset(n for n in self.edges if not self.edges[n])
        #: 走不到锚点时给的有限值。⚠️ **必须存成属性**：它在热路径里当
        #: `dict.get(k, self._far)` 的默认值用，而**默认值是每次求值的** ——
        #: 写成 `@property` 就等于给 30.5M 次调用各加一个函数调用。
        self._far: float = float(len(self.edges) + 1)
        self.traverse_budget = traverse_budget

        # --- 缓存：键 = payload，值 = 整条 BFS 输出（见模块 docstring）------
        self.cache = bool(cache)
        self._dist_cache: OrderedDict[frozenset[str], dict[str, float]] = OrderedDict()
        self._reach_cache: OrderedDict[frozenset[str], frozenset[str]] = OrderedDict()
        self._sig_cache: dict[str, frozenset[str]] = {}
        #: 读数（**度量**，不进退出码）—— 用来看缓存到底有没有在命中。
        self.n_distance = 0
        self.n_distance_hit = 0
        self.n_reachable = 0
        self.n_reachable_hit = 0
        self.n_signature = 0
        self.n_signature_hit = 0

    def cache_stats(self) -> dict[str, int]:
        """缓存读数 —— 供测试与测量脚本读，**不参与任何判据**。"""
        return {
            "distance 调用": self.n_distance,
            "distance 命中": self.n_distance_hit,
            "reachable 调用": self.n_reachable,
            "reachable 命中": self.n_reachable_hit,
            "signature 调用": self.n_signature,
            "signature 命中": self.n_signature_hit,
            "不同 payload（距离）": len(self._dist_cache),
            "不同 payload（可达）": len(self._reach_cache),
        }

    # --- 图 ---------------------------------------------------------------

    def _distances(self, anchors: frozenset[str]) -> dict[str, float]:
        """**一遍**多源反向 BFS ⇒ **所有项**到最近锚点的距离（整条 BFS 输出）。

        值与 `distance(item, anchors)` 逐项相同：原式是「从 item 正向 BFS 找锚点」，
        这里是从**全部锚点**在**反向图**上同时出发 —— 反向图里
        「锚点走几步到 item」就是正向图里「item 走几步到锚点」，多源只是把
        n 次单源并成 1 次。**键与值的粒度必须同时换**：键换成 payload 之后，
        值若是标量，就会被同一 payload 的后一项覆盖。

        返回的字典**只含可达项**；调用方对缺失键取 `|V|+1`（与原来一致）。
        """
        if self.cache:
            got = self._dist_cache.get(anchors)
            if got is not None:
                self.n_distance_hit += 1
                self._dist_cache.move_to_end(anchors)
                return got
        out: dict[str, float] = dict.fromkeys(anchors, 0.0)
        q = deque(out)
        while q:
            cur = q.popleft()
            nxt_d = out[cur] + 1.0
            for prev in self._rev.get(cur, ()):
                if prev not in out:
                    out[prev] = nxt_d
                    q.append(prev)
        if self.cache:
            self._dist_cache[anchors] = out
            if len(self._dist_cache) > self.CACHE_MAXSIZE:
                self._dist_cache.popitem(last=False)
        return out

    def reachable(self, anchors: Iterable[str]) -> frozenset[str]:
        """能走到任一锚点的项（含锚点自身）—— **反向** BFS。"""
        key = frozenset(anchors)
        self.n_reachable += 1
        if self.cache:
            got = self._reach_cache.get(key)
            if got is not None:
                self.n_reachable_hit += 1
                self._reach_cache.move_to_end(key)
                return got
        seen = set(key)
        q = deque(seen)
        while q:
            cur = q.popleft()
            for prev in self._rev.get(cur, ()):
                if prev not in seen:
                    seen.add(prev)
                    q.append(prev)
        out = frozenset(seen)
        if self.cache:
            self._reach_cache[key] = out
            if len(self._reach_cache) > self.CACHE_MAXSIZE:
                self._reach_cache.popitem(last=False)
        return out

    def distance(self, item_id: str, anchors: frozenset[str]) -> float:
        """到最近锚点的步数。走不到返回 `|V|+1`（有限值 —— `§I3` 要求可比）。"""
        self.n_distance += 1
        return self._distances(frozenset(anchors)).get(item_id, self._far)

    def signature(self, item_id: str) -> frozenset[str]:
        """该项能到达的**汇点**（无出边的项）之集合。走不到汇点 ⇒ 退化成自己。"""
        self.n_signature += 1
        if self.cache:
            got = self._sig_cache.get(item_id)
            if got is not None:
                self.n_signature_hit += 1
                return got
        sinks = self._sinks
        if item_id in sinks:
            out = frozenset({item_id})
        else:
            seen = {item_id}
            q = deque([item_id])
            found: set[str] = set()
            while q:
                cur = q.popleft()
                if cur in sinks:
                    found.add(cur)
                    continue
                for nxt in self.edges.get(cur, ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        q.append(nxt)
            out = frozenset(found) or frozenset({item_id})
        if self.cache:
            self._sig_cache[item_id] = out
        return out

    # --- §I2 合并 ---------------------------------------------------------

    def merge(self, ds: Sequence[Direction]) -> frozenset[str]:
        """取并。**必须覆盖**（§I2）。

        覆盖的证明：`reachable(⋃S_i) ⊇ reachable(S_i)`（反向 BFS 从更大的起点集出发
        只会看到更多）⇒ 原属 d_i 的项仍可达新锚点 ⇒ 覆盖成立。
        """
        out: set[str] = set()
        for d in ds:
            out |= set(d.payload)
        return frozenset(out)

    # --- §I3 代价 ---------------------------------------------------------

    def penalty(self, d: Direction, item: Any) -> float:
        """**距离** —— A 的代价是「计数」，这里没有计数的位置。

        §I3 只要求「同一层内两两可比」。距离满足：全序、有限、同一量纲。
        **内核不参与折算** —— 所以「走几步算贵」由这里说了算。

        ⚠️ 这是全量上被调 **30.5M 次**的那条路径（`b3` 一块）。
        它现在走 `_distances(payload)` —— 键是 `payload`、值是**整条 BFS 输出**，
        于是同一个 payload 下的 n 项**共用一遍遍历**（见模块 docstring）。
        返回值与改造前**逐项相同**（`test_reach_cache_transparency`）。
        """
        return self.distance(str(item["id"]), frozenset(d.payload))

    # --- §I4 劈开 ---------------------------------------------------------

    def split(self, parent: Direction, items: Sequence[Any]) -> tuple[frozenset[str], frozenset[str]] | None:
        """按**图上的位置**二分：先按能到达的汇点，再按到汇点的距离，最后按 id。

        payload = **该组各项自己**（每项都是自己的锚点）。

        **为什么这里也是两个**：这个方向的 payload 是**锚点集合**，
        而「按图上的位置排序后取中点」的语义就是二分 —— k 叉在这里没有对应的语义
        （切三段并不比切两段更细）。二分是**自然形状**。
        （反向判据：`§I4` 改成 k 叉之后，B 的数**一个都没动**。）

        ## ⚠️ 父方向在这里**帮不上忙**（实测，不是推的）

        `覆盖(子) ⊆ 覆盖(父)` 要求 `reachable(子.payload) ⊆ reachable(父.payload)`，
        即子 payload 得是父 payload 的子集。但

            子 payload 必须**覆盖得住自己的成员**（`B16` / `B1` 都靠它）
            而维护之后，成员是**后来才长进来的** —— 父的 payload 在劈开时就冻结了
            ⇒ 新成员的 id **不在**父 payload 里 ⇒ 子 payload 不得不**超出**父 payload

        ⇒ 想强行取交（`left ∩ parent.payload`）会**丢掉新成员** ⇒ 假阴（`B1` 红）。
          这是「**成员可以晚于 payload 到达**」的后果，不是这里少写了一句。
          ⇒ 所以 B 只接受这个参数，**不用**它 —— 覆盖嵌套在维护路径上本来就保不住。

        ⚠️ 为什么锚点取「本组各项」而不是「本组能到达的汇点」：
           后者在只有 2 个汇点的图上**只能切一层** —— 切完两边签名相同，
           下层再也分不开（实测：7 个方向、只展开 1 个）。
           取本组各项当锚点，则组内每一项到本组距离恒为 0，
           方向可以一路细到单项，**而「可达」的语义一点没丢** ——
           方向仍然表示「能到达这组项的东西」，只是锚点更细。

        ⇒ 这条改动的代价是**精度**（`reachable` 比 `members` 大得多，
          假阳变多），收益是**层能真的分下去**。
          按 §K8，这个交换是允许的 —— 假阳只赔性能，假阴才赔正确性。

        **分不开**时返回 `None`（**声明**），不返回两个相同 payload。
        """
        del parent                      # 见上：维护之后父 payload 已经不够用了
        ids = sorted(str(it["id"]) for it in items)
        if len(ids) < 2:
            return None
        sinks = self._sinks           # 图的函数 —— 在 `__init__` 里算过一次
        ordered = sorted(
            ids,
            key=lambda i: (sorted(self.signature(i)),
                           self.distance(i, sinks) if sinks else 0.0, i),
        )
        mid = len(ordered) // 2
        left, right = ordered[:mid], ordered[mid:]
        if not left or not right:
            return None
        return (frozenset(left), frozenset(right))

    # --- §I1 命中 ---------------------------------------------------------

    def hit(self, d: Direction, query: Any) -> Tri:
        """**偏函数** —— 有两种情况会返回「未展开」。

        1. **分辨率不够**：`d.rank < q.min_rank` ⇒ 方向太粗，回答不了那个分辨率
        2. **拒绝遍历**：锚点集超过预算 ⇒ 宁可说「不知道」，也不说「没有」

        ⚠️ 两条都往「未展开」偏，**不往「否」偏** —— 「否」是唯一会漏结果的分支（§K8）。
        """
        min_rank = int(getattr(query, "min_rank", 1))
        if d.rank < min_rank:
            return Tri.UNEXPANDED
        anchors = frozenset(d.payload)
        if len(anchors) > self.traverse_budget:
            return Tri.UNEXPANDED
        ideal = frozenset(getattr(query, "ideal", frozenset()))
        reach = self.reachable(anchors)
        return Tri.YES if (reach & ideal) else Tri.NO

    # --- §I5 编码 / 解码 --------------------------------------------------

    def encode(self, d: Direction) -> bytes:
        return ("A:" + ",".join(sorted(d.payload))).encode("utf-8")

    def decode(self, blob: bytes) -> frozenset[str]:
        text = blob.decode("utf-8")
        return frozenset(x for x in text[2:].split(",") if x)

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
                "anchors": sorted(d.payload), "anchors_n": len(d.payload)}
