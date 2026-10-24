"""覆盖类判据 —— `B16` / `B18` / `B19`，与它们的三条度量。

    B16  过滤器健全   §1 硬要求表**第三行**   `members(d) ⊆ 覆盖(d)`
    B18  覆盖不漏     §1 表**第四行**（⬜ 2026-10-07 **已降级**为「度量 + baseline 守卫」）
                       `覆盖(父) ⊆ ∪覆盖(子)`
                       ⚠️ 它与「`滞留 == 0`」是**同一个条件**（实测漏项就是滞留项）
    B19  变细守卫     §10.2 §6                「反复声称能分」却连续 N 次**没变细**
                       ⚠️ 谓词是「没有任何一个子的覆盖严格小于父」，**不是**「细化量 ≤ 0」——
                          后者把「切得不匀」也算进来了（2026-10-07 改，见 `progress_profile`）
                       ⚠️ 名字 2026-10-07 从「变细守卫」改成「变细守卫」：度量叫 `细化量`（`max子`），
                          判据叫 `变细守卫`（`min子`）—— 同名会让人把判据读成那条度量

三条共用**同一个外生 oracle**（`_fixtures.coverage_of`）—— 不调插件、不调内核。

---

## 为什么这一族必须外生

按 `§I3 代价` 判归属（`代价 = 0`）是个**陷阱**：那是**把被检查对象的运算重放一遍**。
McConnell / Mehlhorn / Näher / Schweitzer 2011 §5.5 把这种写法当成**两个被排除的极端**之一：

> We could take w as the record of the computation of P on x … **However, it does not
> satisfy the simplicity requirement, since a proof of the witness property is tantamount
> to proving the correctness of P.**

而且这条判据对 `reach` **在原理上就过强**，不只是实现问题：`代价 = 0` 只对**锚点本身**
成立，而 `覆盖 = reachable(payload)` 是「能走到锚点的**所有**项」。GiST 1995 明说
`Consistent` **允许不精确**（"an accurate test for satisfiability is not required here"）
⇒ 拿一个**精确性**代理去判一个**只要求不许假阴**的量，是判据选错了。

⇒ 所以这一族的判据一律改成**独立重算**：`_fixtures.coverage_of` 按方向的定义算覆盖，
与 `equiv_classes`（`B17` 的 oracle）并列放。**不新增接口方法**，也不用重验 §I1–§I7。

---

## 三条的分工（不要合并）

    度量 · 健全性        一次建完 / 维护两条路径的 `members ⊄ 覆盖` 计数
    度量 · 覆盖不漏      两条路径的 `覆盖(父) − ∪覆盖(子)` 计数
    度量 · 细化量        每次展开的 `|覆盖(父)| − **max**|覆盖(子)|`（**切得多不匀**），
                         与「切得不匀」的次数 —— **只报不判**

    B16                  批建路径上 `members ⊆ 覆盖` —— **判据**
    B18                  两条路径上 `覆盖不漏` 的 **baseline 守卫** —— **判据**
    B19  `变细守卫`        连续 N 次「**没变细**」（没有任何一个子的覆盖严格小于父）—— **判据**

⚠️ **度量与判据用的是两个不同的聚合，这一点是 2026-10-07 改出来的**：

    度量 `细化量 = 父 − **max**子`   报「切得多**不匀**」—— 它是**趋势**
    判据 `B19`  `父 − **min**子 ≤ 0`  守「有没有真**变细**」—— 它是**成分**

    用 `max` 判 ⇒ 「切得不匀」被读成「没切」（实测：全量那条 10 跳链
                  **一跳「没变细」都没有**，其中一步切掉了 1035 项）
    ⇒ 「度量报趋势，判据守成分」在这里是**两个不同的公式**，不是一个公式的两种读法。
      细节与实测见 `progress_profile` 的 docstring。

⚠️ 三条判据的**基线**不同，这一点必须说清：

    B16  批建路径基线**绿**（三个方向都 0）⇒ 可以直接是判据
         维护路径基线**红**（reach 128/328、sequence 36/176）⇒ 只能是度量
         ⇒ 所以 `B16` **只跑批建路径**，维护路径由度量报出（见 `soundness_profile`）
    B18  批建路径基线**绿**（0 漏）⇒ 判据
         维护路径基线**红**（sequence 16 漏 / 2 对）⇒ baseline 冻结 + 对**新增**红
    B19  基线绿，且**必须能红**（注入验证里用一个「子 payload ⊇ 父 payload」的插件证明）
         ⚠️ 而「真语料上不会红」这句**2026-10-07 被实测推翻了一半** —— 见 `b19_progress_guard`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ._framework import Report, result_of as _result
from ..core.tri import Tri

#: 变细守卫的阈值 —— 照 SP-GiST 的前例（`longValuesOK`）：
#: "the SP-GiST core will raise an error if the leaf datum does not become any
#: smaller within **ten** cycles of choose method calls."
PROGRESS_N = 10

#: baseline 文件放**代码旁边**，不是 `outputs/` —— 它要跟着代码走、进版本库，
#: 换了语料或改了插件就该一起改。ESLint 把 `eslint-suppressions.json` 也这么放。
BASELINE_PATH = Path(__file__).resolve().parent / "cover_leak_baseline.json"


# --- 度量：健全性（两条路径） -------------------------------------------------

def soundness_profile(kernel: Any, cover: Callable[[Any], frozenset[str]]) -> dict[str, Any]:
    """`members(d) ⊆ 覆盖(d)` —— §1 硬要求表**第三行**，按方向统计。

    覆盖由**外生 oracle** 算（`_fixtures.coverage_of`），不调插件。

    ## 为什么它必须单独量，而且必须**两条路径都量**

    `B16` 查的就是这一条，但它只在**一次建完**的内核上跑。而这一条在两条路径上
    **不是同一件事**：

        一次建完     每个方向的成员集在**第一次展开时就是完整的**
                     ⇒ 子方向的 payload 从这批成员算出来，一定盖得住它们 ⇒ 天然成立
        维护路径     成员是**后来才长起来的**，而 payload 在劈开时就**冻结**了
                     ⇒ 子方向收不住「它没见过的项」 ⇒ 破了也没人查

    ⇒ **一次建完全绿、维护路径全红** —— 这就是「空转与通过长得一模一样」在
      **流程**上的形态：检查存在、是绿的，但它从没跑在出问题的那条路上。

    ## 读数（36 项语料，先建 6 维护 30）

        方向        `代价 = 0` 判（旧）      **外生覆盖**判（本模块）
        ─────────────────────────────────────────────────────────
        keyset            0 / 232                  0 / 232
        reach          **246 / 328**           **128 / 328**（最差方向 = 根 D0）
        sequence           36 / 176                 36 / 176

    ⇒ `reach` 那一行少了 118 —— 全部是**旧判据自己造出来的**：
      `代价 = 0` 只对锚点成立，于是「能走到锚点的项」被记成「越界」。
      **这不是修好了 118 个缺陷，是换掉了那把尺子。**

    ⚠️ 这是**硬要求**（§K8 禁假阴的结构来源），但它以**度量**形式报出而不是进退出码，
       理由只有一条：B / C 的修法要先落地（§10.2），而一条基线就红的判据
       过不了注入验证（注入验证要求基线绿）。
    """
    bad: dict[str, tuple[int, int]] = {}
    members = 0
    outside = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        members += len(mem)
        try:
            cov = cover(d.payload)
        except Exception:  # noqa: BLE001 - payload 由插件产出，形状可能不合
            bad[d.did] = (len(mem), len(mem))
            outside += len(mem)
            continue
        out = len([i for i in mem if i not in cov])
        if out:
            bad[d.did] = (out, len(mem))
            outside += out
    return {
        "方向数": len(kernel.all_directions()),
        "成员数": members,
        "越界成员数": outside,
        "越界方向": bad,
        "最大越界方向": max(bad, key=lambda k: bad[k][0]) if bad else "—",
    }


def render_soundness(batch: dict[str, Any], inc: dict[str, Any],
                     which: str = "") -> str:
    """把两条路径的健全性读数渲染成**一行**。"""
    head = f"健全性（{which}）" if which else "健全性"
    b, i = batch["越界成员数"], inc["越界成员数"]
    if not b and not i:
        return (f"{head}：一次建完 {b}/{batch['成员数']}、维护 {i}/{inc['成员数']} "
                f"个成员落在覆盖外（两条路径都成立）")
    return (f"★ {head}**被破坏**（§1 硬要求表第三行）："
            f"一次建完 {b}/{batch['成员数']}、**维护 {i}/{inc['成员数']}** 个成员落在覆盖外"
            f"（最差方向 {inc['最大越界方向']}）"
            f"—— §K8 的结构来源，不是无害趋势；修法见设计文档 §10.2")


# --- B16 --------------------------------------------------------------------

def b16_members_covered(kernel: Any, cover: Callable[[Any], frozenset[str]],
                        rep: Report, path: str = "批建") -> None:
    """**成员必须落在自己方向的覆盖里** —— §K8 的结构版本，**与查询集无关**。

        members(d)  ⊆  覆盖(d)

    为什么这条要紧：`命中` 说「否」是在**证明**「这个方向里没有命中」，
    而它手里的依据只有**方向自己**（payload）。若某个成员根本不在方向的覆盖里，
    那个证明就**管不到它** —— 它可能命中，而 `命中` 已经说了「否」。
    ⇒ 这就是假阴的**结构来源**。

    ## 与 `B1` 的分工（实测过，不是推理）

        B1   行为式：拿**查询集**的 ground truth 比 —— 只查得到被查询覆盖到的方向
        B16  结构式：拿**覆盖**比 —— 不看查询集，一次全查

    实测：同一个「插入时选**代价最大**的子方向」的坏内核（健全性破坏 102 个成员），

        B1  在 40 条查询下判「否」  ← 查到了
        B1  在  3 条查询下判「是」  ← **绿了**
        B16                        ← 无论查询集怎么变，都是「否」

    ⇒ 查询集一换，`B1` 就可能瞎；`B16` 不会。**两个都留**：
      `B1` 管「插件**说**得准不准」，`B16` 管「内核**分**得对不对」。

    ## ★ **两条路径各判一次**，`path` 参数就是用来把这件事印出来的

    这条判据**必须在两条路径上各跑一次** —— 因为它们不是同一件事：

        一次建完     每个方向的成员集在**第一次展开时就是完整的**
                     ⇒ 子方向的 payload 从这批成员算出来，一定盖得住它们 ⇒ 天然成立
        维护路径     成员是**后来才长起来的**，而 payload 在劈开时就**冻结**了
                     ⇒ 子方向收不住「它没见过的项」

    ⚠️ **判据落在哪条路径上，由那条路径的基线决定**：
       基线绿 ⇒ 可以当判据，并单独做注入验证；
       基线红 ⇒ 只能当**度量**，或走 baseline 守卫。
       两条路现在都绿 ⇒ 两条路都跑，**每一行单独判、单独注入验证**。
       把 `path` 印进标题，是因为「只跑一条路」与「两条路都过」在汇总里长得一模一样 ——
       **范围一旦放宽，也必须在输出里看得见**。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        checked += len(mem)
        try:
            cov = cover(d.payload)
        except Exception as exc:  # noqa: BLE001 - payload 由插件产出
            bad.append(f"{d.did} 的覆盖算不出来：{type(exc).__name__}: {exc}")
            continue
        outside = sorted(i for i in mem if i not in cov)
        if outside:
            bad.append(f"{d.did} 有 {len(outside)}/{len(mem)} 个成员落在覆盖外"
                       f"（{outside[:3]}）")
    if checked == 0:
        rep.add("B16", f"过滤器健全（{path}路径）", Tri.UNEXPANDED, "没有任何方向有成员")
        return
    rep.add("B16", f"过滤器健全（{path}路径）：成员 ⊆ 覆盖（外生覆盖，与查询集无关）",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 个方向有成员越界：{bad[:2]}" if bad
            else f"{checked} 个成员全部落在自己方向的覆盖里（覆盖由外生 oracle 算）")


# --- 度量：覆盖不漏（两条路径） -----------------------------------------------

def cover_leak_profile(kernel: Any,
                       cover: Callable[[Any], frozenset[str]]) -> dict[str, Any]:
    """`覆盖(父) ⊆ ∪覆盖(子)` —— §1 表**第四行**，按**父子对**统计。

    ⚠️ 该行 **2026-10-07 已降级**为「有代价的度量 + `B18` baseline 守卫」：
    实测**漏项与滞留项是同一批**（16 == 16，双向差 0）⇒ 它与「`滞留 == 0`」是
    **同一个条件**，而后者已被出路 (4) 用「代价在划分」换掉。见 §10.2 B 末。

    返回值里两个数都要有，别只报一个：

        `漏项数`   一共漏了几项 —— **代价**信号
        `漏的对数` 有几对父子漏了 —— **定位**信号（哪一层先破）

    实测（36 项语料）：

        方向        一次建完          维护路径（先建 6 维护 30）
        ────────────────────────────────────────────────────────
        keyset      0 漏 / 12 对       0 漏 / 12 对
        reach       0 漏 / 35 对       0 漏 / 35 对
        sequence    0 漏 /  5 对       **16 漏 / 2 对**（D0 漏 8、D1 漏 8）

    ⇒ 顺带推翻了一句话：`reach` 的覆盖**一次都没漏**，而它的健全性破了 128/328
      ⇒ 健全性越界**不是**「覆盖不漏」造成的，是另一条机制（根的 payload 是**外生**
      锚点集，与「后来添的项」无关）。**根因是两条，不是一条。**
    """
    pairs: list[dict[str, Any]] = []
    leak_items = 0
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        try:
            cp = cover(d.payload)
            union: set[str] = set()
            for k in kids:
                union |= cover(k.payload)
        except Exception:  # noqa: BLE001 - payload 由插件产出
            continue
        leaked = sorted(cp - union)
        if leaked:
            leak_items += len(leaked)
            pairs.append({"父": d.did, "子": [k.did for k in kids],
                          "漏项数": len(leaked), "例": leaked[:3]})
    return {
        "已展开方向数": sum(1 for d in kernel.all_directions() if kernel.children_of(d)),
        "漏项数": leak_items,
        "漏的对数": len(pairs),
        "明细": pairs,
    }


def render_cover_leak(batch: dict[str, Any], inc: dict[str, Any],
                      which: str = "") -> str:
    """一行渲染 —— 两条路径分开报，**别合并成一个数**。"""
    head = f"覆盖不漏（{which}）" if which else "覆盖不漏"
    b, i = batch["漏项数"], inc["漏项数"]
    if not b and not i:
        return (f"{head}：一次建完 {b} 漏 / {batch['已展开方向数']} 对、"
                f"维护 {i} 漏 / {inc['已展开方向数']} 对（两条路径都成立）")
    d = inc["明细"][0] if inc["明细"] else (batch["明细"][0] if batch["明细"] else {})
    where = (f"（例：{d.get('父')} 漏 {d.get('漏项数')} 项）" if d else "")
    return (f"★ {head}**被破坏**（§1 表第四行，⬜ 2026-10-07 已降级）："
            f"一次建完 {b} 漏 / {batch['已展开方向数']} 对、"
            f"**维护 {i} 漏 / {inc['漏的对数']} 对**{where}"
            f"—— ⚠️ 它与「`滞留 == 0`」是**同一个条件**（实测漏项就是滞留项），"
            f"不是与健全性并列的另一条机制")


# --- 「漏了、而且没有证明」—— 判据侧的那一个数 -------------------------------
#
# ⚠️ 这一族是 2026-10-08 从 `cover_leak_profile` **分出来**的，理由只有一条：
#    `cover_leak_profile` 报的那个数**含两类完全不同的东西**，混在一起时
#    「读数」与「违规」长得一模一样（本仓库的中心反模式）。
#
#        ① **论域外**的项     还没进结构的（`holdout`）⇒ 结构对它**没有义务**
#        ② **有证明**的滞留项 每个子方向都**证明**不收它（§10.2 出路 (4)）⇒ 设计如此
#
#    实测（`outputs/_probe_d2_four.py`，4 份语料 × 3 方向 × 3 段 = 36 行）：
#
#        D2 裸          总漏 3    ← `cli` 的 B′ 原来用的是这一个
#        D2 收域        总漏 2    ← 减掉 ①
#        D2 记账        总漏 1    ← 减掉 ②
#        D2 收域+记账   总漏 **0** ← 两个都减掉 ⇒ **基线绿**，才能当判据
#
#    ⇒ 「要么不漏，要么每一条漏都有证明」的**可红形态**是最后那一个。
#
# ⚠️ **两个前提都必须显式给，不许有默认值** —— 理由与 `B18` 的语料指纹同源：
#    防御不能因为调用方少传一个参数而**静默消失**。


def _stayed_map(kernel: Any) -> dict[str, set[str]]:
    """一次扫账本，把 `stayed_of` 全算出来。

    ⚠️ 逐方向调 `kernel.stayed_of(d)` 是 **`O(方向数 × 账本条数)`** ——
       3907 项语料上方向数是 7813、账本上万条 ⇒ 那是**小时级**。
       本函数一次扫完，形状是 `O(账本条数)`。
    """
    from ..core.direction import EVENT_STAYED

    out: dict[str, set[str]] = {}
    for e in kernel.ledger:
        if e.kind == EVENT_STAYED:
            out.setdefault(e.did, set()).add(e.detail.get("item", ""))
    return out


def cover_leak_unaccounted(kernel: Any,
                           cover: Callable[[Any], frozenset[str]]) -> dict[str, Any]:
    """**漏了、而且没有证明**的项 —— 「要么不漏，要么每一条漏都有证明」的判据侧。

    与 `cover_leak_profile` 的分工（两个数都要，缺一个读不出来）：

        `cover_leak_profile`      漏了几项        —— **度量**（报趋势；含上面 ①② 两类）
        本函数 `无证明漏项数`       其中没证明的几项 —— **判据**（进退出码）

    ## 两个前提

    **① 论域收在「结构真的持有的项」上**（`kernel.placed`）。

    `cover(p)` 是在**整份语料**上算的（`_fixtures.coverage_of`），
    而结构的论域是它**真的持有**的项。两者不同域时，父的覆盖里会混进
    **树从没见过**的项 —— 实测（`openalex-n100` / `sequence` 批建后）：

        漏 1 项（`W2995022099`），而它在 `holdout` 里、**不在结构 items 里**
        ⇒ `remove` **改不动**它（`kernel.remove` 的 ③：payload 由插件声明、
          `Direction` 不可变 ⇒ 删一个成员**不改变任何 payload**
          ⇒ `覆盖(父)` 与 `∪覆盖(子)` 两边都不动）
        ⇒ 「删了 12 项，漏还是 1」不是「删不掉」，是**这条读数压根与删除无关**

    ⚠️ 不收论域 ⇒ 判据在**批建之后**就红，而那不是违规 —— 是一条**基线就红**的判据，
       它过不了注入验证（注入验证要求「基线绿 + 注入红」）。

    **② 账目收在「活项」上。**

    `stayed_of(d)` 从**账本**读（`§K4` 只增不改）⇒ 删掉那个滞留项之后，
    账上那条 `STAYED` **还在**。不筛活项 ⇒ 「漏」是空的而「账」还在 ⇒
    报「滞留与账不符」—— 而真相是**账目陈旧**，不是划分破了。
    实测（`outputs/_probe_stay_delete.py`，n100/sequence）：删掉滞留项
    `W2995022099` 之后，`B4` 现写法报「漏 []，账上 ['W2995022099']」。

    ## 返回值

        `无证明漏项数`   判据用的那个数（**进退出码**）
        `论域外漏项数`   被 ① 排除掉的（**读数** —— 排除必须看得见）
        `陈旧滞留数`     被 ② 排除掉的（**读数**）
        `漏项数`         裸漏（与 `cover_leak_profile` 同一个数，便于对照）
    """
    placed = set(kernel.placed)
    stayed = _stayed_map(kernel)
    raw = unaccounted = outside_domain = stale = 0
    detail: list[dict[str, Any]] = []
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        try:
            cp = cover(d.payload)
            union: set[str] = set()
            for k in kids:
                union |= cover(k.payload)
        except Exception:  # noqa: BLE001 - payload 由插件产出
            continue
        leaked = cp - union
        if not leaked:
            continue
        raw += len(leaked)
        acc = stayed.get(d.did, set())
        outside = leaked - placed
        live_leak = leaked & placed
        stale_here = acc - placed
        bad = live_leak - acc
        outside_domain += len(outside)
        stale += len(stale_here)
        unaccounted += len(bad)
        if bad:
            detail.append({"父": d.did, "子": [k.did for k in kids],
                           "无证明": sorted(bad)[:3], "无证明数": len(bad)})
    return {
        "漏项数": raw,
        "无证明漏项数": unaccounted,
        "论域外漏项数": outside_domain,
        "陈旧滞留数": stale,
        "明细": detail,
    }



# --- B18：覆盖不漏的 baseline 守卫 -------------------------------------------

def _key(which: str, path: str) -> str:
    return f"{which}|{path}"


def load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.is_file():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def corpus_fingerprint(nodes: Any, edges: Any) -> dict[str, int]:
    """语料指纹 —— 用来判定「这份 baseline 是不是**本语料**上冻的」。

    ⚠️ 为什么非有不可：baseline 是**语料相关**的读数（`sequence` 维护路径 16 漏
       是在那 36 项上量的）。换一份语料跑，`183 > 16` 会被报成
       **「新增覆盖不漏」** —— 而真相是**「baseline 是别的语料的」**。
       两者在输出里长得一模一样，正是本仓库一直在防的形状。

    ⇒ 只记**两个数**（项数 / 边数），不记内容：够用，而且换语料一定会变。
    """
    return {"项数": len(nodes), "边数": sum(len(v) for v in edges.values())}


#: 基线文件的**格式串**。
#:     `1`（旧）= **单语料** —— 顶层 `语料` 是指纹、顶层 `基线` 直接就是条目。
#:     `2`（现在）= **按语料分区** —— `基线` 是 `{分区键: {语料, 条目}}`。
#: ⚠️ 为什么非要升这一版：基线文件是**一个**，而语料有**多份**（36 项 / 3907 项）。
#:    单语料格式下，「在全量上重冻」会把 36 项那一份**抹掉** ⇒ 36 项那条守卫
#:    立刻变成「跳过」—— **洞没补上，只是从一份语料挪到了另一份**。
BASELINE_FORMAT = 2
BASELINE_FORMAT_KEY = "格式"


def corpus_key(corpus: dict[str, int]) -> str:
    """语料指纹 → **分区键**。**只由指纹的两个数决定**：同语料同键、换语料必换键。

    ⚠️ 用**人可读**的串而不是 `repr(dict)`：出问题时报告里要能直接读出
       「已冻的语料有哪些」，`{'项数': 36, '边数': 47}` 那种串读不动。
    """
    return f"{corpus.get('项数', '?')} 项 / {corpus.get('边数', '?')} 边"


def baseline_zones(doc: dict[str, Any]) -> dict[str, Any]:
    """`{分区键: {语料, 条目}}` —— **旧格式（v1）当场升级**成一个区，不丢数据。

    ⚠️ 升级放在**读**这一侧而不是「让用户手工改文件」：旧文件在仓库里，
       要是读的时候不认，它就会静默退化成「跳过」—— 那正是本仓库一直在防的形状。
    """
    if doc.get(BASELINE_FORMAT_KEY) == BASELINE_FORMAT:
        zones = doc.get("基线")
        return dict(zones) if isinstance(zones, dict) else {}
    old_fp, old_entries = doc.get("语料"), doc.get("基线")
    if isinstance(old_fp, dict) and old_fp and isinstance(old_entries, dict):
        return {corpus_key(old_fp): {"语料": old_fp, "条目": old_entries}}
    return {}


def write_baseline(entries: dict[str, Any], corpus: dict[str, int] | None = None) -> None:
    """冻结**本语料**那一区 —— **合并**，绝不覆盖别的语料。

    ⚠️ 合并是这条命令的**语义**，不是优化：写成覆盖，在第二份语料上重冻就会把
       第一份那区抹掉 ⇒ 第一份那条守卫立刻变「跳过」—— **洞没补上，只是挪了地方**。
       实测（`run_tests.test_cover_leak_baseline` 第 ⑩ 组）：两份语料各判各的。
    """
    corpus = corpus or {}
    zones = baseline_zones(load_baseline())
    zones[corpus_key(corpus)] = {"语料": corpus, "条目": entries}
    body = {
        "_note": ("覆盖不漏（§1 表第四行，⬜ 2026-10-07 已降级为「度量 + baseline 守卫」）"
                  "的**冻结基线**。"
                  "由 `python -m ldv.run_checks --write-cover-leak-baseline` 生成 —— "
                  "它**合并**进本语料那一区，别的语料不动。"
                  "新增违规会红；条目不再发生也会红（ESLint `--prune-suppressions` 那条纪律）。"
                  "`基线` 按**语料指纹**分区：换了语料这条守卫报「跳过」，"
                  "不报「红」：**「baseline 是别的语料的」与「新增违规」必须分得开**。"),
        BASELINE_FORMAT_KEY: BASELINE_FORMAT,
        "基线": zones,
    }
    BASELINE_PATH.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def b18_cover_leak_baseline(obs: dict[str, dict[str, Any]], rep: Report,
                            corpus: dict[str, int],
                            baseline: dict[str, Any] | None = None) -> None:
    """**新增违规会红，条目不再发生也会红** —— 两头都守。

    ## 为什么不是「一条写死的判据」

    §1 表第四行**原为硬要求，2026-10-07 已降级**（它与「`滞留 == 0`」是同一个条件，
    见 §10.2 B 末），而维护路径上它**基线就红**（sequence 16 漏 / 2 对）。
    一条基线就红的判据**过不了注入验证**，只能先做成度量 —— 而纯度量**永远不红**，
    于是「声称是硬的、实际没人管」这个毛病原样保留。

    ESLint 的 Bulk Suppressions 给了第 (4) 条：

    > **Existing violations must be resolved before enabling the rule** …
    > To address this, ESLint provides a way to **suppress existing violations** …
    > **While the rule will be enforced for new code, the existing violations will not
    > be reported.**

    ⇒ **baseline 严格强于纯度量**：它**保留了「能红」**。而且 ESLint 配了一条
      **只减不增**的纪律（"an error is reported about **unused suppressions**"）——
      本模块照抄，因为否则 baseline 会退化成**永久豁免**。

    ## 三条规则

        观测 > baseline   → **红**（新增违规）
        baseline > 0 且 观测 == 0 → **红**（条目不再发生 ⇒ 去收紧 baseline）
        其余              → 绿；观测 < baseline 时**另报**「可以收紧」

    ## ★ 第 0 条：baseline 必须是**本语料**上冻的（否则**跳过**，不红不绿）

    没有这一条的话，「换了一份语料」会被报成「新增覆盖不漏」——
    实测：`openalex-small`（281 项）上 `sequence|maintenance` 漏 **183**，
    而 baseline 是在 36 项上冻的 **16** ⇒ 报「新增覆盖不漏：183 漏（baseline 16）」。
    **那句话是假的**：没有任何东西变差，只是 baseline 换了参照物。

    ⇒ 语料指纹对不上 ⇒ 报 **未展开**（跳过），并说清是**换了语料**。
      跳过 ≠ 通过：它明确地说「这条守卫在本语料上判不了」，而不是「没问题」。

    ⚠️ **2026-10-10 起，baseline 是「按语料分区」的**（`BASELINE_FORMAT = 2`）：
       一个文件装多份语料，各占一区（`corpus_key`）。第 0 条的判定从
       「顶层 `语料` == 现在」变成「**本语料那一区在不在**」。
       ⇒ 于是 `--write-cover-leak-baseline` **必须合并写入**：否则在第二份语料上
         重冻会把第一份那区抹掉，第 0 条立刻把它报成「另一份语料」——
         **洞没补上，只是从一份语料挪到了另一份**（`test_cover_leak_baseline` 第 ⑩ 组守这条）。

    ⚠️ **`corpus` 是必填参数**（不是 `= None` 的默认值）。理由与第 0 条同源：
       防御**不能因为调用方少传一个参数而静默消失** —— 那正是本仓库一直在防的形状。
       漏传会在调用点直接 `TypeError`，而不是变成一条「永远绿」的守卫。

    ⚠️ 而且 **baseline 里没记指纹**（`语料` 为空）也走**跳过**，理由同第 0 条：
       判不了 ≠ 没问题。少了这一条，一个旧格式的 baseline 文件
       会让整条守卫**无声地失去防御**（`frozen == {}` 恒不等于 `corpus` 之外无差别）。
    """
    doc = baseline if baseline is not None else load_baseline()
    zones = baseline_zones(doc)
    key = corpus_key(corpus)
    zone = zones.get(key)
    if zone is None or zone.get("语料") != corpus:
        if not zones:
            why = (f"baseline 里**没记语料指纹**（`语料` = {doc.get('语料')!r}、"
                   f"`格式` = {doc.get(BASELINE_FORMAT_KEY)!r}）"
                   f"⇒ 无法判定它是不是本语料的")
        elif zone is None:
            why = (f"baseline 是在**另一份语料**上冻的 —— 本语料（{key}）"
                   f"不在已冻的分区里（已冻：{sorted(zones)}）")
        else:
            why = (f"baseline 里本语料那一区的**指纹与分区键对不上**"
                   f"（键 {key}、区里记的是 {zone.get('语料')}）⇒ 存档被改坏了")
        rep.add("B18", "覆盖不漏（baseline 守卫）", Tri.UNEXPANDED,
                f"{why} ⇒ 这条守卫在本语料上**判不了**。要判就先 "
                f"`python -m ldv.run_checks --write-cover-leak-baseline` 重冻。"
                f"**跳过 ≠ 通过**：它没说本语料不漏")
        return
    base = zone.get("条目", {})
    bad: list[str] = []
    tight: list[str] = []
    checked = 0
    for key in sorted(obs):
        o = obs[key]
        b = base.get(key, {"漏项数": 0, "漏的对数": 0})
        checked += 1
        if o["漏项数"] > b["漏项数"]:
            bad.append(f"{key} 新增覆盖不漏：{o['漏项数']} 漏（baseline {b['漏项数']}）"
                       f"—— {o['明细'][:1]}")
        if b["漏项数"] > 0 and o["漏项数"] == 0:
            bad.append(f"{key} 的 baseline 条目**不再发生**（baseline {b['漏项数']} 漏）"
                       f"⇒ 该收紧 baseline，不要留着当永久豁免")
        if 0 < o["漏项数"] < b["漏项数"]:
            tight.append(f"{key} {b['漏项数']}→{o['漏项数']}")
    if checked == 0:
        rep.add("B18", "覆盖不漏", Tri.UNEXPANDED, "没有可比的父子对")
        return
    detail = ("；".join(bad) if bad
              else f"{checked} 个「方向|路径」都没有新增覆盖不漏"
                   + (f"；可收紧：{tight}" if tight else ""))
    rep.add("B18", "覆盖不漏：覆盖(父) ⊆ ∪覆盖(子)（baseline 守卫：新增红、条目失效红）",
            Tri.NO if bad else Tri.YES, detail)


# --- 度量 + B19：变细守卫 ---------------------------------------------------

def progress_profile(kernel: Any,
                     cover: Callable[[Any], frozenset[str]]) -> dict[str, Any]:
    """**两条分开的读数**：一条报「切得多不匀」（度量），一条判「有没有真变细」（判据）。

    ## 为什么是两条、而不是一条（2026-10-07 改）

    原来只有一条：

        `细化量(d) = |覆盖(d)| − **max**_k |覆盖(k)|`
        `无进步(d) ⟺ 细化量(d) ≤ 0`

    它把**两件不同的事**算成了同一个记号。实测（`outputs/_probe_b19_depth.py`，
    逐字输出在 `outputs/_b19_depth_fullscale.txt`）：

        没变细      **没有任何一个子**的覆盖严格小于父  ⇒ 这次展开**一个项都没分开**
        不均衡切    至少一个子严格更小，只是**最大的那个**子没变小 ⇒ 它**分开了东西**

        语料    项数   已展开   细化量≤0   其中「没变细」   最长没变细段   B19
        ──────────────────────────────────────────────────────────────────────
        36       36      35       10          **0**            0          绿
        281     281     280       80            5             2          绿
        3907   3907    3906     1515          599            **9**       绿（改谓词后）

    ⇒ `max` 聚合让「切得**不匀**」被读成「**没切**」。全量上把 `B19` 顶红的那条
      10 跳链**逐跳打出来，一跳「没变细」都没有**（其中一步切掉了 1035 项）：

        D3→D5→D7   父 1044 ｜ max子 1044 ｜ min子 1040     ← 确实切掉了 4 项
        D15        父 1044 ｜ max子 1044 ｜ min子    9     ← 这一步切掉 1035 项
        D7369      父    2 ｜ max子    2 ｜ min子    1

    ⇒ 所以现在**分开**：

        度量 · 细化量   `|覆盖(父)| − **max**子` —— 报**切得多不匀**（趋势）
        判据 · `B19`    `|覆盖(父)| − **min**子 ≤ 0` —— 守**有没有真变细**（成分）

    ⚠️ **「无进步」这个词就此作废** —— 它先后指过两件事（`细化量 ≤ 0` 与「没变细」），
       留着必然被读错。现在一律说「**没变细**」（判据）或「**切得不匀**」（度量）。

    ⚠️ 谓词写成 `父 − **min**子 ≤ 0` 而不是 `min子 == 父`：**覆盖(子) 不保证 ⊆ 覆盖(父)**。
       真实插件上两者**等价**（三个方向的子 payload 都是父的子集 ⇒ 覆盖只会更小），
       但一条「子 payload 从父 payload 派生」（§10.2 出路 (3)）的规则会让覆盖**变大** ——
       那同样是「一个项都没分开」，`==` 会把它漏掉，`≤ 0` 不会。
       「没变细」的**直接读法**就是「**没有任何一个子严格更小**」。

    ⚠️ **改谓词不是把红藏起来。** 「没变细」**真的存在**（3907 上 599 次，最长连续 9 跳），
       判据仍在工作：注入一个「子的 payload 退回父的 payload」的插件 ⇒ 全都没变细 ⇒ 必红。
       改的只是「**哪些算**没变细」。

    ⚠️ **但 N=10 仍是拍出来的值**，而改完谓词后全量上最长「没变细」段 = **9** ——
       **离阈值只差 1**。这条**绝对阈值**在 3907 这个尺度上已经贴着边，见 §7.7。

    ⚠️ **另一件不能混进来的事**：连续段是**沿根→叶路径**数的 ⇒ 它**必然** ≤ 树深。
       所以「B19 能不能红」被**树深**卡着，而树深**随尺度长**：

        36 项 层数 7 ｜ 281 项 层数 10 ｜ 3907 项 层数 **13**

       ⇒ 「真语料上不会红」这条声称，**理由是尺度相关的**（详见 `b19_progress_guard`）。
    """
    rows: list[dict[str, Any]] = []
    flat: dict[str, bool] = {}
    uneven: dict[str, bool] = {}
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        try:
            cp = len(cover(d.payload))
            cs = [len(cover(k.payload)) for k in kids]
        except Exception:  # noqa: BLE001 - payload 由插件产出
            continue
        flat[d.did] = min(cs) >= cp          # 「没变细」⟺ 没有任何一个子严格更小
        uneven[d.did] = cp - max(cs) <= 0    # 「切得不匀」⟺ 最大的那个子没变小
        rows.append({"方向": d.did, "秩": d.rank, "覆盖": cp,
                     "最大子覆盖": max(cs), "最小子覆盖": min(cs),
                     "细化量": cp - max(cs), "子数": len(cs)})

    def chain(flag: dict[str, bool]) -> int:
        """沿**根→叶**的最长连续段。⚠️ 按 `flag` 数，不按标签 —— 叶会把它打断。"""
        best = 0

        def walk(d: Any, run: int) -> None:
            nonlocal best
            run = run + 1 if flag.get(d.did, False) else 0
            best = max(best, run)
            for k in kernel.children_of(d):
                walk(k, run)

        walk(kernel.root, 0)
        return best

    flatrows = [r for r in rows if r["最小子覆盖"] >= r["覆盖"]]
    unrows = [r for r in rows if r["细化量"] <= 0]
    return {
        "已展开方向数": len(rows),
        # —— 判据那一条（`B19` 的谓词）：有没有**真变细**
        "没变细展开": len(flatrows),
        "最长没变细连续段": chain(flat),
        "没变细的": [r["方向"] for r in flatrows][:4],
        # —— 度量那三条（只报不判）：切得多不匀
        #     ⚠️ 这两条连续段读数**保留下来**是有意的：改谓词之前它们就是全部读数
        #        （36 → 4、281 → 6、3907 → **10** 那条**尺度趋势**）。
        #        判据换成「没变细」不等于把趋势删掉 —— 趋势归度量。
        "切得不匀展开": len(unrows),
        "不均衡切展开": len(unrows) - len(flatrows),
        "最长切得不匀连续段": chain(uneven),
        "最小细化量": min((r["细化量"] for r in rows), default=0),
        "N": PROGRESS_N,
    }


def render_progress(prof: dict[str, Any], which: str = "") -> str:
    head = f"细化量（{which}）" if which else "细化量"
    if prof["已展开方向数"] == 0:
        return f"{head}：没有已展开的方向（未展开，**不是**通过）"
    return (f"{head}：{prof['已展开方向数']} 次展开｜**没变细** "
            f"{prof['没变细展开']} 次（判据），最长连续段 "
            f"{prof['最长没变细连续段']}（阈值 N={prof['N']}）"
            f"｜切得不匀 {prof['切得不匀展开']} 次、最长连续段 "
            f"{prof['最长切得不匀连续段']}（度量，其中不均衡切 "
            f"{prof['不均衡切展开']}）｜最小细化量 {prof['最小细化量']}"
            f"（度量，不进退出码）")


def b19_progress_guard(prof: dict[str, Any], rep: Report) -> None:
    """**反复声称能分、却连续 N 次一个项都没分开 ⇒ 红。**

    谓词是「**没有任何一个子**的覆盖严格小于父」（没变细），**不是**「`细化量 ≤ 0`」——
    后者把「切得不匀」也算进来了。为什么这是两件事，见 `progress_profile` 的 docstring。

    与 `§K2 判空` 是**两条不同的检查**，不能合并：

        §K2 判空    判「**这次**分不开」—— 单次性质
        B19         判「**反复声称**却分不出东西」—— **跨次数**性质

    N = 10 照 SP-GiST 前例（`longValuesOK` 的 "within ten cycles"）。

    ⚠️ **「真语料上 `B19` 不会红」这句 2026-10-07 被实测推翻了一半。**
       原话给的**理由**是「树只有 7 层，够不到 N=10」—— 那是个**尺度相关**的理由，
       于是它随尺度一长就必然翻（`outputs/_probe_b19_depth.py`，三档实测）：

        语料     项数    层数   最长连续段（旧谓词 `细化量 ≤ 0`）   最长没变细段（新谓词）
        ──────────────────────────────────────────────────────────────────────────
        36       36       7                 4                          0
        281     281      10                 6                          2
        3907   3907      13               **10** ⇒ 旧谓词**红**          9 ⇒ 新谓词绿

       ⇒ 旧谓词下 3907 上**是红的**，而那条链**一跳「没变细」都没有**。
         ⇒ 所以那句声称**原来就不成立**；而它以前**绿着**的理由也**不是**「没有空转」，
           是「**树不够深**」——
           **「树不够深」与「没有空转」长得一模一样**，这正是本判据必须由
           **注入验证**兜底的原因：真语料上的绿，**不能**用来证明判据在守东西。
    """
    if prof["已展开方向数"] == 0:
        rep.add("B19", "变细守卫", Tri.UNEXPANDED, "没有任何方向被展开过")
        return
    over = prof["最长没变细连续段"] >= prof["N"]
    rep.add("B19", f"变细守卫：连续 {prof['N']} 次展开都必须真的让覆盖变细",
            Tri.NO if over else Tri.YES,
            (f"连续 {prof['最长没变细连续段']} 次展开**一个项都没分开**"
             f"（≥ N={prof['N']}）：{prof['没变细的']}") if over
            else (f"{prof['已展开方向数']} 次展开中没变细 {prof['没变细展开']} 次，"
                  f"最长连续段 {prof['最长没变细连续段']} < N={prof['N']}"
                  f"（切得不匀 {prof['切得不匀展开']} 次是度量，不判）"))


def b18_known_answer() -> list[str]:
    """`B18` 的**合成对照** —— 三态各一例。返回**失败清单**（空 = 全对）。

    ⚠️ 同 `b17_known_answer` / `b20_known_answer`：三态**都**要对照，而且
       **期望值必须两两不同**（三例落在同一态 ⇒ 这个对照什么都没证明，当场自检）。
    ⚠️ 它**不碰内核、不碰语料、不碰盘上那份 baseline** —— 全程显式传参。

    ## ⚠️ 为什么 `B18` 也要有这一条（2026-10-12 补）

    `run_checks` 汇总里那段「跳过项的**覆盖证据**」按**编号**出，而它原来只盖
    `A5` / `B17` / `B20`。`B18` 的跳过有**四个分支**（baseline 是别的语料的 /
    baseline 里没记指纹 / 存档被改坏 / 没有可比的父子对），而它**真的会跳** ——
    实测 `snap-hepth` 与 `snap-hepth-dag` 上就跳。那时那段汇总里
    **既没有 `B18` 那一行、也没有任何提示**（静默省略），
    而「没有这一行」与「这一行全对」在输出里**长得一样**。

    ⇒ 这条对照 + `_skip_evidence()` 里的登记 + 汇总块的「点名」三样一起才补齐。

    三例：

        过    baseline 是本语料那一区、条目 ≥ 观测
        红    观测 > baseline（新增覆盖不漏）
        跳过  baseline 是**另一份语料**的（第 0 条：语料指纹不符）
    """
    fp = {"项数": 36, "边数": 47}
    other = {"项数": 281, "边数": 1008}
    entry = {"漏项数": 0, "漏的对数": 0}
    same = {corpus_key(fp): {"语料": fp, "条目": {"keyset|batch": entry}}}
    diff = {corpus_key(other): {"语料": other, "条目": {"keyset|batch": entry}}}
    cases: list[tuple[str, dict[str, dict[str, Any]], dict[str, Any], Tri]] = [
        ("过·baseline 是本语料那一区、没有新增覆盖不漏",
         {"keyset|batch": {**entry, "明细": []}}, same, Tri.YES),
        ("红·观测 > baseline（新增覆盖不漏）",
         {"keyset|batch": {"漏项数": 3, "漏的对数": 2, "明细": ["D0 漏 3 项"]}},
         same, Tri.NO),
        ("跳过·baseline 是**另一份语料**的（第 0 条：语料指纹不符）",
         {"keyset|batch": {**entry, "明细": []}}, diff, Tri.UNEXPANDED),
    ]
    fails: list[str] = []
    seen: list[Tri] = []
    for label, obs, zones, want in cases:
        rep = Report(plugin="(合成)")
        b18_cover_leak_baseline(
            obs, rep, fp,
            baseline={BASELINE_FORMAT_KEY: BASELINE_FORMAT, "基线": zones})
        got = _result(rep, "B18")
        seen.append(got)
        if got is not want:
            fails.append(f"{label}：期望 {want}，实测 {got}")
    if len(set(seen)) != len(seen):
        fails.append(f"对照表退化：三例落在同一态 {seen} —— 这个对照证明不了什么")
    return fails

