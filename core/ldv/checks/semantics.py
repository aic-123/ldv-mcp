"""B10–B11 —— 语义类检查（§K6 / §K7）。

    B10 三态可分  §K6 —— 「未展开」不许在输出里等价成「否」
    B11 倾向完备  §K7 —— 自我优化的输入 100% 带倾向权重

---

## B10 查的是**输出形态**，不是内部枚举

内部有三个枚举值不代表输出分得开 —— 只要 `render()` 把它们印成同一个样子，
用户看到的就还是「两个状态」。所以这里直接比**渲染结果**：

    `全否.render()` 必须 ≠  `全未展开.render()`

这和 arena 的「0 和『没有』分不开，就不许印成 0」是同一件事：
**判据落在印出来的那一层**，不落在内部数据结构上。
"""

from __future__ import annotations

from typing import Any

from ..core.interfaces import UsageSignal, WeightedRecord
from ..core.kernel import Query, QueryResult, exposure_propensity
from ..core.tri import Tri
from ._framework import Report


# --- B10 --------------------------------------------------------------------

def b10_three_states_separable(kernel: Any, plugin: Any, rep: Report) -> None:
    """「未展开」与「否」在输出里必须长得不一样。"""
    bad: list[str] = []

    # (a) 渲染层：构造两个**只差状态**的结果，输出必须不同
    r_no = QueryResult(status=Tri.NO, no=("D1", "D2"))
    r_un = QueryResult(status=Tri.UNEXPANDED, unexpanded=("D1", "D2"))
    if r_no.render() == r_un.render():
        bad.append(f"「确定没有」与「未知」渲染相同：{r_no.render()!r}")
    r_yes = QueryResult(status=Tri.YES, yes=("D1",), hit_items=frozenset({"x"}))
    if r_yes.render() in (r_no.render(), r_un.render()):
        bad.append("「命中」与另外两种状态之一渲染相同")

    # (b) 真跑一次会产生「未展开」的查询：状态不许落到「否」
    max_rank = max((d.rank for d in kernel.all_directions()), default=1)
    probe = Query(ideal=frozenset(kernel.items), min_rank=max_rank + 1, label="要求超高分辨率")
    res = kernel.query(probe)
    if res.counts["未展开"] == 0:
        rep.add("B10", "三态可分", Tri.UNEXPANDED,
                "构造不出「未展开」的查询（插件没有第三态）")
        return
    if res.status is Tri.NO:
        bad.append(f"有 {res.counts['未展开']} 个「未展开」，状态却是「否」：{res.render()}")
    if "未展开" not in res.render():
        bad.append(f"输出里看不出有「未展开」：{res.render()!r}")

    rep.add("B10", "三态可分：未展开 ≠ 否",
            Tri.NO if bad else Tri.YES,
            "；".join(bad[:2]) if bad
            else f"渲染可分；真跑得到 未展开={res.counts['未展开']}，状态={res.status}")


# --- B11 --------------------------------------------------------------------

def b11_propensity_complete(kernel: Any, plugin: Any, rep: Report) -> None:
    """进入自我优化的记录必须 **100%** 带倾向权重，**且倾向必须对曝光位次敏感**。

    两条判据：

    (a) **完备**：每条记录的倾向都在 `(0, 1]`。
    (b) **位置敏感**：同一轮展示里，第 1 位和第 N 位的倾向**必须不同**。
        相同就说明曝光模型退化成「按个数」—— 那等于假设每个位置曝光概率一样，
        而真实检索里第一位和第十位差一个量级。倾向加权会把偏差**原样带进**自优化，
        而自优化的目的恰恰是修掉它。

    ⚠️ 判据 (b) 是**把「按位置」这个决定变成可执行的东西**。
       只写在注释里的话，下一个人改成均匀曝光不会有任何东西变红。

    ⚠️ 位次来自 **`kernel.show(候选)`**，不是遍历的副产品。
       「遍历经过的最后一层」和「摆给使用者看的列表」是两回事 ——
       拿前者当展示，多层查询之后第 1 层的方向会被算成「不在展示里」，
       倾向被压到下界，判据 (b) 就在**假的位次**上通过。
    """
    bad: list[str] = []

    # 结构性：§I6 的产物不含倾向
    fields = set(getattr(UsageSignal, "__dataclass_fields__", {}))
    if "propensity" in fields:
        bad.append("§I6 的产物里出现了 propensity —— 倾向必须由内核填")

    # 行为性：先跑一次查询拿到候选，**显式展示**，再逐条记
    dirs = kernel.all_directions()[:3] or kernel.all_directions()
    made = 0
    for d in dirs:
        for outcome in (1.0, -0.5, None):
            rec = kernel.record_usage(d, {"outcome": outcome})
            if rec is not None:
                made += 1

    probe = Query(ideal=frozenset(kernel.items))
    res = kernel.query(probe)
    shown = kernel.show(res.yes)
    if len(shown) < 2:
        # ⚠️ **「判不了」与「实现坏了」不许长得一样**（2026-10-11 实测）。
        #    判据 (b) 的**前提**是「展示列表至少 2 项」—— 没有两个位次，就谈不上
        #    「第 1 位和第 N 位的倾向是否不同」。前提不成立时分两种，要分得开：
        #
        #      方向总数 < 2  ⇒ 前提**根本不可能**成立（这是**语料侧**的事）
        #                      ⇒ 报**跳过**。报红是在**指控实现坏了**，而实现没坏。
        #      方向够多而列表塌到 1 项 ⇒ 那是**实现侧**的「不该发生」⇒ 照旧报红。
        #
        #    ⚠️ 这条在随仓库那份 36 项语料上**走不到**（25 个方向 ⇒ `shown` 25 项），
        #       所以它**不会**改那份基线；它只在「一个方向」的边界语料上生效
        #       （实测 `edge-single`：1 个方向 ⇒ 原来报红）。
        if len(kernel.all_directions()) < 2:
            rep.add("B11", "倾向完备 + 位置敏感（§K7：记录必须带倾向权重）", Tri.UNEXPANDED,
                    f"`kernel` 只有 {len(kernel.all_directions())} 个方向 ⇒ "
                    f"展示列表最多 1 项 ⇒ 位次敏感**判不了**"
                    f"（判不了 ≠ 红：红是说实现坏了）"
                    f"（§10 停止条件：**跳过 ≠ 通过**）")
            return
        bad.append(f"展示列表只有 {len(shown)} 个方向 —— 判不出位次敏感（不该发生）")
    else:
        first = kernel.record_usage(kernel.direction(shown[0]), {"outcome": 1.0})
        last = kernel.record_usage(kernel.direction(shown[-1]), {"outcome": 1.0})
        made += 2
        if first is not None and last is not None:
            if first.propensity == last.propensity:
                bad.append(
                    f"倾向与曝光位次无关（第 1 位和第 {len(shown)} 位都是 "
                    f"{first.propensity:.4f}）—— 等于均匀曝光"
                )
            if not (last.propensity <= first.propensity):
                bad.append(
                    f"位次越靠后倾向越高（第 1 位 {first.propensity:.4f} < "
                    f"第 {len(shown)} 位 {last.propensity:.4f}）—— 曝光模型反了"
                )

    missing = [r.did for r in kernel.usage if not isinstance(r, WeightedRecord)
               or not (0.0 < r.propensity <= 1.0)]
    if missing:
        bad.append(f"{len(missing)} 条记录倾向缺失/越界：{missing[:3]}")
    if made == 0:
        rep.add("B11", "倾向完备", Tri.UNEXPANDED, "这一轮没有产生任何使用记录")
        return
    total = len(kernel.usage)
    rep.add("B11", "倾向完备 + 位置敏感（§K7：记录必须带倾向权重）",
            Tri.NO if bad else Tri.YES,
            "；".join(bad[:2]) if bad
            else f"{total}/{total} 条带倾向；位次 1→{_p(kernel, shown, 0)}，"
                 f"位次 {len(shown)}→{_p(kernel, shown, -1)}")


def _p(kernel: Any, shown: tuple, idx: int) -> str:
    """报告用：把某个位次的倾向印出来（**只读**，不新增记录）。

    ⚠️ 调的是 `kernel.exposure_propensity` —— **不是**在这里再写一遍公式。
       原来这里抄了一份 `max(1.0 / position, 0.05)`，于是「模型改了、报告没改」
       会**静默地**印出一个旧值：报告里只有一个数，**没有对照**，
       读起来和改对了完全一样。
    """
    if not shown:
        return "?"
    pos = 1 if idx == 0 else len(shown)
    return f"{exposure_propensity(pos):.3f}"
