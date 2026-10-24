"""`§I4` 带上父方向的对照测量（**只报不判**）。

    python -m ldv.tools.measure_parent

三个变体，**单变量**逐级对照 —— 每一级只差一处：

    ① 不续 `forb`                = 带上父方向之前
    ② 续 `forb`，`req` 掺 `common` = 带上父方向之后、本轮修之前
    ③ 续 `forb`，`req` 只续父      = 本轮改后

预测（设计文档 §10 写的）：「能让 A 的过滤器真正嵌套、**假阳率大概率下降**」。
可证伪的三条：

    覆盖嵌套    `覆盖(子) ⊆ 覆盖(父)` 的越界数
    假阳率      A 的假阳率
    ★ 健全性    维护路径上 `members(d) ⊆ 覆盖(d)` 的越界成员数（`§1` 硬要求表第三行）

⚠️ **第三列是本次复核挖出来的东西** —— 前两条都只量「一次建完」，
   而健全性在两条路径上**不是同一件事**：一次建完时成员集在首次展开时就完整，
   维护路径上成员是后来才长的、payload 已冻结。见 `C10-签名改动复核.md`。

B / C 是**反向判据**：带上父方向**不该动**它们的数。
"""

from __future__ import annotations

import sys
from typing import Any, Sequence

from ..checks._fixtures import (coverage_of, items as make_items,
                                keyset_queries, load, sequences)
from ..checks.contract import cover_nesting_profile, false_positive_profile
from ..checks.coverage import soundness_profile
from ..core.kernel import Kernel
from ..plugins.keyset import KeysetPlugin


class NoForbInherit(KeysetPlugin):
    """① **带上父方向之前**：`split` 连父都拿不到，`forb` 从空开始。"""

    def split(self, parent: Any, items: Sequence[Any]) -> Any:  # noqa: ANN401
        keysets = [frozenset(it["keys"]) for it in items]
        common = frozenset.intersection(*keysets) if keysets else frozenset()
        universe = frozenset().union(*keysets) if keysets else frozenset()
        cand = sorted(universe - common)
        if not cand:
            return None
        n = len(items)
        best = min(cand, key=lambda k: (abs(sum(1 for ks in keysets if k in ks) / n - 0.5), k))
        return ((common | {best}, frozenset()), (common, frozenset({best})))


class CommonInReq(KeysetPlugin):
    """② **带上父方向之后、本轮修之前**：`forb` 续上了，`req` 里还掺着 `common`。"""

    def split(self, parent: Any, items: Sequence[Any]) -> Any:  # noqa: ANN401
        p_req, p_forb = parent.payload
        keysets = [frozenset(it["keys"]) for it in items]
        common = frozenset.intersection(*keysets) if keysets else frozenset()
        universe = frozenset().union(*keysets) if keysets else frozenset()
        cand = sorted(universe - common)
        if not cand:
            return None
        n = len(items)
        best = min(cand, key=lambda k: (abs(sum(1 for ks in keysets if k in ks) / n - 0.5), k))
        req = p_req | common
        return ((req | {best}, p_forb), (req, p_forb | {best}))


VARIANTS: tuple[tuple[str, type], ...] = (
    ("① 不续 forb（带上父方向之前）", NoForbInherit),
    ("② 续 forb，req 掺 common（改前）", CommonInReq),
    ("③ 续 forb，req 只续父（改后）", KeysetPlugin),
)


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 未展开（跳过），这不是通过。")
        return 0
    nodes, edges, _ = loaded
    all_items = make_items(nodes)
    ids = sorted(nodes)
    qs = keyset_queries(nodes)

    def build(cls: type, init: list[str], later: list[str]) -> tuple[Kernel, Any]:
        plug = cls()
        k = Kernel(plug, {i: all_items[i] for i in init})
        k.build(plug.merge([]))              # 根 = (∅, ∅)：与 `checks/_fixtures` 一致
        for i in init:
            k.insert(i)
        for i in later:
            k.insert(i, all_items[i])
        return k, plug

    # ── 表一：一次建完 ────────────────────────────────────────────────────
    print("═══ 表一 · 一次建完 36 项 ═══\n")
    print(f"{'变体':<34}{'覆盖越界':>9}{'已展开':>7}{'判「是」':>9}{'假阳':>7}{'假阳率':>9}")
    for label, cls in VARIANTS:
        k, plug = build(cls, ids, [])
        cn = cover_nesting_profile(k, plug, qs)
        fp = false_positive_profile(k, plug, qs)
        print(f"{label:<34}{cn['越界次数']:>9}{cn['已展开方向数']:>7}"
              f"{fp['判「是」总数']:>9}{fp['其中假阳']:>7}{fp['假阳率']:>8.1%}")

    # ── 表二：健全性（两条路径 × 三种建法）────────────────────────────────
    print("\n═══ 表二 · `members(d) ⊆ 覆盖(d)`（§1 硬要求表第三行）═══\n")
    print("    越界成员数 / 成员总数。**一次建完与维护路径不是同一件事。**\n")
    builds: tuple[tuple[str, list[str], list[str]], ...] = (
        ("一次建完 36", ids, []),
        ("先建 2 维护 34", ids[:2], ids[2:]),
        ("先建 12 维护 24", ids[:12], ids[12:]),
    )
    head = f"{'变体':<34}" + "".join(f"{b[0]:>18}" for b in builds)
    print(head)
    for label, cls in VARIANTS:
        row = f"{label:<34}"
        for _, init, later in builds:
            k, plug = build(cls, init, later)
            sp = soundness_profile(k, coverage_of("keyset", nodes, edges))
            row += f"{sp['越界成员数']:>10}/{sp['成员数']:<7}"
        print(row)

    # ── 表三：方向数随初始批大小 ──────────────────────────────────────────
    print("\n═══ 表三 · 方向数随初始批大小（记账；叶容量一律 10）═══\n")
    print(f"{'变体':<34}{'全量':>8}" + "".join(f"{f'k={k}':>8}" for k in (2, 6, 12, 30, 35)))
    for label, cls in VARIANTS:
        full, _ = build(cls, ids, [])
        row = f"{label:<34}{len(full.all_directions()):>8}"
        for k in (2, 6, 12, 30, 35):
            kk, _ = build(cls, ids[:k], ids[k:])
            row += f"{len(kk.all_directions()):>8}"
        print(row)

    print("\n⚠️ 表三读法：② 的**方向数随初始批大小变**，③ 不变。")
    print("   变的那个不是「记账差异」，是 `劈开` 自己切不动了 ——")
    print("   `common` 掺进 `req` 之后，两个子方向对某些项**代价打平**，")
    print("   平手判给靠前那个 ⇒ 一侧为空 ⇒ `§K2` 判「切不动」⇒ 这一层不建。")
    print("   实测（先建 2）：② 出现 3 次「切不动」，方向数比全量少 2；③ 为 0 次。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
