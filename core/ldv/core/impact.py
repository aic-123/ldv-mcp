# -*- coding: utf-8 -*-
"""影响分析（反事实）—— **只算不改**（2026-10-17）。

## 它回答什么问题

维护时最常问的两句：

    删掉这几个节点，哪些方向会**变空**？
    某个方向要是崩了，**影响面**多大？

这两句都是**纯结构**的（只看 `members_of` 与 `children_of`），
**不碰语义、不依赖历史** ⇒ 可复现、可写成判据。

## 为什么这是「推理」而不是「统计」

    预测：删掉 x ⇒ 方向 D3 的成员集减去 {x} 是空集 ⇒ **D3 会变空**
    实测：真的删掉 x ⇒ D3 确实空了（`remove` 会把它标成「已失效」）

**预测与实测对得上**，才叫推理。判据就是拿这两者比（`test_impact` ㊿）。

## 边界

- **只读**：不调 `insert` / `remove` / `expand`，结构签名前后逐字相同（判据 ㊾）
- **不预测新方向**：删掉项不会「长出新方向」，只可能让方向变空/变小
- **不碰语义**：不问「这个节点重不重要」，只问「删了结构会怎样」
"""
from __future__ import annotations

from typing import Any, Iterable


def removal_impact(kernel: Any, ids: Iterable[str]) -> dict[str, Any]:
    """删掉 `ids` 这些项，结构会怎么变（**只算不改**）。

    返回：
    - `会变空`：成员集**恰好**等于待删集的那些方向（删完就空）
    - `成员减少`：与待删集有交集、但删完不空的方向（含减少数）
    - `不在结构里`：待删集里**压根没进结构**的项（删它等于删空气）
    - `影响项数`：真正在结构中的待删项数
    """
    to_remove = {str(x) for x in ids}
    placed = set(kernel.placed)
    in_struct = to_remove & placed
    absent = sorted(to_remove - placed)

    emptied: list[dict[str, Any]] = []
    shrunk: list[dict[str, Any]] = []
    for d in kernel.all_directions():
        mem = set(kernel.members_of(d))
        hit = mem & in_struct
        if not hit:
            continue
        if not (mem - in_struct):
            emptied.append({"方向": d.did, "原成员": len(mem),
                            "全部被删": len(hit)})
        else:
            shrunk.append({"方向": d.did, "原成员": len(mem),
                           "减少": len(hit), "剩": len(mem) - len(hit)})

    return {
        "待删": len(to_remove),
        "影响项数": len(in_struct),
        "不在结构里": absent,
        "会变空": emptied,
        "会变空数": len(emptied),
        "成员减少": shrunk,
        "成员减少数": len(shrunk),
        # ★ 「影响 0」也要明说 —— 否则「没算」与「算了没影响」长得一样
        "说明": ("没有任何方向受影响" if not emptied and not shrunk
                 else f"{len(emptied)} 个方向会变空、{len(shrunk)} 个方向会缩"),
    }


def blast_radius(kernel: Any, did: str) -> dict[str, Any]:
    """一个方向「崩了」的影响面：它的**整个子树**有多少方向、覆盖多少项。

    这是 `removal_impact` 的补集视角 —— 前者问「删项」，这里问「删方向」。
    """
    d = kernel.direction(did)
    subtree: list[str] = []
    stack = [d]
    while stack:
        cur = stack.pop()
        subtree.append(cur.did)
        stack.extend(kernel.children_of(cur))
    members: set[str] = set()
    for x in subtree:
        members |= set(kernel.members_of(kernel.direction(x)))
    # ⚠️ **不报「占比」**（2026-10-17 被 `B12` 当场抓住）：
    #    占比 = 子树规模 / 全树规模 —— 那个分母是「跨方向的全局总数」，
    #    而**归一化就是打分的第一步**（`评估标准.md` 的「不作分」红线）。
    #    ⇒ 只报**绝对数**，比例由调用方自己按需算。
    return {
        "方向": did,
        "子树方向数": len(subtree),
        "覆盖项数": len(members),
        "子树": sorted(subtree),
    }


def compare_prediction(kernel: Any, ids: Iterable[str]) -> dict[str, Any]:
    """**预测 vs 实测**：先算影响，再在**同一份内核**上真删，对比。

    ⚠️ 会**改内核**（真删）—— 调用方要给一份**深拷贝**，否则结构被改掉。
    ⚠️ 「实测」看的是 `remove` 之后**成员数是否归零**（`remove` 会把空方向标成
       「已失效」，但方向对象仍在 `all_directions` 里）。
    """
    pred = removal_impact(kernel, ids)
    target = [str(x) for x in ids]
    before = {d.did: len(kernel.members_of(d)) for d in kernel.all_directions()}
    # ⚠️ `kernel.remove` **一次只收一个 id**（签名是 `remove(item_id: str)`）
    for x in target:
        kernel.remove(x)
    after = {d.did: len(kernel.members_of(d)) for d in kernel.all_directions()}

    # 预测会空的，实测是否真的 0
    pred_empty = {x["方向"] for x in pred["会变空"]}
    actual_empty = {did for did, n in after.items() if n == 0 and before.get(did, 0) > 0}
    pred_shrunk = {x["方向"] for x in pred["成员减少"]}
    actual_shrunk = {did for did, n in after.items()
                     if 0 < n < before.get(did, 0)}

    return {
        "预测会空": sorted(pred_empty),
        "实测会空": sorted(actual_empty),
        "会空一致": pred_empty == actual_empty,
        "预测缩": sorted(pred_shrunk),
        "实测缩": sorted(actual_shrunk),
        "缩一致": pred_shrunk == actual_shrunk,
        "预测": pred,
    }
