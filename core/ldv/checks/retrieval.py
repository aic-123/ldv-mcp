"""`T1`–`T3` —— **检索器层（流程 T）的三条判据**。

    python -m ldv.run_checks            # 在 `(检索)` 那一组里跑

被判对象是 `ldv/retriever.py`（流程 T 的算法）。三条的来源是 `基线§7` 那张表。

| 编号 | 守谁 | 守的东西 | 红形态 |
|---|---|---|---|
| `T1` | 流程 T | **候选集(步骤 T3) ≡ 候选集（不用视图、直接按 `R0` 遍历）** | `步骤 T3` **漏跑了「后看」的块**（早停 / 忘记跑） |
| `T2` | 流程 T | **认识必须是当前的**：用到的 `spec` 指纹 == 当前结构的 `spec` 指纹 | 结构动了（流程 B），那份认识**没跟着变** |
| `T3` | 流程 T | **解释指得到**：`步骤 T4` 的每条理由都指得到实际结果 | 解释里出现一个成分，而它**不在**任何候选方向的覆盖里 |

★ **`T1` 是承重的那一条**：它守住「**没漏**」（`§K8` 假阴禁止）。

## ★★ 「顺序」的后果：从**无处可达**到**少扫**（2026-10-09 → 2026-10-14）

**2026-10-09 的实测**：`T2` 算出的顺序**无处可达** —— `kernel.query` / `flow.run_query`
只收 `q`，从 `root` 遍历整棵树；而视图块**横跨树**（不是子树）⇒ 「只扫先看的块」
**做不到**（不能传参，也不能复制流程 A 的循环）⇒ `T3` 的循环是个**空转循环**。
三个探针（`outputs/_probe_retriever_inert.py`，顺序**反转** / 换成**空元组** /
换成**离散划分**）⇒ 输出**逐项相同**。

**2026-10-14 的改动**：把「后看」的块（块级 `hit` 说「否」）**真的跳过**。
依据是 `§I2` 的一条推论（下面「夹具限制」那节有证明）：

    合并说「否」 ⇒ **每个成员都说「否」**

⇒ `kernel.query` / `flow.run_query` 加了 `skip=`（`§T4` 停止条件 ① **已拍**）。
⇒ **`scanned` 从「全部块」缩到「先看的块」** —— 顺序**第一次**有了可观测的后果。
⚠️ 省的是**重复判定**，**不是结论**：那些方向照 `Tri.NO` 分支处理（不展开）⇒ 候选集逐项不变。

⇒ **所以这一组今天守的是「不许因为顺序而漏东西」**（`T1` 的承重那一半）——
  ★ 而它在改动**前后都成立得很彻底**：`T1` 拿**全扫**当基准，跳错一个方向就红。
⚠️ **「跑过 N/M 块」必须印出来**（`retriever.render_reading` 会印）——
   不印的话，「读数」与「收益」在只看数字时**长得一模一样**。
★★ **口径（`基线§14.6`，2026-10-14 改）：收益 = 影响**，本层认**两种**：
   (a) **判断质量**（块画像 / 建议 / 读法 / 粒度）—— 2026-10-11 起就在交付
   (b) **少扫**（`scanned` < 全部块）—— 2026-10-14 起生效
   ⚠️ 2026-10-11 的旧口径写「访问量是**读数**、不是收益」，**已改**（见 `§14.6`）。

---

★ **三条都是守卫**（`§C3`）—— **正确实现下恒绿**，红形态**只能手造**。
   这不是「判据没在判」，是它们的红本来就落在**契约层**（同 `§M3` / `§M5` 的处境）：
   它们守的是**接口的承诺**，不是**这一份实现**。⇒ 三条都必须配注入，而且注入是手造的。
   这条必须写在这里 —— 否则「恒真」与「没在判」在只看布尔值时**长得一模一样**。

⚠️ **`T3` 判的**不是**「解释暴露了遍历顺序」** —— 那样反而错了：遍历顺序**不许**进输出
   （`基线§3.2`）。`T3` 判的是解释与**结果**对不对得上。

---

## ★ 一处**实测出来的**夹具限制（写在这里，免得下一轮重新推一遍）

`T1` 的注入要造出「**后看的块里真有命中**」（`§C3` 的 ⚠️），否则它恒绿。
**但这条夹具在「满足 `§I2 覆盖契约」的插件上造不出来** —— 有一条证明：

    `覆盖(合并(S)) ⊇ ⋃_{d∈S} 覆盖(d)`          （`§I2` 的契约，`B2` 守它）
    `命中(·, q) = 否`  ⟺  `覆盖(·) ∩ ideal = ∅`
    ⇒ 合并说「否」 ⇒ 覆盖(合并) ∩ ideal = ∅ ⇒ 每个成员的覆盖也 ∩ ideal = ∅ ⇒ 每个成员都「否」

⇒ **后看的块里不可能有「是」的成员**（本仓库的真插件都满足 `§I2`）⇒ 早停**丢不了候选**
   ⇒ 这条判据在真插件上**永远绿**。

⇒ 夹具只能**手造一个 `合并` 会丢成员的插件**（`_LosingMerge`）—— 而那恰恰是
   `基线§3.1` 描述的那个情形（「`合并` **实测**可以丢」）。
   ★ 这不是绕过判据：判据判的是「检索器有没有漏」，
     而「漏得掉」的前提正是**合并会丢**。夹具造的就是这个前提。

## ★★ 2026-10-14：这个前提**从「手造」变成了「生产路径」**

`步骤 T3` 现在**真的跳过「后看」的块**（`retriever.run_in_order(skip=...)`），
而跳过的依据正是上面那条推论 ⇒ 于是：

    真插件（满足 `§I2`）         ⇒ 跳过**不丢候选** ⇒ `T1` 绿（实测：18 条有后看块的查询，0 条丢）
    `_LosingMerge`（破坏 `§I2`） ⇒ 跳过**真丢候选** ⇒ `T1` **红**

⇒ 所以 `known_answer_controls` 里 `T1` 的「红」**不再需要手造** ——
  `_LosingMerge` + 生产路径上的跳过就是那个输入。
  ⚠️ 而 `_early_stop` 仍然留着：它是 `test_injections` 里 `T1` 的**注入**，
     「注入」与「合成对照」是两件事（一个造红形态，一个证判据会红）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

from ..core.tri import Tri
from ..core.views import ViewSpec, coarsest_stable_refinement, partition_of
from ..retriever import (
    _DISK_UNSET,
    Retrieval,
    as_bar,
    deepen,
    render_explanation,
    render_reading,
    render_reason,
    retrieve,
    spec_fingerprint,
)
from ._framework import Report

#: 检索器层的判据编号 —— 与插件侧 / 内核侧 / 视图侧 / CLI 侧**并列**，不是它们的一部分。
#: ⚠️ **只列**已经实现了的（写进来就等于声称「套件全绿」覆盖了它）。
RETRIEVER_CODES = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")

#: 三条的**标题** —— **一处定义，两处用**（真跑时 `rep.add`、跳过时 `skip_all`）。
#:
#: ⚠️ **分开写两份会各自漂移**，而两边都只是字符串 ⇒ 漂移了**没有任何东西看得出来**
#:    ——「跳过时印的标题」与「真跑时印的标题」不一致时，读起来像**两条不同的判据**。
#:    `run_tests.test_retriever` ⑥ 段**逐字对照**两边（并核 `set(TITLES) == set(RETRIEVER_CODES)`）。
TITLES: dict[str, str] = {
    "T1": "等价：候选集(步骤 T3) ≡ 候选集（不用视图、直接按 R0 遍历）",
    "T2": "当前：**盘上那份**认识的 `spec` 指纹 == 当前结构的 `spec` 指纹（陈旧 ⇒ 红）",
    "T3": "解释指得到：`步骤 T4` 的每条理由都指得到实际结果",
    "T4": "画像不许说错：三档读法（已细分过/分不开/还没长出来）+混合 与结构事实相符",
    "T5": "按使用细调真的进了顺序（有记录的块按倾向降序、排在「不知道」之前）",
    "T6": "下一步建议（答 `R3a` 的依据）与结构事实相符 —— 块级判断，不是逐方向",
    "T7": "深化（`R3a` 的接线）**只增不减** —— 展开不许动结论（`§K4` / `§K8`）",
    "T8": "交到检索层的认识**最粗**（`q` 逐块等于 `csr(spec)`）—— 更细是**退化**，不是错",
}


# --- `T1` ---------------------------------------------------------------------

def t1_equivalence(rep: Report, r: Retrieval, kernel: Any) -> None:
    """`T1` —— 候选集(步骤 T3) ≡ 候选集（**不用视图、直接按 `R0` 遍历**）。

    ⚠️ 右边那次遍历是 `kernel.query(q)` —— 流程 A 的 `R0`–`R4`，**不经视图**。
       两边都是**真跑**出来的，不是把左边抄一遍。

    跳过：需求为空 / 没有可判的方向（`§C3`）。
    """
    q = r.query
    if not (q.ideal or q.require or q.forbid):
        rep.add("T1", TITLES["T1"], Tri.UNEXPANDED,
                "需求**为空**（`ideal` / `require` / `forbid` 三样都没有）"
                "⇒ 没有可判的方向。**跳过 ≠ 通过**")
        return
    expected = frozenset(kernel.query(q).yes)
    got = r.candidates
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    bad = bool(missing or extra)
    rep.add("T1", TITLES["T1"], Tri.NO if bad else Tri.YES,
            (f"候选集**不等**：漏 {len(missing)} 个（{missing[:3]}）、"
             f"多 {len(extra)} 个（{extra[:3]}）—— `步骤 T3` 把**块**当成了终止依据？"
             f"（基线§8 停止条件 2：改实现，**不许**放宽本判据）") if bad
            else (f"{len(got)} 个候选 ≡ 不用视图直接按 `R0` 遍历的结果"
                  # ⚠️ 2026-10-09 这里写「代价也没改，顺序无处可达」；2026-10-14 起
                  #    `步骤 T3` 会**跳过「后看」的块** ⇒ 「代价」那一半**现在有了**。
                  f"（★ 2026-10-14 起：跑过 {len(r.scanned)}/{len(r.first) + len(r.late)} 块"
                  f"（跳过后看）—— **不改变结论**这一半照旧成立，判据就是这一条）"))


# --- `T2` ---------------------------------------------------------------------

def t2_current(rep: Report, r: Retrieval, spec: ViewSpec) -> None:
    """`T2` —— **盘上的认识必须是当前的**（`基线§6`）。

    ## ★★ 命题在 2026-10-10 **改过**，原因是旧的**按构造恒真**

    旧命题：「用到的那份认识必须是当前的」。而 `current_recognition` 在
    陈旧/坏档时**会重算**、并把 `fingerprint` 写成**当前**的 ⇒ 三个分支**都**返回
    当前指纹 ⇒ `rec.fingerprint != now` **永远是假** ⇒ 那条判据**不可能红**
    （实测：造一份陈旧 `views.json`，它照样绿）。

    ⇒ 改成判**盘上那份**：`rec.disk_fingerprint`（盘上那份**自己**的指纹）。

        `disk_fingerprint is None`   ⇒ 盘上没有 ⇒ **跳过**（没得比；跳过 ≠ 通过）
        盘上有，但**读不回来**       ⇒ **红**（坏了 / 与当前结构不自洽）
        盘上有，指纹 ≠ 当前          ⇒ **红**（**陈旧** —— 认识没跟着结构变）
        盘上有，指纹 == 当前         ⇒ 过

    ## 为什么「陈旧」该红（用户裁定 2026-10-10：**认识必须要维护**）

        陈旧 ⇒ `current_recognition` **悄悄重算** ⇒ 结果仍然**正确**
        ⇒ 但「抽象层**持续**影响检索层」这件事**归零了**，而它**原先看不见**
        （「悄悄重算」与「真的用了盘上那份」只差一句 `source` 文本）。

        ⇒ 这正是本项目的中心形状：**退化与正常在只看别的判据时长得一模一样**。
          所以它必须**有一条判据**。修法一条命令：`python -m ldv.recognize`。

    ⚠️ **它不判「用没用盘上那份」** —— 那件事由 `current_recognition` **结构上**保证；
       写成本条会把「按构造恒真」当成判据（就是旧命题的错）。
    """
    rec = r.recognition
    if rec.disk_fingerprint is None and not rec.on_disk:
        rep.add("T2", TITLES["T2"], Tri.UNEXPANDED,
                f"盘上**没有任何认识** ⇒ 没有可比的版本（跳过 ≠ 通过）"
                f"　⚠️ 「持续影响」这一趟**不在**（{rec.source}）—— "
                f"`python -m ldv.recognize` 产生 `views.json`")
        return
    if not rec.disk_readable:
        rep.add("T2", TITLES["T2"], Tri.NO,
                f"盘上**有一份**认识，但它**读不回来**（与当前结构不自洽 / 存档坏了）"
                f"⇒ 陈旧与坏档必须看得见，不许悄悄重算过去（本来会**整套崩掉**）")
        return
    now = spec_fingerprint(spec)
    bad = rec.disk_fingerprint != now
    rep.add("T2", TITLES["T2"], Tri.NO if bad else Tri.YES,
            (f"盘上那份是**陈旧的**：指纹 {rec.disk_fingerprint} ≠ 当前 {now}"
             f"　⇒ 结构动了，那份认识**没跟着变**"
             f"（本趟已按重算处理，结果仍对；但**「持续影响」归零了**）"
             f"　修法：`python -m ldv.recognize`") if bad
            else f"盘上那份的指纹 `{now}` == 当前（来源：{rec.source}）")


# --- `T3` ---------------------------------------------------------------------

def t3_reasons(rep: Report, r: Retrieval, kernel: Any) -> None:
    """`T3` —— `步骤 T4` 的每条理由都指得到**实际结果**。

        每条 = (一个**候选**方向, 一个项)，且那个项**在该方向的覆盖里**。

    跳过：没有任何候选（或候选都没有成员）⇒ 没有可指的东西（`§C3`）。
    """
    if not r.reasons:
        rep.add("T3", TITLES["T3"], Tri.UNEXPANDED,
                "没有任何候选（或候选都没有成员）⇒ 没有可指的东西（跳过 ≠ 通过）")
        return
    bad: list[str] = []
    for did, item in r.reasons:
        if did not in r.candidates:
            bad.append(f"{render_reason((did, item))} —— 方向**不在候选里**")
            continue
        if item not in kernel.members_of(kernel.direction(did)):
            bad.append(f"{render_reason((did, item))} —— 项**不在该方向的覆盖里**")
    rep.add("T3", TITLES["T3"], Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 条指不到实际结果：{bad[:2]}") if bad
            else (f"{len(r.reasons)} 条理由都指得到实际结果"
                  f"（每条 = 一个候选方向 + 它覆盖里的一个项）"))


# --- `T4` ---------------------------------------------------------------------

def t4_profile(rep: Report, r: Retrieval, kernel: Any) -> None:
    """`T4` —— **画像不许说错**（`基线§14.5`）。

    ## ★ 方向是**反的**：判据从「读法」**反推**它声称的事实，再逐成员去查

        `已细分过`    声称「每个成员**都有子**」          ⇒ 验 `∀d: children_of(d)`
        `分不开`      声称「每个成员**只有 1 个项**」      ⇒ 验 `∀d: |members_of(d)| < 2`
        `还没长出来`  声称「每个成员**无子且成员 ≥2**」    ⇒ 验 `∀d: ¬children_of(d) ∧ |members_of(d)| ≥ 2`
        `混合`        声称「上面三条**都不成立**」        ⇒ 验三条都不全真

    ⚠️ 四档的**字面**只有**一处**定义：`retriever.BlockProfile.reading`。
       判据这一侧**只许反推**，不许自己再写一遍档名 ——
       写错一个字（例如写成「还没钻进去」「下界到了」，那两个字符串本仓库里
       **根本不存在**）的症状**不是红，是恒真**：任何块都匹配不上 ⇒ 那条断言
       从此什么都不验（2026-10-13 实测踩到：`run_tests.test_retriever` 里
       两条断言各写了两个不存在的档名，绿了很久）。

    ⚠️ 为什么反着来：`block_profile` 是「**从三态计数算读法**」；
       判据若也那样算，两边**共用同一段条件** ⇒ 同时错时判据照样绿
       （`false-green` 形状 3「共享盲点」）。反推则两边**不同路**：一边是 if/elif 计数，
       一边是**逐成员的存在性检查**。

    ⚠️ **注入的形状**：把「还没长出来」算进「分不开」（**三态混同**）⇒
       读法翻成「分不开」，而判据去查 `members_of` 的**大小**会看到 ≥2 ⇒ **红**。
       这正是 `§K6` 的形态：**把「不知道」说成「已经到底了」**。

    跳过：没有块 / 块全空（`§C3`）。
    """
    ps = [p for p in r.profiles if p.block]
    if not ps:
        rep.add("T4", TITLES["T4"], Tri.UNEXPANDED,
                "没有块（或块全空）⇒ 没有可判的画像（跳过 ≠ 通过）")
        return
    bad: list[str] = []
    for p in ps:
        members = [kernel.direction(d) for d in sorted(p.block)]
        all_kids = all(kernel.children_of(d) for d in members)
        # ★ 「分不开」：成员 < 2 ⇒ `§K2` 情形①（`_expand_inner` 直接 return ()）
        all_one = all(len(kernel.members_of(d)) < 2 for d in members)
        # ★ 「还没长出来」：无子 **且** 成员 ≥ 2（⚠️ 内含「试过也分不开」——
        #    那一档公开信息看不到，`_tried` 是私有的。如实并档，不假装分得开）
        all_stub = all(not kernel.children_of(d) and len(kernel.members_of(d)) >= 2
                       for d in members)
        read = p.reading
        ok = (
            (read == "已细分过" and all_kids)
            or (read == "分不开" and all_one)
            or (read == "还没长出来" and all_stub)
            or (read == "混合" and not (all_kids or all_one or all_stub))
        )
        if not ok:
            bad.append(
                f"{sorted(p.block)[:3]}… 读法「**{read}**」与事实不符："
                f"全有子={all_kids}、全单项={all_one}、全无子且成员≥2={all_stub}"
                f"（画像计数 有子{p.n_with_children}/分不开{p.n_singleton}"
                f"/还没长出来{p.n_unexpanded}，共 {p.n_members}）")
    rep.add("T4", TITLES["T4"], Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 块说错：{bad[:2]}"
             f"（**三态混同**是把「不知道」说成「已经到底了」—— `§K6` 的形态）") if bad
            else (f"{len(ps)} 块的读法都与结构事实相符"
                  f"（四档：「{ps[0].reading}」…）"))


# --- `T5` ---------------------------------------------------------------------

def _order_key(order: tuple[frozenset[str], ...]) -> tuple[tuple[str, ...], ...]:
    """一个顺序的可比形态（块按最小元素标识）—— 判「顺序**真的变了**」用。"""
    return tuple(tuple(sorted(b)) for b in order)


def t5_usage_finetune(rep: Report, r: Retrieval, kernel: Any) -> None:
    """`T5` —— **「按使用细调」真的进了顺序**（`基线§14.7`）。

    ## 它守的是什么

    顺序有**两层**（`§14.7`）：① 先由结构设定 ② 按使用细调。
    ⚠️ **② 与「没做」在只看输出时长得一模一样** —— 没有记录时它自动退化，
       所以「用了」（有记录却没生效）与「没用」必须能被**分开**。
       ⇒ 这条判据查的就是：**倾向有内容时，顺序必须真的跟着变。**

    ## 两个方向都查（否则会被两种假绿骗过）

        `有记录`  ⇒ 「先看」档里**有记录的块必须排在没记录的前面**（局部量、降序）
        `无记录`  ⇒ 顺序必须**与纯结构序一致**（退化正确；不许凭空造顺序）

    跳过：没有任何记录（`tendency` 空）**且**「先看」档 ≤1 块 ⇒ 无可判（`§C3`）。

    ⚠️ **它不重复判 `T1`** —— `T1` 判「顺序怎么变都不许漏」，`T5` 判「顺序**确实变了**」。
       两条各守一侧：**只有两条都绿**，「按使用细调」才算真的、且安全的。
    """
    first = r.first
    if not r.tendency:
        rep.add("T5", TITLES["T5"], Tri.UNEXPANDED,
                "**没有任何使用记录** ⇒ 顺序退化到纯结构序，这一条**没有内容**"
                "（跳过 ≠ 通过；它的红路由注入单独验）")
        return
    if len(first) <= 1:
        rep.add("T5", TITLES["T5"], Tri.UNEXPANDED,
                "「先看」档 ≤1 块 ⇒ 无从排序（跳过 ≠ 通过）")
        return

    def w(b: frozenset[str]) -> float:
        return sum(r.tendency.get(d, 0.0) for d in sorted(b))

    has = [b for b in first if any(d in r.tendency for d in b)]
    no = [b for b in first if not any(d in r.tendency for d in b)]
    bad: list[str] = []
    # ① 同档内部：有记录的必须降序（否则「按使用细调」没生效）
    ws = [w(b) for b in has]
    if ws != sorted(ws, reverse=True):
        bad.append(f"有记录的块**没有按倾向降序**：{['%.3f' % x for x in ws]}")
    # ② 有记录的必须**全部排在**没记录的前面（「不知道」不参与比较，不许被当 0 插在中间）
    if has and no and first.index(has[-1]) > first.index(no[0]):
        bad.append(f"「不知道」的块被插进了有记录的块之间（{len(has)} 有 / {len(no)} 无）")

    rep.add("T5", TITLES["T5"], Tri.NO if bad else Tri.YES,
            (f"按使用细调**没生效**：{bad}") if bad
            else (f"{len(has)} 块按倾向降序排在前面、{len(no)} 块（无记录）"
                  f"保持结构序在后（{len(r.tendency)} 个方向有带权记录）"))


# --- `T6` ---------------------------------------------------------------------

#: 建议 → 它**声称**的结构事实（`基线§14.8.2`）。判据从**建议反推**它，再逐成员去查。
#: ⚠️ **不要**拿它跟 `ADVICE[reading]` 比 —— 那是**同一条推导**，比了等于没比
#:    （`false-green` 形状 3「共享盲点」）。这里走的是**独立的一遍逐成员检查**。
_ADVICE_FACT: dict[str, str] = {
    "往下有现成通道": "全有子",
    "值得往下": "全无子且成员≥2",
    "不必往下（分不开）": "全单项",     # ← 成员 < 2：**结构上永远分不开**（`§K2` 情形①）
    "看具体方向": "无",
}


def t6_advice(rep: Report, r: Retrieval, kernel: Any) -> None:
    """`T6` —— **下一步建议必须与结构事实相符**（`基线§14.8`）。

    ## 它守的是什么（与 `T4` 的分工）

        `T4` 判「**读法**（现状）与事实相符」
        `T6` 判「**建议**（下一步）与事实相符」—— ★ 且它是**块级**的：
             一条建议覆盖**整块**，不是逐方向列 25 条

    ★ **它填的是一处一直空着的依据**：流程 A 的 `R3a`（「需要更清晰的方向吗？」）
      在工作流程里只有决定点、没有依据（那一行的红条件是「无」）。
      ⇒ 建议就是那个依据，所以它**不许说错**：说错就是**让 `R3a` 照着错的判**。

    ## 反推（与 `block_profile` 不同路）

        「值得往下」    声称命中块**全部未展开**（`¬is_expanded`）
        「往下有现成通道」声称命中块**全部有子**
        「不必往下」    声称命中块**全部展开过且无子**（`§K2` 判空）
        「看具体方向」  声称**上面三条都不成立**

    跳过：没有建议（没有候选 / 候选都不在任何块里）⇒ 无可判（`§C3`）。

    ⚠️ **它不重复判 `T1`/`T4`**：`T1` 判「候选集没漏」，`T4` 判「读法没说错」，
       `T6` 判「**下一步建议**没说错」—— 三条各守一侧。
    """
    if not r.advice:
        rep.add("T6", TITLES["T6"], Tri.UNEXPANDED,
                "没有任何建议（没有候选 / 候选不在任何块里）⇒ 没有可判的东西（跳过 ≠ 通过）")
        return
    bad: list[str] = []
    for block, a in r.advice:
        claim = _ADVICE_FACT.get(a)
        if claim is None:
            bad.append(f"建议 {a!r} **不在注册表里**（`retriever.ADVICE`）")
            continue
        # ★ **只对这一条建议的**那块**检查** —— 建议是块级的，跨块比对是**另一回事**
        #   （实测踩到：合并去重后再跨块检查 ⇒ 一条建议被拿去对别的块 ⇒ 假红）。
        all_kids = all(kernel.children_of(kernel.direction(x)) for x in block)
        all_one = all(len(kernel.members_of(kernel.direction(x))) < 2 for x in block)
        all_stub = all(not kernel.children_of(kernel.direction(x))
                       and len(kernel.members_of(kernel.direction(x))) >= 2 for x in block)
        ok = (
            (claim == "全有子" and all_kids)
            or (claim == "全单项" and all_one)
            or (claim == "全无子且成员≥2" and all_stub)
            or (claim == "无" and not (all_kids or all_one or all_stub))
        )
        if not ok:
            bad.append(f"块 {sorted(block)[:3]}… 的建议「**{a}**」（声称 {claim}）"
                       f"与**该块**事实不符：全有子={all_kids}、全单项={all_one}、"
                       f"全无子且成员≥2={all_stub}")
    rep.add("T6", TITLES["T6"], Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 条建议说错：{bad[:2]}"
             f"（★ 它骗的是 `R3a` —— 那一步本来没有依据）") if bad
            else (f"{len(r.advice)} 条块级建议都与**各自那一块**的结构事实相符，"
                  f"覆盖 {r.advice_span}/{len(r.candidates)} 个候选方向"))


# --- `T7` ---------------------------------------------------------------------

def t7_deepening(rep: Report, before: Retrieval, after: Retrieval | None,
                 kernel: Any, plugin: Any) -> None:
    """`T7` —— **深化只增不减**（`基线§14.9`）。`R3a` 接线的**安全契约**。

    ## 它守的是「改结构」这一动作的唯一正确性要求

        深化 = `expand` 若干候选方向 + 重跑一次查询
        展开**只建新的子方向**（`§K4` 只分叉不覆盖）⇒ 旧候选不会消失
        ⇒ `候选集(后) ⊇ 候选集(前)` —— 而这正是 `§K8` **允许**的那一侧
          （假阳 / 多找 ⇒ 只损失性能；**假阴禁止**）

    ⚠️ **它是本层唯一一个「会改变世界」的动作的判据** —— 其余六条都只读。
       所以它必须**独立**验：`deepen` 里那条 `assert` 是**同一条**推导，
       判据不能靠它（`false-green` 形状 3「共享盲点」）⇒ 这里**自己再算一遍**。

    跳过：没有做深化（`deepened` 空）⇒ 没有可判的东西（**跳过 ≠ 通过**）。
    """
    if not after or not after.deepened:
        rep.add("T7", TITLES["T7"], Tri.UNEXPANDED,
                "本趟**没有做深化**（没有任何「值得往下」的块）⇒ 无可判（跳过 ≠ 通过）")
        return
    grew = after.candidates >= before.candidates
    n_before, n_after = len(before.candidates), len(after.candidates)
    lost = sorted(before.candidates - after.candidates)
    # ★ **幂等**：再深化一次**不新增方向、不改候选**
    #   （「试过也分不开」那批会被**重复选中** —— 公开信息看不到 `_tried` ——
    #    但第二次 `expand` 命中 `_tried` 立刻返回 `()` ⇒ 幂等）
    n_dirs_before = len(kernel.all_directions())
    again = deepen(kernel, plugin, after)
    idem = (len(kernel.all_directions()) == n_dirs_before
            and again.candidates == after.candidates)
    bad = (not grew) or bool(lost) or (not idem)
    rep.add("T7", TITLES["T7"], Tri.NO if bad else Tri.YES,
            (f"深化**改变了结论**：候选 {n_before}→{n_after}，丢 {lost[:3]}；"
             f"幂等={idem}（违反 `§K4` 只分叉不覆盖 / `§K8` 假阴禁止）") if bad
            else (f"展开 {len(after.deepened)} 个方向 ⇒ 候选 {n_before}→{n_after}"
                  f"（**只增不减**）；**再深化一次不新增**（幂等 —— "
                  f"「试过也分不开」那批会被重复选中，但 `_tried` 立刻返回）"))


# --- `T8` ---------------------------------------------------------------------

def t8_coarsest(rep: Report, r: Retrieval, spec: ViewSpec) -> None:
    """`T8` —— **交到检索层的那份认识必须最粗**（`q` 逐块等于 `csr(spec)`）。

    ## 为什么要有它（2026-10-10 实测补的缺口）

    一个**更细**的 `q` 是**完全合法**的：它仍稳定、仍细化 `P`、
    仍不改变候选集（`§3` 定理）⇒ **`T1`–`T7` 一条都不会红**。
    但它是**退化**的 —— 块被切小 ⇒ 块级建议与画像跟着变差
    ⇒ 「模糊掌握」被磨掉，而**没有任何判据看得见**。

    ⚠️ **实测**（`outputs/_probe_disk_influence.py`）：盘上那份从 8 块换成 25 块
       ⇒ 候选集 `★同`、**块级建议变**、**画像变**。⇒ 退化是**可观测的**，
       只是此前**没人看**。

    ## 它与 `§A3` 的分工（**不许重复判同一件事**）

        `§A3`  判「**算出来的** `q`（生产路径 `build_views`）是不是最粗」—— 守 `_refine` 的**实现**
        `T8`   判「**交到检索层的**那份认识是不是最粗」—— 守**交接**这一步

    判**对象**不同（一个是当场算的，一个可以来自盘）、红的**原因**不同
    （`§A3` 红 = 有没有更粗的稳定划分；`T8` 红 = 这份不用是最粗的却更细）
    ⇒ 不是重复。★ 而 `T8` 是**唯一**能看见「盘上那份退化」的判据。

    ⚠️ **深化的结果是更细的** —— 这**不**与 `T8` 冲突：`T8` 判的是**取到的那份认识**
       （`步骤 T1` 的产物），`deepen` 之后换的是 `candidates` / `profiles`，**不换 `recognition.q`**。
    """
    title = TITLES["T8"]
    if not spec.universe:
        rep.add("T8", title, Tri.UNEXPANDED,
                "`universe` 为空 ⇒ 「最粗」没有内容（跳过 ≠ 通过）")
        return
    q_now = partition_of(r.recognition.q)
    q_csr = partition_of(coarsest_stable_refinement(spec))
    if q_now == q_csr:
        rep.add("T8", title, Tri.YES,
                f"{len(q_now)} 块，逐块等于 `csr(spec)`（来源：{r.recognition.source}）")
        return
    # 红：更细 ⇒ 粗粒度被磨掉。⚠️ 措辞必须说清「合法但退化」，否则读者会以为它是**错**的
    finer = len(q_now) > len(q_csr)
    rep.add("T8", title, Tri.NO,
            (f"交到检索层的那份认识**比 `csr` 细**（{len(q_now)} 块 vs 最粗 {len(q_csr)} 块）"
             f"⇒ **合法但退化**：候选集不受影响（`§3` 定理），而**块级建议与画像已经变差**"
             f"　修法：`python -m ldv.recognize`（它交付的恒为 `csr`）") if finer
            else (f"交到检索层的那份认识**不是 `csr`**（{len(q_now)} 块 vs 最粗 {len(q_csr)} 块）"
                  f"⇒ 它既不等于最粗，也不比它细 —— 只可能是**不细化 `P`** 或**不稳定**"
                  f"（那两件分别归 `§A3` 与 `§A2`，但**代价已经发生在这里**：检索层拿它用了）"))


# --- 跑一遍再判 ----------------------------------------------------------------

def run_retrieval(kernel: Any, plugin: Any,
                  need: Mapping[str, Mapping[str, object]], spec: ViewSpec,
                  rep: Report, *, recognition: Any = None,
                  tendency: Mapping[str, float] | None = None,
                  disk_fingerprint: Any = _DISK_UNSET,
                  disk_unreadable: bool = False,
                  deepen_rounds: int = 1) -> Retrieval:
    """走一遍流程 T（`T0`–`T4`）+ 按建议**深化**（`R3a` 的接线），再逐条判 `T1`–`T8`。

    ⚠️ **判据读的是同一份 `Retrieval`**（`T1` 读候选集、`T2` 读认识的指纹、`T3` 读解释、
       `T4` 读画像、`T5` 读顺序、`T6` 读建议、`T8` 读交出去的那份 `q`）——
       它们不各自跑一遍，否则「红」会分不清是谁造成的。

    ★ **`T7` 是唯一读「另一份 `Retrieval`」的**（深化前 `before` / 深化后 `after`）——
       因为它判的就是**两版之间的差**（只增不减）。其余（`T1`–`T6` 与 `T8`）
       一律读**深化前**那版，这样「顺序 / 认识 / 画像 / 交接」判的还是**同一份结构**
       上的同一件事。
       ⚠️ `T8` 读的也是深化前那版 —— 深化换的是 `candidates` / `profiles`，
          **不换 `recognition.q`**（`§14.9`），所以两条不冲突。
    """
    r = retrieve(kernel, plugin, need, spec=spec, recognition=recognition,
                 tendency=tendency, disk_fingerprint=disk_fingerprint,
                 disk_unreadable=disk_unreadable)
    t1_equivalence(rep, r, kernel)
    t2_current(rep, r, spec)
    t3_reasons(rep, r, kernel)
    t4_profile(rep, r, kernel)
    t5_usage_finetune(rep, r, kernel)
    t6_advice(rep, r, kernel)
    rep.note(f"认识：{r.recognition.source}；指纹 {r.recognition.fingerprint}")
    rep.note(render_reading(r))
    # ★ `R3a` 的接线（`基线§14.9`）：按建议**深化**，再判 `T7`（只增不减）。
    deep = deepen(kernel, plugin, r) if deepen_rounds > 0 else r
    t7_deepening(rep, r, deep, kernel, plugin)
    t8_coarsest(rep, r, spec)
    rep.note(render_explanation(r))
    if deep.deepened:
        # ⚠️ 这一句只报**实测到的**：展开之后**读法可能一个都没变**
        #    （本语料：展开 7 个 ⇒ 全部判空 ⇒ 那一块仍是「混合」）。
        #    原来这里写死了「变成了已细分过或下界到了」—— 那是**假话**，已删。
        before_reads = {p.reading for p in r.profiles}
        after_reads = {p.reading for p in deep.profiles}
        rep.note(f"★ 深化后：三档读法 {sorted(before_reads)} → {sorted(after_reads)}"
                 + ("（⚠️ **一个都没变** —— 展开的那批**判空了**："
                    "「值得往下」试出来的答案是「**这里没有更多**」，"
                    "而那**也是知识**（`_tried` 记着，代价是下次重试一次 dict 查询））"
                    if before_reads == after_reads else
                    "（变了 —— 展开**确实**长出了东西）"))
    return deep


# ⚠️ **这里原来还有一段死代码**（2026-10-11 删）：`return deep` 之后跟了一个
#    `rep.note("⚠️ **本层当前是「预备」的** …")` 与第二个 `return r` ——
#    2026-10-09 加 `deepen`（`§14.9`）时把 `return deep` 插在前面，旧尾巴就**再也跑不到**了。
#    而那条注释自己写着「**必须**印」⇒ **一条印不出来的警告等于没有**。
#    ⇒ 那句话的**唯一来源**现在是 `retriever.render_reading`（它逐趟印，
#      真跑与跳过两条路都印得到）—— **不要再在别处写第二份**：
#      两处各写一份的下场就是这次这样（一份被孤儿化、没人看得出来）。


def skip_all(rep: Report, why: str) -> None:
    """三条一起跳过 —— 与 `run_checks._skip_views` 同一个理由：理由只写一遍。

    ⚠️ 标题取自 `TITLES`（**与真跑时同一个来源**）。分开写两份的话，
       「跳过时印的标题」与「真跑时印的标题」会各自漂移。
    """
    for code in RETRIEVER_CODES:
        rep.add(code, TITLES[code], Tri.UNEXPANDED, why)


# ═══ 已知答案对照 ═════════════════════════════════════════════════════════════
#
# `T1`–`T3` 的红形态只能**手造**（见模块开头）。下面每条都造一个「该红的输入」，
# 否则「这条判据在判」与「这条判据恒真」长得一模一样。


class _LosingMerge:
    """一个 **`合并` 会丢成员**的键集插件 —— `T1` 夹具的**前提**。

    见模块开头那条证明：**满足 `§I2` 契约的插件上，后看的块里不可能有「是」**
    ⇒ 早停丢不了候选 ⇒ `T1` 永远绿。要造出「后看的块里真有命中」，
    就必须有一个**真的会丢**的合并 —— 而这正是 `基线§3.1` 说的那个情形。

    只改 `merge` 一件事：取**最细的那个成员**的 payload（`rank` 最大），
    而不是分量取交 ⇒ 覆盖收窄 ⇒ 别的成员的命中会被**合并**这一层遮住。
    """

    name = "keyset"

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def merge(self, ds: Sequence[Any]) -> Any:
        if not ds:
            return self._inner.merge([])
        return max(ds, key=lambda d: d.rank).payload

    def hit(self, d: Any, query: Any) -> Tri:
        return self._inner.hit(d, query)

    def penalty(self, d: Any, item: Any) -> float:
        return self._inner.penalty(d, item)


def _synth(with_losing_merge: bool) -> tuple[Any, Any, Any]:
    """手造的小内核 + 插件 + 一个「后看的块里有真命中」的查询。

    返回 `(kernel, plugin, query)`。查的是「有 `k2`、没有 `k1`」——
    项 `c` 恰好满足 ⇒ `ideal = {c}`。
    """
    from ..core.kernel import Kernel, Query
    from ..plugins.keyset import KeysetPlugin

    inner = KeysetPlugin()
    plug: Any = _LosingMerge(inner) if with_losing_merge else inner
    items = {
        "a": {"id": "a", "keys": ["k1"]},
        "b": {"id": "b", "keys": ["k1", "k2"]},
        "c": {"id": "c", "keys": ["k2"]},
        "d": {"id": "d", "keys": ["k3"]},
    }
    k = Kernel(inner, items)          # 内核照常拿真插件（`hit` / `penalty` 都是真的）
    k.build(inner.merge([]))
    for iid in sorted(items):
        k.insert(iid)
    q = Query(ideal=frozenset({"c"}), require=frozenset({"k2"}),
              forbid=frozenset({"k1"}), label="有 k2 无 k1")
    return k, plug, q


def _synth_spec(kernel: Any) -> ViewSpec:
    """手造内核的 `spec`：`P = {U}`（信息量最低那一档），`E` = 树边。"""
    dirs = kernel.all_directions()
    u = tuple(sorted(d.did for d in dirs))
    edges = frozenset((d.parent, d.did) for d in dirs if d.parent)
    return ViewSpec(universe=u, partition=(frozenset(u),), relation=edges)


def _early_stop(r: Retrieval) -> Retrieval:
    """注入：**「先看的块跑完就收工」的可观测形态**。

    早停的后果 = 后看的块**没跑到** ⇒ 那些块里的命中**不在候选集里**。
    而「漏掉的候选」与「本来就没有」**长得一模一样** —— 正是本项目从头到尾在防的形状
    （`基线§3.1` 末）。所以注入**造的就是这个可观测形态**。
    """
    keep = frozenset().union(*r.first) if r.first else frozenset()
    return replace(r, candidates=frozenset(d for d in r.candidates if d in keep))


def known_answer_controls() -> list[str]:
    """`T1`–`T3` 的**合成对照** —— 每条都要「该绿时绿、该红时红」。

    返回失败清单（空 = 全过）。
    """
    fails: list[str] = []

    def judge(fn: Callable[[Report], None]) -> Tri:
        rep = Report(plugin="(检索对照)", expects=RETRIEVER_CODES)
        fn(rep)
        return rep.assertions[0].result

    # ── `T1`：真插件 ⇒ 绿；`_LosingMerge`（**破坏 `§I2`**）⇒ 红 ────────────
    #
    # ★ 2026-10-14 起 `retrieve` 会**跳过「后看」的块**（`run_in_order(skip=...)`），
    #   而跳过的依据是 `§I2` 的推论（合并说否 ⇒ 成员说否）⇒ 对照**天然分两档**：
    #
    #     真插件（满足 `§I2`）         ⇒ 跳过**不丢候选** ⇒ `T1` **绿**
    #     `_LosingMerge`（破坏 `§I2`） ⇒ 跳过**真丢候选** ⇒ `T1` **红**
    #
    #   ⚠️ 原来这一段的「红」是**手造**的（`_early_stop` 把候选按「先看」筛一遍）；
    #      现在**不用手造** —— `_LosingMerge` + 跳过**本身就是**那个输入，
    #      而且它走的是**生产路径**（`retrieve` 自己算 `skip`）。
    #      （`_early_stop` **留着**：`test_injections` 的 `T1` 注入还在用它。）
    kernel, plugin, q = _synth(with_losing_merge=True)
    spec = _synth_spec(kernel)
    need = {"keyset": as_bar(q)}
    full = retrieve(kernel, plugin, need, spec=spec)
    if not full.late:
        # 夹具前提：必须**真有一个「后看」的块**，否则这条对照什么都没验。
        fails.append("§T1 夹具不成立：没有「后看」的块")
    else:
        whole = frozenset(kernel.query(q).yes)      # 全扫（不跳过）—— 判据用的基准
        if full.candidates == whole:
            fails.append("§T1 夹具不成立：`_LosingMerge` 上跳过**没丢候选**"
                         "（⇒「合并会丢」那段破坏没起作用）")
        elif judge(lambda rep: t1_equivalence(rep, full, kernel)) is not Tri.NO:
            fails.append("§T1 `_LosingMerge` 上跳过真命中却没被判红")
    # 真插件 ⇒ **绿**（`§I2` 成立 ⇒ 跳过不丢候选）
    k2, p2, q2 = _synth(with_losing_merge=False)
    r2 = retrieve(k2, p2, {"keyset": as_bar(q2)}, spec=_synth_spec(k2))
    if judge(lambda rep: t1_equivalence(rep, r2, k2)) is not Tri.YES:
        fails.append("§T1 真插件（满足 `§I2`）上被判红 —— 跳过**不该**丢候选")

    # `T1` 的跳过：需求为空 ⇒ **跳过**，不是过、也不是红
    empty = replace(full, query=q.__class__(ideal=frozenset(), label="空需求"))
    if judge(lambda rep: t1_equivalence(rep, empty, kernel)) is not Tri.UNEXPANDED:
        fails.append("§T1 空需求没被判「跳过」（跳过 ≠ 通过）")

    # ── `T2`：**四个状态各一例**（2026-10-10 命题改过，见 `t2_current` 的 docstring）──
    #    盘上那份**当前** ⇒ 绿｜**陈旧** ⇒ 红｜**读不回来** ⇒ 红｜盘上**没有** ⇒ 跳过
    from .abstraction import ViewSet

    other = ViewSpec(universe=spec.universe,
                     partition=tuple(frozenset({d}) for d in spec.universe),
                     relation=spec.relation)
    current_vs = ViewSet(spec=spec, q=coarsest_stable_refinement(spec), views=())
    stale_vs = ViewSet(spec=other, q=coarsest_stable_refinement(other), views=())
    # ① 当前 ⇒ 绿（且**用到的那份**也必须是当前的 —— 那是 `current_recognition` 的结构保证）
    ok_r = retrieve(kernel, plugin, need, spec=spec, recognition=current_vs)
    if ok_r.recognition.fingerprint != spec_fingerprint(spec):
        fails.append("§T2 真实现没用当前那份（指纹相符时应当**用盘上那份**）")
    if ok_r.recognition.disk_fingerprint != spec_fingerprint(spec):
        fails.append("§T2 没记下「盘上那份自己的指纹」⇒ 判据没有可判的东西")
    if judge(lambda rep: t2_current(rep, ok_r, spec)) is not Tri.YES:
        fails.append("§T2 盘上那份**当前**被判红")
    # ② 陈旧 ⇒ **红**（★ 这是生产可达的红：结构动了而没跑 `python -m ldv.recognize`）
    st_r = retrieve(kernel, plugin, need, spec=spec, recognition=stale_vs)
    if st_r.recognition.fingerprint != spec_fingerprint(spec):
        fails.append("§T2 陈旧时没按重算走（结果仍要对）")
    if judge(lambda rep: t2_current(rep, st_r, spec)) is not Tri.NO:
        fails.append("§T2 盘上那份**陈旧**没被抓住（用户裁定：认识必须要维护）")
    # ③ 盘上那份**读不回来** ⇒ 红（不是跳过、更不是崩）
    bad_r = retrieve(kernel, plugin, need, spec=spec, recognition=None,
                     disk_unreadable=True)
    if judge(lambda rep: t2_current(rep, bad_r, spec)) is not Tri.NO:
        fails.append("§T2 盘上那份**读不回来**没被抓住")
    # ④ 盘上什么都没有 ⇒ **跳过**（跳过 ≠ 通过）
    none_r = retrieve(kernel, plugin, need, spec=spec, recognition=None)
    if judge(lambda rep: t2_current(rep, none_r, spec)) is not Tri.UNEXPANDED:
        fails.append("§T2 盘上没有任何认识时没被判「跳过」（跳过 ≠ 通过）")

    # ── `T8`：**三态** —— 最粗 ⇒ 过；更细 ⇒ 红；比 `P` 还粗 ⇒ 红 ────────
    #    ★ 第二条是**生产可达的退化**（盘上那份更细）；第三条是「它既不等于 `csr`
    #      也不比它细」那一档（只可能是不细化 `P` 或不稳定）。
    csr_q = coarsest_stable_refinement(spec)
    r_csr = retrieve(kernel, plugin, need, spec=spec,
                     recognition=ViewSet(spec=spec, q=csr_q, views=()))
    if judge(lambda rep: t8_coarsest(rep, r_csr, spec)) is not Tri.YES:
        fails.append("§T8 最粗的那份被判红")
    discrete = tuple(frozenset({d}) for d in sorted(spec.universe))
    r_fine = replace(r_csr, recognition=replace(r_csr.recognition, q=discrete))
    if judge(lambda rep: t8_coarsest(rep, r_fine, spec)) is not Tri.NO:
        fails.append("§T8 **更细**的那份没被抓住（那是「合法但退化」，只有本判据看得见）")
    r_coarse = replace(r_csr, recognition=replace(
        r_csr.recognition, q=(frozenset(spec.universe),)))
    if judge(lambda rep: t8_coarsest(rep, r_coarse, spec)) is not Tri.NO:
        fails.append("§T8 比 `P` 还粗的那份没被抓住")
    # 空 `universe` ⇒ **跳过**（「最粗」没有内容；跳过 ≠ 通过）
    if judge(lambda rep: t8_coarsest(rep, r_csr, ViewSpec(
            universe=(), partition=(), relation=frozenset()))) is not Tri.UNEXPANDED:
        fails.append("§T8 空 `universe` 没被判「跳过」")

    # ── `T3`：真实现的解释 ⇒ 绿；写死成一句固定的 ⇒ 红 ───────────────────
    if not full.reasons:
        fails.append("§T3 夹具不成立：真实现一条理由都没有")
    else:
        if judge(lambda rep: t3_reasons(rep, full, kernel)) is not Tri.YES:
            fails.append("§T3 真实现的解释被判红")
        fixed = replace(full, reasons=(("__写死__", "__写死__"),))
        if judge(lambda rep: t3_reasons(rep, fixed, kernel)) is not Tri.NO:
            fails.append("§T3「解释写死成一句固定的」没被抓住")

    return fails
