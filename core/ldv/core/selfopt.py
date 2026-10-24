"""自优化（§F0–§F4）—— 设计文档 §8.2 的**边界**落在这里。

    F0 收集使用记录
    F1 **内核**填倾向权重（缺权重的不许进 F2）
    F2 更新**内生**量
    F3 断言**外生项未被改动**
    F4 断言**无跨方向的全局评分**

---

## 为什么这个文件很短，而且**只**能动内生量

    方向函数是插件 ⇒ 选哪个插件能否也按 usage 优化？
    ⇒ 那「优化它」需要目标 ⇒ 那个目标又按 usage 优化？⇒ 无限递归

停的地方就是模块化的边界：

| 信号 | 内生 / 外生 | 处置 |
|---|---|---|
| 层内哪个方向值得展开 | 内生 | 可自我优化 |
| 层内局部权衡 | 内生 | 放进 §I3 |
| **什么算一个方向** | **外生** | **必须人声明** |
| **最外层意图** | **外生** | **必须人声明** |

外生项一旦允许自我优化，会**同时**失去：

- **可复现性** —— 同数据、不同历史 ⇒ 不同结构
- **可解释性** —— 说不出为什么长成这样

而且这两样是**很久以后才发现**丢了，那时已经回溯不回去。

⇒ 所以这里**没有** `objective`、没有 `score`、没有跨方向的全局量（§4.3 / §B12）。
   权衡在 `§I3 代价` 和 `§I4 劈开` 里**各自**做，内核不参与折算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 外生项 —— **人声明**，本模块只读不写
EXOGENOUS_KEYS = ("direction_functions", "outermost_intent")
# 内生项 —— 本模块可以改
ENDOGENOUS_KEYS = ("expand_priority", "penalty_scale")


class ExogenousBoundaryError(RuntimeError):
    """有人试图用使用反馈去改外生项 —— §K9。"""


class FrozenExogenous(dict):
    """外生项容器 —— **只读**。

    ⚠️ 为什么是「写的那一刻就炸」而不是「最后比一下指纹」：

       比指纹只能抓住**在 `optimize` 内部**发生的改动。
       调用前就被改过的，`optimize` 看不见 —— 它没有过去。
       实测：把「先改再调」写成测试，指纹比对**放行了**。

       而 §K9 的代价是「可复现性与可解释性同时丢失，**很久以后才发现**」。
       那种错误必须在**写的那一刻**炸，不能等到复盘 —— 到那时已经回溯不回去。

    ⇒ 指纹比对保留（纵深防御），但**真正的守卫是这里**。
    """

    def _blocked(self, *a: object, **k: object) -> None:
        raise ExogenousBoundaryError(
            "外生项是只读的（§K9）：什么算一个方向、最外层意图，"
            "必须人声明，不许被使用反馈改"
        )

    __setitem__ = _blocked
    __delitem__ = _blocked
    update = _blocked
    pop = _blocked
    popitem = _blocked
    clear = _blocked
    setdefault = _blocked

    def copy(self) -> "FrozenExogenous":       # 复制出来仍是只读的
        return FrozenExogenous(self)


@dataclass
class Params:
    """参数表。**外生 / 内生分开放** —— 分不开就没法守边界。"""

    exogenous: dict[str, Any] = field(default_factory=dict)
    endogenous: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.exogenous, FrozenExogenous):
            # `dict.__init__` 走 C 层更新，不经过被覆盖的 `__setitem__`，
            # 所以这里能安全地「冻住」
            self.exogenous = FrozenExogenous(self.exogenous)

    def fingerprint_exogenous(self) -> tuple:
        return tuple(sorted((k, repr(self.exogenous.get(k))) for k in EXOGENOUS_KEYS))

    def fingerprint_endogenous(self) -> tuple:
        return tuple(sorted((k, repr(self.endogenous.get(k))) for k in ENDOGENOUS_KEYS))


def collect(kernel: Any) -> tuple[list[Any], int]:
    """F0 + F1：收记录，并**拦住**缺倾向权重的那些。

    返回 `(带权记录, 被拦下的条数)`。被拦下不是「当作 0 处理」——
    是**不许进入 F2**（§K7 / §B11）。
    """
    ok: list[Any] = []
    blocked = 0
    for rec in kernel.usage:
        if getattr(rec, "propensity", None) is not None and 0.0 < rec.propensity <= 1.0:
            ok.append(rec)
        else:
            blocked += 1
    return ok, blocked


def tendency_by_direction(kernel: Any) -> dict[str, float]:
    """每个方向的**倾向加权均値** —— `Σ (outcome / propensity) / 条数`。

    ## ★ 一处定义、两处用（`expand_priority` 与检索器层的「按使用细调」）

    `optimize`（F2）用它更新 `expand_priority.{did}`；
    `retriever` 用它给视图块排序（`docs/分层方向视图-检索器层.md` §14.7）。
    ⚠️ **两处各写一份会漂移** —— 而漂移了没有任何东西看得出来（都是浮点数）。

    ## ⚠️ 为什么这是**局部**量、不违 `E-1`

        它**按方向分别算**（键是 `did`），**不跨方向求平均** ——
        跨方向求平均就是全局评分（`§B12` / `§4.3`）。
        ⇒ 与 `§I3 代价` 同一种形状：**局部方法**，不是全局标量。

    只收**倾向权重合法**的记录（`§K7` / `B11`）：`None` / `0` / `>1` 一律**不收**
    （「缺权重」与「权重为 0」不是一回事，见 `collect`）。没有任何合法记录的方向
    **不出现**在这个字典里 —— 调用方**不许**把「没有」当成 0 去比较（`§K6` 同源）。
    """
    recs, _blocked = collect(kernel)
    per: dict[str, list[float]] = {}
    for r in recs:
        per.setdefault(r.did, []).append(r.outcome / r.propensity)
    return {did: sum(vals) / len(vals) for did, vals in sorted(per.items())}


def optimize(kernel: Any, params: Params) -> tuple[Params, dict[str, Any]]:
    """F1–F4。**只动内生量**，并自带两条断言。"""
    before_exo = params.fingerprint_exogenous()
    recs, blocked = collect(kernel)

    # --- F2：更新内生量 -------------------------------------------------
    # 倾向加权的平均结果 —— **按方向分别更新**，不是算一个全局分。
    # 「按方向分别更新」是这里唯一允许的形状：跨方向求平均就是全局评分（§B12）。
    endogenous = dict(params.endogenous)
    for did, val in tendency_by_direction(kernel).items():
        endogenous[f"expand_priority.{did}"] = val
    endogenous["penalty_scale"] = float(len(recs))

    after = Params(exogenous=dict(params.exogenous), endogenous=endogenous)

    # --- F3：外生项未被改动 ---------------------------------------------
    if after.fingerprint_exogenous() != before_exo:
        raise ExogenousBoundaryError(
            "自优化改动了外生项 —— §K9 违规：可复现性与可解释性同时丢失"
        )

    # --- F4：无跨方向的全局评分 ------------------------------------------
    banned = [k for k in after.endogenous if k in ("score", "objective", "global", "total")]
    if banned:
        raise ExogenousBoundaryError(f"内生量里出现跨方向全局评分：{banned}（§4.3 / §B12）")

    return after, {
        "带权记录": len(recs),
        "被拦下（缺倾向）": blocked,
        "更新到的方向数": len(tendency_by_direction(kernel)),
        "外生项指纹": before_exo,
    }
