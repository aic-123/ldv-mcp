"""B7–B9 / B13 —— 结构类检查（§K1 / §K3 / §K4 / §K5）。

    B7  见证完备 §K3  —— 不存在没有见证的方向（根除外，根是外生的）
    B8  失效局部 §K3  —— 插入 x 后的变动集合 ⊆ `Cone(x)`
    B9  不覆盖   §K4  —— 历史记录只增不改
    B13 良基树   §K5  —— 方向图无环、无自环、**每方向恰好一个父**

---

## B8 怎么查才有意义

「失效范围 ⊆ 支撑锥」如果只查**最终状态**，是查不出东西的 ——
最终状态本来就自洽。所以这里**逐项插入、每次插完立刻查一遍**：

    快照(方向集合, 归属) → 插入 x → 比对 → 变动必须落在 x 的插入路径上

**这个「逐步 + 前后比对」的形状是可以搬的**：
凡是「局部性」类的断言（改一点不许动全局），都得这么查；
只查终态等于什么都没查（dce 的 `checks/mvp.py` 那边吃过这个亏）。

⚠️ **但「逐步」不等于「每步都从头重建」** —— 那在语料尺度上是平方的。
   一个内核走到底、快照逐次前移，逐步同态而插入次数从 `n²/2` 降到 `n`。
   实测对照写在 `b8_invalidation_local` 的 docstring 里。
"""

from __future__ import annotations

from typing import Any

from ..core.direction import ORIGIN_EXOGENOUS
from ..core.tri import Tri
from ._framework import Report


# --- B7 ---------------------------------------------------------------------

def b7_witness_complete(kernel: Any, rep: Report) -> None:
    """不存在**没有见证**的方向。

    根是唯一例外 —— 它的来源是**外生声明**（§8.2：最外层意图必须人声明），
    所以它不需要见证。这条例外是设计的一部分，不是漏掉。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        if d.origin == ORIGIN_EXOGENOUS:
            continue
        checked += 1
        if not d.witness:
            bad.append(f"{d.did}（rank={d.rank}, origin={d.origin}）没有见证")
    if checked == 0:
        rep.add("B7", "见证完备", Tri.UNEXPANDED, "除了根以外没有任何方向")
        return
    rep.add("B7", "见证完备：不存在没有见证的方向",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 个无见证方向：{bad[:3]}" if bad else f"{checked} 个非根方向全部有见证")


# --- B8 ---------------------------------------------------------------------

def b8_invalidation_local(make_kernel: Any, item_ids: list[str], rep: Report) -> None:
    """**逐项插入**，每次都查「变动 ⊆ 插入路径」。

    ## ★ 一个内核走到底 —— 不是「省事」，是原来那版**在语料尺度上是平方的**

    原来每检查第 k 项就 `make_kernel()` 重建、把前 k−1 项**重插一遍**：
    总插入次数 `n(n−1)/2`。36 项的随仓库语料上看不出来（0.05 s），
    换一份真语料就是灾难：

        语料               n     现写
        openalex-small    281   21 分钟没跑完（被杀）
        openalex          3907   61 分钟没跑完（被杀）

    一个内核逐项插入，总插入次数是 `n`。**这不改变判据的内容**：
    `make_kernel()` 只建根，之后全是 `insert`，中间没有任何别的写操作
    ⇒ 「第 k 次插入之前的状态」**只由插入序列决定**，
    增量走到底与每次重建**逐步同态**。

    这一点是**实测**出来的，不是推的 —— 两个实现各自吐出「每一步的 `changed` 集合」
    的 trace，逐字比对（`outputs/_measure_b8.py`，**12/12 逐字相同**）：

        语料/方向                          现写       增量      逐步 trace
        b160 keyset                       0.62 s    0.01 s   相同
        b160 reach                        6.36 s    0.08 s   相同
        b160 sequence                    17.38 s    0.25 s   相同
        b160 keyset · 注入 `_LeakyKernel`  0.78 s    0.01 s   相同（都是 159 处越界）

    ⇒ 换增量**不动判据**，只把插入次数从 `n²/2` 降到 `n`。
    """
    bad: list[str] = []
    checked = 0
    kernel = make_kernel()
    #: 上一步的归属快照。**只留一份** —— 全留会在 3907 项上吃掉几个 G
    #: （reach 有 7813 个方向，每份快照 7813 个 frozenset）。
    before_mem = {d.did: kernel.members_of(d) for d in kernel.all_directions()}

    for target in item_ids:
        path = kernel.insert(target)
        path_set = set(path)

        # `all_directions()` 排过序，**一次就够** —— 原来调了三次（`after_dirs`、
        # 再遍历一遍取 `members_of`）。reach 在 3907 项上有 7813 个方向，
        # 每一次排序都不便宜。
        after_mem = {d.did: kernel.members_of(d) for d in kernel.all_directions()}
        # 新方向在 `before_mem` 里查不到 ⇒ `get` 给 `None` ⇒ 与 frozenset 必不相等 ⇒ 计为变动。
        changed = {did for did, mem in after_mem.items() if before_mem.get(did) != mem}

        checked += 1
        for did in sorted(changed):
            d = kernel.direction(did)
            # 允许：在路径上；或**父在路径上**（展开是沿路径发生的）
            if did in path_set or (d.parent is not None and d.parent in path_set):
                continue
            bad.append(f"插 {target} 动到了路径外的 {did}（path={path[:3]}…）")
        before_mem = after_mem

    if checked == 0:
        rep.add("B8", "失效局部", Tri.UNEXPANDED, "没有项可插")
        return
    rep.add("B8", "失效局部：变动 ⊆ Cone(x)",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处越界：{bad[:2]}" if bad else f"{checked} 次插入的变动全部落在插入路径上")


# --- B9 ---------------------------------------------------------------------

def b9_ledger_append_only(kernel: Any, rep: Report) -> None:
    """历史只增不改。

    除了查**当前账本干净**，还要查**探测器本身有效** ——
    故意改写一条再问它抓不抓得到。**探测不出问题的探测器等于没探测**，
    这正是「空转与通过长得一模一样」的形态。
    """
    bad = list(kernel.ledger.verify_append_only())
    # 探测器有效性：把第 0 条换成一个改动过的，必须被抓到
    probe_ok = True
    if len(kernel.ledger) == 0:
        rep.add("B9", "不覆盖", Tri.UNEXPANDED, "账本是空的，无从查起")
        return
    from ..core.direction import Event

    saved = kernel.ledger._events[0]                       # noqa: SLF001 - 故意的
    kernel.ledger._events[0] = Event(                      # noqa: SLF001
        seq=saved.seq, kind=saved.kind, did=saved.did,
        detail={**saved.detail, "__probe__": True},
    )
    if not kernel.ledger.verify_append_only():
        probe_ok = False
    kernel.ledger._events[0] = saved                       # noqa: SLF001 - 复原
    if not probe_ok:
        bad.append("探测器无效：改写了一条事件却查不出来")
    rep.add("B9", "不覆盖：历史只增不改",
            Tri.NO if bad else Tri.YES,
            "；".join(bad[:2]) if bad else f"{len(kernel.ledger)} 条事件干净，且探测器已验证有效")


# --- B13 --------------------------------------------------------------------

def b13_rank_well_founded(kernel: Any, rep: Report) -> None:
    """§K5 良基秩 **+ 树性**：`rank = 1 + rank(父)`，无环、无自环、**每方向恰好一个父**。

    ## 为什么「树性」并进这一条，而不是另开一条

    两者守的是**同一个东西的形状** —— 方向图。分开写会出现一条「无环但有多父」
    的中间态，而那个中间态**没有任何设计含义**。

    ## ★ 为什么树性非查不可（这是它进来的理由）

    `§10.2 D`（持久化）的路线**押在「入度是常数」上**：

        树（每方向一个父）  ⇒ node-copying ⇒ 访问旧版本 **O(1)**
        DAG（一个方向多父） ⇒ 只能 fat node ⇒ 访问旧版本 **O(log m)**

    而这条前提**原来没有任何检查守着**：`§K5` 的原文写的是
    `rank = 1 + max(rank(父))` —— 那个 `max` 是**为 DAG 写的**；
    本函数原来只查「无环 / 无自环 / 见证更粗」，**对 DAG 照样放行**。
    ⇒ 把入度改成 2 之后 `B13` 仍报「是」（`outputs/_probe_b13_gap.py` 实测）。

    这正是本仓库一直在防的形状：**前提成立与否，判据上长得一模一样**。

    ## ⚠️ 「入度」在实现里有**两条边**，必须说清查的是哪条

        `_children[父] = (子, …)`   语义边：**父 → 子**
        `Direction.parent = 父`     存储边：**子 → 父**（反指针）
        `Direction.witness = (父,)` §6.1：见证指向**更粗**的层

    按**父→子**算，每个子方向恰好 1 个前驱（常数）；
    按**子→父**算，「前驱」= **扇出**，而 §I4 是 k 叉 ⇒ **不常数**
    （实测 36 项语料上 `sequence` 扇出到 **7**）。

    Driscoll 的 `p` 是**指针前驱** —— 所以持久化层**必须**把 `_children`
    当权威边、把 `parent`/`witness` 当**派生索引**。本函数查的就是
    「两条边互为转置」，即**派生索引没有跑偏**。

    ## 四组断言

        ① 无自环             `d ∉ d.witness`
        ② 见证更粗且存在      `rank(见证) < rank(d)`
        ③ 父链终止（无环）    逐级上溯必须落到根
        ④ **树性**            非根方向恰好一个父；根没有父；
                              `parent` 与 `_children` **互为转置**；
                              `witness == (parent,)`（§6.1）

    扇出（node-copying 的成本侧）**报在 detail 里** —— 它是读数，不进退出码。
    """
    bad: list[str] = []
    dirs = kernel.all_directions()
    by_id = {d.did: d for d in dirs}
    for d in dirs:
        if d.did in d.witness:
            bad.append(f"{d.did} 见证含自己（自环）")
        for w in d.witness:
            if w not in by_id:
                bad.append(f"{d.did} 的见证 {w} 不存在")
                continue
            if by_id[w].rank >= d.rank:
                bad.append(f"{d.did}(rank={d.rank}) 的见证 {w}(rank={by_id[w].rank}) 不比它粗")
    # 父链必须终止（无环）
    for d in dirs:
        seen = {d.did}
        cur = d
        steps = 0
        while cur.parent is not None and steps <= len(dirs) + 1:
            if cur.parent in seen:
                bad.append(f"{d.did} 的父链成环于 {cur.parent}")
                break
            seen.add(cur.parent)
            nxt = by_id.get(cur.parent)
            if nxt is None:
                bad.append(f"{d.did} 的父 {cur.parent} 不存在")
                break
            cur = nxt
            steps += 1
        else:
            if cur.parent is not None:
                bad.append(f"{d.did} 的父链超长（疑似成环）")

    # --- ④ 树性：语义入度（`_children` 的反图）必须恰好是「非根 1 / 根 0」 -----
    # ⚠️ 从**建出来的结构**现算，不看 `parent` 字段自己怎么说 ——
    #    否则「两条边打架」这种情形会各自自洽、合起来矛盾。
    sem: dict[str, int] = {d.did: 0 for d in dirs}
    fanout: dict[str, int] = {}
    for p in dirs:
        kids = kernel.children_of(p)
        fanout[p.did] = len(kids)
        for c in kids:
            sem[c.did] = sem.get(c.did, 0) + 1
    for d in dirs:
        if d.origin == ORIGIN_EXOGENOUS:
            if sem[d.did] != 0:
                bad.append(f"外生方向 {d.did} 有 {sem[d.did]} 个父（它是入口，不许有父）")
            if d.parent is not None:
                bad.append(f"外生方向 {d.did} 的 parent 字段非空（{d.parent}）")
            continue
        if sem[d.did] != 1:
            bad.append(f"{d.did} 有 {sem[d.did]} 个父（树性要求恰好 1）"
                       + ("—— 这是 DAG，`§10.2 D` 的 node-copying 路线不再成立"
                          if sem[d.did] >= 2 else "—— 孤儿方向"))
        # 两条边必须互为转置
        if d.parent is None:
            bad.append(f"{d.did} 非外生却没有 parent 字段")
        else:
            par = by_id.get(d.parent)
            if par is None:
                bad.append(f"{d.did} 的 parent={d.parent} 不在方向表里")
            elif d.did not in [c.did for c in kernel.children_of(par)]:
                bad.append(f"{d.did}.parent={d.parent}，但它不在那个父的子列表里（两条边打架）")
        if d.witness != (d.parent,):
            bad.append(f"{d.did}.witness={d.witness} 与 parent={d.parent} 不符"
                       "（§6.1：子.witness = (父,)）")

    mx_fan = max(fanout.values()) if fanout else 0
    rep.add("B13", "良基树（§K5）：方向图无环、无自环、每方向恰好一个父",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处违例：{bad[:2]}" if bad
            else f"{len(dirs)} 个方向全部良基；扇出 max {mx_fan}"
                 f"（node-copying 的成本侧读数，不进退出码）")


# --- B15 --------------------------------------------------------------------

def _shape(kernel: Any) -> frozenset:
    """结构指纹 —— **与方向 id 编号无关**，只看形状。

    每一项是 `(秩, 本方向成员集合, 子方向成员集合们)`。
    用成员集合而不是 id，是为了让指纹在「编号方式不同但结构相同」时相等。
    """
    out = []
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        kids = frozenset(kernel.members_of(c) for c in kernel.children_of(d))
        out.append((d.rank, mem, kids))
    return frozenset(out)


def b15_reproducible(make_kernel: Any, item_ids: list[str], rep: Report) -> None:
    """**换插入顺序，结构必须同构**（§8.2 可复现性）。

    §8.2 说的失败模式是「同数据、不同历史 ⇒ 不同结构」。
    但那句话只有在「结构不依赖插入顺序」时才成立 —— 而这一点**没有任何检查在守**。

    ## 一、先要一道**非退化前提**，否则这条检查会空转

    「同构」是**关于结构**的断言。若结构根本没建起来（根就是叶），
    三种顺序当然「同构」—— 同构于「什么都没有」。
    实测：把 `build()` 的预装去掉，根第一次展开只看到 1 项 ⇒ 根变叶 ⇒
    **整棵树退化成一个方向**，而 `B15` 照样报「过（3 种插入顺序得到同一结构）」。

    ⇒ 这就是「空转与通过长得一模一样」。所以指纹少于一层分裂时，报 **未展开**，不报「过」。

    ## 二、顺序无关到底靠什么守（实测出来的，不是推出来的）

    真正守住它的**不是**「建根时把全部项装进去」。是：

        `expand(d)` 劈的是 `d` 的**全部成员**，而 `d` 的成员由父的劈法决定
        ⇒ 「成员集合 = 全集的一个确定函数」这条性质**逐层归纳**地传下去
        ⇒ 任一方向被展开时看到的分组都一样，与「谁先来」无关

    「建根时预装」只是这条归纳的**基例**。三种破坏方式实测对照：

        改法                                   结果                      B15
        ─────────────────────────────────────────────────────────────────
        去掉 `build()` 的预装                  根变叶，退化成一个方向      仍是「过」（空转）
        `expand()` 劈之前不给成员排序           同集合迭代序一致，看不出差别 仍是「过」
        方向攒够容量才劈（B-tree 上溢分裂）      **真的顺序相关**（13/15/13） **红**

    ⇒ 第三种才是这条检查真正在守的东西，注入就用它（`tests/test_injections.py`）。
    """
    orders = {
        "正序": sorted(item_ids),
        "逆序": sorted(item_ids, reverse=True),
        "按长度": sorted(item_ids, key=lambda s: (len(s), s)),
    }
    shapes: dict[str, frozenset] = {}
    for label, order in orders.items():
        k = make_kernel()
        for i in order:
            k.insert(i)
        shapes[label] = _shape(k)

    ref = shapes["正序"]
    if len(ref) < 3:
        rep.add("B15", "结构可复现：换插入顺序 ⇒ 树同构", Tri.UNEXPANDED,
                f"结构退化：指纹只有 {len(ref)} 个方向（根都没分过层）—— "
                f"「同构」在这里没有内容，**不算通过**")
        return

    uniq = set(shapes.values())
    if len(uniq) == 1:
        rep.add("B15", "结构可复现：换插入顺序 ⇒ 树同构",
                Tri.YES,
                f"{len(orders)} 种插入顺序得到同一结构（{len(ref)} 个方向的指纹）")
        return

    sizes = {lab: len(s) for lab, s in shapes.items()}
    rep.add("B15", "结构可复现：换插入顺序 ⇒ 树同构",
            Tri.NO,
            f"{len(uniq)} 种不同结构：各顺序的方向数 {sizes} —— "
            f"同数据不同历史 ⇒ 不同结构（§8.2 违规）")
