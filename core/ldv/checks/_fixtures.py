"""夹具 —— 把语料、三个插件、内核、查询集装起来。

**查询的 `ideal` 是外生声明的 ground truth**，由语料算出来，不是插件产出的。
`B1`（假阴）拿它当答案比 —— 拿被测对象自己产出的东西当答案就什么都验不出来。

**随机源是定死的**（`random.Random(seed)`）—— 同数据同历史 ⇒ 同结构，
这是 §8.2 要的可复现性，也让「红了」这件事本身可复现。
"""

from __future__ import annotations

import copy
import os
import random
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.kernel import Kernel, Query
from ..corpus.loader import CorpusError, Node, load_edges, load_nodes

#: 语料目录的候选位置 —— **仓库内的那份排在最前**。
#:
#: ⚠️ 语料路径**绝不能**写死成开发机绝对路径（形如 `C:/Users/<某人>/.../nodes`）：
#: 那对**任何 clone 这个仓库的人**都不存在，而症状不是报错、是**检查报「跳过」**——
#: 一个「语料不在」的仓库和一个「检查全过」的仓库，在汇总上长得不一样（跳过要显式报出），
#: 但都很容易被读成「没问题」。所以：
#:
#:     `LDV_CORPUS` 环境变量（**路径或名字**）→ `ldv/corpus/nodes/`（仓库内，首选）
#:                                            → 仓库根下的 `corpus/nodes/`
#:
#: 找不到就**报跳过**，绝不报通过（`run_checks` 与 `run_tests` 都这样；
#: `.github/workflows/ci.yml` 另有一条判据专门守「语料真的来自仓库内」）。
#:
#: ⚠️ **但「显式指定却找不到」是另一回事** —— 那是报错，不是跳过。见 `find_corpus`。
_HERE = Path(__file__).resolve().parent          # …/ldv/checks


def external_corpus_candidates(name: str) -> tuple[Path, ...]:
    """按**名字**找外部语料：`_data/<名字>/nodes`（仓库旁，再上一级）。

    外部数据**不进仓库**（怎么取、丢了什么，见 `ldv/tools/intake_openalex.py`
    的映射表与 DROP 表）。所以 `LDV_CORPUS=openalex-citations` 这种**名字**写法
    必须能被解析 —— 否则「把料接进来跑一遍」只能靠绝对路径，而绝对路径
    正是上面那条警告要消灭的东西。
    """
    root = _HERE.parent.parent                   # <仓库根>
    return (root / "_data" / name / "nodes",
            root.parent / "_data" / name / "nodes")   # 仓库旁 → 上一级


def corpus_candidates() -> tuple[Path, ...]:
    """**完整候选链**（从具体到兜底）—— 给报错信息与测试看的。

    ⚠️ `find_corpus` **不**直接拿它当查找池：显式指定时只认前两项。
    写成一条链会让「显式指定但找不到」**悄悄走到兜底**上，而那条路是错的。
    """
    env = os.environ.get("LDV_CORPUS")
    out: list[Path] = []
    if env:
        out.append(Path(env))
        out.extend(external_corpus_candidates(env))   # ★ 也允许只给**名字**
    out.append(_HERE.parent / "corpus" / "nodes")            # ldv/corpus/nodes
    out.append(_HERE.parent.parent / "corpus" / "nodes")     # <仓库根>/corpus/nodes
    return tuple(out)


SEED = 20261005


def find_corpus() -> Path | None:
    """找语料目录。**显式指定却找不到 ⇒ 报错，不回落到默认语料。**

    ⚠️ 这一条不是洁癖：回落的后果是「语料**指错了**」与「语料**指对了**」
    **长得一模一样** —— `LDV_CORPUS=openalex-citation`（少个 s）跑的是仓库里那 36 项，
    而所有检查照样报绿、退出码 0。**空转与通过长得一模一样**，
    这一次出现在**指路**上。

    ⇒ 两条路**互斥**：设了 `LDV_CORPUS` 就只在显式候选里找，找不到报错；
       没设才走兜底链，兜底链全落空才报「跳过」。
    """
    env = os.environ.get("LDV_CORPUS")
    pool = ((Path(env),) + external_corpus_candidates(env)) if env else (
        _HERE.parent / "corpus" / "nodes",
        _HERE.parent.parent / "corpus" / "nodes",
    )
    for cand in pool:
        # ⚠️ **`rglob` + 短路**（2026-10-18）：摄入侧会把节点分片到子目录，
        #    `glob("*.md")` 在分片目录下**一个都找不到** ⇒ 判定「这不是语料」。
        #    用 `next(rglob(...), None)` 而不是 `any(rglob(...))` —— 后者会把
        #    10 万个文件全遍历一遍，这里只要知道「有没有」。
        if cand.is_dir() and next(cand.rglob("*.md"), None) is not None:
            return cand
    if env:
        raise CorpusError(
            f"LDV_CORPUS={env!r} 找不到语料 —— 试过：{[str(c) for c in pool]}。"
            " 显式指定的语料找不到时**不回落到默认语料**"
            "（回落会让「指错了」和「指对了」长得一模一样）。")
    return None


def load(predicates: bool = False, indexed: bool = False) -> tuple[dict[str, Node], dict[str, frozenset[str]], frozenset[str]] | None:
    """读语料。`predicates=True` ⇒ **追加推导谓词**（`corpus/predicates.py`）。

    ⚠️ 默认 `False` —— 现有全部套件（`run_checks` 97 / `run_tests` 624 / 注入 71）
       走的都是**不带谓词**的那条路，基线一个数都不变。
    ⚠️ 谓词是**追加**进 `Node.keys`（`keyset` 只读 `item["keys"]` ⇒ 插件不用改），
       不是替换现有键 —— 所以 `type` 那批手写档位仍在，两者并存。
    """
    root = find_corpus()
    if root is None:
        return None
    # ★ **长期缓冲（可选入口）**：跨进程派生索引，避免重复读盘。
    #   实测 ext-coauth：直读 4.66s → 索引命中 0.63s（**7.4×**）。
    #   ⚠️ 索引里**没有 body**（主流程一处都不用正文）⇒ 索引路径的 `Node.body` 是空串。
    if indexed:
        from ..corpus.index import load_nodes_indexed
        nodes, _stats = load_nodes_indexed(root)
    else:
        nodes = load_nodes(root)
    edges, dangling = load_edges(nodes)
    if predicates:
        from ..corpus.predicates import (COMPLEXITY_K, apply_basic,
                                         apply_combinations, apply_to_nodes,
                                         choose_partition, combine, derive,
                                         load_basic, load_rules)
        rules, budget = load_rules(root.parent)
        used, _dropped, _params = derive(nodes, edges, rules, budget)

        # ★★ **组合谓词共用预算**（2026-10-20 用户拍板：「共用预算，可以」）
        #
        #   在这之前 `combine()` **只在测试里被调用** —— 生产路径一次都没算过，
        #   所以「组合谓词」在 CLI 里等于不存在（不是「被挡下」，是「没算」）。
        #
        #   现在的口径：单谓词与「复杂度 ≤ K 的组合」一起按**区分度**排序，
        #   抢**同一份**预算 N（不是各给一份，也不绕过）。
        #   实测 5 份语料：mini（0.492）与 bitcoin（0.472）上组合**真的赢**，
        #   默认/pypi/wiki 上输给单谓词 —— 是竞争，不是摆设。
        #
        #   ⚠️ `复杂度 > K` 的进「待命名」——**提示，不阻塞**：键名默认就是合取
        #      表达式（可读），人想改成「活跃作者」再改，不挡路。
        _to_name, _cands, _crej = combine(nodes, edges, used, COMPLEXITY_K)
        _singles, _combos, _out = choose_partition(used, _cands, budget)
        apply_to_nodes(nodes, edges, _singles)
        apply_combinations(nodes, edges, _combos, used)

        # ★ **人命名**的基础谓词（`基础谓词` 段）—— 人给组合起的名字，
        #   与自动名**并存**（两个键都加上去，不是替换）。
        basic = load_basic(root.parent)
        if basic:
            # ⚠️ 传 **used + dropped**（预算内 + 预算外）—— 预算管的是「自动选谁」，
            #    不是「人能声明什么」。只传 `used` 会让「引用预算外的谓词」**静默跳过**
            #    （实测：加键 0 项、无任何提示）。
            apply_basic(nodes, edges, basic, list(used) + list(_dropped))
    return nodes, edges, dangling


def items(nodes: dict[str, Node]) -> dict[str, dict[str, Any]]:
    return {nid: n.as_item() for nid, n in nodes.items()}


# --- 查询集 -----------------------------------------------------------------

def keyset_queries(nodes: dict[str, Node]) -> list[Query]:
    """按「键」造查询，`ideal` 由语料算出。"""
    vocab = sorted({k for n in nodes.values() for k in n.keys})
    picks = [k for k in vocab if "=" in k] or vocab[:4]
    out: list[Query] = []
    for k in picks:
        out.append(Query(ideal=_ideal(nodes, {k}, set()), require=frozenset({k}),
                         label=f"有 {k}"))
    for i, k in enumerate(picks):
        other = picks[(i + 1) % len(picks)]
        out.append(Query(ideal=_ideal(nodes, {k, other}, set()),
                         require=frozenset({k, other}), label=f"同时有 {k}+{other}"))
    for k in picks:
        out.append(Query(ideal=_ideal(nodes, set(), {k}), forbid=frozenset({k}),
                         label=f"没有 {k}"))
    # 一个**必然为空**的查询：两个互斥的枚举取值同时要求
    out.append(Query(ideal=frozenset(), require=frozenset({"type=概念", "type=案例"}),
                     label="互斥要求（应为空）"))
    return out


def _ideal(nodes: dict[str, Node], require: set[str], forbid: set[str]) -> frozenset[str]:
    return frozenset(
        nid for nid, n in nodes.items()
        if require <= n.keys and not (n.keys & forbid)
    )


def reach_queries(nodes: dict[str, Node], edges: dict[str, frozenset[str]]) -> list[Query]:
    """按「项集合」造查询，`ideal` 是**外生挑的**项集合。"""
    rng = random.Random(SEED)
    ids = sorted(nodes)
    out: list[Query] = []
    for size in (1, 3, 6, 12):
        for t in range(3):
            ideal = frozenset(rng.sample(ids, min(size, len(ids))))
            out.append(Query(ideal=ideal, label=f"随机 {len(ideal)} 项 #{t}"))
    out.append(Query(ideal=frozenset(ids), label="全部项"))
    out.append(Query(ideal=frozenset(), label="空集"))
    return out


# --- 内核装配 ---------------------------------------------------------------

def build_keyset(nodes: dict[str, Node]) -> tuple[Kernel, Any]:
    from ..plugins.keyset import KeysetPlugin

    plug = KeysetPlugin()
    k = Kernel(plug, items(nodes))
    k.build(plug.merge([]))              # 根 = (∅, ∅)：最外层意图不约束键
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


def build_reach(nodes: dict[str, Node], edges: dict[str, frozenset[str]],
                traverse_budget: int = 10_000) -> tuple[Kernel, Any]:
    from ..plugins.reach import ReachPlugin

    plug = ReachPlugin(edges, traverse_budget=traverse_budget)
    k = Kernel(plug, items(nodes))
    # 根 = 全部项当锚点：最外层意图声明为「覆盖一切」
    k.build(frozenset(nodes))
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


# --- 只建根的内核（B8 用） ---------------------------------------------------

def make_keyset_root(nodes: dict[str, Node]) -> Any:
    """每次调用返回一个**全新**的、只建了根的内核 —— B8 逐项插入要用。"""
    def _make() -> Kernel:
        from ..plugins.keyset import KeysetPlugin

        plug = KeysetPlugin()
        k = Kernel(plug, items(nodes))
        k.build(plug.merge([]))
        return k

    return _make


def make_reach_root(nodes: dict[str, Node], edges: dict[str, frozenset[str]],
                    traverse_budget: int = 10_000) -> Any:
    def _make() -> Kernel:
        from ..plugins.reach import ReachPlugin

        plug = ReachPlugin(edges, traverse_budget=traverse_budget)
        k = Kernel(plug, items(nodes))
        k.build(frozenset(nodes))
        return k

    return _make


# --- 方向 C 的序列 -----------------------------------------------------------

def sequences(nodes: dict[str, Node],
              edges: dict[str, frozenset[str]], cap: int = 6) -> dict[str, tuple[str, ...]]:
    """`seq(x)` = 从 x 出发的**确定性链**的 `type` 值序列。

    每步取 **id 最小**的未访问后继，走到无后继或已访问为止（最多 `cap` 步）。
    「确定性」是必须的：只要有一个环节依赖遍历顺序，方向就不可复现（§8.2）。
    """
    out: dict[str, tuple[str, ...]] = {}
    for nid in sorted(nodes):
        toks: list[str] = []
        seen = {nid}
        cur = nid
        for _ in range(cap):
            nxt = sorted(v for v in edges.get(cur, ()) if v not in seen)
            if not nxt:
                break
            cur = nxt[0]
            seen.add(cur)
            toks.append(str(nodes[cur].fields.get("type")))
        out[nid] = tuple(toks)
    return out


def build_sequence(nodes: dict[str, Node], edges: dict[str, frozenset[str]]) -> tuple[Kernel, Any]:
    from ..plugins.sequence import SequencePlugin

    plug = SequencePlugin(sequences(nodes, edges))
    k = Kernel(plug, items(nodes))
    k.build(frozenset({()}))              # 根 = 空前缀：覆盖一切
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


def make_sequence_root(nodes: dict[str, Node], edges: dict[str, frozenset[str]]) -> Any:
    def _make() -> Kernel:
        from ..plugins.sequence import SequencePlugin

        plug = SequencePlugin(sequences(nodes, edges))
        k = Kernel(plug, items(nodes))
        k.build(frozenset({()}))
        return k

    return _make


# --- 「先建 k、再维护 N−k」的构造器 ------------------------------------------
#
# 与上面 `make_*_root` 的区别：那个只建**根**（0 项），这个建的是
# 「**当时只认识这 k 项**」的内核 —— 后来那些项走 `insert(id, item)` 后添。
# 这正是流程 B 的维护路径，也是「增量 ≡ 全量」要比的那一侧。

def make_builder(which: str, nodes: dict[str, Node],
                 edges: dict[str, frozenset[str]]) -> Any:
    """返回 `build(subset_ids) -> (kernel, plugin)`。

    `subset_ids` 是**构造时内核就认识**的项；其余项由调用方 `insert` 后添。

    ⚠️ **根 payload 必须跟着 subset 走**，不能偷偷用全集：
       `reach` 的根是「全部项当锚点」，增量时它只该是「**当时已知的**锚点」。
       用全集就等于作弊 —— 那一侧就不是增量了。
    """
    all_items = items(nodes)

    def build(subset_ids: list[str]) -> tuple[Kernel, Any]:
        from ..plugins.keyset import KeysetPlugin
        from ..plugins.reach import ReachPlugin
        from ..plugins.sequence import SequencePlugin

        sub = sorted(set(subset_ids))
        known = {i: all_items[i] for i in sub}
        if which == "keyset":
            plug = KeysetPlugin()
            k = Kernel(plug, known)
            k.build(plug.merge([]))
        elif which == "reach":
            plug = ReachPlugin(edges)
            k = Kernel(plug, known)
            k.build(frozenset(sub))          # ← 当时**已知**的锚点，不是全集
        elif which == "sequence":
            plug = SequencePlugin(sequences(nodes, edges))
            k = Kernel(plug, known)
            k.build(frozenset({()}))
        else:
            raise ValueError(which)
        for i in sub:
            k.insert(i)
        return k, plug

    return build


def build_incremental(build: Any, nodes: dict[str, Node],
                      init_ids: list[str], later_ids: list[str]) -> Kernel:
    """先建 `init_ids`，再把 `later_ids` 逐个 `insert(id, item)` 后添。

    ⇒ 结束时**项集与全集相同**。所以任何结构差异都是**结构性**的，
      不是「项少了」造成的 —— 这一点由 `divergence.py` 显式断言。
    """
    all_items = items(nodes)
    k, _ = build(init_ids)
    for i in later_ids:
        k.insert(i, all_items[i])
    return k


# --- 「同一份装配只建一次」 ---------------------------------------------------
#
# 一次 `run_checks --no-probes` 里，**内核建造占了墙钟的绝大部分**，而其中很大一块
# 是**同一份装配被建了不止一遍** —— 因为每个消费者都要一份**pristine** 的内核。
#
# 实测（`snap-email-eu` 1005 项，`outputs/_measure_build_duplication.py`，只包
# `Kernel.build` / `Kernel.insert` 两个方法，不抄序列）：
#
#     调用者                          根次  等价全量建      秒    占墙钟
#     cli:_assemble                    24      23.76    586.23    55.7%
#     structure:b15_reproducible         9       9.00    203.09    19.3%
#     run_checks:batch_kernel           6       6.00     70.11     6.7%
#     run_checks:maintenance_kernel     3       3.00     65.77     6.3%
#     structure:b8_invalidation_local   3       3.00     59.42     5.6%
#     ──────────────────────────────────────────────────────────────
#     合计                             46      45.89    987.19    93.8%
#
# 其中**参数完全相同**的重复：`cli:_assemble` 的 24 次里 **15 次**（`holdout=12`
# 那一档 —— `CL1`/`CL2`/`CL3`/`CL5`/`CL7` 与 `cli_split_kernel` 的 `_assemble`
# 入参**逐字相同**，`remove` 只影响建完**之后**挑哪几项待删，不进建造）；
# `batch_kernel` 的 6 次里 **3 次**（`(视图)` / `(L0)` / `(检索)` 各建一份）。
#
# ⇒ 建一次、**每份发一个深拷贝**。实测 `copy.deepcopy(内核)` 是建造的
#   **0.0046 倍**（0.22 s vs 45.5 s，`outputs/_probe_deepcopy_cost.py`）。
#
# ⚠️ **为什么不直接共享同一个对象**：判据跑查询会经 `kernel.query` → `expand`
#    **就地改内核**（`retriever.py:713` 自注「唯一写动作」），流程 A–E 也改。
#    共享 = 让后面的消费者拿到一个**已经被前面的消费者改过**的树 —— 而那正是
#    本仓库的中心反模式（退化与正常长得一样）。
#    实测形状：`cli d` 单独跑**必须红**，理由就是「A 没先跑 ⇒ 没有带权记录」；
#    共享内核会让这条护栏**静默变绿**。**必须拷。**
#
# ⚠️ 判据（能红的那条）在 `run_tests.test_reusable_build` —— 它验的是
#    ① 拷贝与**现建**的那份逐字同构；② 拷贝**不是**同一个对象（改一个不动另一个）。
#    ② 才是这条缓存真正危险的地方，所以它单独占一条断言。

#: 缓存：`tag -> (nodes, edges, pristine)`。
#: ⚠️ **同时持有 `nodes` / `edges` 的引用**，取用时按 `is` 比对 —— 这样 `id()` 被
#:    回收复用**不会**造成「拿错语料的那份」。同一次 `main()` 里 `loaded` 只有一份，
#:    所以正常路径上恒命中；换一份语料（新对象）⇒ 恒未命中 ⇒ 重算。
_BUILD_CACHE: dict[tuple, tuple] = {}


def reusable_build(tag: tuple, nodes: Any, edges: Any,
                   produce: Callable[[], tuple]) -> tuple:
    """`produce()` 建一次；之后每次返回一份**新的深拷贝**。

    `tag` 必须把「哪份装配」说全（方向 + 初始项集的选择），否则两种装配会撞键。
    """
    ent = _BUILD_CACHE.get(tag)
    if ent is not None and ent[0] is nodes and ent[1] is edges:
        return copy.deepcopy(ent[2])
    res = produce()
    _BUILD_CACHE[tag] = (nodes, edges, copy.deepcopy(res))
    return res


def clear_build_cache() -> None:
    """清空 —— 只在测试里用（`main()` 一趟一个进程，不必清）。"""
    _BUILD_CACHE.clear()


# --- 「不可分等价类」—— `B17` 的**外生** oracle ------------------------------
#
# 两个项在该方向的**语义下不可分** ⟺ 对**任何** payload 它们都给同一答案。
# 这个关系**必须外生算**（不调插件），否则就是 `false-green` 形状 3「共享盲点」：
# 拿插件的谓词去算「插件能不能分开」，两边永远同时通过。
#
#     keyset    键集相同           —— 方向谓词只看 `keys(x)`
#     sequence  序列相同           —— 方向谓词只看 `seq(x)` 的前缀
#     reach     **互相可达**        —— 互相可达 ⇒ 对任何锚点集同进同出；
#                                    反之（z 走不到 y）取 S={y} 就分开
#
# ⚠️ **reach 的等价关系不是「汇点签名相同」。** 实测签名只有 3 类，
#    而内核把它们分成了 36 个叶 —— 签名相同**不**蕴涵不可分。
#    这一点由 `equivalence.py` 的读数显式报出（它是「payload 比签名更有表达力」的证据）。


def _forward_closure(edges: dict[str, frozenset[str]],
                     universe: Iterable[str] | None = None) -> dict[str, frozenset[str]]:
    """每个项**从它出发**能走到的项（含它自己）—— **正向** BFS。

    与插件的 `reachable()`（从锚点出发的**反向** BFS）方向相反、代码不共用 ——
    所以不是「同一条路的两个说法」。
    n = 36 时 O(n·(n+m)) 完全够用；**不引 Tarjan**，因为「简单」本身就是
    checker 的硬要求（McConnell 等 2011 的 Simplicity，p.22/§5.1）。
    """
    starts = list(universe) if universe is not None else list(edges)
    out: dict[str, frozenset[str]] = {}
    for start in starts:
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in edges.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out[start] = frozenset(seen)
    return out


def _reverse_closure(edges: dict[str, frozenset[str]],
                     universe: Iterable[str] | None = None) -> dict[str, frozenset[str]]:
    """每个项**反向**闭包：能走到它的项（含它自己）。

    ⚠️ 这是**同一段 `_forward_closure` 喂反向图**，不是另写一份遍历。
       两份手写的遍历会**一起**错（`false-green` 形状 3「共享盲点」）；
       这里要的是「同一段已验证的代码 + 不同的输入」。

    为什么需要它：`reach` 的覆盖是「能走到锚点的**所有**项」，按定义就是
    `∪_{a ∈ payload} rev[a]`。原来的实现按正向闭包算
    （`fwd[x] & anchors`，逐项扫），**代价形状完全不同** —— 见 `coverage_of`。
    """
    starts = list(universe) if universe is not None else list(edges)
    rev: dict[str, set[str]] = {n: set() for n in starts}
    for src, dsts in edges.items():
        for dst in dsts:
            rev.setdefault(dst, set()).add(src)
    return _forward_closure({k: frozenset(v) for k, v in rev.items()}, universe=starts)


def _mutually_reachable(edges: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """每个项的**强连通分量**（= 与它互相可达的项，含它自己）。"""
    fwd = _forward_closure(edges)
    return {n: frozenset(m for m in fwd[n] if n in fwd[m]) for n in fwd}


def equiv_classes(which: str, nodes: dict[str, Node],
                  edges: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """把每个项映到它的**不可分等价类**（一个含它自己的集合）。

    同一个集合 ⟺ 两个项在该方向语义下不可分。**不调插件。**
    """
    if which == "keyset":
        return {i: frozenset(j for j in nodes if nodes[j].keys == nodes[i].keys)
                for i in nodes}
    if which == "sequence":
        seqs = sequences(nodes, edges)
        return {i: frozenset(j for j in nodes if seqs[j] == seqs[i]) for i in nodes}
    if which == "reach":
        return _mutually_reachable(edges)
    raise ValueError(which)


# --- 外生**语义**：标注 —— `B20` 的 oracle ------------------------------------
#
#     语义(d) = 「d 的成员带哪些标签」。它**必须来自语料**（人工标注），
#     不能来自插件 —— 拿插件自己的谓词当语义就是 `false-green` 形状 3「共享盲点」，
#     与 `equiv_classes` / `coverage_of` 是同一条纪律。
#
#     方向      `B17` 的 oracle（**结构**）      `B20` 的 oracle（**语义**）
#     ─────────────────────────────────────────────────────────────────────
#     keyset    键集相同                        ← 都来自 `Node.keys`，
#     sequence  序列相同                          但**语义**问的是「意思一样吗」，
#     reach     互相可达                          而只有**标注**能回答那个
#
# ⚠️ 标签取 `type` 字段，因为 `loader.ENUM_FIELDS` 只把 `type` / `evidence_status`
#    的**取值**放进键集 ⇒ 「标签在不在某个方向的键空间里」成了一个**可查的事实**
#    （`label_bearing`），而不是一句声明。

LABEL_FIELD = "type"


def labels(nodes: dict[str, Node]) -> dict[str, str]:
    """外生**语义**：每个项的标签。**语料没有标注 ⇒ 返回 `{}`**（不是报错）。"""
    out: dict[str, str] = {}
    for nid, node in nodes.items():
        v = node.fields.get(LABEL_FIELD)
        if isinstance(v, str) and v:
            out[nid] = v
    return out


def label_bearing(which: str, nodes: dict[str, Node]) -> bool:
    """这条方向在这份语料上**是不是标签轴** —— 由**语料**决定，不由声明决定。

    判据：标签的**取值键**（`type=<值>`）出现在项的键集里 ⇒ 这个方向的键空间
    包含标签 ⇒ 它的 `split` 可以按标签切 ⇒ 「语义增益」在它上面**有内容**。

    ⚠️ 反过来，`reach` / `sequence` 的 payload 不是键集 ⇒ 它们**不是**标签轴
       ⇒ `B20` 在它们上面只报不判。这与 `B17`(b) 在 `reach` 上降级是**同一个形状**：
       判据的**适用范围**由「它在这条方向上有没有内容」决定，不由偏好决定。

    ⚠️ 这条**不是**「语料里有没有标注」—— 有标注但没有任何方向的轴含标签时，
       它照样返回 `False`，因为那时 `B20` 判不了任何东西。
    """
    if which != "keyset":
        return False
    vals = set(labels(nodes).values())
    if len(vals) < 2:
        return False
    keys = set().union(*[n.keys for n in nodes.values()]) if nodes else set()
    return all(f"{LABEL_FIELD}={v}" in keys for v in vals)


# --- 外生**覆盖**定义 —— 覆盖类判据的公共 oracle -----------------------------
#
#     覆盖(d) = 「按该方向的**语义**，d 说『是』的那些项」
#
# 这份定义**必须外生算**（不调插件、不调内核），理由与 `equiv_classes` 完全相同：
#
#     拿插件的 `§I3 代价` 判「插件覆盖住了没有」 ⇒ 检查器与被检查对象**共用同一段代码**
#     ⇒ `false-green` 形状 3「共享盲点」：两边永远同时通过
#
# McConnell 等 2011 §5.5 把这条排除得更明确：拿「被检查对象的运算记录」当见证
# **不算 certifying** —— 「证明这条见证性质等于证明 P 正确」。
#
#     方向       覆盖的定义                                        与 `代价 = 0` 的关系
#     ─────────────────────────────────────────────────────────────────────────────
#     keyset     `require ⊆ keys(x)` 且 `keys(x) ∩ forbid = ∅`      恰好等价
#     sequence   某个前缀是 `seq(x)` 的前缀                          恰好等价
#     reach      `x` 能走到某个锚点（`forward(x) ∩ payload ≠ ∅`）    **大得多** ——
#                `代价 = 0` 只对**锚点本身**成立，覆盖却是「能走到锚点的所有项」
#
# ⚠️ `reach` 那一行是本段存在的理由：拿 `代价 = 0` 判覆盖**在原理上就过强**，
#    不只是实现问题。GiST 1995 明说 `Consistent` **允许不精确** ——
#    "an accurate test for satisfiability is not required here" ——
#    所以「用精确性代理判一个只要求不许假阴的量」是判据选错了，不是插件写错了。
#
# --- 代价的形状：为什么 `reach` 要按**反向**闭包算 ---------------------------
#
# 这一族是**唯一**随语料超线性增长的地方（`README` 的「已知的未做」）。形状是：
#
#     覆盖族在 `run_checks.run_one` 里分**七八块**读同一个 oracle
#     ⇒ 同一个 payload 被**反复**要（实测 281 项：调用 4552 次、不同 payload 680 个）
#
#     而 `reach` 的单次调用原来按**正向**闭包逐项扫：
#         `{x : fwd[x] ∩ anchors ≠ ∅}`   代价 O(Σ_x min(|fwd[x]|, |anchors|))
#     覆盖的定义却允许按**锚点**逐个并：
#         `∪_{a ∈ payload} rev[a]`       代价 O(Σ_{a ∈ payload} |rev[a]|)
#
# ⇒ 两件事**分开量、分开记**：换单次调用的算法（`_reverse_closure`）与
#   换「同一 payload 算几遍」（`Cover` 的去重）。混在一起就分不清收益来自哪一边。
#
# ⚠️ 这**不是**把超线性修掉了。McConnell 等 2011 §6 第 2 条说得很清楚 ——
#    "**Ideally**, the running time of a checker is linear in the size of its input"
#    ——「Ideally」是**理想**，不是硬要求；Mehlhorn 2010 §3 更直接地给了反例
#    （3-连通性：线性算法有，但**没有一个是 certifying 的**，最快的 certifying
#    是 O(n²)，且"remains a challenge"）。⇒ 这里是**常数因子与单次形状**的改进，
#    渐近问题记在文档里，**不声称解决了**。

#: 方向 C 的「序列到此为止」哨兵 —— **与 `plugins/sequence.py` 的 `END` 同值**。
#: oracle 自己实现前缀关系、不 import 插件的代码；这里只复制这条**约定**。
SEQ_END = "\x00end"


def _is_prefix(p: tuple[str, ...], s: tuple[str, ...]) -> bool:
    return len(p) <= len(s) and s[:len(p)] == p


_EMPTY: frozenset[str] = frozenset()


class Cover:
    """`cover(payload) -> frozenset[str]`，**按 payload 的规范形去重**，并把命中数报出来。

    ## 为什么去重是允许的 —— 前提只有一条：**纯**

    Acar / Blelloch / Harper 2002（POPL，§2 末「Side Effects」）：

    > Also, **the memoization of the kind done by lazy languages will not affect the
    > correctness of change-propagation, because the value remains the same whether it
    > has been calculated or not.**

    ⇒ 「值算没算都一样」是**理由**，不是同义反复：它把「去重是否改变答案」
      归约成「`cover` 是不是纯函数」。这里的 `cover` 闭包住 `nodes` / `edges` / `rev`
      （构造时固定），调用不写任何东西、不读外部状态 ⇒ **纯**。

    ⚠️ 同一节还给了**反面**，所以「纯」不是顺手一提：

    > The problem is that **function caching and modifiables interact in subtle ways —
    > function caching requires purely functional code**, but our framework involves
    > side-effects in its implementation.

    ⇒ 这条前提**必须被检查**，不能靠读代码断言。判据在
      `run_tests.test_cover_oracle_transparency`：去重前后**逐项相同**；
      注入一个「键取错」的缓存就红。

    ## 为什么键是 payload 的**规范形**，不能是 `did`

    `did = f"D{内核计数器}"`（`core/kernel.py::_new`）—— **每个内核各自从 0 开始**。
    批建内核的 `D3` 与维护内核的 `D3` 是**两个不同的方向**。

    ⇒ 按 `did` 去重会把两份覆盖**串台** ⇒ 判据拿错的覆盖去比 ⇒ **假绿**。
      所以键取 `coverage_of` **本来就要做的那一步规范化**（`frozenset(req)` 等），
      不引入任何新假设 —— 也正因为这样，**跨内核**共享是定义上成立的，不是巧合。

    ## 读数的形状（两个数，缺一个都看不出来）

        `调用` / `命中` / `不同 payload`   ⇒ 去重**有没有真的发生**
        `缓存元素数`                        ⇒ 代价落在**内存**上多少

    ⚠️ **只报倍数不够**：一个「从来没命中」的缓存与一个「全命中」的缓存在
       判据的结论上**长得一模一样** —— 那正是本仓库一直在防的形状。
    """

    def __init__(self, which: str, fn: Any, canon: Any, dedup: bool = True) -> None:
        self.which = which
        self._fn = fn                 # 收**规范形**，不再自己解构 payload
        self._canon = canon
        self._dedup = dedup
        self._memo: dict[Any, frozenset[str]] = {}
        self.calls = 0
        self.hits = 0

    def __call__(self, payload: Any) -> frozenset[str]:
        self.calls += 1
        key = self._canon(payload)        # ← 只消费 payload **一次**
        if self._dedup:
            got = self._memo.get(key)
            if got is not None:
                self.hits += 1
                return got
        val = self._fn(key)
        if self._dedup:
            self._memo[key] = val
        return val

    @property
    def stats(self) -> dict[str, int]:
        return {"调用": self.calls, "命中": self.hits,
                "不同 payload": self.calls - self.hits,
                "缓存元素数": sum(len(v) for v in self._memo.values())}


def coverage_of(which: str, nodes: dict[str, Node],
                edges: dict[str, frozenset[str]], dedup: bool = True) -> Cover:
    """返回 `cover(payload) -> frozenset[str]`（只落在语料内的那部分）。

    与 `equiv_classes` **并列**放在夹具里 —— 两者是同一类东西：按方向的定义重算，
    不调被测对象。`B16` / 健全性 / 覆盖不漏 / 变细守卫 四条共用它。

    ## `dedup=False` 是给**单变量对照**用的，不是给生产用的

    它让同一个 oracle 的**两种算法**能并排跑：`dedup=True` 与 `dedup=False`
    在**同一批 payload** 上必须**逐项相同**。这正是 Acar 那条前提的判据形态。

    ⚠️ **两条路的 `raw` 是同一个函数** —— 所以这条对照验的是**去重**，不是 `raw`。
       `raw` 本身由「与参考实现逐项相同」那条对照验（`run_tests` 里另有一条，
       参考实现是 `_forward_closure` 版本，即改动前的那一个）。
    """
    if which == "keyset":
        keys = {i: nodes[i].keys for i in nodes}

        def raw_keyset(key: Any) -> frozenset[str]:
            req, forb = key
            return frozenset(i for i in nodes
                             if req <= keys[i] and not (keys[i] & forb))

        return Cover(which, raw_keyset,
                     lambda p: (frozenset(p[0]), frozenset(p[1])), dedup)

    if which == "sequence":
        seqs = {i: tuple(v) + (SEQ_END,) for i, v in sequences(nodes, edges).items()}

        def raw_sequence(key: Any) -> frozenset[str]:
            return frozenset(
                i for i in nodes
                if any(_is_prefix(p, seqs[i]) for p in key)
            )

        return Cover(which, raw_sequence,
                     lambda p: tuple(tuple(x) for x in p), dedup)

    if which == "reach":
        # ★ 反向闭包，不是正向。覆盖的定义就是「能走到锚点的**所有**项」
        #   = `∪_{a ∈ payload} rev[a]` —— 按锚点逐个并，而不是逐项扫一遍。
        rev = _reverse_closure(edges, universe=nodes)

        def raw_reach(key: Any) -> frozenset[str]:
            out: set[str] = set()
            for a in key:
                out |= rev.get(a, _EMPTY)
            return frozenset(out)

        return Cover(which, raw_reach, lambda p: frozenset(p), dedup)

    raise ValueError(which)

