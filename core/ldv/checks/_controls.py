"""`B` 判据的**合成对照**（2026-10-23）。

## 为什么要有这一层

一条**永远红**的判据和一条**永远绿**的判据，在「跑一遍真实语料」里长得一样。
所以每条判据都得回答同一个问题：**它会不会红？**

做法：造一个**已知违反**的输入，要求判据报红；再造一个**干净的**，要求报绿。
两个结果**必须不同** —— 都落在同一态，这个对照就什么都没证明。

## 与 `b17_known_answer` / `b20_known_answer` 的关系

同一套做法，只是那两条写在各自的检查文件里，这里集中放新增的。
⚠️ 集中不等于可以偷懒：每条都要**真造违反**，不许「跑一遍看它绿」。

## 边界

合成对照证明的是「**这条判据的代码路径能报红**」，
**不是**「真实语料上不存在这种违反」。后者是 `run_checks` 在真语料上的事。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..core.direction import (ORIGIN_EXOGENOUS, ORIGIN_SPLIT, Direction,
                              Ledger)
from ..core.tri import Tri
from ._framework import Report, result_of


# --- 公用：造一个只提供「查得动」的最小内核 --------------------------------

class StubKernel:
    """只实现检查用到的那几个方法 —— **不模拟内核**，只喂形状。

    ⚠️ 刻意不实现 `insert` / `expand`：这个 stub 只用来喂**坏结构**，
       多一个方法就多一处「和真内核行为不一致」的机会。
    """

    def __init__(self, dirs: list[Direction],
                 children: dict[str, tuple[str, ...]] | None = None,
                 members: dict[str, frozenset[str]] | None = None,
                 stayed: dict[str, frozenset[str]] | None = None,
                 items: dict[str, Any] | None = None) -> None:
        self._dirs = dirs
        self._children = children or {}
        self._members = members or {}
        self._stayed = stayed or {}
        self.items = items or {}
        self.ledger = Ledger()

    def all_directions(self) -> list[Direction]:
        return list(self._dirs)

    def direction(self, did: str) -> Direction:
        for d in self._dirs:
            if d.did == did:
                return d
        raise KeyError(did)

    def children_of(self, d: Direction) -> tuple[Any, ...]:
        return tuple(self.direction(x) for x in self._children.get(d.did, ()))

    def members_of(self, d: Direction) -> frozenset[str]:
        return frozenset(self._members.get(d.did, ()))

    def stayed_of(self, d: Direction) -> frozenset[str]:
        return frozenset(self._stayed.get(d.did, ()))


def _d(did: str, rank: int, parent: str | None,
       witness: tuple[str, ...] = (), origin: str = ORIGIN_SPLIT) -> Direction:
    return Direction(did=did, rank=rank, payload=frozenset({did}),
                     witness=witness, origin=origin, parent=parent)


def _tri(fn: Any, code: str, *args: Any, **kw: Any) -> Tri:
    """跑一个检查，取它给 `code` 的三态。"""
    rep = Report(plugin="(合成对照)")
    fn(*args, rep, **kw)
    return result_of(rep, code)


def _tri_src(fn: Any, code: str, extra: list[Path]) -> Tri:
    """B12 / B14 的签名是 `(rep, extra_sources)` —— 与 `_tri` 的顺序相反。"""
    rep = Report(plugin="(合成对照)")
    fn(rep, extra)
    return result_of(rep, code)


def _pair(label: str, bad: Tri, good: Tri, want_bad: Tri = Tri.NO) -> list[str]:
    """一对结果的自检：坏输入必须是 `want_bad`，好输入必须是「是」，且**两者不同**。"""
    fails: list[str] = []
    if bad is not want_bad:
        fails.append(f"{label}：坏输入期望 {want_bad}，实测 {bad}")
    if good is not Tri.YES:
        fails.append(f"{label}：好输入期望 是，实测 {good}")
    if bad is good:
        fails.append(f"{label}：两个结果相同（{bad}）—— 这个对照证明不了什么")
    return fails


# --- B4 劈开成划分 ---------------------------------------------------------

def b4_known_answer() -> list[str]:
    """B4 的合成对照：**子方向重叠** 与 **漏了但账上没有**，两种都要红。"""
    from .contract import b4_split_is_partition

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    c1 = _d("D1", 2, "D0", ("D0",))
    c2 = _d("D2", 2, "D0", ("D0",))

    # ① 坏：D1={a,b}、D2={b,c} ⇒ b 重叠；且 c 不在父里
    bad_k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")},
                       {"D0": frozenset({"a", "b"}),
                        "D1": frozenset({"a", "b"}),
                        "D2": frozenset({"b", "c"})})
    # ② 好：D1={a}、D2={b}，父={a,b}，无滞留
    good_k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")},
                        {"D0": frozenset({"a", "b"}),
                         "D1": frozenset({"a"}),
                         "D2": frozenset({"b"})})
    # ③ 坏（第二种）：漏了 c，但账上没有它
    leak_k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")},
                        {"D0": frozenset({"a", "b", "c"}),
                         "D1": frozenset({"a"}),
                         "D2": frozenset({"b"})})
    fails = _pair("B4 重叠", _tri(b4_split_is_partition, "B4", bad_k),
                  _tri(b4_split_is_partition, "B4", good_k))
    leak = _tri(b4_split_is_partition, "B4", leak_k)
    if leak is not Tri.NO:
        fails.append(f"B4 漏项未记账：期望 否，实测 {leak}")
    return fails


# --- B7 见证完备 -----------------------------------------------------------

def b7_known_answer() -> list[str]:
    """B7 的合成对照：非根方向**没有见证** ⇒ 红；根没有见证 ⇒ 仍然绿（设计如此）。"""
    from .structure import b7_witness_complete

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    bad_k = StubKernel([root, _d("D1", 2, "D0", ())], {"D0": ("D1",)})
    good_k = StubKernel([root, _d("D1", 2, "D0", ("D0",))], {"D0": ("D1",)})
    return _pair("B7 无见证", _tri(b7_witness_complete, "B7", bad_k),
                 _tri(b7_witness_complete, "B7", good_k))


# --- B9 账本只追加 ---------------------------------------------------------

def b9_known_answer() -> list[str]:
    """B9 的合成对照：**改写一条已记录的事件** ⇒ 红；干净账本 ⇒ 绿。

    ⚠️ 改的是 `_events[0].detail` —— `Ledger` 留了 `_digest` 快照，
       所以「改过」和「没改过」在 `verify_append_only()` 上**必须**不同。
    """
    from ..core.direction import Event
    from .structure import b9_ledger_append_only

    def _kernel(tamper: bool) -> StubKernel:
        k = StubKernel([_d("D0", 1, None, origin=ORIGIN_EXOGENOUS)])
        k.ledger.append("born", "D0", rank=1)
        k.ledger.append("born", "D1", rank=2, parent="D0")
        if tamper:
            old = k.ledger._events[0]                     # noqa: SLF001
            k.ledger._events[0] = Event(                  # noqa: SLF001
                seq=old.seq, kind=old.kind, did=old.did,
                detail={**old.detail, "rank": 99})
        return k

    return _pair("B9 账本改写", _tri(b9_ledger_append_only, "B9", _kernel(True)),
                 _tri(b9_ledger_append_only, "B9", _kernel(False)))


# --- B12 无全局标量分 ------------------------------------------------------

def b12_known_answer() -> list[str]:
    """B12 的合成对照：源码里出现 `score` 这类名字 ⇒ 红；`n_expanded` ⇒ 绿。"""
    from .source import b12_no_global_scalar

    with tempfile.TemporaryDirectory(prefix="ldv-b12-") as td:
        bad_p = Path(td) / "bad.py"
        bad_p.write_text("total_score = 1\n", encoding="utf-8")
        good_p = Path(td) / "good.py"
        good_p.write_text("n_expanded = 1\n", encoding="utf-8")
        return _pair("B12 全局标量",
                     _tri_src(b12_no_global_scalar, "B12", [bad_p]),
                     _tri_src(b12_no_global_scalar, "B12", [good_p]))


# --- B13 良基树 -----------------------------------------------------------

def b13_known_answer() -> list[str]:
    """B13 的合成对照：**父链成环** / **一个方向两个父** ⇒ 红；正常树 ⇒ 绿。"""
    from .structure import b13_rank_well_founded

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)

    # ① 坏：D1 与 D2 互认父 ⇒ 成环
    cyc = StubKernel([root, _d("D1", 2, "D2", ("D0",)),
                      _d("D2", 3, "D1", ("D0",))], {"D0": ("D1",)})
    # ② 坏：D3 同时是 D1、D2 的子 ⇒ 两个父
    multi = StubKernel([root, _d("D1", 2, "D0", ("D0",)),
                        _d("D2", 2, "D0", ("D0",)),
                        _d("D3", 3, "D1", ("D1",))],
                       {"D0": ("D1", "D2"), "D1": ("D3",), "D2": ("D3",)})
    # ③ 好
    good = StubKernel([root, _d("D1", 2, "D0", ("D0",))], {"D0": ("D1",)})

    fails = _pair("B13 成环", _tri(b13_rank_well_founded, "B13", cyc),
                  _tri(b13_rank_well_founded, "B13", good))
    m = _tri(b13_rank_well_founded, "B13", multi)
    if m is not Tri.NO:
        fails.append(f"B13 多父：期望 否，实测 {m}")
    return fails


# --- B14 外生边界 ---------------------------------------------------------

def b14_known_answer() -> list[str]:
    """B14 的合成对照：自优化侧 `import` 了插件 ⇒ 红；不 import ⇒ 绿。"""
    from .source import b14_exogenous_boundary

    with tempfile.TemporaryDirectory(prefix="ldv-b14-") as td:
        bad_p = Path(td) / "bad.py"
        bad_p.write_text("from ldv.plugins.keyset import KeysetPlugin\n",
                         encoding="utf-8")
        good_p = Path(td) / "good.py"
        good_p.write_text("X = 1\n", encoding="utf-8")
        return _pair("B14 越界 import",
                     _tri_src(b14_exogenous_boundary, "B14", [bad_p]),
                     _tri_src(b14_exogenous_boundary, "B14", [good_p]))


# --- B2 合并覆盖 -----------------------------------------------------------

class _KeyPlugin:
    """按键集命中的**假插件** —— 只实现 B2/B3/B5 用到的那几个方法。

    `broken` 决定它在哪一步出错：
        `merge`   合并后丢键 ⇒ B2 红
        `penalty` 代价抛异常 ⇒ B3 红
        `decode`  解码丢键   ⇒ B5 红
    """

    def __init__(self, broken: str | None = None) -> None:
        self.broken = broken

    def hit(self, d: Any, q: Any) -> Tri:
        keys = d.payload if isinstance(d.payload, frozenset) else frozenset()
        return Tri.YES if (keys & frozenset(getattr(q, "ideal", ()))) else Tri.NO

    def merge(self, ds: list[Direction]) -> frozenset[str]:
        out: set[str] = set()
        for d in ds:
            out |= set(d.payload)
        return frozenset() if self.broken == "merge" else frozenset(out)

    def penalty(self, d: Any, item: Any) -> float:
        if self.broken == "penalty":
            raise ValueError("代价不可比")
        return 0.5

    def encode(self, d: Any) -> bytes:
        return "|".join(sorted(d.payload)).encode("utf-8")

    def decode(self, blob: bytes) -> frozenset[str]:
        keys = frozenset(x for x in blob.decode("utf-8").split("|") if x)
        return frozenset() if self.broken == "decode" else keys


def b2_known_answer() -> list[str]:
    """B2 的合成对照：**合并后丢键** ⇒ 粗方向拒掉细方向收下的查询 ⇒ 红。"""
    from ..core.kernel import Query
    from .contract import b2_merge_covers

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    # `_d` 的 payload 是 {did} —— 这里要的是**键**，换成键集
    c1 = Direction(did="D1", rank=2, payload=frozenset({"a"}),
                   witness=("D0",), origin=ORIGIN_SPLIT, parent="D0")
    c2 = Direction(did="D2", rank=2, payload=frozenset({"b"}),
                   witness=("D0",), origin=ORIGIN_SPLIT, parent="D0")
    k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")})
    qs = [Query(ideal=frozenset({"a"}), label="a"),
          Query(ideal=frozenset({"b"}), label="b")]
    return _pair("B2 合并丢键",
                 _tri(b2_merge_covers, "B2", _KeyPlugin("merge"), k, qs),
                 _tri(b2_merge_covers, "B2", _KeyPlugin(), k, qs))


# --- B3 代价可比 -----------------------------------------------------------

def b3_known_answer() -> list[str]:
    """B3 的合成对照：`penalty` **抛异常** ⇒ 记成违例 ⇒ 红；正常返回 ⇒ 绿。"""
    from .contract import b3_penalty_comparable

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    c1, c2 = _d("D1", 2, "D0", ("D0",)), _d("D2", 2, "D0", ("D0",))
    k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")},
                   items={"a": frozenset({"x"})})
    return _pair("B3 代价抛异常",
                 _tri(b3_penalty_comparable, "B3", k, _KeyPlugin("penalty")),
                 _tri(b3_penalty_comparable, "B3", k, _KeyPlugin()))


# --- B5 解码覆盖 -----------------------------------------------------------

def b5_known_answer() -> list[str]:
    """B5 的合成对照：**解码丢键** ⇒ `解码(编码(d))` 判否掉 d 说「是」的查询 ⇒ 红。"""
    from ..core.kernel import Query
    from .contract import b5_decode_covers

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    c1 = Direction(did="D1", rank=2, payload=frozenset({"a"}),
                   witness=("D0",), origin=ORIGIN_SPLIT, parent="D0")
    k = StubKernel([root, c1], {"D0": ("D1",)})
    qs = [Query(ideal=frozenset({"a"}), label="a")]
    return _pair("B5 解码丢键",
                 _tri(b5_decode_covers, "B5", _KeyPlugin("decode"), k, qs),
                 _tri(b5_decode_covers, "B5", _KeyPlugin(), k, qs))


# --- B16 成员 ⊆ 覆盖 -------------------------------------------------------

def b16_known_answer() -> list[str]:
    """B16 的合成对照：**覆盖少给一个成员** ⇒ 成员落在覆盖外 ⇒ 红。"""
    from .coverage import b16_members_covered

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    k = StubKernel([root], members={"D0": frozenset({"a", "b"})})
    return _pair(
        "B16 成员越界",
        _tri(b16_members_covered, "B16", k, lambda payload: frozenset({"a"})),
        _tri(b16_members_covered, "B16", k,
             lambda payload: frozenset({"a", "b"})))


# --- B19 变细守卫 ---------------------------------------------------------

def b19_known_answer() -> list[str]:
    """B19 的合成对照：**连续 N 次没变细** ⇒ 红；最长段 < N ⇒ 绿。

    ⚠️ `prof` 是手造的 —— 这正是它的好处：真语料上「树不够深」时
       B19 照样绿，而**绿不能证明它在守东西**（判据自己的 docstring 就这么写）。
    """
    from .coverage import b19_progress_guard

    base = {"已展开方向数": 20, "N": 10, "没变细的": ["D1→D2"],
            "没变细展开": 10, "切得不匀展开": 3}
    bad = {**base, "最长没变细连续段": 10}
    good = {**base, "最长没变细连续段": 9}
    return _pair("B19 连续没变细",
                 _tri(b19_progress_guard, "B19", bad),
                 _tri(b19_progress_guard, "B19", good))


# --- 需要**真内核**的四条（B6 / B8 / B10 / B11）---------------------------
#
# ⚠️ 为什么这几条必须用真内核：它们探的是**内核自己**的行为
#    （`record_usage` 会不会把 None 折成记录、`query` 会不会把未展开并进否、
#     `show` 的位次、插入时的失效范围）—— 假内核只能证明「假内核错了」。
#
# ⚠️ 代价：这几条**依赖内置语料**（毫秒级，12 项）。语料不在 ⇒ 返回 `None`
#    （= **跳过**），由调用方如实报「跳过」，不许当通过。

def _real_kernel(n: int = 12) -> Any:
    """返回 `(kernel, plugin)`；语料不在 ⇒ `None`。"""
    from ._fixtures import load, make_builder
    loaded = load()
    if loaded is None:
        return None
    nodes, edges, _d = loaded
    ids = sorted(nodes)
    k, plug = make_builder("keyset", nodes, edges)(ids[:n])
    return k, plug


def _leaky_kernel_class() -> Any:
    """B8 的**坏输入**：插入之后顺手动一个路径外的方向（失效扩散到锥外）。

    与 `test_injections._LeakyKernel` 同形 —— 这里另存一份是为了不让
    `checks/` 反向依赖 `tests/`（那是分层倒挂）。
    """
    from ..core.kernel import Kernel

    class Leaky(Kernel):
        def insert(self, item_id: str, item: Any = None):  # type: ignore[override]
            path = super().insert(item_id, item)
            pathset = set(path)
            for d in self.all_directions():
                if d.did not in pathset and (d.parent is None or d.parent not in pathset):
                    self._members.setdefault(d.did, set()).add(item_id)  # noqa: SLF001
                    break
            return path

    return Leaky


def b6_known_answer() -> list[str] | None:
    """B6：`record_usage(d, {"outcome": None})` **不许**返回记录（那是「没有观测」）。

    坏输入：把 `record_usage` 换成「把 None 折成 0 分记录」⇒ B6 必须红。
    """
    from ..core.interfaces import WeightedRecord
    from .contract import b6_signal_not_collapsed

    got = _real_kernel()
    if got is None:
        return None
    k, plug = got

    def _folding(d: Any, rec: dict) -> Any:
        # 坏法：**把「没观测到使用」折成一条 0 分记录** —— 这正是 arena 那条
        # 「0 和没有分不开」的形态。
        return WeightedRecord(did=d.did,
                              outcome=0.0 if rec.get("outcome") is None
                              else float(rec["outcome"]),
                              propensity=1.0)

    k.record_usage = _folding                        # type: ignore[assignment]
    bad = _tri(b6_signal_not_collapsed, "B6", k, plug)

    got2 = _real_kernel()
    assert got2 is not None
    k2, plug2 = got2
    good = _tri(b6_signal_not_collapsed, "B6", k2, plug2)
    return _pair("B6 None 被折成记录", bad, good)


def b8_known_answer() -> list[str] | None:
    """B8：插入 x 的失效范围 ⊆ `Cone(x)`。

    坏输入：`_LeakyKernel`（插入后顺手动一个路径外方向）⇒ B8 必须红。
    """
    from ..core.kernel import Kernel
    from ..plugins.keyset import KeysetPlugin
    from .structure import b8_invalidation_local

    got = _real_kernel()
    if got is None:
        return None
    from ._fixtures import items as _items
    loaded = __import__("ldv.checks._fixtures", fromlist=["load"]).load()
    assert loaded is not None
    nodes_d, _edges, _d = loaded
    ids = sorted(nodes_d)
    its = _items(nodes_d)
    leaky = _leaky_kernel_class()

    def _make(cls: Any):
        def build() -> Any:
            plug = KeysetPlugin()
            k = cls(plug, dict(its))
            k.build(plug.merge([]))
            return k
        return build

    bad = _tri(b8_invalidation_local, "B8", _make(leaky), ids)
    good = _tri(b8_invalidation_local, "B8", _make(Kernel), ids)
    return _pair("B8 失效越界", bad, good)


def b10_known_answer() -> list[str] | None:
    """B10：`未展开` 与 `否` 在输出里必须长得不一样。

    坏输入：`query` 返回「状态=否，但 unexpanded 非空」—— 两种去向被并掉了。
    """
    from ..core.kernel import QueryResult
    from ..core.tri import Tri as _Tri
    from .semantics import b10_three_states_separable

    got = _real_kernel()
    if got is None:
        return None
    k, plug = got
    k.query = lambda q: QueryResult(status=_Tri.NO, unexpanded=("D1", "D2"))  # type: ignore[assignment]
    bad = _tri(b10_three_states_separable, "B10", k, plug)

    got2 = _real_kernel()
    assert got2 is not None
    k2, plug2 = got2
    good = _tri(b10_three_states_separable, "B10", k2, plug2)
    return _pair("B10 未展开被并进否", bad, good)


def b11_known_answer() -> list[str] | None:
    """B11：展示位次要能让倾向不同。

    坏输入：`show` 只回 1 个方向 ⇒ 位次敏感**判不出来** ⇒ 必须红
    （不是「跳过」—— 有 ≥2 个方向却说展示只有 1 个，那是坏的）。
    """
    from .semantics import b11_propensity_complete

    got = _real_kernel()
    if got is None:
        return None
    k, plug = got
    k.show = lambda yes: tuple(yes)[:1]              # type: ignore[assignment]
    bad = _tri(b11_propensity_complete, "B11", k, plug)

    got2 = _real_kernel()
    assert got2 is not None
    k2, plug2 = got2
    good = _tri(b11_propensity_complete, "B11", k2, plug2)
    return _pair("B11 位次塌成 1", bad, good)


def b15_known_answer() -> list[str] | None:
    """B15：换插入顺序 ⇒ 树同构。

    坏输入：`_LeakyKernel` —— 它的失效落点**依赖插入顺序** ⇒ 换序不同构 ⇒ 红。
    （顺带说明：同一条坏内核同时让 B8 与 B15 红，这**不是**重复 ——
      两条判的是两件事：失效范围、顺序无关性。）
    """
    from ..core.kernel import Kernel
    from ..plugins.keyset import KeysetPlugin
    from ._fixtures import items as _items
    from .structure import b15_reproducible

    got = _real_kernel()
    if got is None:
        return None
    loaded = __import__("ldv.checks._fixtures", fromlist=["load"]).load()
    assert loaded is not None
    nodes_d, _edges, _d = loaded
    ids = sorted(nodes_d)[:12]
    its = _items(nodes_d)
    leaky = _leaky_kernel_class()

    def _make(cls: Any):
        def build() -> Any:
            plug = KeysetPlugin()
            k = cls(plug, dict(its))
            k.build(plug.merge([]))
            return k
        return build

    bad = _tri(b15_reproducible, "B15", _make(leaky), ids)
    good = _tri(b15_reproducible, "B15", _make(Kernel), ids)
    return _pair("B15 换序不同构", bad, good)


def d8_known_answer() -> list[str]:
    """`§D8` 的**已知答案**对照：四类离散判定（不是分数）。

    ⚠️ 与 `B*` 的对照**形状不同**：`§D8` 的「判据」本身就是一次**计算**
       （`candidate_dimensions` 分类），没有「它会不会红」这回事 ——
       所以这里的对照是**已知答案**：造三种字段，断言分类落在该落的那一类。

        恒定        100 项同值            ⇒ 恒定 ⇒ 分不开
        几乎唯一    100 项各不同          ⇒ 几乎唯一（分出来等于没分）
        可以当维度  4 种取值              ⇒ 可以当维度
    """
    from ..core.request import candidate_dimensions

    class _N:
        def __init__(self, **fields: Any) -> None:
            self.fields = fields

    n = 100
    nodes = {
        f"i{k}": _N(const="same", uniq=f"v{k}",
                    four=["a", "b", "c", "d"][k % 4])
        for k in range(n)
    }
    out = candidate_dimensions(nodes)
    # ⚠️ 行里的键是 `读数`（不是 `判定`）—— 四类的文字就在那一栏
    verdict = {r.get("字段"): r.get("读数", "") for r in out.get("候选维度", [])}

    def _has(field: str, word: str) -> bool:
        return word in str(verdict.get(field, ""))

    fails: list[str] = []
    if not _has("const", "恒定"):
        fails.append(f"恒定字段被判成 {verdict.get('const')!r}")
    if not _has("uniq", "唯一"):
        fails.append(f"唯一字段被判成 {verdict.get('uniq')!r}")
    if not _has("four", "可以当维度"):
        fails.append(f"4 值字段被判成 {verdict.get('four')!r}")
    # 反向：三类**必须两两不同**（都落同一类 = 这个对照什么都没证明）
    if len({verdict.get("const"), verdict.get("uniq"), verdict.get("four")}) < 3:
        fails.append(f"三类落在同一判定：{verdict}")
    return fails


# --- §D 创建层：已知答案对照（2026-10-23）--------------------------------
#
# ⚠️ 这几条与 `B*` 的对照**形状不同**：`§D` 的「判据」多数是**纯函数**
#    （`combination_verdict` / `choose_partition`），没有「它会不会红」这回事
#    ⇒ 用**已知答案**：造已知输入，断言输出落在该落的那一类，且**各类两两不同**。

def d4_known_answer() -> list[str]:
    """`§D4` 独立性两档：精确蕴含（定理）/ 近似蕴含（外生取舍）/ 独立。

    ⚠️ 两档**性质不同**，理由文案也必须不同 —— 否则「定理」与「取舍」在输出里
       长得一样，读者会以为近似档也是定理。
    """
    from ..corpus.predicates import combination_verdict

    base = 100
    exact = combination_verdict(base, base)           # 交集 == 最小项
    near = combination_verdict(int(base * 0.95), base)  # 0.95 ≥ 阈值
    free = combination_verdict(base // 2, base)       # 0.5 < 阈值
    fails: list[str] = []
    if not exact or "精确蕴含" not in exact:
        fails.append(f"精确蕴含被判成 {exact!r}")
    if not near or "强相关" not in near:
        fails.append(f"近似蕴含被判成 {near!r}")
    if free is not None:
        fails.append(f"独立组合被拒收了：{free!r}")
    if exact == near:
        fails.append("两档理由文案相同 ⇒ 定理与取舍分不开")
    return fails


def d3_known_answer() -> list[str]:
    """`§D3` 预算共享：单谓词与组合**抢同一份预算**，进划分的总数恒 ≤ budget。"""
    from ..corpus.predicates import Combination, Predicate, choose_partition

    def _p(name: str, d: float) -> Any:
        return Predicate(谓词=name, 规则="r", 量="m", 阈值=1.0, 满足数=10, 区分度=d)

    used = [_p(f"p{i}", 0.1 * (i + 1)) for i in range(6)]        # 区分度 0.1..0.6
    combos = [Combination(项=(f"p{i}", f"p{i + 1}"), 满足数=10, 区分度=0.9)
              for i in range(3)]                                # 组合更高 ⇒ 会赢
    budget = 4
    singles, picked, dropped = choose_partition(used, combos, budget=budget)
    fails: list[str] = []
    if len(singles) + len(picked) > budget:
        fails.append(f"总数 {len(singles) + len(picked)} > budget {budget}")
    if not picked:
        fails.append("组合区分度更高却没赢 ⇒ 预算没共用（各给一份）")
    if len(singles) + len(picked) + len(dropped) != len(used) + len(combos):
        fails.append("被丢掉的那些没报全 ⇒ 说不清谁没进划分")
    return fails


def d1_known_answer() -> list[str]:
    """`§D1` 外生准入：**只有人写了「维度」的条目才算**。

        没有 `directions.json`        ⇒ 0 条（不猜）
        有文件但条目没写 `维度`        ⇒ 0 条（可能只是「同意这个诊断」）
        写了 `维度`                   ⇒ 1 条
    """
    import json as _json
    import tempfile

    from ..core.request import approved_dimensions

    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ldv-d1-") as td:
        root = Path(td)
        if approved_dimensions(root) != []:
            fails.append("没有 directions.json 时却读出了条目")
        (root / "directions.json").write_text(
            _json.dumps({"已批准": [{"信号": "B19：…"}]}, ensure_ascii=False),
            encoding="utf-8")
        if approved_dimensions(root) != []:
            fails.append("条目没写「维度」却被采纳（那只是同意诊断，不是声明维度）")
        (root / "directions.json").write_text(
            _json.dumps({"已批准": [{"信号": "B19：…", "维度": "scope"}]},
                        ensure_ascii=False), encoding="utf-8")
        got = approved_dimensions(root)
        if len(got) != 1 or got[0].get("维度") != "scope":
            fails.append(f"写了「维度」却没采纳：{got!r}")
    return fails


def d6_known_answer() -> list[str]:
    """`§D6` 解析范围 = **候选全集**：预算管「自动选谁」，**不管人能声明什么**。

    坏法（这一条要防的）：预算=1 时把人的 5 条声明**截成 1 条**。
    """
    import json as _json
    import tempfile

    from ..core.request import approved_predicates

    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ldv-d6-") as td:
        root = Path(td)
        rows = [{"信号": f"B19：{i}", "谓词": {"名": f"p{i}", "定义": ["出度档>2"]}}
                for i in range(5)]
        (root / "directions.json").write_text(
            _json.dumps({"已批准": rows}, ensure_ascii=False), encoding="utf-8")
        # 预算故意设成 1 —— 它**不该**影响人能声明多少条
        (root / "predicates.json").write_text(
            _json.dumps({"预算": 1}, ensure_ascii=False), encoding="utf-8")
        got = approved_predicates(root)
        if len(got) != 5:
            fails.append(f"预算=1 时人的 5 条声明被截成 {len(got)} 条 ⇒ 预算越界管了声明")
    return fails


def d5_known_answer() -> list[str]:
    """`§D5` 改名不改划分：**同一个扩展**换个键名 ⇒ 分组必须逐字相同。

    做法：两个谓词只差 `谓词`（键名）一个字段，`量` / `阈值` 全同 ⇒
    落到同一批节点上。断言：

        ① **被命中的节点集合相同**（划分由扩展集定，不由名字定）
        ② 键名**确实不同**（否则这个对照只是在验同一个对象两遍）
    """
    from ..corpus.predicates import Predicate, apply_to_nodes

    class _Node:
        def __init__(self) -> None:
            self.keys: frozenset[str] = frozenset()

    ids = [f"n{i}" for i in range(6)]
    edges = {ids[0]: frozenset({ids[1], ids[2], ids[3]}),
             ids[1]: frozenset({ids[2]}),
             ids[2]: frozenset()}
    base = dict(量="out_degree", 阈值=1.0, 规则="出度档")

    def _run(name: str) -> set[str]:
        nodes = {i: _Node() for i in ids}
        apply_to_nodes(nodes, edges,
                       [Predicate(谓词=name, 满足数=0, 区分度=0.0, **base)])
        return {i for i, n in nodes.items() if n.keys}

    hit_a, hit_b = _run("出度档>1"), _run("改过名的键")
    fails: list[str] = []
    if not hit_a:
        fails.append("谓词一个节点都没命中 ⇒ 对照无内容")
    if hit_a != hit_b:
        fails.append(f"改名后划分变了：{sorted(hit_a)} vs {sorted(hit_b)}")
    if hit_a == set(ids):
        fails.append("谓词命中了全部节点 ⇒ 分不开，对照无内容")
    return fails


def d2_known_answer() -> list[str]:
    """`§D2` 真二分：**空谓词 / 全谓词零贡献**，只有真二分才进划分。

        全真（6/6 满足）的组合   ⇒ **拒收**
        全空（0/6 满足）的组合   ⇒ **拒收**
        真二分（3/6 满足）的组合 ⇒ **进候选**

    ⚠️ 拒收的**理由**可以是「精确蕴含」也可以是「切不开」—— 两条都对，
       本条只判「有没有被拒收」，不判理由文案（那是 §D4 的事）。
    """
    from ..corpus.predicates import Predicate, combine

    class _Node:
        def __init__(self) -> None:
            self.keys: frozenset[str] = frozenset()

    ids = [f"n{i}" for i in range(6)]
    nodes = {i: _Node() for i in ids}
    # 出度：n0=3、n1=1、其余 0；入度全 0
    edges = {ids[0]: frozenset({ids[1], ids[2], ids[3]}),
             ids[1]: frozenset({ids[2]})}
    for i in ids:
        edges.setdefault(i, frozenset())

    def _p(name: str, which: str, th: float) -> Any:
        return Predicate(谓词=name, 规则="r", 量=which, 阈值=th,
                         满足数=0, 区分度=0.0)

    def _run(used: list[Any]) -> tuple[list[Any], list[Any]]:
        _n, cand, rej = combine(nodes, edges, used, k=3)
        return cand, rej

    fails: list[str] = []
    # ① 全真：两条不同量的「全真」谓词 ⇒ 合取仍全真 ⇒ 零贡献
    _c, rej = _run([_p("全真A", "out_degree", -1.0), _p("全真B", "in_degree", -1.0)])
    if not rej:
        fails.append("全真组合没被拒收 ⇒ 零贡献的谓词进了划分")
    # ② 全空
    _c, rej = _run([_p("全空A", "out_degree", 99.0), _p("全空B", "in_degree", 99.0)])
    if not rej:
        fails.append("全空组合没被拒收")
    # ③ 真二分：出度>0.5 ⇒ 3/6 满足；入度>0.5 ⇒ 3/6 满足
    cand, rej = _run([_p("出度>0", "out_degree", 0.5), _p("入度>0", "in_degree", 0.5)])
    if not cand:
        fails.append(f"真二分的组合被拒收了（{rej[:1]}）⇒ 判据太严")
    return fails


def s3_known_answer() -> list[str] | None:
    """`§S3` 反思显性：每条请求带证据；**未触发时也要报「查了什么」**。

    ★ 判的不是「请求对不对」（那是人的决定），而是**输出能不能由结构复算**：

        ① 未触发 ⇒ `请求` 空，但 `本趟查了` **非空**（「没查」与「查了没有」分得开）
        ② 触发 ⇒ 每条都有 信号 / 方向 / 阈值 / 涉及的项
        ③ `涉及的项 ⊆ members(方向)` —— **可复算**，不是随口报的一串 id
    """
    from ..core.request import direction_requests, leaf_cap_requests

    got = _real_kernel()
    if got is None:
        return None
    k, _plug = got
    fails: list[str] = []

    # ① 未触发：cover 恒空 ⇒ 没有请求，但必须说清查了什么
    quiet = direction_requests(k, lambda payload: frozenset(), n=3)
    if quiet.get("请求"):
        fails.append("cover 恒空却仍产出请求 ⇒ 证据不是从结构算的")
    if not quiet.get("本趟查了"):
        fails.append("未触发时 `本趟查了` 为空 ⇒ 「没查」与「查了没有」长得一样")

    # ②③ 触发：叶容量超限（cap=1）⇒ 每条请求都要带可复算的证据
    loud = leaf_cap_requests(k, cap=1)
    reqs = loud.get("请求") or []
    if not reqs:
        fails.append("cap=1 却一条请求都没有 ⇒ 对照无内容")
    for r in reqs:
        for f in ("信号", "方向", "阈值", "涉及的项"):
            if f not in r:
                fails.append(f"请求缺字段 `{f}`：{sorted(r)[:5]}")
        d = k.direction(r["方向"])
        if not set(r["涉及的项"]) <= set(k.members_of(d)):
            fails.append(f"{r['方向']} 的「涉及的项」不在 members 里 ⇒ 复算对不上")
    return fails


def s4_known_answer() -> list[str] | None:
    """`§S4` 重构显性：变更留账，且**批准了但材料里没有 ⇒ 报「未生效」**。

    判的不是「这个维度该不该批准」，而是**系统有没有如实记账**：
    假成功（报「生效」而实际没动）与真生效在输出上长得一样 —— 这条防的就是它。
    """
    import json as _json
    import tempfile

    from ..core.request import apply_approved
    from ._fixtures import items as _items

    got = _real_kernel()
    if got is None:
        return None
    loaded = __import__("ldv.checks._fixtures", fromlist=["load"]).load()
    assert loaded is not None
    nodes_d, edges_d, _d = loaded
    its = _items(nodes_d)
    ids = sorted(its)[:12]

    class _N:
        def __init__(self, keys: Any) -> None:
            self.keys = keys
            self.fields: dict[str, Any] = {}

    def _ledger(field: str) -> dict[str, Any]:
        nd = {i: _N(its[i]) for i in ids}
        with tempfile.TemporaryDirectory(prefix="ldv-s4-") as td:
            (Path(td) / "directions.json").write_text(
                _json.dumps({"已批准": [{"信号": "x", "维度": field}]},
                            ensure_ascii=False), encoding="utf-8")
            _new, ledger = apply_approved(nd, Path(td), edges_d)
        return ledger

    fails: list[str] = []
    miss = _ledger("不存在的字段")
    if miss.get("生效"):
        fails.append("批准了材料里没有的字段却报「生效」⇒ 假成功")
    if not miss.get("未生效"):
        fails.append("批准了材料里没有的字段却**什么都不报** ⇒ 静默失败")
    return fails


def s5_known_answer() -> list[str]:
    """`§S5` 自我标签一致：每个 `我=是` 的项都能**指回仓库里的一份文件**。

    ★ 判的不是「哪些算我的产物」（那是人的约定，可争论），而是**标签有没有编**：

        ① 生成出来的每个节点都有 `source.ref`，且那个文件**真的存在**
        ② 项数 > 0（否则对照无内容）
        ③ 删掉输出目录 ⇒ 回到原状（不残留）

    ⚠️ 约定那半（「release 正文算不算产物」）**判不了** —— 断言集 §S5 自己写着
       「实验支持 / 推翻不了它」。这里只判可判的那半。
    """
    import shutil
    import tempfile

    from ..tools._paths import REPO
    from ..tools.make_self_corpus import build

    tmp = Path(tempfile.mkdtemp(prefix="ldv-s5-"))
    fails: list[str] = []
    try:
        st = build(REPO, tmp)
        nodes = sorted((tmp / "nodes").glob("*.md"))
        if not nodes:
            fails.append("一个自我项都没生成 ⇒ 对照无内容")
        bad: list[str] = []
        for f in nodes:
            text = f.read_text(encoding="utf-8")
            m = __import__("re").search(r'source:\s*\n\s*ref:\s*"([^"]+)"', text)
            if not m:
                bad.append(f"{f.name} 没有 source.ref")
                continue
            if not (REPO / m.group(1)).exists():
                bad.append(f"{f.name} 的来源 {m.group(1)!r} 不存在")
        if bad:
            fails.append(f"{len(bad)}/{len(nodes)} 个自我项指不回文件：{bad[:2]}")
        if not st.get("断言"):
            fails.append("统计里没有断言条数 ⇒ 说不清抽了多少")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if tmp.exists():
        fails.append("删掉输出目录后仍残留 ⇒ 不可回退")
    return fails


def a2_known_answer() -> list[str]:
    """`§A2` 视图稳定：对每对块，`B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ`。

    坏输入：一块里**一个走得到、一个走不到**同一个目标 ⇒ 这一块跨在
    `E⁻¹(B₂)` 内外 ⇒ 不稳定 ⇒ 红。好输入：整块都在同一侧。
    """
    from ..core.views import ViewSpec
    from .abstraction import a2_stable

    U = ("a", "b", "c")
    # E: a → c（b 走不到 c）
    spec = ViewSpec(universe=U, partition=(frozenset({"a", "b"}), frozenset({"c"})),
                    relation=frozenset({("a", "c")}))
    bad_q = [frozenset({"a", "b"}), frozenset({"c"})]      # {a,b} 跨内外
    good_q = [frozenset({"a"}), frozenset({"b"}), frozenset({"c"})]
    return _pair("A2 视图不稳定",
                 _tri(a2_stable, "A2", spec, bad_q),
                 _tri(a2_stable, "A2", spec, good_q))


def a3_known_answer() -> list[str]:
    """`§A3` 最粗：不存在更粗的稳定划分。

    坏输入：`E` 对称（a↔b）时，`{a}`/`{b}` 稳定但**可合并** ⇒ 不是最粗 ⇒ 红。
    好输入：合并成一块 ⇒ 最粗。
    """
    from ..core.views import ViewSpec
    from .abstraction import a3_coarsest

    U = ("a", "b")
    # ⚠️ `P` 必须是**一个块** —— `coarser_stable_exists` 只在**同一个 P 块内部**合并
    #    （`P` 是「初始划分」，视图只会比它更细）。第一版把 P 写成 {a},{b}，
    #    于是「能不能合并」根本不成立 ⇒ 判据给「是」，对照反过来打了我一枪。
    spec = ViewSpec(universe=U,
                    partition=(frozenset({"a", "b"}),),
                    relation=frozenset({("a", "b"), ("b", "a")}))
    bad_q = [frozenset({"a"}), frozenset({"b"})]           # 稳定，但还能合并
    good_q = [frozenset({"a", "b"})]                       # 最粗
    return _pair("A3 不是最粗",
                 _tri(a3_coarsest, "A3", spec, bad_q),
                 _tri(a3_coarsest, "A3", spec, good_q))


ALL = (b4_known_answer, b7_known_answer, b9_known_answer,
       b12_known_answer, b13_known_answer, b14_known_answer,
       b2_known_answer, b3_known_answer, b5_known_answer,
       b16_known_answer, b19_known_answer,
       b6_known_answer, b8_known_answer, b10_known_answer,
       b11_known_answer, b15_known_answer, d8_known_answer,
       d4_known_answer, d3_known_answer, d1_known_answer,
       d6_known_answer, d5_known_answer, d2_known_answer,
       s3_known_answer, s4_known_answer, s5_known_answer,
       a2_known_answer, a3_known_answer)
