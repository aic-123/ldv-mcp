"""版本访问 —— `§10.2 D` 决策表里那一行「每隔 k 版存一份（混合）」。

---

## 这一层要守的那一句话

    访问版本 i 的代价是 **O(i/k + k)**，不是 **Θ(i)**。

`§10.2 D` 把六种形态摆在一起比（Driscoll 等 1989 / Overmars 第二条办法）：

    形态              空间              访问版本 i      本设计
    ───────────────────────────────────────────────────────────
    显式存每版        Θ(n)/次更新        O(1)           否
    只存账 + 重放     O(m)              **Θ(i)**       ★ 原来的形态（**没有实现**）
    每隔 k 版存一份   O(m) + 快照       **O(i/k + k)**  ★ 本模块
    fat node          O(1)/步            O(log m)       否
    node-copying      O(1)/步（摊还）     O(1)           否

★ 「只存账 + 重放」的病灶不是「慢」，是**代价随 i 线性增长**：
  访问版本 1 与访问版本 1000 差 1000 倍。而**只看「有没有这个能力」时两者长得一样** ——
  这正是本项目一直在防的那个形状，只不过这次出现在**代价**上。
  ⇒ 本模块把重放步数从 `i` 压到 `< every`，**与 i 无关**。

## 混合 = 操作账 + 定期快照

    `_ops`      操作账：每一次变更**原样记下**（`build` / `insert` / `remove` / `expand`）
    `_snaps`    版本号 → 全量快照（`persist.to_dict`），每 `every` 个版本一份

    `at(i)` = 取 `j = (i // every) * every` 那份快照，再重放 `_ops[j:i]`

⇒ 重放步数 **< every**。`i` 再大也不多花一步。

⚠️ **版本 0 没有快照**：它是**空内核**（还没 `build`），而 `to_dict` 需要
   `kernel.root` —— 空内核没有根，`to_dict` **会抛**。
   ⇒ `at(i)` 在 `j == 0` 时从**当场新建的空内核**起跑（`i < every` ⇒ 重放 i 步，
   仍 `< every`）。这不是特例补丁，是「版本 0 = 还没建根」这件事的**唯一正确形状**。

---

## ⚠️ 为什么必须**显式记账**，不能从 `ledger` 反推

内核的 `ledger` 是**事件**账（`EVENT_BORN` / `EVENT_STAYED` / …），不是**操作**账：

    一次 `insert` 会产生**若干**事件（落到哪个方向 / 有没有分裂 / …）
    而事件的字段里**没有** `insert` 的那个 `item_id` —— `EVENT_BORN` 只记
    `did` / `rank` / `origin` / `parent`。

⇒ 同一个 `ledger` 可能由**不同的操作序列**产生 ⇒ 从它反推操作序列**不成立**。
   ⇒ 所以记账必须发生在**调用点**：本模块**代理**内核的四个变更方法，
     调用方改走 `VersionStore`（`store.insert(...)` 而不是 `kernel.insert(...)`）。

⚠️ **内核本身不知道版本**。这是刻意的：`§4.3` 说内核构造只收一个插件和一批项，
   多一个 `version=` / `history=` 参数就该 `B12` 红。版本是**调用方**的能力，
   与并发（`§10.2 D` 的 P8）同一个定位 —— **内核不知道锁，也不知道版本**。

---

## ⚠️ 快照与账**不许各存一份真相**

`_snaps[j]` 与「重放 `_ops[:j]`」必须给出**同一个结构**。本模块**不信任**快照 ——
判据 `V1` 拿**两条路**对拍：

    ① `at(i)`              快照 + 重放
    ② 从**空内核**重放 `_ops[:i]`   纯重放，**一个快照都不用**

两条的指纹（`persist._fingerprint`）必须**逐字相同** ⇒ 快照漂移 ⇒ 红。

★ 这与 `persist` 的纪律同源：`parent` / `witness` 不独立存，因为**两份真相会漂移**；
  这里同理 —— 快照是**缓存**，账才是**真相**，缓存与真相必须对得上。
"""

from __future__ import annotations

from typing import Any

from .kernel import Kernel
from .persist import from_dict, to_dict

#: 默认快照间隔 —— 每隔多少版存一份全量快照。
#:
#: ⚠️ 这是**空间 / 时间**的权衡，不是「越大越好」：
#:
#:     `every` 大 ⇒ 快照少（空间小）、重放多（访问慢）
#:     `every` 小 ⇒ 快照多（空间大）、重放少（访问快）
#:
#: 最优点在 `every ≈ sqrt(版本数)`（那样空间与重放各 ~sqrt），
#: 但版本数**事先不知道**（调用方随时可以继续改）⇒ 取一个常数。
#: 8 是权衡下的默认值，**不是**判据的一部分 —— 判据 `V3` 只断言
#: 「重放步数 < every」，不关心 every 取多少（`V4` 才去动它）。
DEFAULT_EVERY = 8


class VersionStore:
    """操作账 + 定期快照 —— 见模块 docstring。

    ## 用法

        store = VersionStore(plugin, items)
        store.build(root_payload)        # 版本 1
        store.insert("x1")               # 版本 2
        store.expand(d)                  # 版本 3
        old = store.at(2)                # 拿回版本 2 的**内核**（可查、可继续用）
        store.kernel                     # 当前内核（版本 store.versions）

    ⚠️ **变更只许走 store**：`store.kernel.insert(...)` 能跑，但**不进账**
    ⇒ 那一版就**回看不到**。这是本模块的**唯一**使用纪律，
    代价是调用点要从 `kernel.insert` 改成 `store.insert`。
    """

    def __init__(self, plugin: Any, items: dict[str, Any],
                 every: int = DEFAULT_EVERY) -> None:
        if every < 1:
            raise ValueError(f"快照间隔 every 至少为 1，收到 {every!r}")
        self._plugin = plugin
        self._items = dict(items)
        self._every = every
        self._kernel = Kernel(plugin, self._items)
        #: 操作账 —— **真相**。第 `i` 个操作把状态从版本 `i` 推到版本 `i+1`。
        self._ops: list[tuple] = []
        #: 版本号 → 快照 —— **缓存**。只有 `every` 的**正倍数**在里面
        #: （版本 0 不在：它是空内核，`to_dict` 需要一个根，见模块 docstring）。
        self._snaps: dict[int, dict] = {}
        #: 上一次 `at()` **重放了几步** —— **读数**（判据 `V3` / `V4` 读它，
        #: 不进退出码）。它必须**印得出来**：否则「快照生效」与「每版都从头重放」
        #: 在只看结果时长得一模一样。
        self._last_replay = 0

    # --- 属性 -------------------------------------------------------------

    @property
    def every(self) -> int:
        """快照间隔。"""
        return self._every

    @property
    def versions(self) -> int:
        """版本总数 —— 版本 `0`（空内核）到版本 `versions`（当前）共 `versions+1` 个。"""
        return len(self._ops)

    @property
    def kernel(self) -> Kernel:
        """**当前**内核（= 版本 `versions`）。"""
        return self._kernel

    @property
    def last_replay(self) -> int:
        """上一次 `at()` 重放了几步 —— **读数**。"""
        return self._last_replay

    def snapshot_versions(self) -> tuple[int, ...]:
        """盘上有快照的那些版本号（升序）—— 读数 / 判据用。"""
        return tuple(sorted(self._snaps))

    # --- 变更入口（代理内核 + 记账）----------------------------------------

    def build(self, root_payload: Any):
        """建根（版本 +1）。"""
        return self._record(("build", root_payload))

    def insert(self, item_id: str, item: Any = None):
        """插入一个项（版本 +1）。"""
        return self._record(("insert", item_id, item))

    def remove(self, item_id: str):
        """删一个项（版本 +1）。"""
        return self._record(("remove", item_id))

    def expand(self, d):
        """展开一个方向（版本 +1）。**记的是 `d.did`**，不是 `Direction` 对象 ——
        重放时从**当场的**内核里取回那个方向（对象身份会变，编号不会）。"""
        return self._record(("expand", d.did))

    def _record(self, op: tuple) -> Any:
        out = _apply(self._kernel, op)
        self._ops.append(op)
        v = len(self._ops)
        if v % self._every == 0:
            self._snaps[v] = to_dict(self._kernel)
        return out

    # --- 版本访问 ---------------------------------------------------------

    def at(self, i: int) -> Kernel:
        """**访问版本 `i`** —— 最近快照 + 重放余数。

        ⚠️ 重放步数 = `i - (i // every) * every` < `every`，**与 `i` 无关**。
        这个数记在 `last_replay` 里（读数）。
        """
        if not 0 <= i <= len(self._ops):
            raise IndexError(
                f"版本 {i} 不在 0..{len(self._ops)} —— "
                "「越界」与「版本 0 是空内核」必须分得开，所以这里报错"
            )
        j = (i // self._every) * self._every
        if j == 0:
            # 版本 0 = 空内核，没有快照（`to_dict` 需要根，见模块 docstring）。
            k = Kernel(self._plugin, dict(self._items))
        else:
            k = from_dict(self._snaps[j], self._plugin)
        for op in self._ops[j:i]:
            _apply(k, op)
        self._last_replay = i - j
        return k


# ═══ 重放一个操作 ═════════════════════════════════════════════════════════════
#
# 抽成模块级函数，`at()` 与对照（`replay_ops`）**共用**它 —— 两条路的差别只在
# **要不要用快照**，不在「操作怎么重放」。差别若也混进来，「快照漂移」与
# 「重放写错」就分不开了。

def _apply(kernel: Kernel, op: tuple) -> Any:
    name = op[0]
    if name == "build":
        return kernel.build(op[1])
    if name == "insert":
        return kernel.insert(op[1], op[2])
    if name == "remove":
        return kernel.remove(op[1])
    if name == "expand":
        # ★ 按**编号**取回方向：重放出的内核是新造的，`Direction` 对象身份不同。
        return kernel.expand(kernel.direction(op[1]))
    raise ValueError(f"不认识的操作 {name!r} —— 账里只有 build / insert / remove / expand")


def replay_ops(plugin: Any, items: dict[str, Any],
               ops: list[tuple], upto: int | None = None) -> Kernel:
    """**对照**：从空内核纯重放 `ops[:upto]` —— **一个快照都不用**。

    ⚠️ 它是**对照**，不是备选方案。作用：判据 `V1` 拿它与 `VersionStore.at(i)` 对拍，
       把「快照漂移」这一种错**单独**逼出来。没有它，「at(i) 对不对」只能靠
       人眼看 —— 而人眼看不出的漂移正是快照最可能的坏法。
    """
    k = Kernel(plugin, dict(items))
    for op in (ops if upto is None else ops[:upto]):
        _apply(k, op)
    return k


# ═══ 三个「错一处」的对照实现 ═════════════════════════════════════════════════
#
# ⚠️ 它们是**对照**，不是备选方案（与 `persist._to_dict_storing_derived` /
#    `_from_dict_trusting_derived` 同一个定位）：每一个**只多错一件事**，
#    让判据 `V1` / `V2` / `V3` 各有一个**已知答案**去对照。
#    ⇒ 「空转与通过长得一模一样」，不配对照就分不开。

class _NoSnapshots(VersionStore):
    """对照①：**一个快照都不存** —— 退化成决策表里「只存账 + 重放」那一行。

    只多这一件事。`at(i)` 仍**正确**（照样能拿回版本 i），但代价退回 `Θ(i)`。
    ⇒ 它把判据 `V3`（「重放步数 < every」）与「结果对不对」**分开**：
      只看 `V1` 的话，这个退化实现**完全合格**。
    """

    def at(self, i: int) -> Kernel:
        if not 0 <= i <= len(self._ops):
            raise IndexError(f"版本 {i} 不在 0..{len(self._ops)}")
        k = replay_ops(self._plugin, self._items, self._ops, i)
        self._last_replay = i
        return k


class _StaleSnapshot(VersionStore):
    """对照②：快照**存错一处**（把第一个方向的 `rank` 加 1）。

    只多这一件事。账是对的、重放是对的，坏的是**快照** ——
    ⇒ 判据 `V1`（`at(i)` 与纯重放对拍）必须红，而 `V2` / `V3` 不受影响。
    """

    def _record(self, op: tuple) -> Any:
        out = super()._record(op)
        v = len(self._ops)
        if v in self._snaps:
            self._snaps[v]["方向"][0]["rank"] += 1
        return out


class _DroppedOp(VersionStore):
    """对照③：操作**执行了但没进账** ⇒ 账比内核短。

    只多这一件事（`_ops` 不 append）。结构改得对、快照也不会有，
    但**版本号追不上内核** ⇒ 判据 `V2`（`at(最新)` 等于当前内核）必须红。
    """

    def _record(self, op: tuple) -> Any:
        return _apply(self._kernel, op)


__all__ = ["DEFAULT_EVERY", "VersionStore", "replay_ops"]
