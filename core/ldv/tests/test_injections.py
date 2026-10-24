"""注入验证 —— **每条检查都必须能红**。

    跑法：python -m ldv.tests.test_injections

---

## 为什么这一步不能省

「全过」这句话**本身不提供任何信息** —— 一个永远返回「过」的检查
也会给出同样的结果。arena 那边把这件事说得很直白：

> **空转与通过长得一模一样。**

所以每条检查都要**成对**验一次：

    未注入 ⇒ 绿（基线）
    注入   ⇒ 红（判据真的在判）

**只验一半不行**：只验「注入后红」会被「本来就红」骗过；
只验「未注入绿」会被「永远绿」骗过。

---

## 但「全绿」自己也得有个**范围**

上面的「每条检查都验过了」是个**全称句** —— 它说的是「**声明的每一个编号**」。
新加一条检查、忘了配注入，套件照样报「全绿」，而那个「全绿」对新增的编号
**一个字节的信息都没有**。同一句话，范围悄悄缩了，读起来一模一样。

⇒ 所以套件自己第 0 条就是**注册表自检**：`CASES` 的键 ∪ 副判据的编号，
必须**恰好等于** `run_checks` 声明要跑的那批（两个方向都报）。见 `_registry_gap`。

---

## 注入要**改在最靠近判据的地方**

比如 `B1`（假阴）注入的是**返回假「否」的插件**，不是「让检查看不见」。
改在检查自己身上，验的是检查的 bug，不是判据的 bug。

---

## 注入还得**选对**：看起来该红的改法未必真红

`B15` 是实例。三种「该让 B15 变红」的改法里，只有一种真的变红：

    去掉 `build()` 的预装         → 根变叶，退化成一个方向  → B15 仍「过」（空转！）
    `expand()` 劈之前不排序       → 同一集合迭代序一致       → B15 仍「过」
    方向攒够容量才劈（上溢分裂）   → 真的顺序相关             → B15 **红** ← 用它

**「想当然的注入」和「有效的注入」长得一样**，所以注入本身也要挑：
挑不出来，说明这条判据的真实内容还没被说清。
"""

from __future__ import annotations

import itertools
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.checks._fixtures import (  # noqa: E402
    build_incremental,
    build_keyset,
    build_reach,
    build_sequence,
    coverage_of,
    find_corpus,
    keyset_queries,
    load,
    items as make_items,
    make_builder,
    reach_queries,
    sequences,
)
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.abstraction import (  # noqa: E402
    LEDGER_STAYED,
    READINGS,
    SUMMARIES,
    ReadingCtx,
    ViewSet,
    a1_soundness,
    a2_stable,
    a3_coarsest,
    a4_category,
    a5_ledger,
    a6_roundtrip,
    a7_old_q,
    a7_propagate,
    a8_fixture_drops,
    a8_maintenance,
    build_views,
    ledger_entries,
    reading_ctx,
    subtree_of,
    warranted_of,
)
from ldv.core.views import (  # noqa: E402
    Maintenance,
    MIN_SHRINK_RATIO,
    coarsest_stable_refinement,
    maintain,
    propagate,
    propagate_naive,
    shrink_ratio,
    restrict_spec,
    view_parts,
)
from ldv.checks.contract import (  # noqa: E402
    b1_no_false_negative,
    b2_merge_covers,
    b3_penalty_comparable,
    b4_split_is_partition,
    b5_decode_covers,
    b6_signal_not_collapsed,
)
from ldv.checks.coverage import (  # noqa: E402
    BASELINE_FORMAT,
    BASELINE_FORMAT_KEY,
    b16_members_covered,
    b18_cover_leak_baseline,
    b19_progress_guard,
    corpus_fingerprint,
    corpus_key,
    cover_leak_profile,
    progress_profile,
    soundness_profile,
)
from ldv.checks.semantics import b10_three_states_separable, b11_propensity_complete  # noqa: E402
from ldv.checks.source import b12_no_global_scalar, b14_exogenous_boundary  # noqa: E402
from ldv.checks.structure import (  # noqa: E402
    b7_witness_complete,
    b8_invalidation_local,
    b9_ledger_append_only,
    b13_rank_well_founded,
    b15_reproducible,
)
from ldv.core.direction import EVENT_STAYED, ORIGIN_SPLIT, Direction  # noqa: E402
from ldv.core.interfaces import call_penalty  # noqa: E402
from ldv.core.kernel import Kernel, QueryResult  # noqa: E402
from ldv.core.tri import Tri  # noqa: E402
from ldv.core.views import ViewSpec, partition_of  # noqa: E402
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402


def _rep() -> Report:
    return Report(plugin="注入", expects=())


class CannotBuild(Exception):
    """**这份注入在本语料上做不出来** —— 既不是「实现坏了」，也不是「判据不红」。

    三种「不是绿」要分得开（`跳过 ≠ 通过`；`崩比红更糟`）：

        `Tri.NO`         判据**开了火并红了** ⇒ 注入有效（这是我们要的）
        `CannotBuild`    夹具**在本语料上做不出来** ⇒ 问不出「能不能红」
        其它异常          崩 —— 连判据都没跑起来

    ⚠️ 作者原来用 `AssertionError` 表达第二件事（消息里写着「做不出来 /
       报错，不静默跳过」），但驱动把**任何**异常都读成「基线抛异常」
       ⇒ 「判不了」与「崩」长得一模一样。这个类把作者的本意**写进类型**。
    """


def _tmp_source(body: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    fh.write(body)
    fh.close()
    return Path(fh.name)


# ═══ B1 ══════════════════════════════════════════════════════════════════════

class _HitAllNo(KeysetPlugin):
    """假阴注入：不问青红皂白一律说「否」。"""

    def hit(self, d: Direction, query: Any) -> Tri:
        return Tri.NO


def inj_b1(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    rep = _rep()
    b1_no_false_negative(kernel, _HitAllNo() if injected else good,
                         keyset_queries(nodes), rep, path="批建")
    return rep


def inj_b1_maintenance(nodes, edges, injected: bool) -> Report:
    """`B1` 的**第二条**判据（维护路径）：ground truth 是「**维护之后**的成员 ∩ 查询」。

    ⚠️ 为什么不能只跑一条：`B1` 的 ground truth 里**有成员集**，而成员集在两条路径上
       不同（一次建完时它一开始就完整，维护时是后来才长起来的）⇒ 同一批查询
       问的其实是**另一个**成员集。「只跑一条路」与「两条路都跑」在汇总里长得一样。
    """
    all_items = make_items(nodes)
    ids = sorted(nodes)
    init = ids[:6]                                  # 与 `run_checks.MAINT_INIT` 一致
    plug = KeysetPlugin()
    k = Kernel(plug, {i: all_items[i] for i in init})
    k.build(plug.merge([]))
    for i in init:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    rep = _rep()
    b1_no_false_negative(k, _HitAllNo() if injected else plug,
                         keyset_queries(nodes), rep, path="维护")
    return rep


# ═══ B2 ══════════════════════════════════════════════════════════════════════

def _corpus_token(nodes: Any) -> str:
    """从**本语料**的键集里取一个 `type=…` 取值 —— 注入「收窄」时用它。

    ⚠️ **为什么不能写死**：收窄用的 token 必须是本语料键集里**真有**的取值，
       否则它对任何查询都不成立 ⇒ 注入**不红** —— 而「注入不红」与
       「判据不敏感」长得一模一样。

       实测（2026-10-11，**单变量对照**：只换这一个 token，其余不动）：

            语料                写死 `type=概念`   换成本语料的 `type=…`
            pypi-deps           `是`（不红）       `否`（红）  `type=Database`
            gh-langs            `是`（不红）       `否`（红）  `type=C`
            arxiv-taxonomy      `是`（不红）       `否`（红）  `type=Computer Science`
            crossref-journals   `是`（不红）       `否`（红）  任一 `type=…`

       ⇒ 判据 `B2` 本身**在判**（换成真 token 立刻红）；缺的那份红来自**夹具**。

    ⚠️ 取法必须**确定**（不许 `set` 迭代序）：按字典序取第一个。
    """
    keys: set[str] = set()
    for nid in sorted(nodes):
        keys |= set(nodes[nid].as_item().get("keys", ()))
    toks = sorted(k for k in keys if k.startswith("type="))
    if not toks:
        raise CannotBuild(
            "本语料键集里没有 `type=…` 取值 ⇒ 造不出「收窄合并结果」的注入")
    return toks[0]


class _MergeTooNarrow(KeysetPlugin):
    """合并注入：把合并结果收窄 —— 于是粗方向会拒掉细方向收下的东西。

    ⚠️ 收窄用的 token 从**本语料**取（`_corpus_token`）—— 原来写死 `type=概念`，
       换语料后那个 token 不在键集里 ⇒ 注入不红（详见 `_corpus_token` 的实测表）。
    """

    def __init__(self, token: str) -> None:
        self._token = token

    def merge(self, ds):
        req, forb = super().merge(ds)
        return (req | {self._token}, forb)


def inj_b2(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    plug = _MergeTooNarrow(_corpus_token(nodes)) if injected else good
    rep = _rep()
    b2_merge_covers(plug, kernel, keyset_queries(nodes), rep)
    return rep


# ═══ B3 ══════════════════════════════════════════════════════════════════════

class _PenaltyNaN(KeysetPlugin):
    """代价注入：某些方向返回 NaN —— NaN 与任何数都不可比。"""

    def penalty(self, d: Direction, item: Any) -> float:
        if d.rank >= 2:
            return float("nan")
        return super().penalty(d, item)


def inj_b3(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    rep = _rep()
    b3_penalty_comparable(kernel, _PenaltyNaN() if injected else good, rep)
    return rep


# ═══ B4 ══════════════════════════════════════════════════════════════════════

def inj_b4(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        # 把两个子方向的归属**弄重叠** —— 这正是行为式判据查不出来的那一半
        for d in kernel.all_directions():
            kids = kernel.children_of(d)
            if len(kids) >= 2:
                kernel._members[kids[0].did] |= set(kernel._members[kids[1].did])  # noqa: SLF001
                break
    rep = _rep()
    b4_split_is_partition(kernel, rep)
    return rep


def inj_b4_stay_unaccounted(nodes, edges, injected: bool) -> Report:
    """`B4` 的**第二条**判据（§10.2 出路 (4)）：**漏的必须条条有账**。

    ⚠️ 上面那条 `inj_b4` 注入的是「重叠」；这一条注入的是**账**。

    ## 为什么必须单列一条

    裸的 `∪members(子) == members(父)`（等式）在出路 (4) 之下必然破 —— 项可以
    **停在父方向**。所以判据是「`⊆` **且** 漏的恰好是账上那些」。

    带上 `⊆` 就**放宽了** —— 而放宽的东西必须**另有东西钉住**，否则
    「漏」会重新变得看不见（那正是本设计反复要消灭的形状）。
    ⇒ 这一条注入的就是「**放宽之后漏了但没记账**」：拿一个真的有滞留的结构，
      把账**抹掉**。

    判据两侧都要成立才算数：
        基线（账在）   漏 == 账 ⇒ **绿**
        注入（账抹掉） 漏 ≠ 账 ⇒ **红**

    ⚠️ 它**只**能在「真有滞留」的结构上验 —— 在批建路径上 `miss` 恒为空集，
       抹账与不抹账**都是绿的**（那正是「基线就绿的注入验证毫无信息量」）。
       ⇒ 所以这里用**先建 2 维护 34** 的 `sequence`：实测滞留 32 项。
    """
    ids = sorted(nodes)
    k = build_incremental(make_builder("sequence", nodes, edges), nodes, ids[:2], ids[2:])
    if injected:
        # 把「留在这一层」的账抹掉 —— 滞留项仍在父方向里，但**没有账**。
        # （只动 `_events`；`B4` 不查账本完整性，那是 `B9` 的事。）
        k.ledger._events = [e for e in k.ledger._events if e.kind != EVENT_STAYED]  # noqa: SLF001
    rep = _rep()
    b4_split_is_partition(k, rep)
    return rep


# ═══ B5 ══════════════════════════════════════════════════════════════════════


class _DecodeLossy(KeysetPlugin):
    """解码注入：往返时**丢**掉 require —— 于是解码结果不再覆盖原方向。

    ⚠️ 补进去的那个 token 同 `_MergeTooNarrow`：从**本语料**取（原来写死 `type=概念`）。
    """

    def __init__(self, token: str) -> None:
        self._token = token

    def decode(self, blob: bytes):
        req, forb = super().decode(blob)
        return (frozenset(), forb | ({self._token} if req else frozenset()))


def inj_b5(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    plug = _DecodeLossy(_corpus_token(nodes)) if injected else good
    rep = _rep()
    b5_decode_covers(plug, kernel, keyset_queries(nodes), rep)
    return rep


# ═══ B6 ══════════════════════════════════════════════════════════════════════

class _SignalFolds(KeysetPlugin):
    """信号注入：把「没有观测到使用」折成「outcome=0 的记录」。"""

    def signal(self, d: Direction, interaction: Any):
        from ldv.core.interfaces import UsageSignal

        if interaction.get("outcome") is None:
            return UsageSignal(did=d.did, outcome=0.0, note="折成 0 了")
        return super().signal(d, interaction)


def inj_b6(nodes, edges, injected: bool) -> Report:
    # ⚠️ 注入必须发生在**内核持有的那个插件**上 —— `record_usage` 用的是它。
    #    换的是**传给检查的参数**的话，检查照样绿：那个参数压根没被用到。
    plug = _SignalFolds() if injected else KeysetPlugin()
    kernel = Kernel(plug, make_items(nodes))
    kernel.build(plug.merge([]))
    for nid in sorted(nodes):
        kernel.insert(nid)
    rep = _rep()
    b6_signal_not_collapsed(kernel, plug, rep)
    return rep


# ═══ B7 ══════════════════════════════════════════════════════════════════════

def inj_b7(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        kernel._dirs["DX"] = Direction(  # noqa: SLF001
            did="DX", rank=2, payload=(frozenset(), frozenset()),
            witness=(), origin=ORIGIN_SPLIT, parent=kernel.root.did,
        )
    rep = _rep()
    b7_witness_complete(kernel, rep)
    return rep


# ═══ B8 ══════════════════════════════════════════════════════════════════════

class _LeakyKernel(Kernel):
    """失效注入：插入之后**顺手**动一个路径外的方向。"""

    def insert(self, item_id: str):
        path = super().insert(item_id)
        pathset = set(path)
        for d in self.all_directions():
            if d.did not in pathset and (d.parent is None or d.parent not in pathset):
                self._members.setdefault(d.did, set()).add(item_id)  # noqa: SLF001
                break
        return path


def inj_b8(nodes, edges, injected: bool) -> Report:
    def make() -> Kernel:
        plug = KeysetPlugin()
        k = (_LeakyKernel if injected else Kernel)(plug, make_items(nodes))
        k.build(plug.merge([]))
        return k

    rep = _rep()
    b8_invalidation_local(make, sorted(nodes), rep)
    return rep


# ═══ B9 ══════════════════════════════════════════════════════════════════════

def inj_b9(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        from ldv.core.direction import Event

        events = kernel.ledger._events                          # noqa: SLF001
        if len(events) < 2:
            # 这份语料上账本只有 ≤1 条 ⇒ 没有「第二条」可改 ⇒ 做不出注入
            raise CannotBuild(f"账本只有 {len(events)} 条事件 ⇒ 改不出「第二条」")
        saved = events[1]                                       # noqa: SLF001
        kernel.ledger._events[1] = Event(                       # noqa: SLF001
            seq=saved.seq, kind=saved.kind, did=saved.did,
            detail={**saved.detail, "改写": True},
        )
    rep = _rep()
    b9_ledger_append_only(kernel, rep)
    return rep


# ═══ B10 ═════════════════════════════════════════════════════════════════════

def inj_b10(nodes, edges, injected: bool) -> Report:
    kernel, plug = build_keyset(nodes)
    orig = QueryResult.render
    if injected:
        QueryResult.render = lambda self: "同一个输出"          # noqa: ARG005
    try:
        rep = _rep()
        b10_three_states_separable(kernel, plug, rep)
        return rep
    finally:
        QueryResult.render = orig


# ═══ B11 ═════════════════════════════════════════════════════════════════════

def inj_b11(nodes, edges, injected: bool) -> Report:
    kernel, plug = build_keyset(nodes)
    if injected:
        kernel.usage.append(SimpleNamespace(did=kernel.root.did, propensity=None))
    rep = _rep()
    b11_propensity_complete(kernel, plug, rep)
    return rep


# ═══ B12 ═════════════════════════════════════════════════════════════════════

def inj_b12(nodes, edges, injected: bool) -> Report:
    extra = []
    if injected:
        extra = [_tmp_source(
            "class K:\n"
            "    def __init__(self):\n"
            "        self.score = 0.0\n"
            "        self.global_weight = 1.0\n"
            "    def rank_score(self, ds):\n"
            "        return sum(ds) / len(ds)\n"
        )]
    rep = _rep()
    b12_no_global_scalar(rep, extra_sources=extra)
    return rep


# ═══ B13 ═════════════════════════════════════════════════════════════════════

def inj_b13(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        kernel._dirs["DX"] = Direction(  # noqa: SLF001
            did="DX", rank=2, payload=(frozenset(), frozenset()),
            witness=("DX",), origin=ORIGIN_SPLIT, parent=kernel.root.did,
        )
    rep = _rep()
    b13_rank_well_founded(kernel, rep)
    return rep


def inj_b13_two_parents(nodes, edges, injected: bool) -> Report:
    """`B13` 的**第二条**判据：**树性**（每方向恰好一个父）。

    注入的是**最贴近判据的那条边** —— `_children` 是唯一写「父 → 子」的地方，
    所以把一个已有的子方向**再挂到第二个父**下面，就造出了一个 **DAG**：

        语义入度  1 → 2

    ⚠️ **这条注入是实测挑出来的**：改之前 `B13` 对这种结构**照样报「是」**
       （`outputs/_probe_b13_gap.py` 的读数），而 `§10.2 D` 的持久化路线
       正押在「入度 = 1」上 ⇒ 前提**没有任何检查守着**。

    ⚠️ **注入必须造在「父→子」这条边上，不能只改 `parent` 字段**：
       只改 `parent` 会让两条边**打架**（那是另一组断言），
       而入度本身没变 —— 那就测不到树性。
    """
    kernel, _ = build_keyset(nodes)
    if injected:
        parents = [d for d in kernel.all_directions() if kernel.children_of(d)]
        if len(parents) < 2:
            # 只有一个内部父 ⇒ 挂不出「第二个父」⇒ 做不出注入（同 `run_tests` 的守卫）
            raise CannotBuild(f"内部父方向只有 {len(parents)} 个 ⇒ 造不出「第二个父」")
        p1, p2 = parents[0], parents[1]
        victim = kernel.children_of(p2)[0]
        kernel._children[p1.did] = tuple(kernel._children[p1.did]) + (victim.did,)  # noqa: SLF001
    rep = _rep()
    b13_rank_well_founded(kernel, rep)
    return rep


# ═══ B14 ═════════════════════════════════════════════════════════════════════

def inj_b14(nodes, edges, injected: bool) -> Report:
    extra = []
    if injected:
        extra = [_tmp_source(
            "from ldv.plugins.keyset import KeysetPlugin\n"
            "\n"
            "def sneak(params):\n"
            "    params.exogenous['outermost_intent'] = '改了'\n"
            "    return KeysetPlugin\n"
        )]
    rep = _rep()
    b14_exogenous_boundary(rep, extra_sources=extra)
    return rep


# ═══ B15 ═════════════════════════════════════════════════════════════════════

class _SplitOnOverflow(Kernel):
    """结构注入：**边插边长**（B-tree 的上溢分裂）——

        方向攒够 `CAP` 项才劈，而且劈的时候只看到**当时已经有的成员**
        （没满就返回 `()`，并且**不标记已展开**）

    于是「先来的是谁」决定第一次分组 ⇒ 同数据、不同历史 ⇒ 不同结构。

    ⚠️ 这条注入是**实测挑出来的**。另外两种「看起来该红」的改法都不红：

        · 去掉 `build()` 的预装  ⇒ 根第一次展开只看到 1 项 ⇒ 根变叶，
          整棵树**退化成一个方向**；三种顺序仍然「同构」⇒ B15 照样绿。
          （正因为这个盲区，`B15` 现在加了一道非退化前提。）
        · `expand()` 劈之前不排序 ⇒ 同一集合的迭代序一致 ⇒ 看不出差别。

    只有「攒够才劈」真的让先来后到决定分组。
    """

    CAP = 6

    def build(self, root_payload: Any):
        root = super().build(root_payload)
        self._members[root.did] = set()        # 根从空开始，随插入长大
        return root

    def expand(self, d: Direction) -> tuple[Direction, ...]:
        if d.did in self._expanded:
            return self.children_of(d)
        if len(self._members.get(d.did, ())) < self.CAP:
            return ()                          # 未满：不劈，也**不标记已展开**
        return super().expand(d)


def inj_b15(nodes, edges, injected: bool) -> Report:
    def make() -> Kernel:
        plug = KeysetPlugin()
        k = (_SplitOnOverflow if injected else Kernel)(plug, make_items(nodes))
        k.build(plug.merge([]))
        return k

    rep = _rep()
    b15_reproducible(make, sorted(nodes), rep)
    return rep


# ═══ B16 ═════════════════════════════════════════════════════════════════════

class _WorstChild(Kernel):
    """健全性注入：插入时选**代价最大**的子方向（本该选最小）。

    这不是硬凑的坏代码 —— 它是「分配那一步写反了」的形态，
    而分配正是 `members ⊆ 覆盖` 唯一可能被破坏的地方。

    ⚠️ 这条注入同时打红 `B1`（40 条查询下）。**那是对的**：两个检查在这一点上重叠。
       它证明的是 `B16` **不看查询集** —— 把查询集缩到 3 条，`B1` 就绿了，`B16` 照红。
    """

    def insert(self, item_id: str):
        if item_id not in self.items:
            raise KeyError(item_id)
        path: list[str] = []
        d = self.root
        while True:
            path.append(d.did)
            kids = self.expand(d)
            if not kids:
                break
            nxt = max(kids, key=lambda k: (call_penalty(self.plugin, k, self.items[item_id]),
                                           k.did))
            d = nxt
        self._members.setdefault(d.did, set()).add(item_id)
        for did in path:
            self._members.setdefault(did, set()).add(item_id)
        self._path[item_id] = tuple(path)
        return tuple(path)


class _NoRefusal(Kernel):
    """`B16` **维护路径**那条判据的注入：**关掉两条出路**。

        §10.2 出路 (1) 根记账        `insert` 先问 `命中(根, {x})`，只有「否」才不塞进结构
        §10.2 出路 (4) 项留在父方向   每个子方向都**证明**不收它 ⇒ 停在父这一层

    两者都走 `_refuses` ⇒ 一个 override 同时关掉，`insert` 退化成**改前**的行为：
    不看 `命中`，按最小代价一路塞到底。

    ⚠️ **必须用方向 C（`sequence`），不能用 A**：`keyset` 的劈开是**结构上**完备的
       （`x` 满足父 ⇒ 要么有 `best` 落子 1、要么没有落子 2），两条出路**从不触发**
       ⇒ 关掉它们什么也不改，注入红不了。
       `sequence` 是 k 叉，父的前缀后面那个符号来自**无穷字母表** ⇒ 两条出路真的会触发。

    ⚠️ **不能用「选代价最大的子方向」那种注入**（`_WorstChild` 的形态）：实测在维护
       路径上它会让方向数**指数增长**（36 项跑到 100 万个方向），是病态而不是缺陷。
       关出路就够 —— 病灶清楚、代价有界，而且**正好**是这一行要守的那件事。
    """

    def _refuses(self, d: Any, item_id: str) -> bool:  # noqa: ARG002
        return False


def inj_b16(nodes, edges, injected: bool) -> Report:
    plug = KeysetPlugin()
    k = (_WorstChild if injected else Kernel)(plug, make_items(nodes))
    k.build(plug.merge([]))
    for nid in sorted(nodes):
        k.insert(nid)
    rep = _rep()
    b16_members_covered(k, coverage_of("keyset", nodes, edges), rep, path="批建")
    return rep


def inj_b16_maintenance(nodes, edges, injected: bool) -> Report:
    """`B16` 的**第二条**判据（维护路径）：与批建那条**各判一次、各注入一次**。

    ⚠️ 为什么不能只跑一条：两条路径**不是同一件事** ——
       一次建完时，子方向的 payload 从**当时完整的**成员集算出来 ⇒ 天然盖得住；
       维护时成员是**后来才长起来的**，payload 在劈开时就冻结了 ⇒ 盖不住。
       「只跑一条路」与「两条路都跑」在汇总里长得**一模一样**，所以两条各占一行。

    ⚠️ 也不能只注入一条：批建那行的注入（`_WorstChild`）在维护路径上**不红** ——
       维护路径的问题**不是「分配写反了」，是「后来的项没人接」**。同一句话，
       两条路径上的形态不同 ⇒ 两条注入也不共用。
    """
    from ldv.plugins.sequence import SequencePlugin

    all_items = make_items(nodes)
    ids = sorted(nodes)
    init = ids[:6]                                  # 与 `run_checks.MAINT_INIT` 一致
    plug = SequencePlugin(sequences(nodes, edges))
    k = (_NoRefusal if injected else Kernel)(plug, {i: all_items[i] for i in init})
    k.build(frozenset({()}))                        # 根 = 空前缀：覆盖一切
    for i in init:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    rep = _rep()
    b16_members_covered(k, coverage_of("sequence", nodes, edges), rep, path="维护")
    return rep


# ═══ 第三条路径（`cli` 切分）的两行 ══════════════════════════════════════════
#
# ⚠️ **为什么这两行各自要有注入**（不能拿批建 / 维护那两条代替）：
#    **装配是判据的一部分** —— 三条路径的批/维护比例不同
#    （一次建完 / 6:n−6 / n−12:12），结构不同 ⇒ ground truth 不同。
#    `inj_b16_maintenance` 的 docstring 已经把这条纪律说过一次：
#    「批建那行的注入在维护路径上**不红**」。
#
# ⚠️ 两条都**调 `run_checks.cli_split_kernel`**，不另抄一份装配 ——
#    抄的那一份会与生产那份各自演化，而症状不是报错、是**检查报绿**。


def inj_b4_cli_split(nodes, edges, injected: bool) -> Report:
    """`B4` 的**第三条**判据（`cli` 切分）。

    注入的是 **「子方向多出父没有的项」** —— 就是 2026-10-08 修掉的那个真缺陷
    （`_expand_inner` 重试分桶时没把新子方向记进锥 ⇒ `remove` 摘不干净）。
    这里手工把同一件事做出来：**只从父摘、不摘子**。

    ⚠️ 为什么**不**沿用 `inj_b4`（重叠）：那一条建的是**批建**内核，
       而这一行判的是 `cli` 切分的内核 —— 换装配就是换判据。
    """
    from ldv.run_checks import cli_split_kernel

    kernel, _cover, _rm = cli_split_kernel("keyset", nodes, edges)
    if injected:
        for d in kernel.all_directions():
            done = False
            for kid in kernel.children_of(d):
                mk = set(kernel.members_of(kid))
                if mk:
                    kernel._members[d.did].discard(sorted(mk)[0])   # noqa: SLF001
                    done = True
                    break
            if done:
                break
    rep = _rep()
    b4_split_is_partition(kernel, rep, path="cli 切分")
    return rep


def inj_b16_cli_split(nodes, edges, injected: bool) -> Report:
    """`B16` 的**第三条**判据（`cli` 切分）。

    ## ★ 为什么**不**沿用维护路径那条注入（`_NoRefusal`）—— 实测它**空转**

    先试的就是它（同一个病灶、另一条装配）。实测：**基线绿、注入也绿**。

        ✗ B16·cli 切分  基线=是  注入=是   ← 注入后没变红（是）

    理由是**装配比例**：`cli` 切分先建 **24** 项（36 − 12），24 项建出来的树
    已经足够深，后 12 项的**第一符号都已在树里** ⇒ 它们**本来就落对了地方**
    ⇒ 关掉两条出路**什么都不改**。而维护路径先建 **6** 项、树很浅，
    后 30 项才会撞上「没有子方向收它」那种情形。

    ⇒ 这正是本套件开头那条纪律的**第三个实例**（`B15` 两个、这里一个）：
      **「想当然的注入」与「有效的注入」长得一模一样**。
      注入必须**挑**，而挑不出来就说明这条判据的真实内容还没被说清。

    ## 换成「塞进一个盖不住它的方向」

    与 `inj_b4` 同形（直接动 `_members`，不走插件）—— 因为要注入的是
    **「成员 ⊆ 覆盖」这一条被破**，而不是「插件分配得不好」。
    取第一个 `x ∉ cover(d.payload)` 的方向 `d`，把 `x` 塞进它的成员里。

    ⚠️ 根方向天然过不了这一筛（`sequence` 的根 payload 是空前缀 ⇒ 覆盖一切）
       ⇒ 循环会跳过它，落在真正「盖不住」的方向上。
    """
    from ldv.run_checks import cli_split_kernel

    k, cover, _rm = cli_split_kernel("sequence", nodes, edges)
    if injected:
        placed = False
        for d in k.all_directions():
            cov = set(cover(d.payload))
            for x in sorted(k.items):
                if x not in cov:
                    k._members.setdefault(d.did, set()).add(x)      # noqa: SLF001
                    placed = True
                    break
            if placed:
                break
    rep = _rep()
    b16_members_covered(k, cover, rep, path="cli 切分")
    return rep


# ═══ B18 ═════════════════════════════════════════════════════════════════════

class _DropBucket:
    """`B18` 注入：**只报前两个桶**，把其余的桶丢掉。

    ⚠️ 为什么用方向 C 而不是 A：`keyset` 的二分是**结构上**不漏的
       （`x` 满足父 ⇒ 要么有 `best` 落子 1、要么没有落子 2，两半合起来恰好是父的覆盖）
       ⇒ 想让它漏只能造假 payload，而那会立刻撞上 `§K2` 的「两侧非空」。
       `sequence` 是 k 叉，**少报几个桶**就真的漏 —— 这也正是 §10.2 对 C 描述的那条机制
       （父的覆盖是 `{x : p ⊑ seq(x)}`，而子方向只枚举了**当时见过的符号**）。
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name

    def __getattr__(self, k: str) -> Any:
        return getattr(self.inner, k)

    def split(self, parent, items):  # noqa: ANN001, ANN201
        got = self.inner.split(parent, items)
        if got is None or len(got) <= 2:
            return got
        return got[:2]                      # ← 注入：丢掉其余的桶


def _drop_bucket_plugin(nodes, edges, injected: bool):
    from ldv.plugins.sequence import SequencePlugin

    seqs = sequences(nodes, edges)
    inner = SequencePlugin(seqs)
    return _DropBucket(inner) if injected else inner


def inj_b18(nodes, edges, injected: bool) -> Report:
    plug = _drop_bucket_plugin(nodes, edges, injected)
    k = Kernel(plug, make_items(nodes))
    k.build(frozenset({()}))
    for nid in sorted(nodes):
        k.insert(nid)
    cover = coverage_of("sequence", nodes, edges)
    obs = {f"sequence|batch": cover_leak_profile(k, cover)}
    # baseline 取**当前**文件 —— 批建路径三个方向都是 0 漏，所以任何一处漏都是「新增」。
    # ⚠️ 指纹必须一起给：`B18` 的第 0 条会先比语料指纹，对不上就**跳过**。
    #    注入验证要验的是**判据**，不是「跳过」—— 所以这里必须造出**真跑时会出现的**
    #    那种配置（基线冻在本语料上），否则验的是一个生产里不存在的形态。
    fp = corpus_fingerprint(nodes, edges)
    rep = _rep()
    b18_cover_leak_baseline(obs, rep, fp,
                            {BASELINE_FORMAT_KEY: BASELINE_FORMAT,
                             "基线": {corpus_key(fp): {
                                 "语料": fp,
                                 "条目": {"sequence|batch": {"漏项数": 0, "漏的对数": 0}}}}})
    return rep


# ═══ B19 ═════════════════════════════════════════════════════════════════════

class _SplitOffOne:
    """`B19` 注入：**每次只切掉一个**锚点，其余全给另一侧。

    ⇒ 两侧的覆盖**都不比父小**（环上任意非空子集都撑满整个环）
    ⇒ 每次展开都「没变细」，而链长 = 项数 − 1（**不靠平衡二分**）。

    ## ⚠️ 两条**走不通**的注入路，写在这里免得下一个人重走

        路一  「**一个**子退回父 payload」（**原注入**）
              ⇒ 另一侧的子仍然**严格更小** ⇒ 新谓词下**不算**没变细 ⇒ 注入不红
        路二  「**每个**子都退回父 payload」
              ⇒ 两侧的代价都是 0 ⇒ 内核把成员全判给**靠前**那个
              ⇒ 另一侧为空 ⇒ `§K2` 判「分不开」⇒ **这一层根本不建** ⇒ 判据「未展开」

    路二的根因值得记住：`reach` 的 `代价 == 0 ⟺ 项 ∈ payload`（**不是** ∈ 覆盖）。
    所以「两侧覆盖都 ⊇ 父」时，成员在两侧**都**是 0 分 ⇒ 必然平手。

    ⇒ 能红的形状只能是「**两侧代价可区分，但覆盖都不变小**」——
      这正是本类：一侧拿 1 个锚点、另一侧拿其余，各自的成员在自己那侧都是 0 分。
    """

    def __init__(self, inner):  # noqa: ANN001
        self.inner = inner
        self.name = inner.name

    def __getattr__(self, k):  # noqa: ANN201
        return getattr(self.inner, k)

    def split(self, parent, items):  # noqa: ANN001, ANN201
        ids = sorted(str(it["id"]) for it in items)
        if len(ids) < 2:
            return None
        return (frozenset({ids[0]}), frozenset(ids[1:]))


def _cycle(n: int = 16):
    """一个 `n` 项的**有向环**：`c00 → c01 → … → c{n-1} → c00`。

    ⚠️ **为什么 `B19` 的夹具必须是环、不能是链**（2026-10-07 改；实测见
       `outputs/_probe_b19_injection.py`）：

        链   反向锥逐级**严格嵌套** ⇒ 任何真子集的覆盖都严格更小
             ⇒ 「没变细」**按构造不可能出现** ⇒ 判据在这个夹具上**永远绿**
        环   每一项的反向锥都 = **整个环**（强连通）
             ⇒ 任意非空锚点集都撑满同一个覆盖 ⇒ 「没变细」可以**连续成立**

    ⇒ 旧夹具是一条 16 项的**链**，那是给**旧谓词**（`细化量 ≤ 0`）造的 ——
      旧谓词量的是「切得多**不匀**」，链上到处都是（实测 31/31）；
      新谓词量的是「有没有真**变细**」，链上一次都没有（实测 **0/31**）。
      **谓词换了聚合（`max` → `min`），夹具必须跟着换。** 两者是一对。
    """
    from ldv.corpus.loader import Node

    ids = [f"c{i:02d}" for i in range(n)]
    nodes = {i: Node(id=i, fields={"type": i}, keys=frozenset({i})) for i in ids}
    edges = {ids[k]: frozenset({ids[(k + 1) % n]}) for k in range(n)}
    return nodes, edges


def inj_b19(nodes, edges, injected: bool) -> Report:
    """**基线绿 + 注入红**（同一个环、同一个方向、同一个 oracle，只有 `split` 不同）：

        基线（原插件，平衡二分）      最长没变细连续段 = log2(16) = **4**  < 10 ⇒ 绿
        注入（每次只切一个）          最长没变细连续段 = 16 − 1   = **15** ≥ 10 ⇒ 红
    """
    from ldv.plugins.reach import ReachPlugin

    dn, de = _cycle(16)
    inner = ReachPlugin(de)
    plug = _SplitOffOne(inner) if injected else inner
    k = Kernel(plug, make_items(dn))
    k.build(frozenset(dn))
    for nid in sorted(dn):
        k.insert(nid)
    rep = _rep()
    b19_progress_guard(progress_profile(k, coverage_of("reach", dn, de)), rep)
    return rep


# ═══ B17 ═════════════════════════════════════════════════════════════════════

class _GiveUpEarly(KeysetPlugin):
    """`§K2` 判空注入：**能分也判分不开** —— 小组（≤ `LIMIT` 项）直接放弃。

    这不是硬凑的坏代码，它是「判据写得比实际能力弱」的形态：
    插件**有能力**分开那一组（组里混着不同键集），却**谎报**「分不开」。
    ⇒ 那些项只能挤进同一个叶 ⇒ 叶里混进多个不可分等价类。

    ⚠️ **为什么不用「内核平手时乱定序」当注入**：试过了，**不红**。
       `expand()` 的分配用 `(代价, 候选序号)` 取最小 —— 平手判给**靠前**那个，
       而候选的顺序由插件自己的返回顺序定，内核**不看** `did`。
       所以「让 `did` 变」动不了这个判据。
       **(b) 那一半只能被「代价依赖语义之外的东西」打破**，
       而那个形态在**合环图**上才出现（见 `run_tests.test_equivalence` ⑤）。
    """

    LIMIT = 4

    def split(self, parent, items):  # noqa: ANN001, ANN201
        if len(items) <= self.LIMIT:
            return None                     # 谎报：能分也**声明**分不开
        return super().split(parent, items)


def inj_b17(nodes, edges, injected: bool) -> Report:
    from ldv.checks._fixtures import equiv_classes
    from ldv.checks.equivalence import b17_leaf_is_equivalence_class

    plug = _GiveUpEarly() if injected else KeysetPlugin()
    k = Kernel(plug, make_items(nodes))
    k.build(plug.merge([]))
    for nid in sorted(nodes):
        k.insert(nid)
    rep = _rep()
    b17_leaf_is_equivalence_class(k, equiv_classes("keyset", nodes, edges), rep, "keyset")
    return rep


# ═══ B20 ═════════════════════════════════════════════════════════════════════
#
# `B20` 的谓词是**精确整数**判据：`count_child[l]·|父| == count_parent[l]·|子|`
# ⇒ 「空转」= 子方向是父方向的**等比子样本**。
#
# 这条判据有个副作用，它决定了夹具**只能怎么造**：
#
#     **父一旦是纯标签**，子必然也是纯标签 ⇒ 那一刀起往下**每一刀都空转**
#     ⇒ 真插件切出纯标签子树之后，**基线自己就红** ⇒ 对照失效
#
# 实测过、被否掉的三种夹具（都栽在这一条，见 `outputs/_probe_b20_injection.py`）：
#
#     成对夹具（每对 1×L0 + 1×L1，靠 `pair` 键切）
#         基线先挑 `type=L0` ⇒ 切出纯标签子树 ⇒ 基线自己红；
#         想让基线不挑 `type`，就得把标签做成不均衡，而不均衡又凑不出等比块
#     纯标签子树做成「键完全相同」的叶子
#         真插件在那里 `candidates` 为空 ⇒ 确实不空转；
#         但注入方**也没有键可用了** ⇒ 注入切不动
#     标签取**高位**位
#         前几刀的子方向与父**同分布**（各标签等比稀释）⇒ 基线自己就空转
#
# ⇒ 结论：**「同一个夹具 + 真插件」当基线，与「在同一个夹具上做一次标签盲的
#   切分」这两件事互斥** —— 基线要不红，就必须永远切不出纯标签方向；
#   而「永远切不出纯标签方向」就等于「标签与切分轴正交」，
#   正交又正是注入方要利用的东西。
#
# ⇒ 所以夹具、插件、树**全不动**，只换 **oracle**。`B20` 判的是
#   「展开有没有在**标签**上产出东西」，能让它红的本来就不是某一种 `split`，
#   而是「标签与切分轴无关」这件事本身。


def _labeled_bits(bits: int, label_bits: int):
    """`2**bits` 项；每项带 `bits` 个位键 `b{k}`；`type` 按 `label_bits` 取低若干位。

    `b{k}` 的每个取值 = 「下标 ≡ 某个值 mod 2^{k+1}」的那批项 ⇒
    `KeysetPlugin` 选「最接近 1/2 的键」，所有位键都在 1/2 ⇒ 它**逐位二分**，
    树是深度 `bits` 的平衡二叉树。

    ⚠️ 键集里**始终**带 `type=L0…`（两份 oracle 都认得出这是标签轴，
       见 `_fixtures.label_bearing`）—— 这正是要验的东西：判据的**适用范围**
       不该因为标签怎么指派而变。
    """
    from ldv.corpus.loader import Node

    mod = 1 << label_bits
    nodes: dict[str, Node] = {}
    for i in range(1 << bits):
        nid = f"n{i:04d}"
        keys = {f"type=L{i % mod}"} | {f"b{k}={((i >> k) & 1)}" for k in range(bits)}
        nodes[nid] = Node(id=nid, fields={"type": f"L{i % mod}", "i": i},
                          keys=frozenset(keys))
    return nodes, {nid: frozenset() for nid in nodes}


def inj_b20(nodes, edges, injected: bool) -> Report:
    """**基线绿 + 注入红**（同一个夹具、同一个插件、同一棵树 —— 只有 oracle 不同）：

        基线  `type = L{i mod 64}`（低 6 位）     最长语义空转连续段 =  **4**  < 10 ⇒ 绿
        注入  `type = L{popcount(i) mod 2}`      最长语义空转连续段 = **11** ≥ 10 ⇒ 红

    注入那版为什么**每一刀都空转**：位键 `b{k}` 的每个取值都是
    「下标 ≡ 某个值 mod 2^{k+1}」，这种集合里剩下的自由位至少还有一个，
    而「自由位的 popcount 奇偶」正好各占一半 ⇒ 任何方向都恰好 50/50
    ⇒ 子与父**精确同分布** ⇒ 空转。

    ⚠️ 注入那版的最长连续段 = **树深 − 1**：最底下那一刀是「2 项父 → 2 个单项子」，
       单项子的分布与 50/50 的父**不同分布** ⇒ 那一刀不空转，链在它那里断掉。
       所以树深必须 ≥ 12 才能把连续段推到 ≥ 10 —— 这是 `bits = 12` 的理由。
    """
    from ldv.checks._fixtures import label_bearing, labels as _labels
    from ldv.checks.refinement import b20_semantic_refinement, refinement_profile

    dn, _de = _labeled_bits(12, 6)
    if injected:
        for nd in dn.values():
            nd.fields["type"] = f"L{bin(nd.fields['i']).count('1') % 2}"

    plug = KeysetPlugin()
    k = Kernel(plug, make_items(dn))
    k.build(plug.merge([]))
    for nid in sorted(dn):
        k.insert(nid)
    rep = _rep()
    prof = refinement_profile(k, _labels(dn))
    b20_semantic_refinement(prof, rep, judged=label_bearing("keyset", dn))
    return rep


# ═══ B11b ════════════════════════════════════════════════════════════════════
#: B11 有**两条**判据（完备 + 位置敏感），所以注入也要两条。
#: 只验「缺倾向会红」的话，把曝光模型退回均匀**不会有任何东西变红** ——
#: 而那正是最容易被悄悄改回去的一处。

def inj_b11_uniform(nodes, edges, injected: bool) -> Report:
    """把曝光模型退回「按个数」：所有位次的倾向都相同。"""
    from ldv.core.interfaces import WeightedRecord

    orig = Kernel.record_usage
    if injected:
        def uniform(self, d, interaction):
            sig = self.plugin.signal(d, interaction)
            if sig is None:
                return None
            rec = WeightedRecord(did=d.did, outcome=float(sig.outcome), propensity=1.0)
            self.usage.append(rec)
            return rec

        Kernel.record_usage = uniform
    try:
        kernel, plug = build_keyset(nodes)
        rep = _rep()
        b11_propensity_complete(kernel, plug, rep)
        return rep
    finally:
        Kernel.record_usage = orig


# ═══ 度量也要能变（不是 B 系列） ══════════════════════════════════════════════
#
# B 系列是**判据**：基线绿、注入红。
# 「增量 ≡ 全量」（`checks/divergence.py`）是**度量**：它没有红不红，只有读数。
# ⇒ 它的注入验证问的是另一件事：**读数会不会跟着系统变？**
#    一个永远报同一个数的度量，和一条永远绿的检查一样没有信息量。
#
# ⚠️ 这条注入在「同构」那一列上**看不出来** —— 基线与注入**都是不同构**。
#    它只在**尺度**那一列上露出来（方向数 / 叶容量）。这正是「度量必须报多个数」
#    的理由：只报一个布尔，这个缺陷就被度量放过去了。


class _LeafIsTerminal(Kernel):
    """注入：把「分不开」写成**永久标记** —— 「一个布尔看不出来」的那种形状。

    一旦某个方向被判过「分不开」，就**永远**是叶，即使成员集后来变了。
    ⇒ 树只在第一批项上长过层，之后插进来的项全堆进已有叶。
    """

    def expand(self, d: Any):  # noqa: ANN201
        if d.did in self._children:
            return self.children_of(d)
        if d.did in self._tried:            # ← 永久：不再随成员集变化重试
            return ()
        return super().expand(d)


def metric_scale_matches(nodes: Any, edges: Any, injected: bool) -> bool:
    """「增量与全量的**尺度**是否逐项相同」—— 基线 True，注入 False。"""
    from ldv.checks.divergence import divergence_profile

    cls = _LeafIsTerminal if injected else Kernel
    all_items = make_items(nodes)

    def build(subset_ids: list[str]):
        plug = KeysetPlugin()
        sub = sorted(set(subset_ids))
        k = cls(plug, {i: all_items[i] for i in sub})
        k.build(plug.merge([]))
        for i in sub:
            k.insert(i)
        return k, plug

    prof = divergence_profile(build, nodes, sorted(nodes), ks=(2, 6, 12))
    f = prof["全量"]
    return all((r["方向数"], r["最大叶容量"]) == (f["方向数"], f["最大叶容量"])
               for r in prof["各 k"].values())


# 「分批维护不许改变**分辨率**」—— 这是 `test_emergence` ③ 收窄之后留下的那条判据。
# ⚠️ 只查**叶容量**，不查方向数：`§I4` 允许插件定扇出之后，扇出在**首次展开时定形**
#    （§K3：已分配的项不许挪走）⇒ 「首次展开时组里有几个符号」取决于初始批次
#    ⇒ **方向数（记账）会变**。叶容量才是分辨率（§7.1）。
# 这条注入证明**收窄之后判据仍然抓得到那处旧缺陷**（叶是终态 ⇒ 项全堆进叶 ⇒ 叶容量爆）。

def metric_resolution_stable(nodes: Any, edges: Any, injected: bool) -> bool:
    """「分批维护是否不改变**最大叶容量**」—— 基线 True，注入 False。"""
    from ldv.checks._fixtures import sequences
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin

    cls = _LeafIsTerminal if injected else Kernel
    all_items = make_items(nodes)
    ids = sorted(nodes)

    cases = {
        "keyset": (lambda: KeysetPlugin(), lambda p: p.merge([])),
        "reach": (lambda: ReachPlugin(edges), lambda p: frozenset(nodes)),
        "sequence": (lambda: SequencePlugin(sequences(nodes, edges)), lambda p: frozenset({()})),
    }

    def leaf_cap(k: Any) -> int:
        return max((len(k.members_of(d)) for d in k.all_directions()
                    if not k.children_of(d)), default=0)

    for mk_plug, mk_root in cases.values():
        full_plug = mk_plug()
        full = cls(full_plug, {i: all_items[i] for i in ids})
        full.build(mk_root(full_plug))
        for i in ids:
            full.insert(i)
        inc_plug = mk_plug()
        inc = cls(inc_plug, {i: all_items[i] for i in ids[:6]})
        inc.build(mk_root(inc_plug))
        for i in ids[:6]:
            inc.insert(i)
        for i in ids[6:]:
            inc.insert(i, all_items[i])
        if leaf_cap(inc) != leaf_cap(full):
            return False
    return True


# 「覆盖嵌套」也是**度量**（`§I4` 带上父方向买到的那个东西）。
# 注入：A 的 `劈开` **不续父的 `forb`** —— 于是子方向重新收下父已排除的项。
# 基线 `覆盖(子) ⊆ 覆盖(父)`，注入后**越界**（实测 0 → 14 次）。

class _ForgetsParentForbid(KeysetPlugin):
    """注入：子方向把父的 `forb` 丢了（= 拿不到父方向时的必然结果）。"""

    def split(self, parent, items):  # noqa: ANN001, ANN201
        keysets = [frozenset(it["keys"]) for it in items]
        common = frozenset.intersection(*keysets) if keysets else frozenset()
        universe = frozenset().union(*keysets) if keysets else frozenset()
        cand = sorted(universe - common)
        if not cand:
            return None
        n = len(items)
        best = min(cand, key=lambda k: (abs(sum(1 for ks in keysets if k in ks) / n - 0.5), k))
        return ((common | {best}, frozenset()), (common, frozenset({best})))


def metric_cover_nesting(nodes: Any, edges: Any, injected: bool) -> bool:
    """「覆盖嵌套是否成立」—— 基线 True，注入 False。"""
    from ldv.checks.contract import cover_nesting_profile

    plug = _ForgetsParentForbid() if injected else KeysetPlugin()
    k = Kernel(plug, make_items(nodes))
    k.build(plug.merge([]))
    for i in sorted(nodes):
        k.insert(i)
    from ldv.checks._fixtures import keyset_queries

    return cover_nesting_profile(k, plug, keyset_queries(nodes))["越界次数"] == 0


# 「健全性」也是**度量**，而且它**必须在维护路径上量** —— 这是本设计里
# 「空转与通过长得一模一样」在**流程**上的那一例。
#
# 注入：A 的 `劈开` 把「当前成员的键集之交」（`common`）掺进子方向的 `req`。
#
# ⚠️ **这条注入在「一次建完」那条路上看不出来** —— 每个方向第一次展开时成员集
#    就是完整的，`common` 一定盖得住它们（实测 0/159 与基线相同）。
#    它只在**维护路径**上露出来（实测 123/224）：成员后来才长起来，
#    而 `req` 在劈开时就**冻结**了 ⇒ 子方向收不住「它没见过的项」。
# ⇒ 所以这个度量函数**只量维护路径**。若它去量一次建完，基线与注入都是绿的，
#    这条注入验证会**通过而毫无信息量** —— 那正是它要防的东西。

class _CommonInReq(KeysetPlugin):
    """注入：子方向的 `req` 掺进「当前成员」算出来的 `common`。"""

    def split(self, parent, items):  # noqa: ANN001, ANN201
        p_req, p_forb = parent.payload
        keysets = [frozenset(it["keys"]) for it in items]
        common = frozenset.intersection(*keysets) if keysets else frozenset()
        universe = frozenset().union(*keysets) if keysets else frozenset()
        cand = sorted(universe - common)
        if not cand:
            return None
        n = len(items)
        best = min(cand, key=lambda k: (abs(sum(1 for ks in keysets if k in ks) / n - 0.5), k))
        req = p_req | common                       # ← 注入：掺进 common
        return ((req | {best}, p_forb), (req, p_forb | {best}))


def metric_soundness(nodes: Any, edges: Any, injected: bool) -> bool:
    """「健全性在**维护路径**上是否成立」—— 基线 True，注入 False。"""
    plug = _CommonInReq() if injected else KeysetPlugin()
    all_items = make_items(nodes)
    ids = sorted(nodes)
    k = Kernel(plug, {i: all_items[i] for i in ids[:6]})
    k.build(plug.merge([]))
    for i in ids[:6]:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    return soundness_profile(k, coverage_of("keyset", nodes, edges))["越界成员数"] == 0


# 「覆盖不漏」（§1 表**第四行**，⬜ 2026-10-07 已降级）也是度量。注入：`sequence` 的 `劈开`
# **少报桶** —— 父的覆盖里那些「下一个符号没被枚举到」的项，任何子方向都收不住。
# ⇒ 这正是 §10.2 对 C 描述的那条机制。
# ⚠️ 但它**不是**「与健全性并列的另一条独立机制」：实测漏项与滞留项是**同一批**
#    （16 == 16，双向差 0，见 `outputs/_probe_leak_vs_stay.py`）⇒ 两者是同一条件的两种处置。
#    这里注入的「少报桶」**同时**造出漏与滞留，正是这一点的直接证据。

def metric_cover_leak(nodes: Any, edges: Any, injected: bool) -> bool:
    """「覆盖不漏是否成立」—— 基线 True，注入 False。"""
    plug = _drop_bucket_plugin(nodes, edges, injected)
    k = Kernel(plug, make_items(nodes))
    k.build(frozenset({()}))
    for nid in sorted(nodes):
        k.insert(nid)
    return cover_leak_profile(k, coverage_of("sequence", nodes, edges))["漏项数"] == 0


# 「细化量」也是度量：**每次展开的覆盖细化量**。注入：每次只切掉一个锚点。
# ⚠️ 夹具是**环**不是链 —— 链上「没变细」按构造不可能出现（见 `_cycle`）。
#    读数在链上恒为 0，注入也动不了它 ⇒ 那会是一条**假绿**的度量对照。

def metric_progress(nodes: Any, edges: Any, injected: bool) -> bool:
    """「最长没变细连续段」这个读数 —— 基线 **4**（平衡二分的深度 `log2(16)`），注入 **15**。"""
    from ldv.plugins.reach import ReachPlugin

    dn, de = _cycle(16)
    inner = ReachPlugin(de)
    plug = _SplitOffOne(inner) if injected else inner
    k = Kernel(plug, make_items(dn))
    k.build(frozenset(dn))
    for nid in sorted(dn):
        k.insert(nid)
    return progress_profile(k, coverage_of("reach", dn, de))["最长没变细连续段"] == 4


# ═══ §A1–§A7（流程 E · 抽象层）════════════════════════════════════════════════
#
# 判据本体在 `checks/abstraction.py`，算法在 `core/views.py`。
# 注入的形态都一样：**在真链路上装出视图集合，然后只动那个视图集合的一处**。
#
# ## ⚠️ 这里的 `P` / `E` 是**测试自己写的** —— 这不违反 §K9
#
# §K9 / `B14` 禁的是「**系统**替人猜 `P` / `E`」：生产路径只能从
# `ldv/checks/view_spec.json` 读（见 `run_checks.view_report`，读不到就**跳过**）。
# **测试就是人的代言**，所以测试里构造 `P` / `E` 不但允许，而且是**必须**的 ——
# 否则这七条判据的注入就挂在「那个配置文件在不在」上：
# 文件一没，基线就塌，注入验证报出来的是**配置缺失**，不是**判据能不能红**。
#
# ⇒ 分工写清楚，免得下一个人以为哪边漏了：
#
#     注入验证（本段）          「判据能不能红」        —— 自带 `P` / `E` / 读数声明 / 账
#     `run_tests.test_view_spec_file`  「committed 的声明成不成立」—— 读那个文件
#
# ## `P` / `E` 取什么
#
#     `E = 父→子`（内核自己的树边）
#     `P = {U}`（人**没有先验意见**：所有方向同属一组）
#
# ⚠️ **`E` 的方向**：`E⁻¹(B)` 是「**能走到** `B` 的方向」，所以 `(父, 子) ∈ E`
#    读作「父能走到子」。写反了**不会报错**，只会得到一个「看着也在跑、
#    答案是另一个划分」的实现（`core/views._preimage` 的 ⚠️ 专门记了这条）。
#
# `P = {U}` 是 `(P, E)` 里信息量**最低**的一档 —— 正因为如此它最能说明
# 「`Q` 唯一 ⇒ 视图集合不用挑」：一点先验都不给，答案照样是唯一的。


def _view_chain(nodes: Any, edges: Any, head: int | None = None):
    """从**真内核**读出方向与树边，装成 `(spec, kernel, plugin, cover)`。

    `head` 只取前 `head` 个方向 —— 给 `§A3` 那条**走暴力 oracle** 的注入用：
    `coarser_stable_exists` 的候选数是 `∏ Bell(组内 Q 块数)`，全量 25 个方向时
    `Bell(25) ≈ 4.6e18` ⇒ 撞上限**跳过**（那是对的，见 `core/views._bell`）。
    想让它真的**搜完**并且**搜出结果**，`universe` 必须小到 `Bell` 撑得住。
    """
    kernel, plugin = build_keyset(nodes)
    dirs = kernel.all_directions()
    U = tuple(d.did for d in dirs)
    if head is not None:
        U = U[:head]
    keep = set(U)
    rel = frozenset((d.did, c.did) for d in dirs if d.did in keep
                    for c in kernel.children_of(d) if c.did in keep)
    spec = ViewSpec(universe=U, partition=(frozenset(U),), relation=rel)
    return spec, kernel, plugin, coverage_of("keyset", nodes, edges)


def _view_report(
    vs: ViewSet, *, kernel: Any = None, cover: Any = None, plugin: Any = None,
    decls: list[dict[str, Any]] | None = None,
    entries: list[tuple[str, str, str, str]] | None = None,
    a6kw: dict[str, Any] | None = None,
    a7: dict[str, Any] | None = None,
    a8: dict[str, Any] | None = None,
) -> Report:
    """跑视图侧的判据。`kernel` / `decls` / `entries` / `a6kw` / `a7` 不给就**不跑**那一条。

    ⚠️ 不给就**不跑**，而不是「跑成跳过」：注入要验的是**那一条判据**，
       把别的判据也塞进来会让「谁红的」分不清。驱动只读 `code` 那一条。
    """
    rep = _rep()
    a1_soundness(vs, rep)
    a2_stable(vs.spec, vs.q, rep)
    a3_coarsest(vs.spec, vs.q, rep)
    if kernel is not None and decls is not None:
        a4_category(view_parts(vs.q, lambda d: subtree_of(kernel, d)),
                    reading_ctx(kernel, vs.spec), decls, rep)
    if kernel is not None and entries is not None:
        a5_ledger(entries, vs,
                  lambda did, item, kind: warranted_of(kernel, did, item, kind),
                  frozenset(kernel.items), rep)
    if a6kw is not None:
        with tempfile.TemporaryDirectory() as td:
            a6_roundtrip(vs, kernel, cover, plugin, rep,
                         path=Path(td) / "views.json", **a6kw)
    if a7 is not None:
        a7_propagate(a7["old_q"], vs.spec, rep, new_q=a7.get("new_q"))
    if a8 is not None:
        a8_maintenance(a8["fixtures"], vs.spec, rep, overrides=a8.get("overrides"))
    return rep


def inj_a1(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A1` 注入：把某张视图的**具体化掐掉一项**。

        基线（真最粗稳定细化 + 真 `合并`）  具体化 ⊇ 声明收着的 ⇒ **绿**
        注入（掐掉一个成员）                那一项没了       ⇒ **红**

    ⚠️ 掐的必须是 `covered` 里的项 —— 掐一个本来就不在 `covered` 里的项
       **不会**造成违规（`§A1` 只查「少了」，不查「多了」）。
       所以这里从 `covered` 里挑，挑不到就**报错**（不是静默跳过）：
       那说明基线本身已经不对了，是另一回事。

    ⚠️ 这条注入是**判据的独立内容**的直接证据：同样的内核、同样的 `P`/`E`，
       `B16` 在这一处**照样绿**（它逐方向判，不看合并）—— 见
       `run_tests.test_view_a1_not_implied_by_b16`。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    if injected:
        host = max(vs.views, key=lambda v: (len(v.covered), v.vid))
        if not host.covered:
            raise AssertionError("基线里没有任何视图声明收着原始项 ⇒ 掐一项掐不出违规")
        victim = sorted(host.covered)[0]
        vs = replace(vs, views=tuple(
            replace(v, concretization=v.concretization - {victim})
            if v.vid == host.vid else v for v in vs.views))
    return _view_report(vs)


def inj_a2(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A2` 注入：把 `Q` **粗化一格**（合并两块）⇒ 有一块跨在 `E⁻¹(B₂)` 内外 ⇒ 红。

    ⚠️ **合并哪两块不影响结论** —— 实测在全量 spec 上「稳定合并对」是**空集**
       （8 块两两合并、28 对，没有一对稳定）。⇒ 这条注入不是**挑出来的**，
       是**任意一对**都红。挑出来的注入只能证明「存在一个红」，
       证明不了「判据真的在判」。

    ⚠️ `§A3` 在这条注入上也红（不稳定 ⇒ 不是那个唯一解）。**那是对的**，
       两条的**重叠**与各自**额外的覆盖面**写在 `a3_coarsest` 的 docstring 里。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    if injected:
        if len(vs.q) < 2:
            raise CannotBuild(f"基线 `Q` 只有 {len(vs.q)} 块 ⇒ 「合并两块」做不出来")
        vs = replace(vs, q=partition_of([vs.q[0] | vs.q[1]] + list(vs.q[2:])))
    return _view_report(vs)


def inj_a3(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A3` 注入**甲**：喂一个**未到不动点**的 `Q`（`Q := P`，迭代 **0 轮**）。

        基线（真不动点）      没有更粗的稳定划分 ⇒ **绿**
        注入（`Q := P`）      `{U}` 里混着叶方向 ⇒ 它**自己就不稳定** ⇒ **红**

    设计稿 §9 那张表的 `§A3` 行写的就是这一条（「喂一个未到不动点的 `Q`」）。

    ⚠️ 它走的是 `§A3` 的**第二条**红（`Q` 自己不稳定），而这条**`§A2` 也红**。
       能验出 `§A3` **独立内容**的是下一条（`inj_a3_not_coarsest`）。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    if injected:
        vs = replace(vs, q=partition_of(spec.partition))
    return _view_report(vs)


def inj_a3_not_coarsest(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A3` 注入**乙**：喂一个**稳定但更细**的 `Q`（离散划分）⇒ 走「存在更粗的稳定划分」。

    ## ★ 为什么非有这一条不可

    设计稿 §9 那张表里，`§A3` 的**红条件**列写的是「存在更粗的稳定划分 ⇒ 红」，
    而**注入**列写的是「未到不动点」—— 那一条走的是**另一个**红分支
    （`Q` 自己不稳定），`§A2` 也红。⇒ 照字面只配那一条注入的话，
    **表里声明的那个红条件一次都没被验过**，而套件照样报「全绿」。

    能且只能验到那个分支的形态是「**稳定**、但**不是最粗**」：

        离散划分**总是稳定**（小定理：单元素块对任何 `E⁻¹(B)` 要么整个在里面、
        要么整个在外面）⇒ `§A2` **绿**，而 `§A3` **红**。

    ⇒ 这条注入是「`§A3` 有独立于 `§A2` 的内容」的**唯一**证据。

    ## 为什么用受限 `universe`

    暴力 oracle 要**搜完**才能给出「有」；`Bell(25) ≈ 4.6e18` 会撞上限
    （那时报**跳过**，不是红 —— 那是对的，但不是这条注入要验的东西）。
    ⇒ 取前 6 个方向：`|Q| = 4`、`Bell(4) = 15` ⇒ 毫秒级搜完。
    ⚠️ 若基线的 `Q` 恰好**就是**离散划分，这条注入**红不了**（两个划分重合）
       ⇒ 那种情况下**报错**，不静默放过。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges, head=6)
    vs = build_views(kernel, spec, cover, plugin)
    if len(vs.q) >= len(spec.universe):
        raise CannotBuild(
            f"受限 spec 上基线 `Q` 已经是离散划分（{len(vs.q)} 块 / "
            f"{len(spec.universe)} 个方向）⇒ 离散注入与基线重合，红不了")
    if injected:
        vs = replace(vs, q=partition_of([frozenset({x}) for x in spec.universe]))
    return _view_report(vs)


# ═══ §A4 ═════════════════════════════════════════════════════════════════════

def _median_witness(ctx: ReadingCtx) -> tuple[list[str], list[str]]:
    """从**真数据**里搜一组「定长摘要相同、中位数不同」的方向 —— 见证不许编。

    ⚠️ 编一个见证（照抄别处的方向 id）在真链路上可能**根本不成立**，
       那时 `§A4` 报红是**对的**，而注入会以为「基线该是绿的」——
       于是失败被记在判据头上，真正的原因（见证是编的）看不见。

    ⚠️ 只搜**三元素**集合：两个元素的集合只要和相同，中位数必然相同
       （`(a+b)/2`），永远给不出见证。`C(25,3) = 2300` ⇒ 毫秒级。
    """
    f, _ = READINGS["中位数"]
    sf = SUMMARIES["计数与和"]
    buckets: dict[Any, list[frozenset[str]]] = {}
    for combo in itertools.combinations(sorted(ctx.value_of), 3):
        S = frozenset(combo)
        buckets.setdefault(sf(S, ctx), []).append(S)
    for group in buckets.values():
        for A, B in itertools.combinations(group, 2):
            if abs(float(f(A, ctx)) - float(f(B, ctx))) > 1e-9:
                return sorted(A), sorted(B)
    raise CannotBuild("真链路上找不到中位数的见证 ⇒ 这条注入做不出来（报错，不静默跳过）")


def inj_a4(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A4` 注入：把 `中位数` 从 `holistic` **改标成** `distributive`。

        基线  三类各一条（`计数` distributive / `平均` algebraic /
              `中位数` holistic + **真数据里搜出来的**见证）  ⇒ **绿**
        注入  `中位数` 标成 distributive（`G=取平均`）        ⇒ **红**

    设计稿 §9 那张表的 `§A4` 行逐字就是这一条：「把 `Median` 标成 distributive ⇒ 红」。

    ⚠️ 这条注入只改**一条**声明，别的三条一个字没动 ⇒ 红只可能来自那一条
       （`a4_category` 的 detail 会印出是哪条、实测多少、声称多少）。
       若把基线整个换掉，就分不清「红」是注入造成的还是基线本来就不行。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    ctx = reading_ctx(kernel, spec)
    A, B = _median_witness(ctx)
    decls: list[dict[str, Any]] = [
        {"名": "计数", "类别": "distributive", "G": "取和"},
        {"名": "平均", "类别": "algebraic", "摘要": "计数与和", "H": "和除计数"},
        {"名": "中位数", "类别": "holistic", "摘要": "计数与和",
         "见证": {"摘要": "计数与和", "A": A, "B": B}},
    ]
    if injected:
        decls[-1] = {"名": "中位数", "类别": "distributive", "G": "取平均"}
    return _view_report(vs, kernel=kernel, decls=decls)


# ═══ §A5 ═════════════════════════════════════════════════════════════════════

def _a5_account(kernel: Any, spec: ViewSpec) -> list[tuple[str, str, str]]:
    """一份**条条成立**的账：每个方向的**结构停留项** = `members(d) \\ ∪ members(子)`。

    ⚠️ **不直接用 `ledger_entries`**：批建路径上账**本来就是空的**
       （`滞留 == 0`、`根覆盖之外 == 0`）⇒ `§A5` 报**跳过**，而注入验证要求
       **基线绿**。跳过不是绿。⇒ 由测试（人的代言）给一份非空且条条成立的账。

    ⚠️ 这份账是不是「恰好该有那些条」是 **`B4` 的事**，`§A5` 只判**可指认**。
       两条判同一件事会让「红」分不清是谁的（设计稿 §9 的 `§A5` 行写了这条边界）。
    """
    out: list[tuple[str, str, str, str]] = []
    for did in spec.universe:
        d = kernel.direction(did)
        kids: set[str] = set()
        for c in kernel.children_of(d):
            kids |= set(kernel.members_of(c))
        for item in sorted(set(kernel.members_of(d)) - kids):
            out.append((did, item, LEDGER_STAYED, "结构停留：子方向都不收它"))
    return out


def inj_a5(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A5` 注入：账里有一条的**方向指不到任何视图**。

        基线  一份条条成立的账                              ⇒ **绿**
        注入  把第一条的方向改成 `ZZ`（不在任何视图的块里）   ⇒ **红**

    ⚠️ 判据有三个**互不替代**的红分支，这一条只走 ①；另外两条各有各的注入行
       （`A5·项不是语料的项` / `A5·独立重算不成立`）。
       只配一条的话，「账逐条可指认」这句话的另外两个分支**一次都没被验过**。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    entries = _a5_account(kernel, spec)
    if not entries:
        raise CannotBuild("真链路上结构停留项为空 ⇒ 造不出一份非空的账（基线就绿不了）")
    if injected:
        entries = [("ZZ", entries[0][1], entries[0][2], "编的")] + entries[1:]
    return _view_report(vs, kernel=kernel, entries=entries)


def inj_a5_bad_item(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A5` 注入②：账里有一条的**项不是语料里的项**（方向是对的）。"""
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    entries = _a5_account(kernel, spec)
    if not entries:
        raise CannotBuild("真链路上结构停留项为空 ⇒ 造不出一份非空的账")
    if injected:
        entries = [(entries[0][0], "这个项不存在", entries[0][2], "编的")] + entries[1:]
    return _view_report(vs, kernel=kernel, entries=entries)


def inj_a5_unwarranted(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A5` 注入③：账里有一条**独立重算不成立**（项在语料里，但不在那个方向上）。"""
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    entries = _a5_account(kernel, spec)
    if not entries:
        raise CannotBuild("真链路上结构停留项为空 ⇒ 造不出一份非空的账")
    if injected:
        did = entries[0][0]
        mine = set(kernel.members_of(kernel.direction(did)))
        other = sorted(set(kernel.items) - mine)
        if not other:
            raise CannotBuild(f"`{did}` 的成员集就是整个语料 ⇒ 造不出「不在它上面」的项")
        entries = [(did, other[0], LEDGER_STAYED, "编的")] + entries[1:]
    return _view_report(vs, kernel=kernel, entries=entries)


def inj_a5_out_of_scope(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A5` 注入④：**「根覆盖之外」那一类**的独立重算不成立。

        基线  **维护路径**的真实账（先建 6、再维护 30）—— 里面有真的
              `out_of_scope` 行，修后**条条成立**                      ⇒ **绿**
        注入  把第一条的项换成**真的进了结构**的项 ⇒ 与「根本没进结构」矛盾 ⇒ **红**

    ⚠️ ★ **为什么必须单独一条**（2026-10-12 查出）：另外三条注入的基线账都由
       `_a5_account` 造，而它**只造滞留类**（批建路径上 `out_of_scope` 恒空）。
       ⇒ 「根覆盖之外」那一类**从来没被验过**，于是它「恒判 False」的缺陷
       **一直没露**。这一条把那一类**真的走一遍**。

    ⚠️ 它**不能**用批建：批建上 `out_of_scope` 恒空（`滞留 == 0`、`根覆盖之外 == 0`），
       造不出非空且成立的基线。所以走 `maintenance_kernel`（`reach`）——
       那一趟**先建 6 项**，根的覆盖只含那 6 项 ⇒ 其余项**按证明**落在根覆盖之外。
    """
    from ldv.core.kernel import _did_order
    from ldv.run_checks import batch_kernel, maintenance_kernel

    kernel = maintenance_kernel("reach", nodes, edges)
    plugin = batch_kernel("reach", nodes, edges)[1]
    cover = coverage_of("reach", nodes, edges)
    dirs = sorted(kernel.all_directions(), key=lambda d: _did_order(d.did))
    U = tuple(d.did for d in dirs)
    spec = ViewSpec(universe=U, partition=(frozenset(U),),
                    relation=frozenset((d.parent, d.did) for d in dirs if d.parent))
    vs = build_views(kernel, spec, cover, plugin)
    entries = ledger_entries(kernel, spec)
    if not entries:
        raise CannotBuild("维护路径上账为空 ⇒ 造不出非空的账（这条注入的前提不成立）")
    if injected:
        inside = sorted(kernel.members_of(kernel.root))
        if not inside:
            raise CannotBuild("根的成员集为空 ⇒ 造不出「真的进了结构」的项")
        entries = [(entries[0][0], inside[0], entries[0][2], "编的")] + entries[1:]
    return _view_report(vs, kernel=kernel, entries=entries)


# ═══ §A6 ═════════════════════════════════════════════════════════════════════

def inj_a6(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A6` 注入：换成「**存了派生边** + **读回时信任它**」那一对**对照实现**。

        基线  真实现（盘上只有权威边，派生边读回现算）      ⇒ **绿**
        注入  两个对照都上 ⇒ 盘上带派生栏、读回时信任它     ⇒ **红**

    ⚠️ **必须两个都上**。只上「存派生边」的那一个 ⇒ 读回时照样重算，
       漂移没了；只上「信任派生边」的那一个 ⇒ 盘上根本没有派生栏，
       退回重算，漂移也没了。⇒ 单独上任何一个都**造不出漂移**。

    ⚠️ 于是这一条注入里红的是 `§A6` 的**分支②**（存档带了派生边），
       **不是分支③**（漂移）。分支③ 只有在**盘上的派生值本身就是错的**时候才亮，
       而那要求基线视图自己就先违反 `§A1` —— 那会让**基线就红**，
       违反「基线绿 + 注入红」。⇒ 分支③ 由 `run_tests` 的
       `test_view_a6_branch_split` 在**合成**的坏视图上单独验，两条路各管一段。
    """
    from ldv.core import view_persist as vp

    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    kw: dict[str, Any] = {}
    if injected:
        kw = {"to_d": vp._to_dict_storing_derived,
              "from_d": vp._from_dict_trusting_derived}
    return _view_report(vs, kernel=kernel, cover=cover, plugin=plugin, a6kw=kw)


# ═══ §A7 ═════════════════════════════════════════════════════════════════════

def _a7_old_q(spec: ViewSpec) -> tuple[frozenset[str], ...]:
    """`§A7` 要的那次**结构变动**：把 `spec.universe` 里**最后长出来**的方向拿掉。

    ⚠️ 挑法**不在这里写** —— 调 `abstraction.a7_old_q`（生产路径用的同一个）。
       抄一份的做法实测已经漂过（`§A8` 那份副本原样保留了写死的 `universe[-13:]`，
       而生产那边已经改成按比例缩放了）。**它改了这里跟着改。**
    """
    old_q = a7_old_q(spec)
    if old_q is None:
        # 2026-10-11：**抛 `CannotBuild`**，不是 `AssertionError`。理由见那个类的注释：
        # 驱动把任何异常都读成「基线抛异常」，而这里其实是「本语料做不出来」。
        raise CannotBuild(
            f"`universe` 只有 {len(spec.universe)} 个方向 ⇒ 拿掉任何一个都会拿光整个"
            f" `universe`（`restrict_spec` 是**拿掉**）⇒ 构造不出「变动前」的结构")
    return old_q



def _a8_fixtures(spec: ViewSpec) -> list[tuple[str, Any]]:
    """`§A8` 要的**两个**种子 —— 与 `run_checks.view_report` **同一条挑法**。

    ⚠️ 与生产路径分开写会漂移（本仓库记过的形状：`reach` 的根口径分岔过一次）。
       2026-10-11 起**不再是「逐字照抄」**，而是**调同一个助手**
       （`abstraction.a8_fixture_drops`）—— 抄一份的做法实测已经漂过：生产那边
       写死的 `universe[-13:]` 在 `|U| <= 13` 的语料上会拿掉整个 `universe` 并崩，
       而这份副本原样保留了那个写法。**改成调同一个函数，它改了这里跟着改。**
    """
    drops = a8_fixture_drops(len(spec.universe))
    if drops is None:
        raise CannotBuild(
            f"`universe` 只有 {len(spec.universe)} 个方向 ⇒ `a8_fixture_drops` 挑不出"
            f"两个互不相同的种子")
    light, heavy = drops
    return [
        ("甲·传播在预算内", coarsest_stable_refinement(
            restrict_spec(spec, frozenset(spec.universe[-light:])))),
        ("乙·传播严重退化", coarsest_stable_refinement(
            restrict_spec(spec, frozenset(spec.universe[-heavy:])))),
    ]


def inj_a8_deliver_propagated(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A8` 注入**甲**：**把传播结果当交付**（省了重建，代价是把 `§A3` 弄瞎）。

        基线  交付 `csr`                                  ⇒ **绿**
        注入  交付 `propagate(seed, spec)`（更细的划分）     ⇒ **① 红**

    ## 红的机理 —— 这是本判据存在的**全部理由**（而且是**量出来的**）

        `§A3` 要枚举**更粗的**候选划分，候选数随块数**组合爆炸**：

            `csr`  8 块 ⇒ 候选 **4140**    ⇒ `§A3` **判得了**（枚举完）
            传播  12 块 ⇒ 候选 **4213597** ⇒ `§A3` **判不了**（超上限）⇒ **跳过**

        ⚠️ 而本仓库的规矩是 **「跳过 ≠ 通过」** ⇒ 交付传播结果 =
          **让「最粗」这条承重性质从可判退化成判不了**。

    ⚠️ 这个注入**必须**用传播**真的更细**的那一格（甲那格传播在预算内，但**仍更细**）——
       命题是「**不论传播多划算，交付物都得是 `csr`**」。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    fixtures = _a8_fixtures(spec)
    ov = None
    if injected:
        name, seed = fixtures[0]
        q = tuple(propagate(seed, spec))
        ov = {name: Maintenance(q=q, action="重建" if False else "重建",
                                shrink=shrink_ratio(q, spec), propagate_equal=False,
                                shrink_if_propagated=shrink_ratio(q, spec),
                                reason="注入：把传播结果当交付")}
    return _view_report(build_views(kernel, spec, cover, plugin),
                        a8={"fixtures": fixtures, "overrides": ov})


def inj_a8_mislabelled(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A8` 注入**乙**：**动作谎报** —— 两条路**并不同构**，却把动作标成「传播」。

        基线  动作与实相符                                ⇒ **绿**
        注入  报「传播」而 `propagate ≠ csr`                ⇒ **③ 红**

    ⚠️ 守的是「**报的是不是实话**」：维护记录的**全部**可解释性都挂在「动作」上 ——
       它错了，读者就**说不出**这份认识是怎么来的。
       ⇒ 而 ①② 两条**都抓不到**（那份交付物本身是 `csr`、也稳定）。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    fixtures = _a8_fixtures(spec)
    ov = None
    if injected:
        name, seed = fixtures[1]
        m = maintain(seed, spec)
        ov = {name: Maintenance(q=m.q, action="传播",      # ← 谎报（实际不同构）
                                shrink=m.shrink, propagate_equal=m.propagate_equal,
                                shrink_if_propagated=m.shrink_if_propagated,
                                reason="注入：动作谎报")}
    return _view_report(build_views(kernel, spec, cover, plugin),
                        a8={"fixtures": fixtures, "overrides": ov})


def inj_a7(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A7` 注入**甲**：把传播换成「**挂到父方向所在的那一块**」（`propagate_naive`）。

        基线  真传播（新方向各自成块 + 跑到不动点）      ⇒ **绿**
        注入  挂到父块、**不重跑不动点**                  ⇒ **① 不稳定 ⇒ 红**

    ⚠️ 红的机理是**新边改变了 `E⁻¹`**：`(父, 新)` 把 `父` 塞进 `E⁻¹({新})`，
       而 `{新}` 那一块**跨在内外**（`父` 在里面、同块兄弟在外面）。
       ⇒ 这不是「实现写得丑」，是**传播的定义被换掉了**。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    old_q = _a7_old_q(spec)
    return _view_report(build_views(kernel, spec, cover, plugin),
                        a7={"old_q": old_q,
                            "new_q": propagate_naive(old_q, spec) if injected else None})


def inj_a7_recompute(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A7` 注入**乙**：**用全量重算冒充传播** ⇒ **② 不细化 `Q_ext`** ⇒ 红。

    ## ★ 为什么非有这一条不可

    只配甲（`propagate_naive`）的话，`§A7` 的**分支②**一次都没被验过 ——
    而套件照样报「全绿」，那句「全绿」的范围会**悄悄缩掉**。
    同一条纪律在 `§A3`（三分支 → 两行）与 `§A5`（三分支 → 三行）上各用过一次。

    ⚠️ 这一条**不是**说「重算不对」：重算是**另一个**合法对象（它就是 `§A3`
       要的那个唯一解）。红的是**把它叫做「传播」** —— 那换了 `E′` 的定义
       而没改文档。实测：`重算 ⊑ 旧 Q` 为**真**（重算不合并旧块），
       而 `重算 ⊑ Q_ext` 为**假** ⇒ 谓词写「细化旧 `Q`」这条注入**一次都红不了**。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    old_q = _a7_old_q(spec)
    return _view_report(build_views(kernel, spec, cover, plugin),
                        a7={"old_q": old_q,
                            "new_q": coarsest_stable_refinement(spec) if injected else None})


# ═══ 视图层 L0 的声明 + 折叠契约 `§M0`–`§M6`（2026-10-08） ═══════════════════
#
# ⚠️ **这一组不叫「多层」** —— 产物是**第 0 层**（视图层），折叠是**机制**。
#    七条按**守谁**分两拨：
#
#       守 L0 的**声明**   `§M0`（`E` 无自环）`§M1`（`Q₀ == csr`）`§M4`（验证代价，**读数**）
#       守**若继续折**的契约 `§M2`（层数上限）`§M3`（总代价）`§M5`（出口）`§M6`（账）
#
# ## ⚠️ 先读这一段：七条里有**五条对生产输出恒真**，不是会开火的判据
#
# 与删除路径的 `D2`/`D3` 同一个处境（设计文档 §10.2 C：它们是**守卫**）：
#
#     `§M1`  视图层 `Q₀ == csr(spec)` —— 生产路径喂的 `q0` **就是** `csr(spec)`
#            ⇒ 对它的输出**恒真**。守的是「把传播结果 / 别的划分当交付」
#            （`§A8` 的 `maintain` 已经为这件事立过规矩：交付物必须是 `csr`）。
#     `§M2`  层数 ≤ 声明的 `MAX_LEVELS` —— `fold_until` 自己就按它停 ⇒ **恒真**。
#            守的是「调用方把刹车拆掉」。
#     `§M3`  总代价 `Σ|U_k| ≤ 2|U_0|` —— `fold_until` 每层缩一半以上 ⇒ **几何级数的推论**
#            ⇒ 对它的输出**恒真**。守的是「另一个实现允许不缩的层继续折」。
#     `§M5`  末层 `stopped` 为空 —— `fold_until` 的循环**只有两个出口**，
#            都带非空 `stopped` ⇒ 这一条**不可达**。守的是「另一个实现跑到不动点就停」。
#     `§M6`  顶层账 ⊇ 各层丢的并 —— `ledger_k = ∪_{j≤k} lost_j` **按构造相等**
#            ⇒ 对它的输出**恒真**。守的是「另一个实现不携带账」。
#
# ⇒ 这五条的红形态只能**注入**：`§M0` / `§M1` / `§M2` 从**真路径的缝**喂坏输入
#   （`E` 塞自环 / `q0` 喂非 `csr` / `max_levels` 抬过声明值）；
#   `§M3` / `§M5` / `§M6` **手造层叠**。这不是「注入挑不出来」，是**它们的红形态
#   本来就在契约层**：判据守的是**接口的承诺**，不是**这一份实现**。写在这里，
#   免得把「恒真」读成「没在判」—— 两者在只看布尔值时长得一模一样。
#
# ⚠️ `§M1` 的判据 2026-10-10 **换了**（`shrink ≤ 阈值` → `Q₀ == csr(spec)`）：
#   旧形式判的是**语料**（分子由结构决定、分母由键集决定 ⇒ 系统无自由度），
#   三份新语料上它是**唯一的红**，而 `§A3` 同时实测「已是最粗」⇒ 可证的不可能。
#   收缩比降为**读数**（`render_levels` 逐层印）。见 `multilevel.m1_view_layer`。
#
# ⚠️ `§M4` 2026-10-10 **降级为读数**（前提没了：`§A3` 有了多项式 oracle）⇒ 它不判。


def _ml_chain(nodes, edges):
    """这一组注入的公共装配：**生产口径**的 spec / kernel / cover / plugin。

    ⚠️ **`load_spec_file` 必须带 `(find_corpus(), corpus)`** —— 与 `run_checks` 里
       那三处**同一个调法**（2026-10-11 实测修）。原来这里是**不带参数**的
       `load_spec_file()`，那等于「只找位置、不认语料」⇒ 永远拿**仓内那份**声明
       （指纹 `{'项数': 36, '边数': 47}`）⇒ 换任何一份别的语料，
       `spec_for` 都因指纹不符返回 `None` ⇒ `run_multilevel(spec=None, …)` 当场
       `AttributeError: 'NoneType' object has no attribute 'partition'`。
       症状**不是红**：本文件把异常逐条捕获成「基线抛异常」，所以 11 条注入
       （`M0`/`M1`/`M2`/`M3`/`M5`/`M6`/`T4`/`T5`/`T6`/`T7`/`M1·反侧`）会**一起**
       报成「抛异常」，而真因是**装配拿错了声明**。

    ⚠️ 抄 `run_checks` 的调法而不是「自己再写一遍找法」—— 本仓库记过的形状：
       两处「长得一样」的代码各自演化，症状是**检查报绿**。
    """
    from ldv.checks.abstraction import load_spec_file, spec_for
    from ldv.checks.coverage import corpus_fingerprint
    from ldv.run_checks import batch_kernel

    loaded = load()
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), corpus)
    which = str(doc.get("方向") or "keyset")
    spec, _why = spec_for(doc, which, corpus)
    kernel, plugin, _q, _mk = batch_kernel(which, nodes, edges)
    cover = coverage_of(which, nodes, edges)
    return spec, kernel, plugin, cover


def _ml_report(spec, kernel, plugin, cover, *, q0=None, max_levels=None,
               min_shrink_ratio=None) -> Report:
    from ldv.checks.multilevel import MULTILEVEL_CODES, run_multilevel

    kw = {}
    if max_levels is not None:
        kw["max_levels"] = max_levels
    if min_shrink_ratio is not None:
        kw["min_shrink_ratio"] = min_shrink_ratio
    rep = Report(plugin="(L0)", expects=MULTILEVEL_CODES)
    run_multilevel(spec, kernel, cover, plugin, rep, q0=q0, **kw)
    return rep


def inj_m0(nodes, edges, injected: bool) -> Report:
    """`§M0` —— 注入一条**自环**（人写进 `E` 的那种）。

    红形态：`E` 里出现 `(x, x)`。后果是「稳定」在某些块上**自动为真**
    ⇒ 判据退化成**空转**。所以它必须在这里被挡住，不能靠下游「顺便不管它」。
    """
    from ldv.core.views import ViewSpec

    spec, kernel, plugin, cover = _ml_chain(nodes, edges)
    if injected:
        x = spec.universe[0]
        spec = ViewSpec(universe=spec.universe, partition=spec.partition,
                        relation=spec.relation | {(x, x)})
    return _ml_report(spec, kernel, plugin, cover)


def inj_m1(nodes, edges, injected: bool) -> Report:
    """`§M1` —— 从 `q0` 那条缝喂一个**不是 `csr(spec)`** 的划分：**离散划分**。

    ## 判据 2026-10-10 换了，红形态的说法跟着换

    旧判据是 `shrink = |Q₀|/|U₀| ≤ 0.5`，注入取「**真的不缩**」那一侧（离散）。
    ⚠️ 旧 docstring 里那段「**不是** `q0 = spec.partition`」的更正是**对的**
    （`Q == P` = 1 块 ⇒ `shrink = 1/|U|` ⇒ 缩得**最狠**，拿它注入不会红），
    但它现在只说清了**旧**判据：新判据下 `spec.partition` **也会红** ——
    因为 `{U}` 同样**不是** `csr(spec)`。两侧见 `known_answer_controls`。

    新判据是 `Q₀ == coarsest_stable_refinement(spec)`，注入仍取**离散**这一侧：
    块数 == 方向数 ⇒ 它显然不是最粗稳定细化（`csr` 一定比它粗，
    否则 `csr` 自己就是离散的 —— 那时这份语料上根本没有可合并的结构，
    判据**没有内容**，而那种输入在真语料上不存在）。

    ⚠️ 这条注入走的是**真路径**（真 `fold_until`、真内核），不是手造层叠：
       `q0` 喂进去 ⇒ `shrink = 1.0 > 阈值` ⇒ 循环**第一轮就停**
       ⇒ `levels = [L0]`，而 `L0.q` 就是那个离散划分 ⇒ `§M1` 红。
       见 `multilevel.m1_view_layer` 的 docstring。
    """
    spec, kernel, plugin, cover = _ml_chain(nodes, edges)
    q0 = tuple(frozenset({x}) for x in spec.universe) if injected else None
    return _ml_report(spec, kernel, plugin, cover, q0=q0)


def inj_m1_too_coarse(nodes, edges, injected: bool) -> Report:
    """`§M1` 的**第二条**注入 —— **反侧**：`q0` = `spec.partition`（**比 `csr` 更粗**）。

    ⚠️ **这一侧旧形式报绿**：`spec.partition` 在本语料上是 `{U}`（**1 块**）
       ⇒ `shrink = 1/25 = 0.040`，是全表**缩得最狠**的一层
       ⇒ 旧判据（`shrink ≤ 阈值`）在它上面**照样过**。
       新判据两侧都挡（更细 / 更粗），而 `§M1` 的 docstring 与两份文档都写着
       「**对称**」这句话 —— 那就**必须有这一条注入**，否则那句话没人守。

    ⚠️ 与 `inj_m1`（更细那一侧）**互不替代**：两条都只命中了 `§M1` 的一个侧面。
       （本仓库的既有形状：`A3·稳定但更细` / `B11·位置敏感` / `B4·滞留要记账` …）
    """
    spec, kernel, plugin, cover = _ml_chain(nodes, edges)
    q0 = tuple(spec.partition) if injected else None
    return _ml_report(spec, kernel, plugin, cover, q0=q0)


def inj_m2(nodes, edges, injected: bool) -> Report:
    """`§M2` —— 注入「把主刹车拆掉，只靠层数兜底」。

    `min_shrink_ratio = 1.0` ⇒ 收缩比**永远不超阈值** ⇒ 折叠一路折到层数上限。
    再给一个 `max_levels = 12`（> 声明的 `MAX_LEVELS = 8`）⇒ `§M2` 红。

    ⚠️ **`§M3` 会一起红**（12 层里后面 11 层都不缩 ⇒ `Σ|U_k|` 远超 `2|U_0|`）——
       那是**对的**，不是串扰：前作核验 §3.2 的原文就写着
       「喂一个「每层不缩」的构造 ⇒ **与 `§M1` 同时红**」。多判据同时开火在本项目里是常态。
       ★ **但 `§M1` 现在不跟着红了**（2026-10-10 它改判 `Q₀ == csr`）：
       这条注入没动 `q0`（仍走 `csr(spec)`）⇒ `§M1` 绿是**对的** ——
       它判的是「交付物是不是 `csr`」，与「折了几层」无关。
    """
    spec, kernel, plugin, cover = _ml_chain(nodes, edges)
    if not injected:
        return _ml_report(spec, kernel, plugin, cover)
    return _ml_report(spec, kernel, plugin, cover,
                      max_levels=12, min_shrink_ratio=1.0)


def inj_m3(nodes, edges, injected: bool) -> Report:
    """`§M3` —— **手造**一个「每层不缩」的层叠（见本节开头：这一条是守卫）。"""
    from ldv.checks.multilevel import m3_cost
    from ldv.core.views import Level, ViewSpec

    spec, kernel, plugin, cover = _ml_chain(nodes, edges)
    if not injected:
        return _ml_report(spec, kernel, plugin, cover)
    u = tuple(spec.universe[:4])
    flat = ViewSpec(universe=u, partition=(frozenset(u),), relation=frozenset())
    levels = [Level(spec=flat, q=tuple(frozenset({x}) for x in u),
                    shrink=1.0, n_cand=1) for _ in range(3)]
    rep = Report(plugin="(L0)", expects=("M3",))
    m3_cost(levels, rep)
    return rep


def inj_m5(nodes, edges, injected: bool) -> Report:
    """`§M5` —— 注入「末层停因**为空**」（= 没有刹车，跑到不动点）。

    ⚠️ **这一条是守卫**（见本节开头）：`fold_until` 的循环**只有两个出口**，
       都带非空 `stopped` ⇒ 这个红形态**真跑不出来**，只能**手造层叠**
       （与 `inj_m3` / `inj_m6` 同一个处置）。守的是**契约**：
       另一个实现若「跑到不动点就停」，它开火。

    ⚠️ 旧的注入用的是「`q0` = 离散划分 ⇒ 只折一层」那一侧。**已删**（2026-10-08），
       两条理由各自都够：

           ① 它是**反需求**：本设计的产物是第 0 层，**一层就够**。
              「第一层就停」不是误停，是期望的结局。
           ② 它与 `§M1` **判同一件事**：`len(levels) < 2` ⇔ `L0` 没缩够 ——
              而那正是 `§M1` 现在**直接**判的。留着会让「红」分不清是谁的。

       ⇒ 删掉它**没有**丢掉覆盖（那个条件仍被 `§M1` 判着，且判得更准），
         但**必须换一个红形态**，否则这一条会变成恒绿。
         见 `multilevel.m5_termination` 的 docstring。
    """
    from ldv.checks.multilevel import m5_termination
    from ldv.core.views import Level, ViewSpec

    spec, kernel, plugin, cover = _ml_chain(nodes, edges)
    if not injected:
        return _ml_report(spec, kernel, plugin, cover)
    u = tuple(spec.universe[:4])
    flat = ViewSpec(universe=u, partition=(frozenset(u),), relation=frozenset())
    lv0 = Level(spec=flat, q=(frozenset(u),), shrink=1.0, n_cand=1, stopped="M1 收缩比")
    lv1 = Level(spec=flat, q=(frozenset(u),), shrink=1.0, n_cand=1, stopped="")  # ← 没刹车
    rep = Report(plugin="(L0)", expects=("M5",))
    m5_termination([lv0, lv1], rep)
    return rep


def inj_m6(nodes, edges, injected: bool) -> Report:
    """`§M6` —— **手造**一个「下层有账、上层账为空」的层叠（见本节开头：这一条是守卫）。"""
    from dataclasses import replace

    from ldv.checks.multilevel import m6_ledger
    from ldv.core.views import Level, ViewSpec

    spec, kernel, plugin, cover = _ml_chain(nodes, edges)
    if not injected:
        return _ml_report(spec, kernel, plugin, cover)
    u = tuple(spec.universe[:4])
    flat = ViewSpec(universe=u, partition=(frozenset(u),), relation=frozenset())
    lv0 = Level(spec=flat, q=(frozenset(u),), shrink=1.0, n_cand=1,
                lost=frozenset({"x"}), ledger=frozenset({"x"}))
    lv1 = Level(spec=flat, q=(frozenset(u),), shrink=1.0, n_cand=1,
                lost=frozenset({"y"}), ledger=frozenset())      # ← 账被丢了
    rep = Report(plugin="(L0)", expects=("M6",))
    m6_ledger([lv0, lv1], rep)
    return rep


# ═══ T1–T3（检索器层）═════════════════════════════════════════════════════════
#
# 三条**都是守卫**（`§C3`）：正确实现下恒绿 ⇒ 基线只能是**手造**的绿，
# 注入也只能是**手造**的红 —— 生产语料上题面造不出来（见 `checks/retrieval.py`
# 开头那条证明：满足 `§I2 契约`的插件上，「后看的块里有真命中」不可能存在）。
# ⇒ 三条照 `inj_m3` 的体裁写：**在判据最靠近的地方造输入**，直接调判据。


def _retrieve_on_synth(*, losing: bool = True):
    """手造的内核 / 插件 / 查询 + 一次真检索（`_synth` 在本仓库里只此一处）。"""
    from ldv.checks.retrieval import _synth, _synth_spec
    from ldv.retriever import as_bar, retrieve

    kernel, plugin, q = _synth(with_losing_merge=losing)
    spec = _synth_spec(kernel)
    return kernel, plugin, retrieve(kernel, plugin, {"keyset": as_bar(q)}, spec=spec), spec


def inj_t1(nodes, edges, injected: bool) -> Report:
    """`T1` —— 注入「**先看的块跑完就收工**」（`§C1` 那处默认值的反例）。

    ⚠️ 夹具是**手造**的（`_LosingMerge`：合并会丢成员）—— 因为满足 `§I2`
       的插件上「后看的块里有真命中」**造不出来**，真插件上这条判据**永远绿**。
       见 `checks/retrieval.py` 开头。`known_answer_controls()` 里那条保护会
       在夹具不成立时**大声报**，不会静默变成空转。
    """
    from ldv.checks.retrieval import RETRIEVER_CODES, _early_stop, t1_equivalence

    kernel, _plugin, full, _spec = _retrieve_on_synth()
    # ★ 2026-10-14 起 `retrieve` **自己就会跳过「后看」的块** ⇒ 在 `_LosingMerge`
    #   （**破坏 `§I2`**）上，`full.candidates` **已经是漏的** ⇒ 拿它当基线**不绿**。
    #   ⇒ 基线改成「**没有跳过**」的形态（全扫）—— 那才是「正确实现」该有的样子。
    #   ⚠️ 而**生产路径上**这个红已经真实存在（`known_answer_controls` 就在验它）。
    whole = replace(full, candidates=frozenset(kernel.query(full.query).yes))
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t1_equivalence(rep, _early_stop(whole) if injected else whole, kernel)
    return rep


def inj_t8(nodes, edges, injected: bool) -> Report:
    """`T8` —— 注入「**交到检索层的那份认识更细**」（`§3` 定理的另一半）。

        基线  `q == csr(spec)`（最优粒度）        ⇒ **绿**
        注入  换成**离散划分**（每个方向一块）     ⇒ **红**（**合法但退化**）

    ## 红形态为什么是「合法但退化」，而不是「错」

        那个更细的 `q` **仍然**稳定、**仍然**细化 `P`、**仍然**不改变候选集
        ⇒ `T1`–`T7` **一条都不会红**。而被磨掉的是**收益**（块级建议与画像变差）。
        ⇒ 实测（`outputs/_probe_disk_influence.py`）：8 块 → 25 块 ⟹
          候选集 `★同`、**块级建议变**、**画像变**。

    ⚠️ **它是唯一能看见「盘上那份退化」的判据** —— 没有它，「收益退化」与「一切正常」
       在只看 `T1`–`T7` 时**长得一模一样**（那正是本仓库的中心形状）。
    """
    from ldv.checks.abstraction import ViewSet
    from ldv.checks.retrieval import RETRIEVER_CODES, t8_coarsest
    from ldv.core.views import coarsest_stable_refinement, partition_of

    from ldv.checks.retrieval import _synth, _synth_spec
    from ldv.retriever import as_bar, retrieve

    kernel, plugin, q = _synth(with_losing_merge=False)
    spec = _synth_spec(kernel)
    on_disk = ViewSet(spec=spec, q=coarsest_stable_refinement(spec), views=())
    r = retrieve(kernel, plugin, {"keyset": as_bar(q)}, spec=spec, recognition=on_disk)
    if injected:
        # 「更细」的可观测形态：离散划分（每个方向一块）
        discrete = tuple(frozenset({d}) for d in sorted(spec.universe))
        r = replace(r, recognition=replace(r.recognition, q=discrete,
                                           source="盘上（指纹相符）"))
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t8_coarsest(rep, r, spec)
    return rep


def inj_t2_unreadable(nodes, edges, injected: bool) -> Report:
    """`T2` 注入**乙**：`disk_unreadable=True` —— 「**盘上那份读不回来**」。

        基线  盘上那份能读回来（且当前）      ⇒ 绿
        注入  盘上**有一份**、但**读不回来**    ⇒ **红**

    ## ★ 这一条是**实测踩出来的**（2026-10-10）

        造一份陈旧的 `views.json` ⇒ `view_persist.from_dict` **抛**
        ⇒ 而 `retrieval_report` 当时**没有兜住** ⇒
          **`(检索)` 组整个消失、`run_checks` 当场崩**（`Traceback` 一节都不剩）。

    ⇒ 「崩」比「红」更糟：**红是判据在说话，崩是判据没了**。
      所以这条注入守的是「**坏档必须变成一个红，而不是一次崩溃**」。
    """
    from ldv.checks.abstraction import ViewSet
    from ldv.checks.retrieval import RETRIEVER_CODES, t2_current
    from ldv.core.views import coarsest_stable_refinement
    from ldv.retriever import as_bar, retrieve

    from ldv.checks.retrieval import _synth, _synth_spec

    kernel, plugin, q = _synth(with_losing_merge=False)
    spec = _synth_spec(kernel)
    on_disk = ViewSet(spec=spec, q=coarsest_stable_refinement(spec), views=())
    r = retrieve(kernel, plugin, {"keyset": as_bar(q)}, spec=spec, recognition=on_disk,
                 disk_unreadable=injected)
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t2_current(rep, r, spec)
    return rep


def inj_t2(nodes, edges, injected: bool) -> Report:
    """`T2` —— 注入「**盘上那份陈旧**」（`基线§6`；命题 2026-10-10 改过）。

    红形态 = **盘上那份**的 `spec` 指纹 ≠ 当前。

    ⚠️ 三条边界（`known_answer_controls` 里各有一条对照）：

        盘上那份**当前**   ⇒ 过
        盘上那份**读不回来**⇒ 红（**另一条红形态**，见 `inj_t2_unreadable`）
        盘上**没有**       ⇒ **跳过**（跳过 ≠ 通过）

    ★ 这一条现在**生产可达**（不再是只能手造的守卫）：结构动了而没跑
      `python -m ldv.recognize` ⇒ 盘上那份陈旧 ⇒ **红**。用户裁定 2026-10-10：
      「认识必须要维护」。修法一条命令。
    """
    from ldv.checks.abstraction import ViewSet
    from ldv.checks.retrieval import RETRIEVER_CODES, t2_current
    from ldv.core.views import coarsest_stable_refinement
    from ldv.retriever import as_bar, retrieve, spec_fingerprint

    from ldv.checks.retrieval import _synth, _synth_spec

    kernel, plugin, q = _synth(with_losing_merge=False)
    spec = _synth_spec(kernel)
    on_disk = ViewSet(spec=spec, q=coarsest_stable_refinement(spec), views=())
    if injected:
        # 「**盘上那份陈旧**」的可观测形态：结构已经动了（这里用一个**另一个 `spec`**
        # 代表「盘上那份按旧结构写的」），而盘上那份没跟着变。
        other = ViewSpec(universe=spec.universe,
                         partition=tuple(frozenset({d}) for d in spec.universe),
                         relation=spec.relation)
        on_disk = ViewSet(spec=other, q=coarsest_stable_refinement(other), views=())
    r = retrieve(kernel, plugin, {"keyset": as_bar(q)}, spec=spec, recognition=on_disk)
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t2_current(rep, r, spec)
    return rep


def inj_t3(nodes, edges, injected: bool) -> Report:
    """`T3` —— 注入「解释**写死成一句固定的**」（`§C3`）。

    写死的那条**指不到**任何候选方向的覆盖 ⇒ 红。
    ⚠️ 它判的**不是**「解释暴露了遍历顺序」（那样反而错，`基线§3.2`）。
    """
    from ldv.checks.retrieval import RETRIEVER_CODES, t3_reasons

    kernel, _plugin, full, _spec = _retrieve_on_synth()
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t3_reasons(rep, replace(full, reasons=(("__写死__", "__写死__"),)) if injected else full,
               kernel)
    return rep


def inj_t4(nodes, edges, injected: bool) -> Report:
    """`T4` —— 注入「**三态混同**」：把「还没长出来」计入「分不开」（`基线§14.5`）。

    红形态 = 读法翻成「**分不开**」，而事实是「**还没长出来**」——
    `§K6` 在本层的形态：**把「不知道」说成「已经到底了」**。
    ⚠️ 四档的**字面**只有 `retriever.BlockProfile.reading` 那一处定义；
       `分不开` = 成员**只有 1 个项**（`§K2` 情形①，结构上永远分不开），
       `还没长出来` = 无子**且成员 ≥2**（值得试）—— 只看「有没有子」时两者**长得一样**。

    ⚠️ **用真语料**（`nodes` / `edges`），不用 `_synth`：手造那个小夹具上未必有
       「还没长出来」的块，而这一注入正是在**那一档**上才分得开。
       本语料实测（`sequence`，批建路径）：块 `['D2','D3','D4','D6','D7']`
       是 5 个成员、2 个有子 / 2 个只有 1 个项 / 1 个还没长出来 ⇒ 读法「混合」；
       注入把「还没长出来」并进「分不开」后，那 2 个单项的成员**事实没变**而读法翻档。
    """
    from ldv.checks.abstraction import spec_for
    from ldv.checks.retrieval import RETRIEVER_CODES, t4_profile
    from ldv.run_checks import batch_kernel, corpus_fingerprint, load_spec_file
    from ldv.retriever import as_bar, retrieve

    # ⚠️ `load_spec_file` 必须带 `(find_corpus(), corpus)` —— 与 `run_checks` 里
    #    那三处、以及本文件的 `_ml_chain` **同一个调法**。不带参数等于「只找位置、
    #    不认语料」⇒ 永远拿**仓内那份**声明 ⇒ 换任何别的语料，`spec_for` 都因指纹
    #    不符返回 `None` ⇒ 下面 `retrieve(..., spec=None)` 当场 `AttributeError`。
    #    症状**不是红**：本文件把异常逐条捕获成「基线抛异常」（`T4`–`T7` 一起中招）。
    _corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), _corpus)
    which = str(doc.get("方向") or "")
    spec, _why = spec_for(doc, which, _corpus)
    kernel, plugin, queries, _mk = batch_kernel(which, nodes, edges)
    r = retrieve(kernel, plugin, {which: as_bar(queries[0])}, spec=spec)
    if injected:
        # 「三态混同」的可观测形态：**「还没长出来」被并进「分不开」**
        #   ⇒ 读法从「还没长出来」/「混合」翻成「分不开」，而**事实没变**。
        #   ⚠️ 四档的**字面**只有 `retriever.BlockProfile.reading` 那一处定义：
        #      `已细分过` / `分不开` / `还没长出来` / `混合`。
        r = replace(r, profiles=tuple(
            replace(p, n_singleton=p.n_singleton + p.n_unexpanded, n_unexpanded=0)
            for p in r.profiles))
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t4_profile(rep, r, kernel)
    return rep


def inj_t5(nodes, edges, injected: bool) -> Report:
    """`T5` —— 注入「**顺序没按使用细调排**」（`基线§14.7`）。

    红形态 = 倾向**还在**（判据不会跳过），但顺序**不是**按它降序的 ——
    也就是「记录收着、却没用上」。⚠️ 这正是「**用了**」与「**没用**」唯一分得开的地方：
    没有记录时它会自动退化 ⇒ 只看输出看不出「退化得对」还是「根本没做」。

    ⚠️ 用**真语料**（同 `inj_t4`）—— 它要**真的使用记录**才有内容，
       而手造小夹具上不一定跑了查询。

    夹具前提：**倾斜值必须互不相同**（否则「按权重排序」与「原序」可能重合 ⇒ 注入无效）。
    这一条由 `known_answer_controls` 里的保护**大声报**，不静默变成空转。
    """
    from ldv.checks.abstraction import spec_for
    from ldv.checks.retrieval import RETRIEVER_CODES, t5_usage_finetune
    from ldv.core import selfopt
    from ldv.run_checks import batch_kernel, corpus_fingerprint, load_spec_file
    from ldv.retriever import as_bar, retrieve
    from ldv import flow

    # ⚠️ `load_spec_file` 必须带 `(find_corpus(), corpus)` —— 与 `run_checks` 里
    #    那三处、以及本文件的 `_ml_chain` **同一个调法**。不带参数等于「只找位置、
    #    不认语料」⇒ 永远拿**仓内那份**声明 ⇒ 换任何别的语料，`spec_for` 都因指纹
    #    不符返回 `None` ⇒ 下面 `retrieve(..., spec=None)` 当场 `AttributeError`。
    #    症状**不是红**：本文件把异常逐条捕获成「基线抛异常」（`T4`–`T7` 一起中招）。
    _corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), _corpus)
    which = str(doc.get("方向") or "")
    spec, _why = spec_for(doc, which, _corpus)
    kernel, plugin, queries, _mk = batch_kernel(which, nodes, edges)
    for q0 in queries[:3]:                      # ★ 先攒记录（流程 A 本来就记）
        flow.run_query(kernel, q0)
    tendency = selfopt.tendency_by_direction(kernel)
    r = retrieve(kernel, plugin, {which: as_bar(queries[0])}, spec=spec,
                 tendency=tendency)
    if injected and len(r.first) > 1:
        # 「没用上」的可观测形态：**权重最小**的那块被挪到最前（倾向仍在 ⇒ 判据不跳过）。
        def w(b):
            return sum(tendency.get(d, 0.0) for d in sorted(b))
        fs = list(r.first)
        lo = min(fs, key=w)
        r = replace(r, first=tuple([lo] + [b for b in fs if b is not lo]))
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t5_usage_finetune(rep, r, kernel)
    return rep


def _opposite_advice(a: str) -> str:
    """把一条建议翻到**相反**的一档 —— 对没见过的档也要有定义。

    ⚠️ 见 `inj_t6` 里的注释：写死的映射表撞上第五档就 `KeyError`。
       「说不出来」不该是 `KeyError` —— 那与「实现坏了」长得一样。
    """
    return "值得往下" if "不必" in a else "不必往下"


def inj_t6(nodes, edges, injected: bool) -> Report:
    """`T6` —— 注入「**建议与该块的事实不符**」（`基线§14.8`）。

    红形态 = 建议**还在**（判据不跳过），但它与**那一块**的结构事实相反 ——
    也就是「说的和事实不一样」。★ 它骗的是流程 A 的 `R3a`：那一步**本来没有依据**，
    拿一条错的建议去判「要不要继续往下」，就是**照着错的判**。

    ⚠️ 用**真语料**（同 `inj_t4` / `inj_t5`）—— 它要**真的块**（含「值得往下」/
       「看具体方向」那一档）才有内容：那一档正是 `deepen` 会去展开的那一带。
    """
    from ldv.checks.abstraction import spec_for
    from ldv.checks.retrieval import RETRIEVER_CODES, t6_advice
    from ldv.run_checks import batch_kernel, corpus_fingerprint, load_spec_file
    from ldv.retriever import as_bar, retrieve

    # ⚠️ `load_spec_file` 必须带 `(find_corpus(), corpus)` —— 与 `run_checks` 里
    #    那三处、以及本文件的 `_ml_chain` **同一个调法**。不带参数等于「只找位置、
    #    不认语料」⇒ 永远拿**仓内那份**声明 ⇒ 换任何别的语料，`spec_for` 都因指纹
    #    不符返回 `None` ⇒ 下面 `retrieve(..., spec=None)` 当场 `AttributeError`。
    #    症状**不是红**：本文件把异常逐条捕获成「基线抛异常」（`T4`–`T7` 一起中招）。
    _corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), _corpus)
    which = str(doc.get("方向") or "")
    spec, _why = spec_for(doc, which, _corpus)
    kernel, plugin, queries, _mk = batch_kernel(which, nodes, edges)
    r = retrieve(kernel, plugin, {which: as_bar(queries[0])}, spec=spec)
    if injected:
        # 「说的和事实不一样」的可观测形态：每条建议都翻成**另一档**（与事实相反）。
        # ⚠️ 这张表原来**写死四档**。换语料后实测撞上第五档
        #    `'不必往下（分不开）'` ⇒ `KeyError` ⇒ 驱动读成「注入后抛异常」。
        #    「崩」与「红」是两件事，但**都不是**「这条注入有效」。
        #    表里那四档**照旧**（随仓库那份语料上逐字不变），表外走兜底。
        flip = {"值得往下": "不必往下", "不必往下": "值得往下",
                "往下有现成通道": "不必往下", "看具体方向": "值得往下"}
        r = replace(r, advice=tuple(
            (b, flip.get(a) or _opposite_advice(a)) for b, a in r.advice))
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t6_advice(rep, r, kernel)
    return rep


def inj_t7(nodes, edges, injected: bool) -> Report:
    """`T7` —— 注入「**深化改变了结论**」（`基线§14.9`）。

    红形态 = 深化之后**候选集变小**了 —— 也就是「展开把某个候选弄丢了」。
    ⚠️ 那正是 `§K8` **禁止**的那一侧（假阴），而 `§K4`（只分叉不覆盖）保证它不会发生
       ⇒ 这条判据守的是**那个保证**，不是「这一份实现」。

    ★ 注入形态（手造）：把 `deepen` 前的候选集**人为置多一点**（比真实的多），
       于是「深化后 ⊇ 深化前」**不成立** ⇒ 红。
       ⚠️ 不能靠真跑造出来（真跑下它恒真）—— 同 `T1`/`T4`/`T6` 的处境。

    ⚠️ **夹具前提：查询要选得让深化真的发生**（2026-10-13，v0.1.21）。
       `T7` 在 `deepened` 为空时**跳过**，而 `queries[0]`（`随机 1 项 #0`）是 14 条里
       最小的一条 —— 它的候选只落在「已细分过」的块里 ⇒ `deepen` 无事可做
       ⇒ **基线未展开**（实测：`✗ T7: 基线不是绿的（未展开）`）。
       ⇒ 这里**逐查询试**，取第一条能让 `deepen` 展开的；一条都没有就抛 `CannotBuild`
         （那是第三种状态：**问不出**「会不会红」，不是「判据不红」）。
    """
    from ldv.checks.abstraction import spec_for
    from ldv.checks.retrieval import RETRIEVER_CODES, t7_deepening
    from ldv.core import selfopt
    from ldv.run_checks import batch_kernel, corpus_fingerprint, load_spec_file
    from ldv.retriever import as_bar, deepen, retrieve
    from ldv import flow

    # ⚠️ `load_spec_file` 必须带 `(find_corpus(), corpus)` —— 与 `run_checks` 里
    #    那三处、以及本文件的 `_ml_chain` **同一个调法**。不带参数等于「只找位置、
    #    不认语料」⇒ 永远拿**仓内那份**声明 ⇒ 换任何别的语料，`spec_for` 都因指纹
    #    不符返回 `None` ⇒ 下面 `retrieve(..., spec=None)` 当场 `AttributeError`。
    #    症状**不是红**：本文件把异常逐条捕获成「基线抛异常」（`T4`–`T7` 一起中招）。
    _corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), _corpus)
    which = str(doc.get("方向") or "")
    spec, _why = spec_for(doc, which, _corpus)
    kernel, plugin, queries, _mk = batch_kernel(which, nodes, edges)
    # ★★ 2026-10-13（v0.1.21）：**选一条能让「深化」真的发生的查询**。
    #
    #   `T7` 在「本趟没有做深化」（`deepened` 为空）时**跳过**（`跳过 ≠ 通过`），
    #   而 `queries[0]` 是 14 条里**最小**的一条（`随机 1 项 #0`）：它的候选只落在
    #   **已细分过**的块里 ⇒ `deepen` 无事可做 ⇒ **基线未展开**。
    #   ⚠️ 声明换成 `sequence` 之前，`queries[0]` 恰好落在「值得往下」那一带
    #      ⇒ 这条基线**碰巧**能过。碰巧能过的夹具与「夹具前提成立」长得一样，
    #      直到换了方向才露出来。
    #   ⚠️ **不许**把基线的「判不了」当成「注入有效」：判不了 ≠ 红
    #      （驱动会把 `CannotBuild` 记成「夹具做不出来」，那是第三种状态）。
    #   ⚠️ **每条候选查询一棵新树** —— `deepen` 会 `expand`（唯一写动作）就地改结构。
    r = deep = None
    for _q in queries:
        _k = batch_kernel(which, nodes, edges)[0]
        for _q0 in queries[:3]:
            flow.run_query(_k, _q0)
        _r = retrieve(_k, plugin, {which: as_bar(_q)}, spec=spec,
                      tendency=selfopt.tendency_by_direction(_k))
        _d = deepen(_k, plugin, _r)
        if _d.deepened:
            kernel, r, deep = _k, _r, _d
            break
    if r is None:
        raise CannotBuild("这一条方向上**没有任何查询**能让 `deepen` 真的展开"
                          "（`T7` 的基线在每一条查询上都「未展开」）⇒ 问不出「会不会红」")
    if injected:
        # 「深化把候选弄丢了」的可观测形态：**深化前**那份被塞进一个不存在的候选。
        r = replace(r, candidates=r.candidates | {"__幽灵__"})
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    t7_deepening(rep, r, deep, kernel, plugin)
    return rep


# ═══ 驱动 ════════════════════════════════════════════════════════════════════

def _cli_report(nodes, edges, *, run_one=None) -> Report:
    """`(cli)` 那一组的公共装配 —— 与 `run_checks.cli_report` **同一条**。"""
    from ldv.checks.cli_paths import CLI_PATH_CODES, run_cli_paths

    rep = Report(plugin="(cli)", expects=CLI_PATH_CODES)
    run_cli_paths((nodes, edges, None), ["keyset", "reach", "sequence"],
                  rep, run_one=run_one)
    return rep


def _cli_flipped(always_guarded: bool):
    """对照实现：**保留** `cli.run_one` 的真实跑法，只把它的**护栏结论**翻掉。

        `always_guarded=False` ⇒ 护栏**永不**开火（正常该红的不红）
        `always_guarded=True`  ⇒ 每条路径都开火（正常该绿的也红）

    ⚠️ 它证明的是「这一组断言**真的在读护栏的结论**」。
       它**不**证明护栏自己写对了 —— 护栏的**存在**由 `cli` 自己的
       `ldv/C7-流程跑通.md` 与五份语料上的 `cli all --remove 12`（退出码 0）守着。
       两条各占其位：这里守的是「`run_checks` 绿 ⇒ `cli` 的四条入口绿」这句话。
    """
    from ldv import cli

    def fake(name, holdout, only, remove=0, loaded=None):
        _ok, text = cli.run_one(name, holdout, only, remove, loaded=loaded)
        return (not always_guarded), text

    return fake


def inj_cli_guard_absent(nodes, edges, injected: bool) -> Report:
    """`CL3` / `CL4` / `CL5` —— 注入「**护栏不见了**」。

    三条的**已知答案是红**（`cli d` 单独跑 / `cli b --holdout 0` / `cli r` 不给 `--remove`
    都是空转）。把护栏结论翻成「永远全过」⇒ 这三条**变绿** ⇒ 断言失败 ⇒ 红。

    ⚠️ 反过来看才说明它有意义：若把这三条写成「必须绿」，那么**拆掉护栏也照样绿** ——
       这就是「空转与通过长得一模一样」在**断言**上的样子。
    """
    return _cli_report(nodes, edges,
                       run_one=None if not injected else _cli_flipped(False))


def inj_cli_guard_always(nodes, edges, injected: bool) -> Report:
    """`CL1` / `CL2` / `CL6` / `CL7` —— 注入「**护栏处处开火**」。

    四条的正常答案是**绿**（`cli a` / `cli b` 单独跑、`--holdout 7`、`--remove 5`）。
    让护栏无条件开火 ⇒ 这四条**变红** ⇒ 断言失败 ⇒ 红。

    ⚠️ 与 `inj_cli_guard_absent` 是**两个不同的分支**，互不替代：
       前者证明「该红的那三条真的红」，后者证明「该绿的那四条真的绿」。
       只用一条的话，「四条本来就恒红」这件事**没人守**。
    """
    return _cli_report(nodes, edges,
                       run_one=None if not injected else _cli_flipped(True))


CASES: dict[str, Callable] = {
    "B1": inj_b1, "B2": inj_b2, "B3": inj_b3, "B4": inj_b4, "B5": inj_b5,
    "B6": inj_b6, "B7": inj_b7, "B8": inj_b8, "B9": inj_b9, "B10": inj_b10,
    "B11": inj_b11, "B12": inj_b12, "B13": inj_b13, "B14": inj_b14, "B15": inj_b15,
    "B16": inj_b16, "B17": inj_b17, "B18": inj_b18, "B19": inj_b19, "B20": inj_b20,
    "A1": inj_a1, "A2": inj_a2, "A3": inj_a3,
    "A4": inj_a4, "A5": inj_a5, "A6": inj_a6, "A7": inj_a7, "A8": inj_a8_deliver_propagated,
    "M0": inj_m0, "M1": inj_m1, "M2": inj_m2, "M3": inj_m3,
    "M5": inj_m5, "M6": inj_m6,
    # ★ `(检索)` 那一组：四条**都是守卫** ⇒ 四条注入**全是手造的**（见上面的段落）。
    #   ⚠️ `T4` 的注入用**真语料**（它判的那一档「未展开」在手造小夹具上未必有）。
    "T1": inj_t1, "T2": inj_t2, "T3": inj_t3, "T4": inj_t4, "T5": inj_t5,
    "T6": inj_t6, "T7": inj_t7, "T8": inj_t8,
    # ★ `(cli)` 那一组：**两条注入覆盖七条**（两个分支互不替代，见各自的 docstring）。
    "CL1": inj_cli_guard_always, "CL2": inj_cli_guard_always,
    "CL6": inj_cli_guard_always, "CL7": inj_cli_guard_always,
    "CL3": inj_cli_guard_absent, "CL4": inj_cli_guard_absent,
    "CL5": inj_cli_guard_absent,
}

#: 同一条检查的**第二条**判据。键是标签，值是 `(判据编号, 注入函数)`。
#: 标签是给人看的，不参与注册表核对 —— 核对比的是**编号**（见 `_registry_gap`）。
EXTRA_CASES: dict[str, tuple[str, Callable]] = {
    "B1·维护路径": ("B1", inj_b1_maintenance),
    "B11·位置敏感": ("B11", inj_b11_uniform),
    "B4·滞留要记账": ("B4", inj_b4_stay_unaccounted),
    "B4·cli 切分": ("B4", inj_b4_cli_split),
    "B16·维护路径": ("B16", inj_b16_maintenance),
    "B16·cli 切分": ("B16", inj_b16_cli_split),
    "B13·树性": ("B13", inj_b13_two_parents),
    "A3·稳定但更细": ("A3", inj_a3_not_coarsest),
    "A5·项不是语料的项": ("A5", inj_a5_bad_item),
    "A5·独立重算不成立": ("A5", inj_a5_unwarranted),
    "A5·根覆盖之外那类不成立": ("A5", inj_a5_out_of_scope),
    "A7·用重算冒充传播": ("A7", inj_a7_recompute),
    "A8·动作谎报": ("A8", inj_a8_mislabelled),
    # ★ `§M1` 的红形态是**对称**的（比 `csr` 更细 / 更粗都红）⇒ 一条注入只够一个侧面。
    #   更细那侧见 `CASES["M1"]`，这里补**更粗**那侧（`q0 = spec.partition`）。
    "M1·反侧（比 csr 更粗）": ("M1", inj_m1_too_coarse),
    "T2·盘上那份读不回来": ("T2", inj_t2_unreadable),
}


def _result(rep: Report, code: str) -> Tri:
    for a in rep.assertions:
        if a.code == code:
            return a.result
    return Tri.UNEXPANDED


def _registry_gap() -> tuple[list[str], list[str]]:
    """注册表对不上的地方 —— 返回 `(声明了但没验, 验了但没声明)`。

    比的两个集合都**外生给定**，不看任何一边的自我声明：

    左边  `run_checks.PLUGIN_CODES | KERNEL_CODES | abstraction.VIEW_CODES
           | multilevel.MULTILEVEL_CODES | cli_paths.CLI_PATH_CODES
           | retrieval.RETRIEVER_CODES`
              —— 声明要跑的编号
    右边  `CASES` 的键 ∪ `EXTRA_CASES` 的值          —— 真被验过的编号

    ⚠️ **每一组必须一起比进来**，不能只比插件与内核那两批：
       视图那七条 / 层叠那七条 / CLI 那七条 / 检索那三条**各是另一组**
       （`Report(plugin="(视图)")` / `(L0)` / `(cli)` / `(检索)`），
       漏掉任一组的话「新增一条判据、忘了配注入」这件事**照样报全绿** ——
       而这句话对新增的编号一个字节的信息都没有。这正是本节开头那个形状。
       （`MULTILEVEL_CODES` 是 2026-10-08 补进来的 —— 补之前那一组**没有任何东西守**；
         `RETRIEVER_CODES` 是本轮补的，与 `(检索)` 那三条注入**必须同时做**。）

    ⚠️ 反过来也要比：`VIEW_CODES` **只列已经实现了的**。
       少实现一条就**不写进来** —— 于是「套件全绿」这句话**不覆盖它**，
       而且这里是**双向**核对，多写一个编号也会红。

    ## ⚠️ 有一批编号**故意不在这里**：`D1`–`D6`（删除路径）

    它们**不是遗漏，是另一套机制**：
    `D2` / `D3` 在真 `Kernel` 上**按构造恒绿** ⇒ 它们靠 `run_tests.test_deletion_path`
    的**「错一处」对照内核**每条各验一次（18/18 格：基线绿、注入红），
    而不是靠这里的注入表。⇒ 把它们并进 `declared` 会让**这个**核对要一套它不需要的夹具。

    ★ 这件事**已经写在明处**（`run_checks.py` 里那一行 `rep.note` 的注释逐字说：
      「`D1–D6` … 不在 `PLUGIN_CODES` / `KERNEL_CODES` / `VIEW_CODES` / `MULTILEVEL_CODES` 里
      ⇒ 这一行**不声称**覆盖它们」，并写明这句话**必须印出来**）。
      ⇒ 这里再指一次，是因为：**注释里的「不在范围」不写进这一处，
        读这个函数的人无法区分「故意不列」与「忘了列」** ——
        而这两件事的症状**一模一样**（本节开头那个形状）。

    ## 两个方向都要报，因为它们**症状不同**：

        声明了但没验   新加一条检查、忘了配注入 ⇒ 套件报「全绿」，
                       而那个「全绿」对新增的编号一个字节的信息都没有
        验了但没声明   注入还在、检查被删了 ⇒ 注入在验一条没人跑的判据

    ## 为什么这条自检非有不可

    上面第一种正是本仓库一直在防的形状（「空转与通过长得一模一样」），
    只不过它这次出现在**注册表**上。别处没有任何东西守它，所以守在这里。
    """
    from ldv.checks.abstraction import VIEW_CODES
    from ldv.checks.cli_paths import CLI_PATH_CODES
    from ldv.checks.multilevel import MULTILEVEL_CODES
    from ldv.checks.retrieval import RETRIEVER_CODES
    from ldv.run_checks import KERNEL_CODES, PLUGIN_CODES

    declared = (set(PLUGIN_CODES) | set(KERNEL_CODES) | set(VIEW_CODES)
                | set(MULTILEVEL_CODES) | set(CLI_PATH_CODES)
                | set(RETRIEVER_CODES))
    covered = set(CASES) | {code for code, _ in EXTRA_CASES.values()}
    return sorted(declared - covered), sorted(covered - declared)


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 没有语料 —— 注入验证跑不了")
        return 0
    nodes, edges, _ = loaded

    print("═══ 注入验证：每条检查都要能红 ═══")
    failures: list[str] = []

    # ── 第 0 条：注册表自检 —— 「全绿」这句话覆盖到了哪些编号 ────────────────
    undeclared, unverified = _registry_gap()
    if undeclared or unverified:
        if undeclared:
            print(f"  ✗ 注册表  {undeclared}  **声明了要跑，却没有任何注入验过它**"
                  f" —— 上面的「全绿」管不到它们")
            failures.append(f"注册表: {undeclared} 未被注入覆盖（新增检查忘了配注入？）")
        if unverified:
            print(f"  ✗ 注册表  {unverified}  **有注入，却不在 `run_checks` 声明的编号里**"
                  f" —— 注入在验一条没人跑的判据")
            failures.append(f"注册表: {unverified} 有注入但不在声明里（检查删了、注入没删？）")
    else:
        print(f"  ✓ 注册表  {len(CASES)} 个编号 + "
              f"{len(EXTRA_CASES)} 条副判据 —— 与 `run_checks` 声明的那批**恰好相等**")
    n = 1  # 上面这条也算一条

    cases: list[tuple[str, str, Callable]] = [(c, c, fn) for c, fn in CASES.items()]
    cases += [(label, code, fn) for label, (code, fn) in EXTRA_CASES.items()]

    # ⚠️ **三类要分得开**（`跳过 ≠ 通过`；`崩比红更糟`）：
    #
    #     ✗ 没通过   基线绿、注入没红 ⇒ 判据**开了火却没红** —— 这才是「这条注入没用」
    #     ∅ 判不了   基线**不是**绿的 ⇒ 本语料压根不开火这条判据 ⇒ 问不出「能不能红」
    #     ⚡ 崩      夹具/实现抛异常 ⇒ 连判据都没跑起来
    #                （`CannotBuild` 不算崩：那是夹具**明说**「这份料上做不出来」）
    #
    # 原来三类**都**进 `failures`、都印 `✗` —— 「判不了」与「真没通过」长得一模一样。
    # 本仓库对这件事有明文（`§10` 停止条件：**跳过 ≠ 通过**；`B11` 那条修正：
    # **判不了 ≠ 红**）⇒ 换语料跑时，这个混淆会把「这份料太小」读成「判据坏了」。
    #
    # ⚠️ **但随仓库那份语料上 `∅` 不成立**：那份是**基准**，70 条本来就该全开火。
    #    所以跑的是它时 `∅` 降级回 `✗` —— 否则一条真回归会被读成「判不了」而放过。
    shipped = Path(__file__).resolve().parents[1] / "corpus" / "nodes"
    on_shipped = find_corpus() == shipped

    nope: list[str] = []      # ✗ 真没通过
    cannot: list[str] = []    # ∅ 本语料判不了
    broke: list[str] = []     # ⚡ 崩

    for label, code, fn in cases:
        try:
            base = _result(fn(nodes, edges, False), code)
        except CannotBuild as exc:
            cannot.append(f"{label}: 夹具在本语料上做不出来 —— {exc}")
            print(f"  ∅ {label}  夹具做不出来：{exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            broke.append(f"{label}: 基线就抛异常 {type(exc).__name__}: {exc}")
            print(f"  ⚡ {label}  基线抛异常：{type(exc).__name__}: {exc}")
            continue
        try:
            shot = _result(fn(nodes, edges, True), code)
        except CannotBuild as exc:
            cannot.append(f"{label}: 夹具在本语料上做不出来 —— {exc}")
            print(f"  ∅ {label}  夹具做不出来：{exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            broke.append(f"{label}: 注入后抛异常 {type(exc).__name__}: {exc}")
            print(f"  ⚡ {label}  注入后抛异常：{type(exc).__name__}: {exc}")
            continue

        if base is not Tri.YES:
            why = f"基线不是绿的（{base}）"
            if on_shipped:
                # 基准语料上「判不了」不成立 —— 它本来就该全开火 ⇒ 是回归，不是前提。
                nope.append(f"{label}: {why}")
                print(f"  ✗ {label}  基线={base}  注入={shot}"
                      f"   ← {why}（**随仓库那份语料上不该判不了**）")
            else:
                cannot.append(f"{label}: {why}")
                print(f"  ∅ {label}  基线={base}  注入={shot}"
                      f"   ← 本语料判不了（{why}；跳过 ≠ 通过）")
            continue
        if shot is Tri.UNEXPANDED:
            # ⚠️ **注入后判不了 ≠ 注入没红**（`§10` 停止条件：跳过 ≠ 通过；
            #    `B11` 那条修正：**判不了 ≠ 红**）。
            #    注入可能把语料推到「这条判据**没有内容**」的形态 —— 例：4 项语料上
            #    `B15` 注入后结构塌成 1 个方向，「同构」无从谈起（判据自己印了这句话）。
            #    这时**问不出**「判据能不能红」，与「判据开了火却没红」是两件事：
            #    后者才是「这条注入没用」。
            #    ⚠️ 基准语料上不该出现这种形态 ⇒ 那里仍算回归（与上面 `∅` 同一个闸门）。
            why = f"注入后判不了（{shot}）—— 判不了 ≠ 红"
            if on_shipped:
                nope.append(f"{label}: {why}")
                print(f"  ✗ {label}  基线={base}  注入={shot}"
                      f"   ← {why}（**随仓库那份语料上不该判不了**）")
            else:
                cannot.append(f"{label}: {why}")
                print(f"  ∅ {label}  基线={base}  注入={shot}   ← {why}")
            continue
        if shot is not Tri.NO:
            why = f"注入后没变红（{shot}）"
            nope.append(f"{label}: {why}")
            print(f"  ✗ {label}  基线={base}  注入={shot}   ← {why}")
            continue
        print(f"  ✓ {label}  基线={base}  注入={shot}")

    print()
    n += len(cases)

    # ── 度量那一条：问的不是「红不红」，是「读数会不会变」 ──────────────────
    for label, fn in (("度量·增量≡全量", metric_scale_matches),
                      ("度量·分辨率不变", metric_resolution_stable),
                      ("度量·覆盖嵌套", metric_cover_nesting),
                      ("度量·健全性（维护路径）", metric_soundness),
                      ("度量·覆盖不漏", metric_cover_leak),
                      ("度量·细化量", metric_progress)):
        n += 1
        try:
            m_base = fn(nodes, edges, False)
            m_shot = fn(nodes, edges, True)
        except Exception as exc:  # noqa: BLE001
            broke.append(f"{label}: 抛异常 {type(exc).__name__}: {exc}")
            print(f"  ⚡ {label}  抛异常：{type(exc).__name__}: {exc}")
            continue
        if not m_base:
            why = "基线就不一致（度量本来该说一致）"
            if on_shipped:
                nope.append(f"{label}: {why}")
                print(f"  ✗ {label}  基线一致=False   ← {why}（**随仓库那份语料上不该如此**）")
            else:
                cannot.append(f"{label}: {why}")
                print(f"  ∅ {label}  基线一致=False   ← 本语料上度量的前提不成立（{why}）")
            continue
        if m_shot:
            why = "注入后读数没变（度量对缺陷不敏感）"
            if on_shipped:
                nope.append(f"{label}: {why}")
                print(f"  ✗ {label}  基线一致=True  注入后仍一致=True"
                      f"   ← {why}（**随仓库那份语料上不该如此**）")
            else:
                cannot.append(f"{label}: {why}")
                print(f"  ∅ {label}  基线一致=True  注入后仍一致=True"
                      f"   ← 本语料上这个注入没碰到它量的东西（{why}）")
            continue
        print(f"  ✓ {label}  基线一致=True  注入后仍一致=False")

    failures = nope + broke + cannot
    if failures:
        print(f"═══ 真没通过 {len(nope)} · 判不了 {len(cannot)} · 崩 {len(broke)}"
              f"（共 {n} 条）═══")
        for f in nope:
            print(f"  ✗ {f}")
        for f in broke:
            print(f"  ⚡ {f}")
        for f in cannot:
            print(f"  ∅ {f}")
        return 1
    print(f"═══ {n}/{n} 条：基线绿、注入红 ═══")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
