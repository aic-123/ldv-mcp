"""§I1–§I7 的协议 —— 设计文档 §5。

GiST 是**六方法**，本设计是**七个**。多出的 §I6 是因为 GiST 没有反馈环。

    §I1 命中(方向, 查询) → 是 | 否 | 未展开      ← Consistent
    §I2 合并(一组方向) → 更粗的方向              ← Union
    §I3 代价(方向, 待放入项) → 可比较的数        ← Penalty
    §I4 劈开(父方向, 一组项) → None | 一组方向    ← PickSplit
    §I5 编码 / 解码(方向)                        ← Compress / Decompress
    §I6 信号(方向, 交互) → 使用记录 | 无          ← **本设计新增**
    §I7 记录(方向) → 可序列化

---

## §I4 的三个位置：**「分不开」是插件说的话**，**父方向是插件该看的东西**

    None         插件**声明**：这一层分不开（一条**判定**，进账本，可数）
    序列(≥2 个)  插件**提出**候选切分；归属由内核按 §I3 分配（§I4 那条「划分」）
    序列(<2 个)  **违约** —— 说 `None`，或者给 ≥2 个。内核**不替它猜**

**为什么「分不开」要显式返回**：原来它编码成「返回两个相同的 payload」，
于是判定**藏在内容里** —— 内核只能从「分配后一侧为空」反推出「插件分不开」。
两种完全不同的东西因此长得一样：

    (a) 插件**判**分不开          —— 接口的**正常**用法，是插件的判断
    (b) 插件给的候选**切不动**     —— 候选是退化的（分配后一侧为空）

(a) 是插件的声明，(b) 是**插件与内核之间的摩擦**。两者都该记，但**归因不同**，
分开记才能看出「这个插件到底有没有在说话」。见 `core/kernel.py` 的 `stats()`。

前例：SP-GiST 的 `PickSplit` **直接返回布尔**
（「indicating whether further partitioning should take place or not」），
而不是靠「返回两个相同的东西」让内核去猜。逐字引文见
`../docs/分层方向视图-成熟方案与跨领域文献.md` §2.2。

**为什么要把父方向递给插件**：`劈开` 只拿到项，就只能**从项里重新猜**父的意图 ——
猜出来的子方向**不保证**是父的细化（实测：A 的 `覆盖(子) ⊆ 覆盖(父)` 有 14 次越界）。
递上父方向，插件就能把父的约束**继承**下去 ⇒ 那个包含关系从「碰巧成立」变成**结构上成立**。

⚠️ 但它**不是**必须的：健全性（`members ⊆ 覆盖`）由 `B16` 独立守着，
「子覆盖 ⊆ 父覆盖」**不在** §1 的硬要求表里。它买的是**假阳率**（性能），
按 §K8 属于「允许但不许不管」的那一半 ⇒ 所以它有**读数**、也要有**判据**（见 §7.4）。

---

## 那个不对称契约（§K8）—— **整个模块化方案安全的原因**

    假阳 **允许** —— 代价是**性能**
    假阴 **禁止** —— 破坏**正确性**

GiST 原文对这条的表述：

> an **accurate test for satisfiability is not required** here: Consistent **may return true
> incorrectly without affecting the correctness of the tree algorithms**. The penalty for such
> errors is in **performance**

⇒ 方向函数在结构上只是一个**过滤器**，过滤器的错只损失性能。
⇒ **所以插件可以是不准的**，「不需要一开始就有完美规则」有**结构性**理由，
   而且**可自动检测**（专门测假阴 = `B1`）。

---

## 三条纪律（写在这里，因为每个插件都必须遵守）

1. **不许抛异常**（§B1 会红）：`命中` 遇到不认识的输入要返回「未展开」，
   不是 `raise`。异常会中断遍历，效果等于假阴。
2. **「无信号」≠「负信号」**（§I6 / §B6）：没观测到使用，返回 `None`；
   观测到使用且结果不好，返回一条**记录**。两者在输出里必须长得不一样。
3. **插件不许自己算倾向权重**（§8.1）：倾向由**内核**填 ——
   只有内核知道它展示了什么。

---

## 六个边界问题

「能不能改传入对象 / 能不能存跨调用缓存 / 能不能多线程同时调 /
能不能持有内核引用 / 抛异常后内核是否还有效 / 兼容性由谁保证」——
逐条答在 `../docs/插件契约.md`，每条注明它是**结构上成立**、**纪律+判据**、
还是**纪律但无判据**（第三种明说守不住，不装作有）。
"""

from __future__ import annotations

import numbers
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from .direction import Direction
from .tri import Tri


@dataclass(frozen=True)
class UsageSignal:
    """§I6 的产物。**不含倾向权重** —— 倾向由内核填（§8.1）。"""

    did: str
    outcome: float          # 正 = 有帮助，负 = 帮倒忙。**「无信号」用 None，不用 0**
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, numbers.Real):
            raise TypeError("outcome 必须是数；「没有观测到使用」请返回 None 而不是 0")


@dataclass(frozen=True)
class WeightedRecord:
    """内核填完倾向权重之后的记录 —— §F1 的产物，§F2 的输入。"""

    did: str
    outcome: float
    propensity: float
    note: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 < self.propensity <= 1.0):
            raise ValueError(f"倾向权重必须落在 (0, 1]，得到 {self.propensity}")


@runtime_checkable
class DirectionPlugin(Protocol):
    """七个方法。**内核只认这七个**，多一个都不认。"""

    name: str

    # §I1
    def hit(self, d: Direction, query: Any) -> Tri: ...

    # §I2 —— 「更粗」= **行为上的覆盖**（细方向收下的，粗方向不许拒）。
    #         ⚠️ 别读成 `合并({细}) = 粗`：**合并(单元素) 是恒等**，那个等式
    #            在三个方向上都**不成立**（实测 A 0/39、B 0/71、C 0/37）。
    #            见 `C5b-第三方向复核.md` §2.2。
    def merge(self, ds: Sequence[Direction]) -> Any: ...

    # §I3 —— 局部权衡藏在这里。内核不参与折算
    def penalty(self, d: Direction, item: Any) -> float: ...

    # §I4 —— `None` = **插件声明**「这一层分不开」；否则必须给 **≥2** 个候选方向。
    #         ⚠️ 返回值**不要求**恰好两个：有序方向（trie 形状）自然就是 k 叉。
    #            少给（<2）是**违约** —— 内核不替插件猜，也不静默当成「分不开」。
    #         ⚠️ **父方向**递进来，是为了让子方向成为父的**可证细化**（覆盖嵌套），
    #            而不是让插件从项里**重新猜**父的意图。不要求用，但不给就猜不准。
    def split(self, parent: Direction, items: Sequence[Any]) -> Sequence[Any] | None: ...

    # §I5 —— 解码必须**覆盖**编码前（可以更粗，不可以更细）
    def encode(self, d: Direction) -> bytes: ...
    def decode(self, blob: bytes) -> Any: ...

    # §I6 —— 允许返回 None。「没有观测到使用」≠「使用了但为负」
    def signal(self, d: Direction, interaction: Any) -> UsageSignal | None: ...

    # §I7 —— 形状要求：方向必须可序列化，否则无法留痕、无法分叉
    def record(self, d: Direction) -> dict: ...


# --- 运行时校验 -------------------------------------------------------------
# 协议是**形状**，不是保证。下面的函数是内核在调用插件时的**守门人**，
# 因为「插件写错了」和「内核押错了」必须分得开（C5 的整个判定靠这个区分）。

class PluginContractError(RuntimeError):
    """插件违反了 §I1–§I7 的形状要求。**修插件，不修内核。**"""


def validate_plugin(plugin: Any) -> list[str]:
    """静态形状检查 —— 七个方法在不在、`name` 有没有。返回缺失项。"""
    missing: list[str] = []
    for meth in ("hit", "merge", "penalty", "split", "encode", "decode", "signal", "record"):
        if not callable(getattr(plugin, meth, None)):
            missing.append(meth)
    if not isinstance(getattr(plugin, "name", None), str):
        missing.append("name")
    return missing


def call_hit(plugin: DirectionPlugin, d: Direction, query: Any) -> Tri:
    """调 §I1，并把**异常**折成「未展开」。

    纪律 1：插件抛异常 = 中断遍历 = 实际效果是假阴。
    但**折成「未展开」而不是「否」** —— 「否」是危险的那一侧（§K8）。
    同时记一笔，让 B1 之外还能看到「有插件在抛异常」。
    """
    try:
        out = plugin.hit(d, query)
    except Exception:  # noqa: BLE001 - 故意的：插件是外部代码
        return Tri.UNEXPANDED
    if not isinstance(out, Tri):
        raise PluginContractError(
            f"§I1 必须返回 Tri，插件 {plugin.name!r} 返回了 {type(out).__name__}"
        )
    return out


def call_penalty(plugin: DirectionPlugin, d: Direction, item: Any) -> float:
    """调 §I3，并校验「可比较」—— 非数 / NaN / inf 都是不可比。"""
    try:
        out = plugin.penalty(d, item)
    except Exception as exc:  # noqa: BLE001
        raise PluginContractError(f"§I3 抛异常：{exc}") from exc
    if not isinstance(out, numbers.Real):
        raise PluginContractError(
            f"§I3 必须返回可比较的数，插件 {plugin.name!r} 返回了 {type(out).__name__}"
        )
    val = float(out)
    if val != val or val in (float("inf"), float("-inf")):
        raise PluginContractError(f"§I3 返回了不可比的数：{val}")
    return val


def call_split(plugin: DirectionPlugin, parent: Direction,
               items: Sequence[Any]) -> tuple[Any, ...] | None:
    """调 §I4，并把「**分不开**」与「**违约**」分开。

        None          插件声明分不开 —— **正常**返回，不是错误
        元组(≥2 个)   候选切分
        其他          `PluginContractError`

    为什么把「少给」判成**违约**而不是「当成分不开」：

        当成分不开   插件写错了（返回 `()`、返回单个候选）会被**静默**记成
                     「插件说它分不开」—— 于是**插件有没有在说话**这件事查不出来
       判成违约     插件必须**显式**说 `None`；说了什么、没说什么，账本分得清

    这正是「空转与通过长得一模一样」在接口层的形态：
    一个**永远返回空**的插件与一个**诚实声明分不开**的插件，
    在「当成分不开」的读法下产出完全相同的账本。

    ⚠️ 不折异常。`§I1` 抛异常折成「未展开」是因为**遍历不能中断**；
       `§I4` 是**建树**路径，抛在这里说明插件根本没法用，
       悄悄吞掉只会让树长成另一个样子而没人知道。
    """
    try:
        out = plugin.split(parent, items)
    except PluginContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - 插件是外部代码
        raise PluginContractError(f"§I4 抛异常：{type(exc).__name__}: {exc}") from exc

    if out is None:
        return None
    if isinstance(out, (str, bytes, bytearray)) or not isinstance(out, Sequence):
        raise PluginContractError(
            f"§I4 必须返回 None 或一组候选方向，插件 {plugin.name!r} "
            f"返回了 {type(out).__name__}"
        )
    group = tuple(out)
    if len(group) < 2:
        raise PluginContractError(
            f"§I4 返回了 {len(group)} 个候选 —— 少于 2 个请返回 None（声明分不开），"
            f"不要给一个切不开的组。插件 {plugin.name!r}"
        )
    return group
