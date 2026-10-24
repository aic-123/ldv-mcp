"""检索器层（流程 T）—— 接受需求 `r`，用抽象层交出的**认识**决定「**先看哪一带**」。

    python -m ldv.run_checks            # 判据在 `(检索)` 那一组里（`ldv/checks/retrieval.py`）

**只做六件事**（`基线§4`）—— 一条都不多：

    T0 分派     `r` 的每栏 → `Query`（**结构映射**，不解释语义）  分栏是**结构**，不是语义
    T1 认识     盘上 `spec` 的指纹 ⇄ 当前结构的 `spec` 的指纹     `csr` 唯一 ⇒ **`spec` 即版本**
    T2 排序     `插件.merge` + `插件.hit` 把块分成「先看 / 后看」   两个都是**已有方法**，不新增
    T3 跑 A     **调** `flow.run_query`，不自立终止依据        本层**不自立**终止依据
    T4 组装     候选 + **结果层面**的解释                        遍历顺序**不许**进输出
    T5 展示/记录 走流程 A 的 `R5a` / `R5b`                       —— 由 `run_query` 一并完成

★ **中心定理**（`基线§3`）：**候选集 ≡ 不用视图时的候选集；视图只改变「跑了多少块才得到它」。**

## ★★ 本层的现状：**判断质量**（2026-10-11 起）+ **少扫**（2026-10-14 起）

    `T0` / `T1` / `T4` / `T5` 有实效（分派 / 认识是否当前 / 解释 / 展示记录）；
    `T2` / `T3` **2026-10-14 起也生效了** —— 做法是给内核加一个「**已知说否**」的入口
    （`Kernel.query(skip=...)`），把「后看」的块**整块跳过**：

        `步骤 T2` 把块分成「先看 / 后看」，而「后看」= 块级 `hit` 说「否」的块
        由 `§I2` 的推论（**合并说否 ⇒ 每个成员都说否**）⇒ 这些块可以**不跑**
        ⇒ `scanned`（跑过的块）从「全部块」缩到「先看的块」= **少扫**

    ⚠️ **它省的是「重复判定」，不是「结论」**：那些方向照 `Tri.NO` 分支处理（不展开）
      ⇒ 候选集**逐项不变** ⇒ 判据 `T1` 拿**全扫**当基准守这一条。

    ## 之前为什么做不到（2026-10-09 的实测，保留）

        `kernel.query` / `flow.run_query` **只收 `q`**，从 `root` 遍历整棵树，
        而视图块**横跨树**（不是子树）⇒ 「只扫先看的块」**做不到**
        （不能传参，也不能复制流程 A 的循环 —— `流程§0` 禁）
        ⇒ 三个探针（`outputs/_probe_retriever_inert.py`）：顺序**反转** / 换成**空元组** /
          换成**离散划分** ⇒ 输出**逐项相同** ⇒ 当时**访问量零变化**。

    ⇒ 加那个参数 = 改流程/内核接口 = `§T4` 停止条件 ① —— **2026-10-14 已拍**。

## 本层不许做的事

    ✗ 用**块**的判断代替**方向**的判断（终止）
      —— 按块终止的正确性依赖 `§A1` 绿，而**一条判据不该依赖另一条判据绿**（`基线§3.1` 信任链）
    ✗ 复制流程 A 的遍历循环
      —— `流程§0`：**直接调，不复制**。症状**不是红，是检查报绿**（本仓库栽过）
    ✗ 把「先看 / 后看」摆给用户看
      —— `基线§3.2` / `E-2`「排序即推荐」

## `CONSTRAINTS.md §C1` 的默认值：**从「扫完全部块」改到「跳过说否的块」**

    §C1 原来给的是「**扫完全部块，不早停**」，理由是「顺序只影响代价」。
    ⚠️ 那个**理由**是错的（2026-10-09 实测：代价也没变，顺序当时无处可达）。
    ★ 但「**不早停**」这个**结论**在 2026-10-14 之后**换了个形式仍然成立**：

        允许省的**只是**「插件已经说过『否』」的那些块（`§K8` 允许的那一侧）
        **不许**省任何「先看」的块，**更不许**因为顺序而少给候选

    ⇒ 前者由 `run_tests` ④ 第一条守（`scanned == first`），
      后者由判据 `T1` 守（候选集 ≡ 全扫）—— **两条各自能红**。
    ⚠️ `基线§4 T3 ①`「不自立终止依据」**照旧**：说「否」的是**插件**，不是本层。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from .plugins import DIRECTIONS as BAR_NAMES
from .core.kernel import Kernel, Query
from .core.tri import Tri
from .core.views import ViewSpec, coarsest_stable_refinement, partition_of
from . import flow
from .core.direction import did_order as _did_order

__all__ = [
    "BAR_NAMES",
    "BlockProfile",
    "DispatchError",
    "as_bar",
    "Reason",
    "Recognition",
    "Retrieval",
    "block_profile",
    "current_recognition",
    "deepen",
    "dispatch",
    "explain",
    "order_blocks",
    "profile_of",
    "render_explanation",
    "render_profile",
    "render_reading",
    "render_reason",
    "retrieve",
    "run_in_order",
    "spec_fingerprint",
]


class DispatchError(RuntimeError):
    """`r` 里有栏、而没有对应插件 —— **分派不了**（`基线§8` 停止条件 1）。

    ⚠️ 这是**抛**，不是三态：猜一个「这段处境该交给谁」就是替人做决定。
    """


# ═══ T0 分派 ═══════════════════════════════════════════════════════════════════
#
# `基线§2` 唯一的实质内容：**本层只分派，不解释语义**。
# ⇒ 分派只能是**结构映射**：栏 → `Query`。字段名**照抄** `Query`，一个字不新发明。
#
# ⚠️ 为什么不是「每插件一个不透明 blob + 新方法 `parse(栏) → q`」：
#    `§I1`–`§I7` 七个方法里**没有**这一个。加它就是 `§T4` 停止条件 ①（新增内核方法）。

#: 栏里**允许**出现的字段 —— 就是 `Query` 的字段。多一个就抛（静默丢掉一个约束
#: 会让「这个约束被考虑了」与「它被扔了」长得一模一样）。
_BAR_FIELDS = ("ideal", "require", "forbid", "min_rank", "label")


def dispatch(need: Mapping[str, Mapping[str, object]],
             known: Iterable[str] = BAR_NAMES) -> dict[str, Query]:
    """`步骤 T0` —— 把 `r` 的每一栏交给对应插件，取回一组 `q`。

    `need` 的外层键 = **插件名**（= `插件.name`）；值 = 那一栏。

    ⚠️ 栏**存在但为空**（`§C5` 缺口 4）⇒ 得到一个默认 `Query`，**交给插件自己判**
       （`§I1` 三态本来就允许空查询）。**不是**跳过、**不是**红。
    """
    known_set = set(known)
    out: dict[str, Query] = {}
    for bar, spec in need.items():
        if bar not in known_set:
            raise DispatchError(
                f"`r` 里有栏 {bar!r} 没有对应插件（已知 {sorted(known_set)}）"
                f" —— 分派不了，**不许猜**（基线§8 停止条件 1）")
        out[bar] = _to_query(bar, spec)
    return out


def as_bar(query: Query) -> dict[str, object]:
    """`Query` → 一栏。`_to_query` 的**逆**，与它**一处定义**（两处用：`run_checks` 与对照）。

    ⚠️ 只产出 `_BAR_FIELDS` 那几个键 —— 多一个 `_to_query` 就会抛（那是**故意**的）。
    """
    return {"ideal": query.ideal, "require": query.require, "forbid": query.forbid,
            "min_rank": query.min_rank, "label": query.label}


def _to_query(bar: str, spec: Mapping[str, object]) -> Query:
    """栏 → `Query`。**结构映射**：只认 `_BAR_FIELDS`，多一个字段就抛。"""
    extra = sorted(set(spec) - set(_BAR_FIELDS))
    if extra:
        raise DispatchError(
            f"栏 {bar!r} 里有本层不认识的字段 {extra} —— 本层**不解释语义**（基线§2），"
            f"静默丢掉一个约束与「它被考虑了」长得一模一样")
    return Query(
        ideal=frozenset(spec.get("ideal") or ()),
        require=frozenset(spec.get("require") or ()),
        forbid=frozenset(spec.get("forbid") or ()),
        min_rank=int(spec.get("min_rank", 1)),
        label=str(spec.get("label", "")),
    )


# ═══ T1 取当前认识 ════════════════════════════════════════════════════════════

def spec_fingerprint(spec: ViewSpec) -> str:
    """**认识的版本** = `spec` 的指纹（`基线§6`）。

    `Q = csr(spec)` 而 `csr` **唯一** ⇒ `spec` 一变 `Q` 必然跟着变、`spec` 不变 `Q` 必然不变
    ⇒ **`spec` 的指纹就是「认识版本」**。不需要新加一个量。

    ⚠️ 落盘件另有一个 `FORMAT`（`"ldv-views/1"`）—— 那是**格式**版本，与认识版本**不是一回事**。
    """
    blob = json.dumps(spec.as_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Recognition:
    """`步骤 T1` 的产物 —— **当前**那份认识。

        `spec`          当前结构的 `spec`（**唯一**的版本载体）
        `q`             实际拿来给块排序的那个划分
        `source`        它是**怎么来的**：`盘上` / `重算`
        `fingerprint`   **实际用到的那份认识**的 `spec` 指纹
        `on_disk`       盘上到底有没有一份认识（读到文件了没有）
        `disk_fingerprint`  **盘上那份**自己的指纹（`None` = 盘上没有）
        `disk_readable`     盘上那份**读得回来吗**（`False` = 坏了 / 与当前结构不自洽）

    ## ★ 为什么后两项必须分开（2026-10-10 实测）

        `fingerprint` 是**用到的那份**，而 `current_recognition` 在陈旧/坏档时
        **会修好它**（重算）⇒ 它**永远**等于当前指纹 ⇒ 拿它做判据**恒真**
        （实测：三个分支**都**返回 `spec_fingerprint(spec)`）。
        ⇒ 「盘上那份是不是当前的」必须**另外记**，否则那条判据是空的。
    """

    spec: ViewSpec
    q: tuple[frozenset[str], ...]
    source: str
    fingerprint: str
    on_disk: bool
    #: 盘上那份**自己**的指纹；`None` = 盘上没有（或读不回来）。
    disk_fingerprint: str | None = None
    #: 盘上那份读得回来吗。`False` 且 `on_disk` 为真 ⇒ 坏了 / 与当前结构不自洽。
    disk_readable: bool = False


#: 「**没传**」与「传了 `None`」**必须分得开**（2026-10-10 实测踩到，见下）。
#: 前者 = 由 `recognition` 推断（旧调用点）；后者 = **盘上根本没有那份**。
_DISK_UNSET: Any = object()


def current_recognition(spec: ViewSpec, recognition: Any = None, *,
                        disk_fingerprint: Any = _DISK_UNSET,
                        disk_unreadable: bool = False) -> Recognition:
    """`步骤 T1` —— 取**当前**认识（`基线§6`）。

        `recognition is None`     盘上没有任何认识 ⇒ 现算 `csr(spec)`
        指纹相符                   ⇒ **用盘上那份**（它是当前的）
        指纹不符                   ⇒ **不许用**（重算 `csr(spec)`）

    `recognition` 是 `core/view_persist.from_dict` 读回来的那份 `ViewSet`
    （**读盘不在这里做** —— 本模块只管认不认识它是不是当前的）。

    ⚠️ **不比对的话，「陈旧的认识」与「当前的认识」在只看输出时长得一模一样** ——
       正是本项目最忌的形状（`基线§6`）。
    """
    # ★★ **`disk_fingerprint` 是「盘上那份自己的指纹」的唯一来源**（2026-10-10 修）
    #
    #    原来它是**从传进来的 `recognition` 推断**的 —— 而调用方「读盘」与「现造」
    #    传的是**同一类型**的对象 ⇒ `current_recognition` **分不出来** ⇒
    #    实测（全量语料）：盘上**根本没有** `views.json`，而 `T2` 报
    #    「**盘上那份**的指纹 `4aa0510d54470392` == 当前（来源：盘上…）」——
    #    **判据在编造一个来源**，而同一趟的 `note` 写着「盘上是现造的」。两者直接冲突。
    #    ⚠️ 那比「跳过」更坏：**跳过是「没得比」，假绿是「比过了、而且对」**。
    #  ⇒ 现在：`_DISK_UNSET` ⇒ 由 `recognition` 推断（旧调用点，语义仍是「这就是盘上那份」）；
    #          `None`       ⇒ **盘上没有**（哪怕传了 `recognition`，那是现造的）。
    if disk_fingerprint is _DISK_UNSET:
        disk_fingerprint = (spec_fingerprint(recognition.spec)
                            if recognition is not None else None)
    if disk_unreadable:
        # ★ **盘上有一份，但读不回来**（与当前结构不自洽 / 存档坏了）。
        #   它与「盘上什么都没有」**必须分得开**：后者是「还没建」，前者是「建了但坏了」。
        #   ⇒ 不崩、按重算走（结果仍对），但把状态**记在 `Recognition` 上**，由 `T2` 判红。
        return Recognition(spec=spec, q=coarsest_stable_refinement(spec),
                           source="重算（盘上那份**读不回来**）",
                           fingerprint=spec_fingerprint(spec), on_disk=True,
                           disk_fingerprint=None, disk_readable=False)
    if disk_fingerprint is None or recognition is None:
        return Recognition(spec=spec, q=coarsest_stable_refinement(spec),
                           source="重算（盘上没有任何认识）",
                           fingerprint=spec_fingerprint(spec), on_disk=False,
                           disk_fingerprint=None, disk_readable=False)
    if disk_fingerprint != spec_fingerprint(spec):
        return Recognition(spec=spec, q=coarsest_stable_refinement(spec),
                           source=f"重算（盘上陈旧：{disk_fingerprint} ≠ 当前）",
                           fingerprint=spec_fingerprint(spec), on_disk=True,
                           disk_fingerprint=disk_fingerprint, disk_readable=True)
    return Recognition(spec=spec, q=partition_of(recognition.q),
                       source="盘上（指纹相符）",
                       fingerprint=spec_fingerprint(spec), on_disk=True,
                       disk_fingerprint=disk_fingerprint, disk_readable=True)


# ═══ T2 排序 ══════════════════════════════════════════════════════════════════

def _carrier(kernel: Kernel, block: frozenset[str], plugin: Any) -> Any:
    """把「一块」交给 `§I1 命中` 时的**载体方向**。

    `§I1 hit(d, q)` 的第一参数是 `Direction`（三个插件的 `hit` 都读 `d.rank` / `d.payload`），
    而 `§I2 合并` 产出的是 **payload**，不是方向 ⇒ 需要一个载体。

    ⚠️ `rank` 取块内**最小**秩：`§K6`「不知道不许当没有」⇒ 秩取小 ⇒ 更容易`未展开`
       ⇒ 更容易排「**先看**」= **保守的那一侧**。
    ⚠️ 这**只影响排序**，**永远不影响终止**（`基线§3` 定理 + `§C1`）。
    """
    ids = sorted(block, key=_did_order)
    dirs = [kernel.direction(d) for d in ids]
    return replace(dirs[0], payload=plugin.merge(dirs),
                   rank=min(d.rank for d in dirs))


def order_blocks(kernel: Kernel, plugin: Any, q: tuple[frozenset[str], ...],
                 query: Query, *,
                 tendency: Mapping[str, float] | None = None,
                 ) -> tuple[tuple[frozenset[str], ...], tuple[frozenset[str], ...]]:
    """`步骤 T2` —— 把块排成「**先看** / **后看**」（`基线§4` T2）。

    ★★ **两层排序**（`基线§14.7`，按最初那两份文档的想法）：

        ① **先由我们设定**（结构）：`hit(块) = 否` ⇒ 后看；`是` / `未展开` ⇒ 先看
        ② **按使用细调**（局部）：先看档内部，**有使用记录**的块按倾向**降序**排前面

    ⚠️ **② 是分层排序，不是加权求和** —— 合成一个数需要定一个系数（λ），
       而 λ 是**拍的**。分层则两个键各自独立、各自可解释。

    ⚠️ **「没有记录」不等于「倾向 0」**（`§K6` 同源）：没有记录 = **不知道**，
       所以它们**不参与**比较，只按 `Q` 自己的规范化顺序排在**有记录的之后**。
       把它们当 0 去比 = 把「没看见」当成「不好」。

    ⚠️ **`否` 只是「排在后面」，不是「不跑」** —— 见 `§3.1`。

    ⇒ 返回 `(先看, 后看)`。块**内部**顺序 = `Q` 的规范化顺序（`views.partition_of`）。
    """
    first: list[frozenset[str]] = []
    late: list[frozenset[str]] = []
    for block in q:
        if plugin.hit(_carrier(kernel, block, plugin), query) is Tri.NO:
            late.append(block)
        else:
            first.append(block)

    if tendency:
        # ★ 只有**有记录**的块参与这一步；没有的留在原地（规范化序）在后面。
        known = [b for b in first if any(d in tendency for d in b)]
        unknown = [b for b in first if not any(d in tendency for d in b)]

        def weight(b: frozenset[str]) -> float:
            return sum(tendency.get(d, 0.0) for d in sorted(b, key=_did_order))

        # 降序；同权时按规范化序（`min` 元素）⇒ 可复现
        known.sort(key=lambda b: (-weight(b), min(b)))
        first = known + unknown      # ⚠️ `unknown` 保持 `Q` 原序 —— 「不知道」不参与比较

    return tuple(first), tuple(late)


# ═══ T3 按顺序跑流程 A ════════════════════════════════════════════════════════

def run_in_order(kernel: Kernel, query: Query,
                 order: tuple[frozenset[str], ...],
                 *, skip: frozenset[str] = frozenset(),
                 ) -> tuple[Any, tuple[frozenset[str], ...]]:
    """`步骤 T3` —— 按 T2 的顺序**逐块**把该块的方向交给**同一条路**，跑流程 A。

    ★ **「同一条路」就是 `flow.run_query`** —— 流程 A 的入口。`流程§0`：**直接调，不复制**。
      复制它的遍历循环是**违约**，而症状**不是红，是检查报绿**（本仓库栽过那一次）。

    ## ★★ `skip` —— 让「后看」的块**真的不跑**（2026-10-14，`§T4` 停止条件 ① 已拍）

    `步骤 T2` 把块排成「先看 / 后看」，而「后看」= **块级 `hit` 说「否」**的块。
    由 `§I2` 的一条推论（证明在 `ldv/checks/retrieval.py` 的模块 docstring 里）：

        合并说「否」 ⇒ **每个成员都说「否」**

    ⇒ 这些块的方向可以**整块交给内核跳过**：`flow.run_query(kernel, query, skip=...)`。
    ⇒ 于是 `scanned`（**真正跑过的块**）从「全部块」缩到「先看的块」——
      这是本层**第一次**让 `T2` 的顺序产生可观测的后果。

    ⚠️ **它省的是「重复判定」，不是「结论」**：那些方向照 `Tri.NO` 分支处理
      （`Kernel.query` 的 docstring 有全解），候选集**逐项不变** ⇒ 判据 `T1` 守这一条。
    ⚠️ **它不违反 `§C1` ①「不自立终止依据」**：说「否」的是**插件**（`§K8` 允许的那一侧：
      `否` 必须是**保证**不满足），不是本层立的依据；而且 `Tri.NO` 分支**本来就不展开**
      ⇒ 那些方向的子树本来就不跑。
    ⚠️ **`scanned` 不再是恒等记录** —— 它现在**是读数**（见 `Retrieval.scanned`）。

    ## 为什么以前做不到（2026-10-09 → 2026-10-14）

        `kernel.query` / `flow.run_query` **只收 `q`**，从 `root` 遍历**整棵树**；
        而视图块**横跨树**（不是子树）⇒ 「只扫先看的块」当时**做不到**。
        ⇒ 加这个参数 = 改流程/内核接口 = `§T4` 停止条件 ① —— **2026-10-14 已拍**。

    ## ⚠️ 原来这里有一条断言，**已删**（2026-10-09）

        原来是：`assert set(scanned) == set(order)` —— 它**恒真**
        （`scanned` 就是由 `order` 构造出来的）。**恒真式冒充守卫是本仓库最忌的形状**：
        它读起来像「有人守着『不许自立终止依据』」，实际什么都没守。

        ⇒ 删掉，不换成另一条断言 —— 循环体是空的，**没有任何可断言的东西**。
          「本层不自立终止依据」这句话的真实位置是**文档**（`基线§4 T3 ①`），
          而不是一条恒真的断言。

    ⚠️ **本层不自立终止依据**（`基线§4 T3 ①`）：能决定「停」的只有流程 A 自己的 `R3`。
    """
    scanned = tuple(b for b in order if not (b & skip))   # ★ 真正跑过的块
    return flow.run_query(kernel, query, skip=skip), scanned


# ═══ 结构画像（`基线§14`） ══════════════════════════════════════════════════════
#
# ★ 它补的是**另一维**：不是「先看哪个」（顺序），是「**这一块是什么**」（性质）。
#   参照 HNSW 的 `zoom-out`：先拉远看清全貌，再往里钻（`基线§14.0`）。
#
# ⚠️ **画像里不许有跨块可比的量**（`E-1` / `E-2`）：它只回答「这一块是什么」，
#    不回答「这一块更好吗」。任何「重要性 / 命中率 / 访问次数」都不许进来。


#: 读法 → **下一步**（`基线§14.8`）—— **一处定义**，判据 `T6` 从它反查。
#: ⚠️ 它是 `reading` 的**函数**（不是独立判断）：改这里就等于改两条判据的口径。
ADVICE: dict[str, str] = {
    "已细分过": "往下有现成通道",
    "还没长出来": "值得往下",
    "分不开": "不必往下（分不开）",
    "混合": "看具体方向",
}


@dataclass(frozen=True)
class BlockProfile:
    """一块的**三态结构画像** —— 五项全是结构事实，**现算**（`基线§14.1`）。

    ## ★ 三档必须分开（`§K6` 在本层的形态）

        `n_with_children`  成员里有**子方向**的个数   ⇒ **可继续往下**（已细分过）
        `n_singleton`      成员里**只有 1 个项**的     ⇒ **结构上永远分不开**（`§K2` 情形①）
        `n_unexpanded`     其余（无子、成员 ≥ 2）      ⇒ **还没长出来**（含「试过也分不开」）

    ## ★★ 一档**被删掉了**：原来还有「已展开·无子 ⇒ 下界到了」，那是**用错字段**

        原来算的是 `is_expanded(d) and not children_of(d)` —— **恒假**：
        内核里 `_expanded.add(did)` **只在「长出子方向」那条分支**写
        （`kernel.py`：`self._children[d.did] = …` 的下一行），
        而**判空记在 `_tried`**（`stats()['§K2 判空'] = len(_tried)`）。
        ⇒ 实测：`is_expanded ∧ ¬children` 的方向数 = **0**，而 `_tried` 有 **7** 条。

    ## ⚠️ 于是有一档**用公开信息看不到**：「**试过、但分不开**」

        `_tried` 是**私有**的，内核**没有**公开访问器 ⇒
        「试过也分不开」与「还没试过」在**只看公开信息时长得一模一样**。
        ⇒ 本层**如实**把它们并成一档（`还没长出来`），**不假装分得开**。
        ⇒ 后果（**已量**）：「值得往下」那一带会被**重复试**，但**幂等** ——
          第二次 `expand` 命中 `_tried` 立刻返回 `()`（一次 dict 比较），
          方向数与候选数**都不变**（判据 `T7` 的第二半守这条）。

    ★★ **`n_singleton` 这一档是量出来的**（2026-10-09）：`_expand_inner` 在
       `len(items) < 2` 时**直接 `return ()`**（`§K2` 情形①：单项分不开）⇒
       这类方向**永远不会有子**。实测：本语料那一块的 13 个方向里，
       **6 个只有 1 个成员**（结构上永远分不开）、**7 个成员 ≥2**（值得试）。

    ⚠️ 把「未展开」报成「下界到了」= 把「**不知道**」说成「**已经到底了**」。
       而这两档在**只看「有没有子」时长得一模一样** —— 实测：本语料那 13 个成员
       「都没有子」，看起来像判空，**真相是未展开**（`基线§14.2`）。
       ⇒ 三档分开**不是洁癖**，是防止画像**说错话**。
    """

    block: frozenset[str]
    rank_lo: int
    rank_hi: int
    n_with_children: int
    n_singleton: int
    n_unexpanded: int

    @property
    def n_members(self) -> int:
        return len(self.block)

    @property
    def reading(self) -> str:
        """四档读法 —— **可判**（`基线§14.3`；判据 `T4` 从**读法反推事实**来验它）。

        ⚠️ 四档**互斥**；「混合」是**真的混着就报混合**，不许硬归纳成某一档。
        """
        t = self.n_members
        if t and self.n_with_children == t:
            return "已细分过"
        if t and self.n_singleton == t:
            return "分不开"          # ★ 它与 `is_expanded` 无关 ⇒ 先判
        if t and self.n_unexpanded == t:
            return "还没长出来"
        return "混合"

    @property
    def advice(self) -> str:
        """`步骤 T4` 的**下一步建议** —— 读法 → 建议（`基线§14.8`，`ADVICE` 一处定义）。

        ★ 它答的是流程 A `R3a` 那个**一直空着的依据**（「需要更清晰的方向吗？」）。
        ⚠️ 它是**判断**不是**动作**：本层只读（`§1` 表），不代检索器调 `expand`。
        """
        return ADVICE[self.reading]


def block_profile(kernel: Kernel, block: frozenset[str]) -> BlockProfile:
    """算一块的画像 —— **逐成员查内核**（`children_of` / `is_expanded`），不猜。

    ⚠️ 三档**互斥且完备**：对每个成员恰好落一档 ⇒ `n_with_children + n_singleton
       + n_unexpanded == |block|`。⚠️ 这条是**按构造恒真**的 ⇒ 它**不是判据**，
       只是计数方式的自洽性（判据 `T4` 判的是**读法与事实相符**，见 `checks/retrieval.py`）。
    """
    ranks: list[int] = []
    c = u = one = 0
    for did in sorted(block, key=_did_order):
        d = kernel.direction(did)
        ranks.append(d.rank)
        if kernel.children_of(d):
            c += 1                       # 有子 ⇒ 已细分过
        elif len(kernel.members_of(d)) < 2:
            one += 1                     # ★ 单项 ⇒ **结构上永远分不开**（`§K2` 情形①）
        else:
            u += 1                       # 无子、成员≥2 ⇒ **还没长出来**（含「试过也分不开」）
    return BlockProfile(block=block,
                        rank_lo=min(ranks) if ranks else 0,
                        rank_hi=max(ranks) if ranks else 0,
                        n_with_children=c, n_singleton=one, n_unexpanded=u)


def profile_of(kernel: Kernel, q: Sequence[frozenset[str]]) -> tuple[BlockProfile, ...]:
    """一个划分的画像 —— 按 `Q` 自己的规范化顺序（不排序子句以外的东西）。"""
    return tuple(block_profile(kernel, b) for b in q)


def render_profile(p: BlockProfile) -> str:
    """画像的**打印形态** —— 判定与打印**同源**（同 `render_reason` 的纪律）。

    ★ **必须印出「是哪一块」**：只说「成员 13 个」的话，读者不知道在说哪一块 ——
      而且判据也没法按块定位（`render_explanation` 的块顺序断言就用这个）。
    """
    ids = sorted(p.block, key=_did_order)
    shown = ", ".join(ids[:3]) + ("…" if len(ids) > 3 else "")
    return (f"块 [{shown}]：成员 {p.n_members} 个（rank {p.rank_lo}–{p.rank_hi}）"
            f"：**{p.reading}**（有子 {p.n_with_children} / 分不开 {p.n_singleton}"
            f" / 还没长出来 {p.n_unexpanded}）")


# ═══ T4 组装 ══════════════════════════════════════════════════════════════════

#: 一条理由 = **(一个候选方向, 一个项)**，且那个项**在该方向的覆盖里**。
#:
#: ⚠️ 为什么不是一句自然语言：`T3` 判据要**机器可判**（`§C5` 缺口 3）。
#:    一句散文判不了 ⇒ 那条判据只能恒绿。
Reason = tuple[str, str]


def render_reason(reason: Reason) -> str:
    """理由的**打印形态** —— 判定与打印**同源**（照 `multilevel.TITLES` 的「一处定义、两处用」）。"""
    return f"{reason[0]} 覆盖 {reason[1]}"


def explain(candidates: Iterable[str], kernel: Kernel) -> tuple[Reason, ...]:
    """`步骤 T4` —— **结果层面**的解释：每条理由指得到一个**实际结果**。

    ⚠️ 它说的**不是**「内核先看了哪一带」（调度层面）—— 遍历顺序**不许**进输出
       （`基线§3.2`）。顺序按方向 id **字符串序**排，那是**规范化**顺序，不是遍历顺序。
    """
    out: list[Reason] = []
    for did in sorted(candidates):
        items = sorted(kernel.members_of(kernel.direction(did)))
        if items:
            out.append((did, items[0]))
    return tuple(out)


# ═══ 组装：一次检索 ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Retrieval:
    """一次检索的**结果** —— `T1`–`T4` 的产物，也是三条判据读的对象。"""

    query: Query
    candidates: frozenset[str]
    first: tuple[frozenset[str], ...]      # 「先看」的块（**内部调度**，不进输出）
    late: tuple[frozenset[str], ...]       # 「后看」的块
    #: ★ `步骤 T3` **真正跑过**的块（`= first`，2026-10-14 起）—— **是读数，不是恒等记录**。
    #: ⚠️ 2026-10-09 至 2026-10-14 之间它是 `tuple(order)`（恒等）⇒「跑过 N/N 块」
    #:    是**构造性恒真**，不是读数。现在「后看」的块**真的不跑** ⇒ 它才成为读数。
    #: ⚠️ 判据 `T1` 守的是**候选集**（不是它）：跳过**不许**丢候选。
    scanned: tuple[frozenset[str], ...]
    reasons: tuple[Reason, ...]            # T4 的解释（每条指得到一个实际结果）
    recognition: Recognition
    profiles: tuple[BlockProfile, ...] = ()  # 块层面的**性质**（`基线§14`，进解释）
    #: `步骤 T2` ② **真正用到**的倾向（`基线§14.7`）。空 = 没有任何记录 ⇒ 退化到纯结构序。
    #: ⚠️ 它是**判据 `T5` 的输入**（「按使用细调」有没有真的进顺序），不是日志。
    tendency: Mapping[str, float] = field(default_factory=dict)
    #: **块级的下一步建议**（`基线§14.8`）—— `((块, 建议), …)`，按 `Q` 序。
    #: ⚠️ **不跨块去重/合并** —— 去重会把「**哪一带**」丢掉，
    #:    而「哪一带」正是「整体倾向」的全部内容（实测踩到：合并后判据 `T6` 立刻红，
    #:    因为一条建议被拿去对**别的块**检查）。
    #: ⚠️ 它是**判据 `T6` 的输入**；不是日志（写错就是红）。
    advice: tuple[tuple[frozenset[str], str], ...] = ()
    #: ★ `R3a` 的接线（`基线§14.9`）—— **深化**：被按需 `expand` 的方向（按 `Q` 序）。
    #: 空 = 没有做深化（没调 `deepen`，或没有任何「值得往下」的块）。
    #: ⚠️ 它是**判据 `T7` 的输入**（深化只许增、不许减）。
    deepened: tuple[str, ...] = ()
    #: 深化**之后**的候选数 —— 与 `len(candidates)`（深化前）比，就是 `T7` 判的东西。
    candidates_after_deepening: int = -1

    @property
    def advice_span(self) -> int:
        """建议**覆盖的方向数** —— 读数（进输出，不进退出码）。

        ★ 它就是「整体性」的量：离散划分下它会退化成「每个方向一条」，
          而认识下它是「13 个成员那一带」⇒ **判断的粒度不同**（`基线§14.8.4`）。
        """
        return sum(len(b) for b, _a in self.advice)

    @property
    def n_blocks_with_tendency(self) -> int:
        """「先看」档里**有使用记录**的块数 —— 读数（进输出，不进退出码）。"""
        return sum(1 for b in self.first if any(d in self.tendency for d in b))

    def reading(self) -> dict[str, int]:
        """**现算**的读数（`§T0` 第 5 条：会漂的数不许写死）。"""
        n_dirs = sum(len(b) for b in self.first) + sum(len(b) for b in self.late)
        return {
            "方向数": n_dirs,
            "块数": len(self.first) + len(self.late),
            "先看的方向数": sum(len(b) for b in self.first),
            "先看的块数": len(self.first),
            "后看的块数": len(self.late),
            "跑过的块数": len(self.scanned),
        }


def render_reading(r: Retrieval) -> str:
    """**读数** —— 进输出，**不进退出码**（`设计§2.2`）。

    ★★ **访问量（2026-10-14 起）：`T2` 的顺序有后果了 —— 「后看」的块真的不跑。**
       口径见 `基线§13 13.0` / `§14.6`：本层认**两种**影响 ——
         (a) **判断质量**（块画像 / 建议 / 读法 / 粒度）—— 2026-10-11 起就在交付
         (b) **少扫**（`scanned` < 全部块）—— 2026-10-14 起生效（`§T4` 停止条件 ① 已拍）
       ⚠️ 两者**都不是**「省时间」意义上的收益；(b) 省的是**重复判定**，不改结论。

    ★★ **口径（`基线§14.6`）：收益 = 影响**。
       本层的收益是「检索器拿到的那份判断」变好了（块画像 / 建议 / 读法 / 粒度）。
       ⚠️ **2026-10-11 的口径说「访问量是读数、不是收益」** —— 2026-10-14 已改为
          「**少扫是一种影响**」（见 `§14.6`）。

    ⇒ 这一点**必须印出来** —— 否则「读数」与「收益」在只看数字时**长得一模一样**。
      ⚠️ **这句话只有这一个来源**：`run_retrieval` 里原来还有一份（`rep.note`），
        2026-10-09 加 `deepen` 时被 `return deep` 孤儿化 ⇒ 2026-10-11 删掉那份。
        真跑与跳过两条路都印得到这里。
    """
    d = r.reading()
    return (f"认识 |Q| = {d['块数']} 块 / {d['方向数']} 个方向；"
            f"「先看」{d['先看的块数']} 块（{d['先看的方向数']} 个方向）、"
            f"「后看」{d['后看的块数']} 块"
            f"　★ **少扫**：「后看」的块**不跑** ⇒ 跑过 {d['跑过的块数']}/{d['块数']} 块"
            f"（省 {d['块数'] - d['跑过的块数']} 块）—— 由 `§I2` 的推论保证**不改结论**"
            f"（判据 `T1` 守这一条）。"
            f"　★★ **本层的收益 = 影响**（基线§14.6）：① **判断质量**（块画像 / 建议 / "
            f"读法 / 粒度）② **少扫**（这一行）—— 两者都不是「省时间」意义上的收益")


def advice_of(candidates: Iterable[str],
              profiles: Sequence[BlockProfile]) -> tuple[tuple[frozenset[str], str], ...]:
    """`步骤 T4` 的**下一步建议** —— `((块, 建议), …)`，按 `Q` 序（`基线§14.8`）。

    ⚠️ **每块一条，不跨块合并**（实测踩到：合并后判据 `T6` 立刻红 ——
       一条建议被拿去对**别的块**检查）。★ 理由不是「判据不好写」，是**语义**：
       「整体倾向」的全部内容就是「**哪一带** + 那一带什么性质」；
       把块去掉的「建议列表」**恰好把「哪一带」丢了**。

    ⚠️ **只取「命中」的块** —— 没命中的块再「值得往下」也与本次结果无关。
    ⚠️ 顺序按 `Q` 的规范化序 —— **可复现**，且**不是**遍历顺序（`§3.2`）。
    """
    hit = frozenset(candidates)
    return tuple((p.block, p.advice) for p in profiles if p.block & hit)


def retrieve(kernel: Kernel, plugin: Any, need: Mapping[str, Mapping[str, object]], *,
             spec: ViewSpec, recognition: Any = None,
             tendency: Mapping[str, float] | None = None,
             disk_fingerprint: Any = _DISK_UNSET,
             disk_unreadable: bool = False,
             known: Iterable[str] = BAR_NAMES) -> Retrieval:
    """`步骤 T0`–`步骤 T4` 走一遍。`T5`（展示 + 记录）由 `flow.run_query` 一并完成。

        `need`         `r` —— 按插件分栏的约束集合（外层键 = 插件名）
        `spec`         **当前**结构的外生声明（`U` / `P` / `E`）
        `recognition`  盘上那份认识（`view_persist.from_dict` 读回来的）；`None` = 没有
        `tendency`     每个方向的**倾向加权值**（`core/selfopt.tendency_by_direction`）；
                       `None` / 空 = **没有任何使用记录** ⇒ `步骤 T2` **退化到纯结构序**

    ⚠️ `tendency` 由**调用方**给，不由本模块自己算 —— 它的一处定义在 `core/selfopt.py`
       （`F2` 与这里**共用**那个函数；两处各写一份会漂移，且两边都是浮点数 ⇒ 看不出来）。

    本次调用只跑**一个内核**（一个插件）⇒ T2/T3 用的 `q = r[plugin.name]`；
    其余栏**只做分派校验**（这正是 `步骤 T0` 的产物「一组 `q`」）。
    """
    dispatched = dispatch(need, known)
    if plugin.name not in dispatched:
        raise DispatchError(
            f"`r` 里**没有** {plugin.name!r} 这一栏 —— 这个内核没有需求可跑"
            f"（已知栏 {sorted(dispatched)}）")
    query = dispatched[plugin.name]

    rec = current_recognition(spec, recognition, disk_fingerprint=disk_fingerprint,
                              disk_unreadable=disk_unreadable)
    tend = dict(tendency or {})
    first, late = order_blocks(kernel, plugin, rec.q, query, tendency=tend)
    # ★ 「后看」= 块级 `hit` 说「否」的块 ⇒ 由 `§I2` 的推论，其成员**都**说「否」
    #   ⇒ 整块交给内核跳过：省掉重复判定，**不改结论**（判据 `T1` 守这一条）。
    skip = frozenset().union(*late) if late else frozenset()
    flow_a, scanned = run_in_order(kernel, query, first + late, skip=skip)
    candidates = frozenset(flow_a.result.yes)
    profs = profile_of(kernel, rec.q)
    return Retrieval(query=query, candidates=candidates, first=first, late=late,
                     scanned=scanned, reasons=explain(candidates, kernel),
                     recognition=rec, profiles=profs, tendency=tend,
                     advice=advice_of(candidates, profs))


def deepen(kernel: Kernel, plugin: Any, r: Retrieval) -> Retrieval:
    """`R3a` 的**接线**（`基线§14.9`）—— 对「值得往下」那一带**按需展开**，再跑一次。

    ## 它接的是流程 A `R3a` 那个决定点

        流程 A：`R3a`「需要更清晰的方向吗？」⇒ 需要就展开下一层、回到 `R1`
        ⚠️ 而流程 A **自己不会**展开「未展开」的方向 —— `R3b` 把「需展开」交给
           **维护侧**（工作流程 §1 逐字：「不是叫查询继续钻」）
        ⇒ **本函数做的就是那件流程 A 不做的事**：替检索器**按需**展开候选方向

    ## ★★ 安全契约：**候选集只增不减**（判据 `T7`）

        展开**只建新的子方向**（`§K4` 只分叉不覆盖）⇒ 旧方向仍在 ⇒ 旧候选不会消失
        ⇒ `候选集(后) ⊇ 候选集(前)` —— 这是本函数**唯一**的正确性要求，
          也是它**敢**改结构的原因：它偏的是 `§K8` **允许**的那一侧（假阳 / 多找）

    ## 为什么只展开「值得往下」那一带

        那一块的成员**全部未展开且成员数 ≥ 2** ⇒ 展开它**可能**长出东西。
        ⚠️ 而「分不开」那一档（成员 < 2）**结构上永远长不出** ⇒ **不试**（`§K2` 情形①）。

    ⚠️ **它改结构**（`expand` 建方向）。⇒ `§1` 表的「只读」要**精确化** ——
       这不是绕开边界，是发现 `§1` 表与 `R3a`（「展开下一层」）**本来就矛盾**（`基线§14.9`）。
    """
    if not r.advice:
        return r
    # 「去哪看」由**建议**定：`值得往下`（全块值得试）与`看具体方向`（**块内**有值得试的）。
    # ⚠️ 其余两档**不可能**含「无子且成员 ≥ 2」的方向：
    #    `往下有现成通道` 全有子、`不必往下（分不开）` 全成员<2
    #    ⇒ 用建议筛块**不会漏**。
    # ⇒ 块内再**逐方向**筛：`值得试` = 无子 **且** 成员 ≥ 2（成员<2 的永远分不开 ⇒ 不试）。
    worth = {ADVICE["还没长出来"], ADVICE["混合"]}
    todo: list[str] = []
    for block, a in r.advice:
        if a not in worth:
            continue
        for d in sorted(block & frozenset(r.candidates), key=_did_order):
            dirn = kernel.direction(d)
            if not kernel.children_of(dirn) and len(kernel.members_of(dirn)) >= 2:
                todo.append(d)
    if not todo:
        return r
    for d in todo:
        kernel.expand(kernel.direction(d))      # 唯一写动作；返回 () 也算「试过了」
    after = frozenset(kernel.query(r.query).yes)
    # ⚠️ **只增不减**要在这里成立才敢返回（判据 `T7` 在测试里独立再验一遍）
    assert after >= r.candidates, (
        "深化让候选集**变小**了 —— 违反 `§K4`（只分叉不覆盖）或 `§K8`（假阴禁止）")
    profs = profile_of(kernel, r.recognition.q)
    return replace(r, candidates=after, reasons=explain(after, kernel),
                   profiles=profs, advice=advice_of(after, profs),
                   deepened=tuple(todo), candidates_after_deepening=len(after))


def render_explanation(r: Retrieval) -> str:
    """`步骤 T4` 的解释 —— **结果层面的理由 + 块层面的性质**（`基线§14.4`）。

    ⚠️ 它**不是**「内核先看了哪一带」—— 那是**遍历顺序**，`§3.2` 明令不许进输出。
       块的性质是**块的定义性质**（`rank` / 子 / 展开），与顺序无关。
    """
    lines = [f"候选 {len(r.candidates)} 个；解释 {len(r.reasons)} 条（每条 = 方向 + 它覆盖里的一个项）："]
    for did, item in r.reasons[:3]:
        lines.append(f"    {render_reason((did, item))}")
    if len(r.reasons) > 3:
        lines.append(f"    … 其余 {len(r.reasons) - 3} 条同类")
    if r.deepened:
        lines.append(f"  **加深**（`R3a` 的接线）：按需展开 **{len(r.deepened)}** 个方向"
                     f"（只展开「值得往下」那一带）；候选 "
                     f"{len(r.candidates)} → **{r.candidates_after_deepening}**"
                     f"（**只增不减** —— `§K4` 只分叉不覆盖）")
    lines.append("  涉及的块（**性质**，与顺序无关）：")
    # ⚠️ 编号 + 按 `Q` 的规范化顺序（**不是**遍历顺序）—— `§3.2`：
    #    「先看哪一带」是**调度**，不许进输出；这里给的是**块的定义性质**。
    for i, p in enumerate(r.profiles, 1):
        touched = "  ← 有候选" if p.block & r.candidates else ""
        lines.append(f"    {i}/{len(r.profiles)} {render_profile(p)}{touched}")
    # ★★ **下一步建议**（`基线§14.8`）—— 这是「整体倾向」的落点：
    #    它答的是流程 A `R3a` 那个一直空着的依据（「需要更清晰的方向吗？」）。
    if r.advice:
        lines.append(f"  **下一步建议**（答 `R3a` 的依据；覆盖 "
                     f"{r.advice_span}/{len(r.candidates)} 个候选方向 —— **块级判断**）：")
        for b, a in r.advice:
            ids = sorted(b, key=_did_order)
            shown = ", ".join(ids[:3]) + ("…" if len(ids) > 3 else "")
            lines.append(f"    [{shown}]（{len(b)} 个成员）⇒ **{a}**")
        lines.append("    ⚠️ 它是**判断**：`R3a` 拿它当依据。★ 而 `§14.9` 之后本层"
                     "**也会做事** —— `deepen` 会对「值得往下」那一带按需 `expand`"
                     "（`§1` 表已精确化为「**只允许按需 `expand`**」；"
                     "安全契约：**只增不减 + 幂等**，判据 `T7` 守）。")
    # ★ 「按使用细调」的**证据**（`§14.7`）：不印的话，「用了记录」与「没用」长得一样。
    if r.tendency:
        lines.append(f"  按使用细调：{len(r.tendency)} 个方向有带权记录；"
                     f"「先看」档里 **{r.n_blocks_with_tendency}/{len(r.first)}** 块有记录"
                     f"（没有记录的**不参与**比较 —— 「不知道」不等于「不好」）")
    else:
        lines.append("  按使用细调：**没有任何使用记录** ⇒ 顺序**退化到纯结构序**"
                     "（「不用一开始就创建最完美的规则」—— 增量，不要求一开始就对）")
    return "\n".join(lines)

