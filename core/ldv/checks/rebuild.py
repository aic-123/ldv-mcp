"""规范重建 —— `C8` §7 第 3 步的实测（**只报不判**）。

    python -m ldv.run_checks            # 读数随报告一起输出

---

## 要回答的问题

第 1 步量到「增量 ≡ 全量」**不成立** —— 知道未来会改变结构。
第 3 步问的是：**有没有办法让它成立，代价是什么。**

「规范重建」是唯一的路：把结构做成**项集的函数**（于是与到达顺序无关）。

## 两种做法，都试了

    (A) 规范不动点   每次插入后，从**当前项集**按**规范序**（id 升序）整树重建
    (B) 阈值重建     只在规模跨过阈值（2 的幂）时重建 —— Naor–Teague 的做法

## 实测结论（36 项语料，扫 k ∈ {2,6,12,18,24,30,34,35}）

    方向       做法         同构全量的 k   k 指纹数   重建次数  重建工作量  失效范围比
    ─────────────────────────────────────────────────────────────────────────────
    keyset     规范不动点      8 / 8         1        35       665       0.06
    keyset     阈值重建        1 / 8         3         5        62         —
    reach      规范不动点      8 / 8         1        35       665       0.29
    reach      阈值重建        0 / 8         4         5        62         —
    sequence   规范不动点      8 / 8         1        35       665       0.06
    sequence   阈值重建        1 / 8         3         5        62         —
    （基线「路径局部增量」的失效范围比 = 0.00）

⇒ **四条，逐条都是决策依据**：

**① 规范不动点确实买到了「增量 ≡ 全量」** —— 8/8 个 k 都与全量同构，
   而且 **k 指纹只有 1 种** ⇒ 结果**不再依赖初始批次大小**。这是买到了。

**② 代价是 O(n) 每次插入**：35 次重建 / 工作量 665 = `2+3+…+36`。
   阈值重建把它压到 5 次 / 62（= `2+4+8+16+32`，摊还 O(1)）—— 但见 ③。

**③ 阈值重建买不到**：同构全量 1/8（keyset、sequence）、**0/8**（reach）。
   它只让 **k < 阈值**的那几个 k **互相同构**（不再依赖初始批次），
   而差异发生在**两次重建之间** —— 阈值之后添的那些项仍然是增量的。
   ⇒ Naor–Teague 买到的是「摊还 O(1) 的重建」，**不是**「结构 = 项集的函数」。

**④ `B8` 红** —— 规范重建每次都**全量重算**，正是 §K3 要挡的形态。
   但**形状上的**失效范围其实**不大**（0.06 / 0.29，基线 0.00）：
   规范结构对插入**近似稳定**。
   ⇒ 所以重建在**结果**上是安全的，代价**全在重算**上。

## ⇒ 决定：**不做**

理由不是「太贵」，是**交换的东西不对等**：

    它要拿  §K3（**接口级**不变量：改一点不重算全量）
    去换    「结构可复现」（**实现**性质）

而第 1 步已经说明：「不同构」**不破坏任何一条检查** —— 它是一条**读数**，
不是违规。拿一条硬不变量去换一条读数的好看，不划算。

## ★ 一条可以搬走的判据：什么时候「增量 ≡ 全量」才是正确性标准

Acar 和 Godin 都把「增量结果 = 全量重算结果」当**正确性标准**在证，
而本设计里它**只是一条读数**。差别不在工程习惯，在**输出唯不唯一**：

    输出**唯一**（一个值 / 一个格 / 一个规范形）
      ⇒ 「增量 = 全量」是**正确性**要求 —— 两条路必须到同一个地方
      Acar：增量求值的**值**；Godin：Galois 格对给定 context 是**唯一**的

    输出**不唯一**（一族都合法的结构）
      ⇒ 它只是**实现性质** —— 「哪一棵树」不是正确性的一部分
      本设计：§K1–§K9 描述的是一族合法树，不是唯一那棵

⇒ **判据**：搬一条「增量 ≡ 全量」到新领域之前，先问
   **「这个输出是唯一的吗？」** 唯一 ⇒ 可以当检查（进退出码）；
   不唯一 ⇒ 只能当读数，否则你会为了一个**不必要的**规范形去付全量重算的代价。

## 为什么 §K3 与「结构 = 项集的函数」不相容（论证，不靠比对）

    结构 = f(项集)   ⇒ 项集变一个元素，f 的取值就变
    f 的取值变       ⇒ **整棵树**都是 f 的函数 ⇒ 全部方向都要重新确定
    ⇒ 失效范围 = 全部 ≠ Cone(x) ⇒ **§K3 违反**

⚠️ 实测的「形状失效范围」只有 0.06–0.29，**不改变这条结论** ——
   违反的是「**有没有全量重算**」，不是「重算之后变了多少」。
   `§K3` 挡的是**工作量**（改一点不许全量重算），不是**结果差异**。

---

## 一个必须说清的测量细节

`B8` 用**方向 id** 比对（`before_dirs` / `after_dirs`）。重建会把 id 全部换掉，
所以**任何**重建实现都会被 `B8` 判红 —— 这会让结论显得像比对的产物。

⇒ 所以本模块另报一个**与 id 无关**的读数：**失效范围比**

    失效范围比 = |{成员集}_before △ {成员集}_after| / 方向数

    （用**成员集合**做对称差，不用 id。加一项会让路径上那些方向的成员集变大，
      于是它们进出集合各算一次 —— 这正是「失效范围」的 id 无关形态。）
"""

from __future__ import annotations

from typing import Any

from ..core.kernel import Kernel
from ._fixtures import items, make_builder, sequences


class RebuildKernel(Kernel):
    """**规范不动点**：每次插入后，从当前项集按规范序（id 升序）整树重建。

    ⇒ 结构 = 当前项集的**函数** ⇒ 与到达顺序无关（`增量 ≡ 全量` 成立）。

    ⚠️ 重建**不动账本** —— `§K4` 只增不改。结构被替换掉，历史留着。
    """

    def __init__(self, plugin: Any, items_: dict[str, Any], root_payload_of: Any,
                 threshold: int = 0, item_source: dict[str, Any] | None = None) -> None:
        super().__init__(plugin, items_)
        self._root_payload_of = root_payload_of
        #: 0 = 每次插入都重建；>0 = 只在 `len(items)` 是它的幂时重建
        self._threshold = threshold
        #: `insert(id)` 不给 item 时上哪儿找 —— `B8` 的驱动是这么调的（`make_keyset_root` 同形）
        self._item_source = item_source
        self.rebuilds = 0
        self.rebuild_work = 0          # 累计「重建时的项数」
        self._quiet = False

    # --- 重建 ---------------------------------------------------------------

    def rebuild(self) -> None:
        """从 `self.items` 按规范序重建成树。"""
        ids = sorted(self.items)
        self._dirs.clear()
        self._children.clear()
        self._members.clear()
        self._expanded.clear()
        self._tried.clear()
        self._path.clear()
        self._root = None
        self.rebuilds += 1
        self.rebuild_work += len(ids)
        self.build(self._root_payload_of(ids))
        self._quiet = True
        try:
            for i in ids:
                super().insert(i)
        finally:
            self._quiet = False

    def _should_rebuild(self, n: int) -> bool:
        """`threshold = 0` ⇒ 每次都重建；否则只在 `n` 是 `threshold` 的幂时重建。"""
        if self._threshold <= 1:
            return True
        return _is_power(n, self._threshold)

    def insert(self, item_id: str, item: Any = None) -> tuple[str, ...]:
        if item is None and item_id not in self.items and self._item_source is not None:
            item = self._item_source.get(item_id)
        if item is not None:
            self.items[item_id] = item
        if item_id not in self.items:
            raise KeyError(item_id)
        if self._quiet or not self._should_rebuild(len(self.items)):
            return super().insert(item_id)
        self.rebuild()
        return self._path.get(item_id, ())


class ThresholdKernel(RebuildKernel):
    """**阈值重建**：只在规模跨过 2 的幂时重建（Naor–Teague 的做法）。

    摊还 O(1)，但**两次重建之间仍然是顺序相关的**。
    """

    def __init__(self, plugin: Any, items_: dict[str, Any], root_payload_of: Any) -> None:
        super().__init__(plugin, items_, root_payload_of, threshold=2)


def _is_power(n: int, base: int) -> bool:
    if n < 1:
        return False
    while n % base == 0:
        n //= base
    return n == 1


# --- 构造器（与 `_fixtures.make_builder` 同签名） ----------------------------

def _make_plugin_and_root(which: str, nodes: dict[str, Any],
                          edges: dict[str, Any]) -> tuple[Any, Any]:
    from ..plugins.keyset import KeysetPlugin
    from ..plugins.reach import ReachPlugin
    from ..plugins.sequence import SequencePlugin

    if which == "keyset":
        plug = KeysetPlugin()
        return plug, lambda ids: plug.merge([])          # 最外层意图不约束键
    if which == "reach":
        plug = ReachPlugin(edges)
        return plug, lambda ids: frozenset(ids)          # ← 当时**已知**的锚点
    if which == "sequence":
        plug = SequencePlugin(sequences(nodes, edges))
        return plug, lambda ids: frozenset({()})         # 空前缀：覆盖一切
    raise ValueError(which)


def make_rebuild_builder(which: str, nodes: dict[str, Any], edges: dict[str, Any],
                         cls: Any = RebuildKernel) -> Any:
    """返回 `build(subset_ids) -> (kernel, plugin)` —— 与 `_fixtures.make_builder` 同签名。

    ⚠️ **根 payload 必须跟着 subset 走**，规则与 `_fixtures.make_builder` **一致** ——
       否则比的是「根不一样」而不是「结构不一样」。
    """
    all_items = items(nodes)

    def build(subset_ids: list[str]) -> tuple[Kernel, Any]:
        sub = sorted(set(subset_ids))
        plug, root_of = _make_plugin_and_root(which, nodes, edges)
        k = cls(plug, {i: all_items[i] for i in sub}, root_of)
        k.rebuild()
        return k, plug

    return build


def make_rebuild_root(which: str, nodes: dict[str, Any], edges: dict[str, Any]) -> Any:
    """给 `B8` 用：每次返回一个**空的**、会重建的内核 —— 它随插入自己长出来。

    ⚠️ 不能像 `make_keyset_root` 那样「一开始就把全部项装进 `items`」——
       重建内核是从 `items` 建树的，那样第一次插入就会把整棵树建完，
       `B8` 量到的就不是「插入带来的变动」了。
    """
    all_items = items(nodes)

    def _make() -> RebuildKernel:
        plug, root_of = _make_plugin_and_root(which, nodes, edges)
        return RebuildKernel(plug, {}, root_of, item_source=all_items)

    return _make


# --- 读数 -------------------------------------------------------------------

def _failure_scope(build: Any, ids: list[str], all_items: dict[str, Any]) -> dict[str, Any]:
    """**与 id 无关**的失效范围：成员集合的对称差 / 方向数。

    ⚠️ **必须把新插的那一项从 after 的每个成员集里扣掉**，否则量到的是
       「x 加进了路径上的每个方向」—— 那对**任何**实现都一样，读数就没有区分力。
       不扣时：基线 0.78 / 重建 0.79 —— 差 0.01，两个系统读不出差别。
       扣掉之后，「路径上的方向」在 before / after 里是**同一个集合**，自动抵消；
       剩下的差异才是「**别人的分组被改了**」⇒ 基线 0.00 / 重建 0.06。
    """
    ratios: list[float] = []
    for upto in range(1, len(ids) + 1):
        k, _ = build(ids[: upto - 1])
        before = {frozenset(k.members_of(d)) for d in k.all_directions()}
        target = ids[upto - 1]
        k.insert(target, all_items[target])
        after = {frozenset(k.members_of(d) - {target}) for d in k.all_directions()}
        before.discard(frozenset())
        after.discard(frozenset())
        ratios.append(len(before ^ after) / max(len(after), 1))
    return {"平均失效范围比": round(sum(ratios) / len(ratios), 2) if ratios else 0.0,
            "最大": round(max(ratios), 2) if ratios else 0.0}


def rebuild_profile(which: str, nodes: dict[str, Any], edges: dict[str, Any]) -> dict[str, Any]:
    """报三件事：**同构性** / **§K3 的失效范围** / **代价**。全部只报不判。"""
    from ._fixtures import build_incremental
    from .divergence import DEFAULT_KS, _scan_points, _shape, divergence_profile

    ids = sorted(nodes)
    all_items = items(nodes)
    out: dict[str, Any] = {}

    for name, cls in (("规范不动点", RebuildKernel), ("阈值重建", ThresholdKernel)):
        build = make_rebuild_builder(which, nodes, edges, cls=cls)
        prof = divergence_profile(build, nodes, ids)          # 带比较器自检
        # ⚠️ 各 k 之间是否互相同构 —— 与「vs 全量」是**两件事**：
        #    「互相同构」= 结果不再依赖**初始批次大小**；「vs 全量」= 增量 ≡ 全量。
        shapes = {k: _shape(build_incremental(build, nodes, ids[:k], ids[k:]))
                  for k in _scan_points(len(ids), DEFAULT_KS)}
        # 代价：走**增量那条路**（先建 2 项，再维护其余），才是真实的重建次数
        inc, _ = build(ids[:2])
        for i in ids[2:]:
            inc.insert(i, all_items[i])
        out[name] = {
            "扫描的 k": len(prof["各 k"]),
            "同构的 k 数": sum(1 for r in prof["各 k"].values() if r["同构"]),
            "最早同构的 k": prof["最早同构的 k"],
            "k 指纹数": len(set(shapes.values())),
            "k 之间互相同构": len(set(shapes.values())) == 1,
            "全量方向数": prof["全量"]["方向数"],
            "重建次数": inc.rebuilds,
            "重建工作量": inc.rebuild_work,
            "比较器自检": all(prof["比较器自检"].values()),
        }

    out["失效范围"] = {
        "基线（路径局部增量）": _failure_scope(make_builder(which, nodes, edges), ids, all_items),
        "规范不动点（每次重建）": _failure_scope(
            make_rebuild_builder(which, nodes, edges, cls=RebuildKernel), ids, all_items),
    }
    return out


def render_rebuild(prof: dict[str, Any], label: str = "") -> str:
    head = f"规范重建（{label}）" if label else "规范重建"
    a, b = prof["规范不动点"], prof["阈值重建"]
    sc = prof["失效范围"]
    return (f"{head}：规范不动点 {a['同构的 k 数']}/{a['扫描的 k']} 个 k 同构全量"
            f"、k 指纹 {a['k 指纹数']} 种"
            f"（重建 {a['重建次数']} 次 / 工作量 {a['重建工作量']}）"
            f"｜阈值重建 {b['同构的 k 数']}/{b['扫描的 k']} 同构全量"
            f"、k 指纹 {b['k 指纹数']} 种"
            f"（重建 {b['重建次数']} 次）"
            f"｜失效范围比 基线 {sc['基线（路径局部增量）']['平均失效范围比']}"
            f" → 重建 {sc['规范不动点（每次重建）']['平均失效范围比']}"
            f"（度量，不进退出码）")
