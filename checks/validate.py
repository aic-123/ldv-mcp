"""发货自带的判据 —— 这个插件与「随便包一层」的区别就在这。

三条，都是**机制**不是 prompt：

    ① 键校验   查询里的每个键必须已在词汇表里 —— 未知键当场拒，
               不许「猜一个最近的」（入口自己也拒，这里是产品的第一道闸）
    ② 插件白名单  冻结声明里的插件名必须在已知集合里
    ③ 支序原样   中转给调用方的支序必须与内核返回的逐字相同 ——
                agent 重排 = 违规，且**可判**

⚠️ 这三条都不依赖「agent 听话」。它们在任何一环被绕过时会让结果**变红**，
   而不是变安静 —— 这是本产品唯一站得住的差异点。
"""
from __future__ import annotations

from typing import Any, Sequence

#: 内核已知的三个方向函数（= 三个预设）
KNOWN_PLUGINS = ("keyset", "reach", "sequence")


class CheckError(RuntimeError):
    """产品的判据红了 —— 调用方拿到的是一条**错**，不是一个静默的错误结果。"""


def check_keys(all_keys: Sequence[str], require: Sequence[str],
               forbid: Sequence[str]) -> dict:
    """① 键校验。返回 {ok, unknown, suggest}。

    ⚠️ 「键写错了」与「材料里真没有」在界面上长得一模一样 —— 所以这里必须
       **当场拒**，并把差多少印出来（difflib 的近似只当「可能拼错」的提示，
       不拿来替换 —— 替换就是替用户决定他问的是什么）。
    """
    import difflib

    known = set(all_keys)
    unknown = sorted((set(require) | set(forbid)) - known)
    if not unknown:
        return {"ok": True, "unknown": []}
    suggest = {k: difflib.get_close_matches(k, sorted(known), n=3, cutoff=0.5)
               for k in unknown}
    return {"ok": False, "unknown": unknown, "suggest": suggest,
            # ★ 2026-10-24 用户定：**不报「拒收」、不打断用户** ——
            #   缺口交给 agent 去补。这里只给 agent 一句「你能怎么补」。
            "can_agent_fill": True,
            "next_step": ["derive 一个维度（把这类取值建成可查的轴，带理由与来源）",
                          "或映射到 vocabulary 里已有的键（换个问法）"],
            "why": "这些键不在词汇表里 —— 可能是拼错了；若确实没有这个维度，"
                   "agent 自己补一个就行（derive），不用打断用户"}


def check_plugin(name: str) -> None:
    """② 插件白名单。"""
    if name not in KNOWN_PLUGINS:
        raise CheckError(f"未知的方向函数 {name!r} —— 只认 {list(KNOWN_PLUGINS)}"
                         "（要加新的，先在内核里实现并过它的判据，再加进来）")


def check_order(original: Sequence[Any], relayed: Sequence[Any]) -> None:
    """③ 支序原样。原样 = 同一个序列，不是一个集合。

    ⚠️ 只比集合会放过「重排」—— 而重排在本产品里就是**推荐**，
       那是红线（排序即推荐，本层不做推荐器）。
    """
    if list(original) != list(relayed):
        raise CheckError(
            f"中转时支序被改了：原 {[getattr(x, 'did', x) for x in original][:6]}…"
            f"→ 发出去 {[getattr(x, 'did', x) for x in relayed][:6]}…"
            "（支序必须原样 —— 重排即推荐）")
