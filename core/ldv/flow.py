"""四条流程的**可运行实现** —— 工作流程 §1–§4。

    流程 A 运行      `run_query()`       收到查询 → 展示候选 → 记录使用
    流程 B 维护      `insert_items()`    插入新项 → 报告四情形 → 断言局部性
    流程 C 构建验收   → `run_checks.py`（已单独实现）
    流程 D 自优化    `run_optimize()`    收集记录 → 更新内生量 → 断言外生未动

**流程之间只通过三个东西耦合**：方向结构、见证、使用记录（工作流程 §0）。
所以这里每个函数只读它该读的那三样，彼此没有共享状态。

---

## 为什么这一步不是「把已有函数包一层」

流程 A / B / D 原来**只存在于工作流程文档的表格里**。把它们写成能跑的东西时，
每一处「照文档实现」的地方都压出了一个真问题：

    A-1  §8.1 说「只有内核知道它**展示**了什么」——
         但内核原来只知道它**遍历**过什么。两者在多层查询下不同。
         ⇒ 内核补了 `show()`，「展示」变成**显式的一步**。

    A-2  流程 A 原来没有入口 —— 一次「查询 + 展示 + 记账」要调用方自己拼。
         ⇒ `run_query()` 把它固定成 R0–R5 的五步，并保证
           「没给反馈」**不产记录**（没看见 ≠ 看见了不好）。

    B-1  §M0–§M5 是**维护**（插入新项）——
         但 `insert()` 原来只接受「`build()` 时已声明的项」。
         ⇒ 补了 `insert(id, item)`。

    B-2  ★ **`insert()` 从来没让结构长大过。** `expand()` 把「分不开」也写成
         永久标记，于是一个方向一旦被判成叶就永远是叶 —— 树只在**第一批**项上
         长过层。实测（36 项语料，方向 A）：

             一次建完 36         39 个方向   最大叶容量 10
             先建  6，维护 30   **7 个方向** 最大叶容量 **31**   ← 退化成平表

         ⇒ 叶不是终态：成员集变了就**重试**。§M2 情形④（涌现）原来不可达。
           改后同一格：47 个方向，最大叶容量 10。

    B-3  `InsertReport.render()` 把「没长出新层」印成「结构未动」——
         而同一份报告里 `changed` 有 5 项（整条锥的归属都 +1 了）。报告自相矛盾。
         ⇒ 两件事分开印。另外「§K2 判分不开」原来被印成「涌现 2」——
           那两个方向一步都没走 —— 也拆出来单独印（见 B-4）。

    B-4  ★ **§K2 的字面是「不建这一层」，而实现是「先建出来再标记失效」。**
         两者产出不同的结构：字面读法下，被判「分不开」的那一层**根本不存在**。
         旧实现留下**一对空壳子方向**，于是「空权威层比例」这个度量里混进了
         「试过几次」的痕迹，不再是纯粹的「形状好不好用」。
         ⇒ `expand()` 改成：先用**探针**算归属（不建方向、不写账本），
           两侧都非空才真的建；一侧为空则只追加一条 `unsplittable`（挂在**父**上）。
         ⇒ 代价没消失，只是**换了地方**：那一层不建 ⇒ 那些项只能留在这个叶里
           ⇒ **叶容量变大、分辨率下降**。度量也跟着搬（`§K2 判空` + 叶容量）。

    D-1  §F0–§F4 的实现早就在 `core/selfopt.py` 里了 ——
         这一步只是把它按流程串起来，并**第一次在真实使用记录上跑**。

    D-2  `FlowD.内生量确实变了` **退化恒真**：`optimize()` 无条件把
         `penalty_scale` 设成记录条数，所以 0 条记录时 `1.0 → 0.0` 也算「变了」。
         那是「记录数」在动，不是学到了东西。
         ⇒ 加 `学到东西`（至少一个方向拿到 `expand_priority`），
           并让它进退出码 —— **空转不许与通过长得一样**。

⇒ 六个「文档写了、代码没接上」+ 一个「断言退化恒真」。这就是要真跑一遍的理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .core import selfopt
from .core.direction import (
    EVENT_BORN,
    EVENT_OUT_OF_SCOPE,
    EVENT_UNSPLITTABLE,
    EVENT_WITNESS_UPDATED,
)
from .core.kernel import _did_order

# ═══ 流程 A · 运行（工作流程 §1） ═════════════════════════════════════════════


@dataclass(frozen=True)
class FlowA:
    """流程 A 的产物。"""

    query: Any
    result: Any                       # QueryResult（R3 的三去向）
    shown: tuple[str, ...]            # ★ 展示顺序 —— **就是曝光位次**
    records: tuple[Any, ...]          # 本次产生的使用记录（没反馈的方向不产记录）

    @property
    def 没给反馈(self) -> int:
        return len(self.shown) - len(self.records)

    @property
    def 空转(self) -> bool:
        """一次候选都没摆出来 ⇒ 这一步没有产出（**不许与通过长得一样**）。"""
        return not self.shown

    def render(self) -> str:
        r = self.result
        return (
            f"查询「{self.query.label or '—'}」→ {r.render()}\n"
            f"  展示 {len(self.shown)} 个候选"
            f"（位次 1..{len(self.shown)}）｜产生记录 {len(self.records)} 条"
            f"｜没给反馈 {self.没给反馈} 个（**不产记录**：没看见 ≠ 看见了不好）"
        )


def synthetic_feedback(kernel: Any, query: Any, result: Any) -> dict[str, float]:
    """**合成**反馈 —— 真实系统里这一格来自使用者。

    为什么可以合成：`ideal` 本身就是**外生声明的 ground truth**（§I2 的纪律），
    所以「这个方向收下的项里有没有该命中的」是**算得出来**的。

    判据：方向成员与 `ideal` 有交 ⇒ `+1`（有用）；否则 `−1`（白摆）。

    ⚠️ 两者都**不是**「没看见」。「没看见」用 `None` ⇒ §I6 返回 `None` ⇒ **不产记录**
       （§K6 / `B6`：无信号 ≠ 负信号）。

    ⚠️ 这只是**把 A → D 串起来**用的，不代表真实使用分布。
       真实分布下倾向加权才真正起作用（见 `C7-流程跑通.md` §4）。
    """
    fb: dict[str, float] = {}
    for did in result.yes:
        d = kernel.direction(did)
        fb[did] = 1.0 if (kernel.members_of(d) & query.ideal) else -1.0
    return fb


def run_query(kernel: Any, query: Any, *,
              skip: frozenset[str] = frozenset(),
              feedback: dict[str, float] | None = None,
              auto_feedback: bool = True) -> FlowA:
    """流程 A。R0–R4 由内核做；**R5 的关键是「展示」与「遍历」分开**。

    R5 原来没有入口 —— 内核在遍历时顺手记了「最后一层前沿」当展示。
    这里把它拆成显式两步：

        R4  内核遍历，得到候选（说「是」的方向）
        R5a `kernel.show(候选)` —— **展示**：内核记下摆了哪些、什么次序
        R5b 逐条 `record_usage` —— 倾向权重由内核按**展示位次**填

    `feedback` 给的是「这次交互有没有用」；**缺席 = 没看见 = 不产记录**。

    `skip` 是**透传给内核**的「已知说否的方向」集合（`Kernel.query` 的 docstring 有全解）
    —— 检索器层的 `步骤 T3` 用它跳过「后看」的块。⚠️ **它不改结论**：那些方向照 `Tri.NO`
      分支处理；判据 `T1` 守「候选集必须与全扫逐项相同」。
    """
    result = kernel.query(query, skip=skip)           # R0–R4
    shown = kernel.show(result.yes)                   # R5a ★ 展示
    fb = (dict(feedback) if feedback is not None
          else (synthetic_feedback(kernel, query, result) if auto_feedback else {}))
    records = []
    for did in shown:                                 # R5b
        rec = kernel.record_usage(kernel.direction(did), {"outcome": fb.get(did)})
        if rec is not None:
            records.append(rec)
    return FlowA(query=query, result=result, shown=shown, records=tuple(records))


# ═══ 流程 B · 维护（工作流程 §2） ═════════════════════════════════════════════


@dataclass(frozen=True)
class InsertReport:
    """一次插入的变动报告 —— §M2 的四种情形各占一格。

    ⚠️ 这里只有**三**格（情形①②④）。

    §M2 情形③「标记失效（不删）」**不在这一格上** —— 它属于**删除**那条路，
    由 `remove_items` 的 `RemoveReport` 报（§10.2 C 已落地）。
    ⚠️ 它曾经是「当前不可达」所以**刻意留空**；现在可达了，**也不放回来**：
       一个「删除才可能非空」的槽塞进「插入」的报告里，等于让读报告的人
       在插入这条路上看见一个**永远为空**的字段 —— 那正是本仓库在防的形状
       （「空转与通过长得一模一样」）。**放回它自己的报告里**，两条路各自干净。
    """

    item: str
    cone: tuple[str, ...]                       # M1 支撑锥 = 插入路径
    born: tuple[str, ...] = ()                  # 情形④ 涌现 —— **真的长出来了**
    unsplittable: tuple[str, ...] = ()          # §K2 判「这一层不建」（**判定**，不是失效）
    witness_updated: tuple[str, ...] = ()       # 情形② 见证更新
    changed: tuple[str, ...] = ()               # 存在或归属发生变化的全部方向
    out_of_cone: tuple[str, ...] = ()           # M3：落在 Cone(x) 外的变动 ⇒ 必须为空
    #: ★ 这一项**落在根覆盖之外**，因此**没有进结构**（§10.2 出路 (1)）。
    #: 它不是一个「失败」，是一个**范围**事实 —— 但它必须**被印出来**，
    #: 否则「没长出新层」会被读成「结构没动」，而真相是「这个项压根没进去」。
    out_of_scope: bool = False
    events: int = 0                             # ★ 本次追加的账本事件**总条数**
    #: ⚠️ `events` 和上面几个字段**不是一回事**：那些是按 did 归类后的**去重视图**。
    #: 账本核对必须用 `events` —— 用去重视图去比会差出「重叠数」那么多条。

    def render(self) -> str:
        if self.out_of_scope:
            # ⚠️ 这一格必须**单独**、**在最前**印出来。
            #    它落进「锥 0 层 + 没有变动」那个形状里，与「插进去了但结构没动」
            #    长得**一模一样** —— 正是本仓最防的那件事。
            return (f"插 {self.item}｜★ 落在**根覆盖之外** ⇒ 不塞进结构"
                    f"（§10.2 出路 (1)：范围事实，已记账）")
        bits = [f"插 {self.item}｜锥 {len(self.cone)} 层"]
        if self.born:
            bits.append(f"涌现 {len(self.born)}：{list(self.born[:3])}")
        if self.witness_updated:
            bits.append(f"见证更新 {len(self.witness_updated)}：{list(self.witness_updated[:3])}")
        if self.unsplittable:
            # ⚠️ 这不是「涌现」，也**不是「失效」** —— §K2 判「这一层不建」时
            #    那一层**根本没建出来**，所以没有东西可失效。它是一条**判定**。
            bits.append(f"§K2 判空（这一层不建）{len(self.unsplittable)}"
                        f"：{list(self.unsplittable[:2])}")
        if not self.born and not self.witness_updated:
            # ⚠️ 「没长出新层」**不等于**「结构未动」。
            #
            # 原来这里印的是「结构未动」—— 而同一份报告里 `changed` 有 5 项
            # （整条锥的归属都 +1 了）。**报告自己打自己**。
            bits.append(f"没长出新层（归属沿锥更新 {len(self.changed)} 个方向）")
        if self.out_of_cone:
            bits.append(f"‼ 锥外变动 {list(self.out_of_cone[:3])}（M3 违规）")
        return "｜".join(bits)


def insert_items(kernel: Any, items: Iterable[tuple[str, Any]]) -> list[InsertReport]:
    """流程 B。M0–M5，**逐项插入、每次前后比对**。

    入参是 `(item_id, item)` 对；`item` 非空 ⇒ 这是**新项**（不在 `build()` 的全集里）。

    M3 的「变动 ⊆ `Cone(x)`」如果只查**终态**就什么都没查 —— 终态本来就自洽。
    所以这里逐项比对，与 `B8` 是同一个形状（那条检查吃过「只查终态」的亏）。
    """
    reports: list[InsertReport] = []
    for iid, item in items:
        before_dirs = {d.did for d in kernel.all_directions()}
        before_mem = {d.did: kernel.members_of(d) for d in kernel.all_directions()}
        before_events = len(kernel.ledger)

        path = kernel.insert(iid, item)                     # M1 / M2

        new_events = list(kernel.ledger)[before_events:]
        out_of_scope = any(e.kind == EVENT_OUT_OF_SCOPE for e in new_events)
        changed: set[str] = set()
        for d in kernel.all_directions():
            if d.did not in before_dirs:
                changed.add(d.did)                          # 新出现的
            elif kernel.members_of(d) != before_mem.get(d.did):
                changed.add(d.did)                          # 归属变了
        cone = set(path)
        # 允许：在路径上；或**父在路径上**（展开是沿路径发生的）
        out = sorted(
            did for did in changed
            if did not in cone
            and (kernel.direction(did).parent is None
                 or kernel.direction(did).parent not in cone)
        )
        # ★ 「建出来」和「真的长出来了」是两件事；「判空」是第三件。
        #   §K2 判「不建」时**一个方向都不建**，所以 did 不重叠 —— 但按种类分开
        #   归类仍然必要（一个 did 可能先判空、后来长出来）。
        reports.append(InsertReport(
            item=iid,
            cone=tuple(path),
            born=tuple(sorted({e.did for e in new_events if e.kind == EVENT_BORN},
                              key=_did_order)),
            unsplittable=tuple(sorted({e.did for e in new_events
                                       if e.kind == EVENT_UNSPLITTABLE}, key=_did_order)),
            witness_updated=tuple(e.did for e in new_events
                                  if e.kind == EVENT_WITNESS_UPDATED),
            changed=tuple(sorted(changed)),
            out_of_cone=tuple(out),
            out_of_scope=out_of_scope,
            events=len(new_events),
        ))
    return reports


# ═══ 流程 B′ · 删除（工作流程 §2 的另一半；设计文档 §10.2 C） ═════════════════


@dataclass(frozen=True)
class RemoveReport:
    """一次删除的变动报告 —— `§M2 情形③`。

    与 `InsertReport` **分开**，不是合并成一个「维护报告」：两条路查的东西不同。
    删除这条路**不产生**「涌现」「判空」「见证更新」—— 那三个是**插入**的产物。
    合并的话，读报告的人会在删除那条路上看见三个**永远为空**的槽。

    ## 六条检查 `D1–D6` 落在这里

        字段                  对应
        ──────────────────────────────────────────────
        `invalidated`         `D1` 空方向必须带失效记录（这里是「记了几条」）
        `unrecorded_empty`    `D1` 的反面：空了却**没记账**的方向 ⇒ 必须为空
        `cover_leak`          `D2` **度量**：覆盖(父) − ∪覆盖(子)（整份语料）
        `cover_leak_unaccounted` `D2` **判据**：其中**没有证明**的项数 ⇒ 必须为 0
        `outside_members`     `D3` members(父) ⊆ 覆盖(父)
        `out_of_cone`         `D4` 变动 ⊆ `Cone(x)`（M3 的删除路径版本）
        `ledger_rewritten`    `D5` 账本只增不改
        `gone_directions`     `D6` 「已失效」与「从来没存在过」分得开

    ⚠️ **`D2` 为什么要两个数（2026-10-08 改）**：`cover_leak` 那个数**含两类不同的东西**
    —— ① 论域外的项（还没进结构的 `holdout`）、② 有证明的滞留项（§10.2 出路 (4)）。
    拿它当判据 ⇒ **基线就红**，而那条红与删除**毫无关系**（`remove` 不碰 payload）。

        实测（`outputs/_probe_d2_four.py`，4 份语料 × 3 方向 × 3 段 = 36 行）
            D2 裸 3 ｜收域 2 ｜记账 1 ｜**收域+记账 0**
        以及 `outputs/_probe_d2_stage2.py`（n100/sequence 逐对摊开）：
            批建后  归属漏 0 语义漏 1（漏的是 `holdout`，不在树里）
            维护后  归属漏 1 语义漏 1 滞留 1（**同一项** ⇒ 有证明）
            删除后  与维护后**逐项相同**（12 次删除，读数一次都没动）

    ⇒ 判据取「**漏了、而且没有证明**」，度量取裸漏。两个都印 —— 只印一个，
      读的人分不出「没有漏」与「漏了但有账」。

    ⚠️ `D2` / `D3` 的读数**要外生 oracle**（`checks/_fixtures.coverage_of`）。
       所以这两个字段**默认 `None`**（= 未展开），由**调用方**填；`flow.py` 平时
       不依赖 `checks/`，只在传了 `cover` 时**惰性**引入那一个函数。
       ⚠️ `None` 与 `0` **不是一回事** —— 前者是「没量」，后者是「量了，是 0」。
       `render()` 把两者印成不同的字（`D2 漏 **未展开**` vs `D2 漏 0`）。
    """

    item: str
    cone: tuple[str, ...]                  # `Cone(x)` = 删除路径（返回值）
    emptied: tuple[str, ...] = ()          # 这一删让哪些方向**支持集空了**
    invalidated: tuple[str, ...] = ()      # 记了失效账目的方向（= 锥）
    unrecorded_empty: tuple[str, ...] = ()  # `D1`：空了却没记账 ⇒ 必须为空
    changed: tuple[str, ...] = ()          # `members` 变了的全部方向
    out_of_cone: tuple[str, ...] = ()      # `D4` / M3：落在锥外的变动 ⇒ 必须为空
    gone_directions: tuple[str, ...] = ()  # `D6`：删前在、删后不在 `_dirs` ⇒ 必须为空
    ledger_rewritten: tuple[str, ...] = ()  # `D5`：被改写的既有账目 ⇒ 必须为空
    cover_leak: int | None = None          # `D2` 度量（整份语料；`None` = 未展开）
    #: `D2` **判据** —— 漏了、而且**没有证明**的项数（论域收在 `placed` 上）。
    #: ⚠️ 与 `cover_leak` **不是同一个数**，见类 docstring。
    cover_leak_unaccounted: int | None = None
    #: `D2` 的**排除读数** —— 被论域/账目筛掉的项数。排除必须**看得见**，
    #: 否则「一个漏都没有」与「漏都被筛掉了」长得一模一样。
    cover_leak_excluded: int | None = None
    outside_members: int | None = None     # `D3`（外生 oracle；`None` = 未展开）
    events: int = 0                        # 本次追加的账本事件条数

    @property
    def 违规(self) -> tuple[str, ...]:
        """**进退出码**的那些 —— 与 `render()` 里印的不完全是一回事。

        ⚠️ `D2` / `D3` 只在**量过**（`is not None`）时才算违规。
           「没量」不能算过，也不能算违规 —— 它是**未展开**，必须**印出来**。
        """
        bad: list[str] = []
        if self.unrecorded_empty:
            bad.append(f"D1 空方向没记账：{list(self.unrecorded_empty)}")
        if self.out_of_cone:
            bad.append(f"D4/M3 锥外变动：{list(self.out_of_cone)}")
        if self.gone_directions:
            bad.append(f"D6 方向被真删：{list(self.gone_directions)}")
        if self.ledger_rewritten:
            bad.append(f"D5 账本被改写：{list(self.ledger_rewritten)}")
        # ⚠️ 判据取的是**无证明**那个数，不是裸漏 —— 见类 docstring 的实测表。
        if self.cover_leak_unaccounted:
            bad.append(f"D2 无证明的覆盖漏 {self.cover_leak_unaccounted}")
        if self.outside_members:
            bad.append(f"D3 越界成员 {self.outside_members}")
        return tuple(bad)

    def render(self) -> str:
        bits = [f"删 {self.item}｜锥 {len(self.cone)} 层"]
        if self.emptied:
            bits.append(f"支持集空了 {len(self.emptied)}：{list(self.emptied[:3])}")
        bits.append(f"失效记账 {len(self.invalidated)} 条")
        # ★ `D2` / `D3` 必须把「没量」与「量了是 0」印成**不同的字**；
        #   `D2` 还要把「漏了几项」与「其中没有证明的几项」分开印 ——
        #   只印一个数的话，「没有漏」与「漏了但有账」长得一模一样。
        d23 = []
        if self.cover_leak is None:
            d23.append("D2 漏 **未展开**")
        else:
            ex = self.cover_leak_excluded
            d23.append(f"D2 漏 {self.cover_leak}"
                       f"（其中无证明 {self.cover_leak_unaccounted}"
                       + (f"；论域/账目外 {ex}" if ex else "") + "）")
        d23.append(f"D3 越界 {self.outside_members}"
                   if self.outside_members is not None else "D3 越界 **未展开**")
        bits.append("｜".join(d23))
        if self.违规:
            bits.append("‼ " + "；".join(self.违规))
        return "｜".join(bits)


def remove_items(kernel: Any, ids: Iterable[str], *,
                 cover: Any = None) -> list[RemoveReport]:
    """流程 B′。删项，**逐项前后比对**（§10.2 C）。

    与 `insert_items` 同形，理由也一样：`D4`（变动 ⊆ `Cone(x)`）**只查终态就什么都没查**
    —— 终态本来就自洽。所以逐项比对。

    `cover` 是**外生覆盖 oracle**（`cover(payload) -> frozenset[str]`）。
    传了 ⇒ `D2` / `D3` 被量；不传 ⇒ 它们是 `None`（未展开），**不是 0**。
    """
    from .core.direction import EVENT_INVALIDATED

    reports: list[RemoveReport] = []
    for iid in ids:
        before_dirs = {d.did for d in kernel.all_directions()}
        before_mem = {d.did: kernel.members_of(d) for d in kernel.all_directions()}
        before_events = len(kernel.ledger)
        # `D5` 要**外部**指纹：`Ledger._digest` 只由 `append` 维护，就地改 `detail`
        # 它看不见 —— 而 `detail` 是可变字典，`Event` 冻结拦不住。
        before_lines = [(e.seq, e.kind, e.did,
                         tuple(sorted((k, repr(v)) for k, v in e.detail.items())))
                        for e in kernel.ledger]

        path = kernel.remove(iid)                       # §10.2 C 的 ①②

        new_events = list(kernel.ledger)[before_events:]
        after_lines = [(e.seq, e.kind, e.did,
                        tuple(sorted((k, repr(v)) for k, v in e.detail.items())))
                       for e in kernel.ledger]
        rewritten = [f"第 {i} 条" for i, fp in enumerate(before_lines)
                     if i >= len(after_lines) or after_lines[i] != fp]

        changed: set[str] = set()
        for d in kernel.all_directions():
            if d.did not in before_dirs:
                changed.add(d.did)
            elif kernel.members_of(d) != before_mem.get(d.did):
                changed.add(d.did)
        cone = set(path)
        emptied = tuple(did for did in path if not kernel.members_of(kernel.direction(did)))
        recorded = {e.did for e in new_events
                    if e.kind == EVENT_INVALIDATED and e.detail.get("members_after") == 0}
        # `D1`：空了、但**没有**失效账目。⚠️ 这里判的是「**支持集空**且无记录」，
        #      不是「有没有出现过 `invalidated`」—— 后者会把**还活着**的方向也标上。
        unrecorded = tuple(did for did in emptied if did not in recorded)

        leak = outside = leak_bad = leak_ex = None
        if cover is not None:
            from .checks.coverage import (
                cover_leak_profile,
                cover_leak_unaccounted,
                soundness_profile,
            )
            leak = cover_leak_profile(kernel, cover)["漏项数"]
            # ★ **判据**取「漏了、而且没有证明」的那一个数 —— 不是裸漏。
            #   理由与实测见 `RemoveReport` 的 docstring：裸漏含
            #   ① 论域外的项（还没进结构的）、② 有证明的滞留项，
            #   拿它当判据 ⇒ **基线就红**，而那条红与删除毫无关系。
            acc = cover_leak_unaccounted(kernel, cover)
            leak_bad = acc["无证明漏项数"]
            leak_ex = acc["论域外漏项数"] + acc["陈旧滞留数"]
            outside = soundness_profile(kernel, cover)["越界成员数"]

        reports.append(RemoveReport(
            item=iid,
            cone=tuple(path),
            emptied=tuple(sorted(emptied, key=_did_order)),
            invalidated=tuple(sorted({e.did for e in new_events
                                      if e.kind == EVENT_INVALIDATED}, key=_did_order)),
            unrecorded_empty=tuple(sorted(unrecorded, key=_did_order)),
            changed=tuple(sorted(changed)),
            out_of_cone=tuple(sorted(did for did in changed if did not in cone)),
            gone_directions=tuple(sorted(before_dirs - {d.did for d in kernel.all_directions()})),
            ledger_rewritten=tuple(rewritten),
            cover_leak=leak,
            cover_leak_unaccounted=leak_bad,
            cover_leak_excluded=leak_ex,
            outside_members=outside,
            events=len(new_events),
        ))
    return reports


# ═══ 流程 D · 自优化（工作流程 §4） ═══════════════════════════════════════════


@dataclass(frozen=True)
class FlowD:
    """流程 D 的产物。"""

    before: tuple = ()            # 内生量指纹（改之前）
    after: tuple = ()             # 内生量指纹（改之后）
    info: dict = field(default_factory=dict)
    params: Any = None            # 更新后的参数表

    @property
    def 内生量确实变了(self) -> bool:
        return self.before != self.after

    @property
    def 学到东西(self) -> bool:
        """**有证据的**变化 —— 至少一个方向拿到了 `expand_priority`。

        ⚠️ 判「这一步有没有产出」**不能**用 `内生量确实变了`：它**退化恒真**。

        `optimize()` 无条件把 `penalty_scale` 设成记录条数，所以 0 条记录时
        `1.0 → 0.0` 也算「变了」。那是「记录数」这个数在动，
        **不是学到了任何东西** —— 空转与通过长得一模一样。

        实测：`python -m ldv.cli d`（不先跑 A ⇒ 没有记录）
             旧输出「内生量：2 项 → 2 项（**变了**）」＋退出码 0。
        """
        return self.info.get("更新到的方向数", 0) > 0

    def render(self) -> str:
        i = self.info
        if not self.学到东西:
            verdict = "⚠ **空转**：没有带权记录 ⇒ 内生量只动了常数项，没学到东西"
        elif self.内生量确实变了:
            verdict = "内生量确实变了（**有证据**）"
        else:
            verdict = "内生量没变 —— 该查为什么"
        return (
            f"带权记录 {i.get('带权记录', '?')} 条｜被拦下（缺倾向）{i.get('被拦下（缺倾向）', '?')} 条"
            f"｜更新到 {i.get('更新到的方向数', '?')} 个方向\n"
            f"  内生量：{len(self.before)} 项 → {len(self.after)} 项（{verdict}）\n"
            f"  外生项：{i.get('外生项指纹')}（**逐项未变**，F3 已断言）"
        )


def run_optimize(kernel: Any, params: selfopt.Params) -> FlowD:
    """流程 D。F0–F4 —— 实现全在 `core/selfopt.py`，这里按流程把它串起来。

    `optimize()` 自带 F3（外生项未动）与 F4（无全局评分）两条断言，红了就抛。
    所以「跑完了」本身就是这两条护栏成立的证据。
    """
    before = params.fingerprint_endogenous()
    after, info = selfopt.optimize(kernel, params)          # F0–F4
    return FlowD(before=before, after=after.fingerprint_endogenous(),
                 info=info, params=after)


# ═══ 装配：把 A → B → D 串成一条链 ═══════════════════════════════════════════


def make_params(direction_name: str, outermost_intent: str) -> selfopt.Params:
    """外生项**由人声明**（§8.2）—— 这里把两个必填的外生量写死成人给的值。"""
    return selfopt.Params(
        exogenous={"direction_functions": [direction_name],
                   "outermost_intent": outermost_intent},
        endogenous={"expand_priority": {}, "penalty_scale": 1.0},
    )


def render_chain(a: FlowA | None, b: Sequence[InsertReport] | None,
                 d: FlowD | None, r: Sequence[RemoveReport] | None = None) -> str:
    """把四个流程的输出拼成一份可读追踪。

    `r` 是**删除**那一段（流程 B′，§10.2 C）。⚠️ 它与 `b` **分开印**，
    不合并成「维护」一段 —— 合并会让「插入 12 项、删除 0 项」与
    「插入 0 项、删除 12 项」在输出上长得一模一样。
    """
    lines: list[str] = []
    if a is not None:
        lines.append("── 流程 A · 运行 ──")
        lines.append("  " + a.render().replace("\n", "\n  "))
    if b is not None:
        lines.append("── 流程 B · 维护 ──")
        grew = [r_ for r_ in b if r_.born]
        touched = sum(len(r_.changed) for r_ in b)
        # ⚠️ 这里原来写 `// 2` —— 因为旧实现一次判空**建一对**方向，报告里记两个 did。
        #    字面 §K2 之后判空**不建方向**，事件是**一条**（挂在父上），所以不再除 2。
        judged = sum(len(r_.unsplittable) for r_ in b)
        lines.append(f"  插入 {len(b)} 项：长出新层 {len(grew)} 项；"
                     f"归属更新累计 {touched} 处（**都在各自的锥上**）"
                     + (f"；§K2 判「这一层不建」{judged} 次" if judged else ""))
        # ★ 落在**根覆盖之外**的项必须单独计数（§10.2 出路 (1)）。
        #   它们既不在「长出新层」里，也不在「归属更新」里 —— 不单列就等于没发生。
        oos = [r_ for r_ in b if r_.out_of_scope]
        if oos:
            lines.append(f"  ★ 其中 {len(oos)} 项落在**根覆盖之外**（不塞进结构，"
                         f"§10.2 出路 (1)）：{ [r_.item for r_ in oos[:3]] }"
                         f" —— 这是**范围**事实，不是失败；它们**在账上**")
        for r_ in b[:6]:
            lines.append("  · " + r_.render())
        if len(b) > 6:
            lines.append(f"  · …（还有 {len(b) - 6} 项）")
        viol = [r_ for r_ in b if r_.out_of_cone]
        lines.append(f"  M3 断言：锥外变动 {len(viol)} 处"
                     + ("（**必须为 0**）" if not viol else " ‼ 违规"))
    if r is not None:
        lines.append("── 流程 B′ · 删除（§10.2 C）──")
        if not r:
            # ⚠️ 「没删」与「删了但什么都没发生」必须分得开。
            lines.append("  删除 0 项（**本趟没跑**）—— 未删与删了无变化不共用一行")
        else:
            empt = sum(len(x.emptied) for x in r)
            inv = sum(len(x.invalidated) for x in r)
            lines.append(f"  删除 {len(r)} 项：支持集空了 {empt} 个方向；"
                         f"追加失效账目 {inv} 条")
            for x in r[:6]:
                lines.append("  · " + x.render())
            if len(r) > 6:
                lines.append(f"  · …（还有 {len(r) - 6} 项）")
            bad = [x for x in r if x.违规]
            lines.append(f"  D1–D6 断言：违规 {len(bad)} 处"
                         + ("（**必须为 0**）" if not bad else " ‼ 违规"))
            if any(x.cover_leak is None for x in r):
                lines.append("  ⚠️ D2/D3 **未展开**（调用方没传覆盖 oracle）"
                             "—— 未展开 ≠ 通过")
    if d is not None:
        lines.append("── 流程 D · 自优化 ──")
        lines.append("  " + d.render().replace("\n", "\n  "))
    return "\n".join(lines)
