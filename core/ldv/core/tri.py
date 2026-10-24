"""三态 —— 设计文档 §K6 / §B10。

「未展开」是**独立的第三值**，不许折叠为「否」。

⚠️ 所以 `Tri` **不是** `bool`：
    `bool(Tri.UNEXPANDED)` 会**抛异常**，而不是悄悄变成 `False`。

arena 的「0 和『没有』分不开，就不许印成 0」在这里变成
「**未展开 和 否 分不开，就不许印成 否**」。

同一个毛病在别处的形态（dce 的 `STATUS.md` 里记过）：把「跳过」计入「通过」。
所以这里连**计数**都是分开的 —— 见 `checks/_framework.py` 的 `Report`。
"""

from __future__ import annotations

import enum


class Tri(enum.Enum):
    """`命中` 的返回值，也是每条检查的返回值。"""

    YES = "是"
    NO = "否"
    UNEXPANDED = "未展开"

    def __bool__(self) -> bool:  # pragma: no cover - 故意抛错
        raise TypeError(
            "Tri 不是 bool —— 「未展开」不能当 False 用（§K6）。"
            "要判断请显式写 `is Tri.YES` / `is Tri.NO` / `is Tri.UNEXPANDED`。"
        )

    def __str__(self) -> str:
        return self.value


def is_bad_hit(value: Tri) -> bool:
    """`命中` 说「否」是**危险**的那个分支（§K8：假阴禁止）。

    「是」错了只损失性能，「未展开」是诚实的不知道，
    只有「否」错了会**漏掉真结果**。
    """
    return value is Tri.NO


def fold(*values: Tri) -> Tri:
    """把若干三态折成一个 —— **只在明确允许折叠的地方用**。

    规则（按危险度从低到高）：
        全「是」      → 是
        有「未展开」  → 未展开      ← 不许降级成「否」
        全「否」      → 否
        混合「是/否」 → 未展开      ← 不许因为「有否」就判否

    最后一条是刻意的保守：**说不清就说不清**，
    因为「否」是唯一会漏结果的分支。
    """
    vals = set(values)
    if vals == {Tri.YES}:
        return Tri.YES
    if vals == {Tri.NO}:
        return Tri.NO
    return Tri.UNEXPANDED
